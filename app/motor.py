"""Motor espacial: coordenada → zonas que la contienen, y nombre → polígono.

Carga las capas de datos/ en memoria una sola vez por proceso. Todas comparten el
esquema de propiedades que genera scripts/construir_datos.py.
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process
from shapely import STRtree
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union

from .texto import CIUDADES_COLOQUIALES, clave, contiene_frase, normalizar, quitar_frase

DATOS = Path(__file__).resolve().parent.parent / "datos"

CAPAS = ["departamentos", "municipios", "distritos", "unidades_vecinales", "oficiales", "localidades", "barrios"]
# Orden de lo general a lo específico; se usa para ordenar la jerarquía y, en la
# búsqueda, para preferir lo más específico cuando dos nombres empatan.
NIVELES = ["departamento", "area_especial", "municipio", "canton", "macrodistrito", "distrito", "zona",
           "localidad", "uv", "barrio", "condominio"]

# Al buscar por nombre sin decir dónde, a igual puntaje se prefiere este orden: un
# municipio antes que un barrio homónimo, y un barrio antes que un condominio.
PRIORIDAD_BUSQUEDA = {"departamento": 0, "municipio": 1, "macrodistrito": 2, "zona": 3, "barrio": 4, "distrito": 5,
                      "uv": 6, "localidad": 7, "canton": 8, "condominio": 9, "area_especial": 10}

UMBRAL_BUSQUEDA = 86       # puntaje mínimo (0-100) para dar un nombre por encontrado
RADIO_BARRIO_PUNTO_M = 600  # distancia máxima a un barrio que solo existe como punto
# Calle o plaza entre dos polígonos: se acepta el polígono más cercano hasta ~45 m.
TOLERANCIA_M = 45
TOLERANCIA_GRADOS = TOLERANCIA_M / 111_000
NIVELES_CON_TOLERANCIA = {"macrodistrito", "distrito", "zona", "localidad", "uv", "barrio"}
# Distancia máxima al punto central de un poblado, según su tamaño en OSM.
RADIO_LOCALIDAD_M = {"town": 3000, "village": 1500}


@dataclass
class Zona:
    props: dict
    geom: object

    @property
    def nivel(self):
        return self.props["nivel"]

    @property
    def id(self):
        return self.props["id"]

    def resumen(self) -> dict:
        return {k: v for k, v in self.props.items() if v is not None}


def distancia_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Formatos de salida del polígono
# ---------------------------------------------------------------------------
def a_geojson(geom) -> dict:
    return mapping(geom)


def a_lista(geom) -> list:
    """Lista de partes; cada parte es su anillo exterior como [[lat, lon], ...].

    Siempre es una lista de partes, aunque la zona tenga una sola, para que el
    consumidor no tenga que distinguir Polygon de MultiPolygon. Los huecos
    interiores se omiten: para eso está el formato GeoJSON.
    """
    partes = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    return [[[round(y, 6), round(x, 6)] for x, y in p.exterior.coords] for p in partes]


def formatear_poligono(geom, formato: str) -> dict:
    c = geom.centroid
    minx, miny, maxx, maxy = geom.bounds
    salida = {"centroide": {"lat": round(c.y, 6), "lon": round(c.x, 6)},
              "bbox": [round(minx, 6), round(miny, 6), round(maxx, 6), round(maxy, 6)]}
    if formato in ("geojson", "ambos"):
        salida["geojson"] = a_geojson(geom)
    if formato in ("lista", "ambos"):
        salida["lista"] = a_lista(geom)
    return salida


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------
class Motor:
    def __init__(self, carpeta: Path = DATOS):
        self.zonas: list[Zona] = []
        for capa in CAPAS:
            ruta = carpeta / f"{capa}.geojson"
            if not ruta.exists():
                continue
            for f in json.loads(ruta.read_text(encoding="utf-8"))["features"]:
                self.zonas.append(Zona(f["properties"], shape(f["geometry"])))
        self.por_id = {z.id: z for z in self.zonas}

        self.puntos: list[dict] = []
        ruta = carpeta / "barrios_puntos.geojson"
        if ruta.exists():
            self.puntos = json.loads(ruta.read_text(encoding="utf-8"))["features"]
        self.arbol_puntos = STRtree([shape(p["geometry"]) for p in self.puntos]) if self.puntos else None

        self.alias = self._cargar_alias(carpeta / "alias.json")
        for z in self._armar_compuestas():
            self.zonas.append(z)
            self.por_id[z.id] = z
        self.arbol = STRtree([z.geom for z in self.zonas])

        self.poblacion = {normalizar(z.props["nombre"]): z.props.get("poblacion_2012") or 0
                          for z in self.zonas if z.nivel == "municipio"}
        self._indexar_nombres()

    # -- carga ---------------------------------------------------------------
    def _cargar_alias(self, ruta: Path) -> dict:
        if not ruta.exists():
            return {"nombres_populares": {}, "compuestas": []}
        return json.loads(ruta.read_text(encoding="utf-8"))

    def _armar_compuestas(self) -> list[Zona]:
        """Convierte las entradas 'compuestas' de alias.json en zonas con polígono propio.

        Cada parte puede venir de:
          zonas:         ids de zonas existentes;
          uvs:           códigos de UV del municipio de la compuesta;
          desde_nombres: nombres de barrios del municipio; si el barrio solo existe
                         como punto se usa la UV que lo contiene.
        """
        # Varias ciudades numeran sus UV (Santa Cruz, Montero): el código solo es único por municipio.
        uv_por_codigo = {(normalizar(z.props["municipio"]), z.props["uv"]): z
                         for z in self.zonas if z.nivel == "uv" and z.props.get("uv")}
        salida = []
        for c in self.alias.get("compuestas", []):
            mun = normalizar(c.get("municipio"))
            partes: dict[str, Zona] = {}
            for i in c.get("zonas", []):
                if i in self.por_id:
                    partes[i] = self.por_id[i]
            for codigo in c.get("uvs", []):
                if uv := uv_por_codigo.get((mun, codigo)):
                    partes[uv.id] = uv
            buscados = {normalizar(n) for n in c.get("desde_nombres", [])}
            for z in self.zonas:
                if normalizar(z.props["nombre"]) in buscados and normalizar(z.props.get("municipio")) == mun:
                    partes[z.id] = z
            for pto in self.puntos:
                pp = pto["properties"]
                if normalizar(pp["nombre"]) in buscados and normalizar(pp["municipio"]) == mun:
                    uv = self.por_id.get(pp.get("uv_contenedora") or "")
                    if uv:
                        partes[uv.id] = uv
            if not partes:
                continue
            props = {"id": f"alias-{normalizar(c['nombre']).replace(' ', '-')}", "nombre": c["nombre"],
                     "nivel": c.get("nivel", "barrio"), "municipio": c.get("municipio"),
                     "provincia": c.get("provincia"), "departamento": c.get("departamento"),
                     "alias": c.get("alias", []), "compuesta_por": sorted(partes),
                     "fuente": c.get("fuente", "alias.json (zona compuesta)")}
            salida.append(Zona(props, unary_union([z.geom for z in partes.values()])))
        return salida

    def _indexar_nombres(self):
        """Arma la lista de candidatos para la búsqueda por nombre.

        Cada candidato: (clave normalizada, tipo, referencia). Un mismo lugar puede
        aparecer varias veces con distintas claves (nombre oficial, alias).
        """
        self.candidatos: list[tuple[str, str, object]] = []
        for z in self.zonas:
            self.candidatos.append((clave(z.props["nombre"]), "zona", z.id))
            for alias in z.props.get("alias", []):
                self.candidatos.append((clave(alias), "zona", z.id))
            if z.nivel == "uv" and z.props.get("uv"):
                self.candidatos.append((f"uv {normalizar(z.props['uv'])}", "zona", z.id))
                self.candidatos.append((f"unidad vecinal {normalizar(z.props['uv'])}", "zona", z.id))
        for i, p in enumerate(self.puntos):
            self.candidatos.append((clave(p["properties"]["nombre"]), "punto", i))
        self.claves = [c[0] for c in self.candidatos]

        # Nombres de lugares administrativos, para reconocer "… en Cochabamba".
        self.lugares: dict[str, set[str]] = {}
        for z in self.zonas:
            if z.nivel in ("municipio", "departamento"):
                self.lugares.setdefault(normalizar(z.props["nombre"]), set()).add(z.id)
        for coloquial, oficial in CIUDADES_COLOQUIALES.items():
            if oficial in self.lugares:
                self.lugares.setdefault(coloquial, set()).update(self.lugares[oficial])

        # Nombre popular por (municipio, nombre OSM normalizado).
        self.populares = {
            (normalizar(mun), normalizar(k)): v
            for mun, tabla in self.alias.get("nombres_populares", {}).items()
            for k, v in tabla.items()
        }

    # -- utilidades ------------------------------------------------------------
    def estadisticas(self) -> dict:
        conteo = {}
        for z in self.zonas:
            conteo[z.nivel] = conteo.get(z.nivel, 0) + 1
        conteo["barrio_solo_punto"] = len(self.puntos)
        return conteo

    def cobertura(self) -> list[dict]:
        """Por municipio: polígonos por nivel, barrios solo-punto y fuentes usadas."""
        tabla: dict[tuple, dict] = {}
        for z in self.zonas:
            if z.nivel in ("departamento", "municipio", "area_especial") or not z.props.get("municipio"):
                continue
            fila = tabla.setdefault((z.props["municipio"], z.props.get("departamento")),
                                    {"poligonos": {}, "fuentes": set(), "solo_punto": 0})
            fila["poligonos"][z.nivel] = fila["poligonos"].get(z.nivel, 0) + 1
            fila["fuentes"].add(z.props["fuente"])
        for p in self.puntos:
            pp = p["properties"]
            fila = tabla.setdefault((pp["municipio"], pp["departamento"]),
                                    {"poligonos": {}, "fuentes": set(), "solo_punto": 0})
            fila["solo_punto"] += 1
        return sorted(({"municipio": m, "departamento": d, "barrios_con_poligono": f["poligonos"].get("barrio", 0),
                        "poligonos": f["poligonos"], "barrios_solo_punto": f["solo_punto"],
                        "fuentes": sorted(f["fuentes"])} for (m, d), f in tabla.items()),
                      key=lambda x: -self.poblacion.get(normalizar(x["municipio"]), 0))

    def nombre_popular(self, props: dict) -> str | None:
        return self.populares.get((normalizar(props.get("municipio")), normalizar(props["nombre"])))

    # -- coordenada → zonas ----------------------------------------------------
    def zonas_en(self, lat: float, lon: float) -> list[Zona]:
        """Todas las zonas que contienen el punto, de la más general a la más específica.

        Dentro de un mismo nivel, las capas oficiales van después que las de OSM y, entre
        iguales, la más chica al final: quien toma el último de cada nivel se queda con
        el polígono oficial más preciso.
        """
        punto = Point(lon, lat)
        hallazgos = [self.zonas[i] for i in self.arbol.query(punto, predicate="within")]

        # Las capas municipales dibujan los polígonos por manzano y dejan fuera calles y
        # plazas. Si ningún polígono de un nivel urbano contiene el punto, vale el más
        # cercano del mismo municipio a menos de TOLERANCIA_M.
        niveles = {z.nivel for z in hallazgos}
        municipio = next((z.props["municipio"] for z in hallazgos if z.nivel == "municipio"), None)
        cercanas: dict[str, tuple[float, Zona]] = {}
        for i in self.arbol.query(punto.buffer(TOLERANCIA_GRADOS)):
            z = self.zonas[i]
            if z.nivel not in NIVELES_CON_TOLERANCIA or z.nivel in niveles or z.props.get("municipio") != municipio:
                continue
            d = z.geom.distance(punto)
            if d <= TOLERANCIA_GRADOS and (z.nivel not in cercanas or d < cercanas[z.nivel][0]):
                cercanas[z.nivel] = (d, z)
        hallazgos += [z for _, z in cercanas.values()]
        return sorted(hallazgos, key=lambda z: (NIVELES.index(z.nivel), bool(z.props.get("oficial")), -z.geom.area))

    def barrio_punto_cercano(self, lat: float, lon: float, municipio: str | None,
                             uv_id: str | None = None) -> dict | None:
        """Barrio registrado solo como punto más cercano al punto consultado.

        Vale si está a menos de RADIO_BARRIO_PUNTO_M, o a cualquier distancia si
        está dentro de la misma UV (en Santa Cruz la UV acota bien el barrio).
        """
        if not self.arbol_puntos:
            return None
        punto = Point(lon, lat)
        # ~0.02° ≈ 2 km: cubre el radio y una UV grande, y sigue siendo barato.
        mejor, mejor_d = None, float("inf")
        for i in self.arbol_puntos.query(punto.buffer(0.02)):
            p = self.puntos[i]["properties"]
            # Los poblados (town/village) marcan el centro de una localidad, no un barrio, y
            # algunos puntos OSM llamados "Distrito 1" son etiquetas de distrito mal puestas.
            if p["nivel"] not in ("barrio", "zona") or normalizar(p["nombre"]).startswith("distrito"):
                continue
            if municipio and p["municipio"] != municipio:
                continue
            plon, plat = self.puntos[i]["geometry"]["coordinates"]
            d = distancia_m(lat, lon, plat, plon)
            misma_uv = uv_id is not None and p.get("uv_contenedora") == uv_id
            if (d < RADIO_BARRIO_PUNTO_M or misma_uv) and d < mejor_d:
                mejor, mejor_d = p, d
        if mejor is None:
            return None
        return {**mejor, "distancia_m": round(mejor_d)}

    def localidad_punto_cercana(self, lat: float, lon: float, municipio: str | None) -> dict | None:
        """Pueblo (town/village) registrado como punto más cercano, dentro de su radio típico."""
        if not self.arbol_puntos:
            return None
        punto = Point(lon, lat)
        mejor, mejor_d = None, float("inf")
        # ~0.03° ≈ 3,3 km: el radio de un pueblo grande.
        for i in self.arbol_puntos.query(punto.buffer(0.03)):
            p = self.puntos[i]["properties"]
            radio = RADIO_LOCALIDAD_M.get(p.get("tipo_osm"))
            if p["nivel"] != "localidad" or radio is None:
                continue
            if municipio and p["municipio"] != municipio:
                continue
            plon, plat = self.puntos[i]["geometry"]["coordinates"]
            d = distancia_m(lat, lon, plat, plon)
            if d < radio and d < mejor_d:
                mejor, mejor_d = p, d
        if mejor is None:
            return None
        return {**mejor, "distancia_m": round(mejor_d)}

    # -- nombre → zona ---------------------------------------------------------
    def interpretar(self, consulta: str) -> tuple[str, set[str] | None, str | None]:
        """Separa el nombre buscado del lugar que lo acota.

        'sopocachi en la paz' → ('sopocachi', {ids de La Paz}, 'la paz')
        Si la consulta es solo un lugar ('cochabamba'), no se acota.
        """
        texto = normalizar(consulta)
        for lugar in sorted(self.lugares, key=len, reverse=True):
            if contiene_frase(texto, lugar):
                resto = quitar_frase(texto, lugar)
                if clave(resto):
                    return resto, self.lugares[lugar], lugar
        return texto, None, None

    def _dentro_de(self, props: dict, ids_lugar: set[str]) -> bool:
        for i in ids_lugar:
            lugar = self.por_id[i].props
            campo = "municipio" if lugar["nivel"] == "municipio" else "departamento"
            if normalizar(props.get(campo)) == normalizar(lugar["nombre"]):
                return True
        return False

    def _props_candidato(self, tipo: str, ref) -> dict:
        return self.por_id[ref].props if tipo == "zona" else self.puntos[ref]["properties"]

    def buscar(self, consulta: str, limite: int = 5) -> dict:
        nombre, ids_lugar, lugar = self.interpretar(consulta)
        k = clave(nombre)
        if not k:
            return {"consulta": consulta, "resultados": []}

        crudos = process.extract(k, self.claves, scorer=fuzz.WRatio, limit=400, score_cutoff=60)
        vistos, resultados = set(), []
        for _, puntaje, idx in crudos:
            clave_c, tipo, ref = self.candidatos[idx]
            if (tipo, ref) in vistos:
                continue
            props = self._props_candidato(tipo, ref)
            if ids_lugar and not self._dentro_de(props, ids_lugar):
                continue
            vistos.add((tipo, ref))
            # WRatio premia coincidencias parciales ("san" dentro de "san pedro");
            # el ratio simple desempata a favor de nombres completos.
            exacto = clave_c == k
            puntaje_final = 100.0 if exacto else round(0.6 * puntaje + 0.4 * fuzz.ratio(k, clave_c), 1)
            resultados.append((puntaje_final, tipo, ref))

        # Mejor puntaje primero. A igualdad: el tipo de lugar más relevante, luego el
        # que está en el municipio más poblado (el "Miraflores" de La Paz antes que el
        # de un municipio rural) y, por último, el que tiene polígono.
        def orden(r):
            puntaje, tipo, ref = r
            props = self._props_candidato(tipo, ref)
            prioridad = PRIORIDAD_BUSQUEDA.get(props["nivel"], len(PRIORIDAD_BUSQUEDA))
            poblacion = self.poblacion.get(normalizar(props.get("municipio") or props["nombre"]), 0)
            return (-puntaje, prioridad, -poblacion, not props.get("oficial"), tipo == "punto")

        resultados.sort(key=orden)
        return {"consulta": consulta, "nombre_interpretado": nombre, "lugar_interpretado": lugar,
                "resultados": [r for r in resultados[:limite]]}

    def resolver(self, tipo: str, ref) -> tuple[dict, object | None, str]:
        """(propiedades, geometría o None, precisión) de un candidato de búsqueda."""
        if tipo == "zona":
            z = self.por_id[ref]
            return z.resumen(), z.geom, ("poligono_compuesto" if "compuesta_por" in z.props else "poligono")
        p = self.puntos[ref]["properties"]
        uv = self.por_id.get(p.get("uv_contenedora") or "")
        if uv:
            return {**p, "aproximado_por": uv.resumen()}, uv.geom, "aproximado_uv"
        return dict(p), None, "solo_punto"

    def punto_de(self, ref: int) -> dict:
        lon, lat = self.puntos[ref]["geometry"]["coordinates"]
        return {"lat": lat, "lon": lon}
