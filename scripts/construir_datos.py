"""Construye las capas GeoJSON de datos/ a partir de las fuentes públicas.

Uso:
    python scripts/construir_datos.py            # usa la caché de fuentes/ si existe
    python scripts/construir_datos.py --refrescar  # vuelve a descargar todo

Fuentes:
  - YoViajo/geodatos: departamentos, 339 municipios (INE 2012), distritos y
    unidades vecinales de Santa Cruz de la Sierra.
  - OpenStreetMap (Overpass): barrios/zonas/urbanizaciones con polígono y los
    barrios que solo existen como punto.

Todas las capas salen con el mismo esquema de propiedades:
    id, nombre, nivel, municipio, provincia, departamento, fuente
"""
import argparse
import io
import json
import re
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from shapely import STRtree
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import linemerge, polygonize, unary_union
from shapely.validation import make_valid

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from app.texto import clave, normalizar  # noqa: E402 — misma normalización que usa la API

FUENTES = RAIZ / "fuentes"
DATOS = RAIZ / "datos"

GEODATOS = "https://raw.githubusercontent.com/YoViajo/geodatos/master/"
OVERPASS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
USER_AGENT = "barrios-bolivia/4.1 (construccion de datos)"

# Tolerancia de simplificación en grados (~0.0001° ≈ 11 m). Los municipios son
# enormes y se simplifican más; las capas urbanas conservan casi todo el detalle.
SIMPLIFICAR = {"departamento": 0.001, "municipio": 0.0003, "macrodistrito": 0.00005, "distrito": 0.00005,
               "zona": 0.00003, "localidad": 0.00005, "uv": 0.00002, "barrio": 0.00002,
               "condominio": 0.00002, "canton": 0.0001}

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# Descargas con caché
# ---------------------------------------------------------------------------
def descargar(url: str, destino: Path, refrescar: bool, data: bytes | None = None) -> bytes:
    if destino.exists() and not refrescar:
        return destino.read_bytes()
    print(f"  ↓ {url[:90]}")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as r:
        contenido = r.read()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(contenido)
    return contenido


def overpass(consulta: str, nombre_cache: str, refrescar: bool) -> dict:
    destino = FUENTES / nombre_cache
    if destino.exists() and not refrescar:
        return json.loads(destino.read_text(encoding="utf-8"))
    cuerpo = urllib.parse.urlencode({"data": consulta}).encode()
    ultimo_error = None
    for intento in range(6):
        url = OVERPASS[intento % len(OVERPASS)]
        try:
            contenido = descargar(url, destino, True, data=cuerpo)
            resultado = json.loads(contenido)  # un 429/504 devuelve HTML y falla aquí
            return resultado
        except Exception as e:  # noqa: BLE001 — cualquier fallo de red o de parseo se reintenta
            ultimo_error = e
            destino.unlink(missing_ok=True)
            espera = 30 * (intento + 1)
            print(f"  ! Overpass falló en {url} ({e}); reintento en {espera}s")
            time.sleep(espera)
    raise RuntimeError(f"Overpass no respondió tras varios intentos: {ultimo_error}")


# ---------------------------------------------------------------------------
# Utilidades de geometría
# ---------------------------------------------------------------------------
def limpiar(geom, nivel: str):
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        geom = unary_union([g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
    geom = geom.simplify(SIMPLIFICAR[nivel], preserve_topology=True)
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return geom


def sin_huecos(geom):
    if geom.geom_type == "Polygon":
        return Polygon(geom.exterior)
    if geom.geom_type == "MultiPolygon":
        return unary_union([Polygon(p.exterior) for p in geom.geoms])
    return geom


def redondear(obj, decimales=6):
    if isinstance(obj, float):
        return round(obj, decimales)
    if isinstance(obj, (list, tuple)):
        return [redondear(x, decimales) for x in obj]
    return obj


def feature(geom, props: dict) -> dict:
    g = mapping(geom)
    return {"type": "Feature", "properties": props,
            "geometry": {"type": g["type"], "coordinates": redondear(g["coordinates"])}}


def escribir(nombre: str, features: list) -> None:
    DATOS.mkdir(exist_ok=True)
    ruta = DATOS / nombre
    ruta.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                               ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  ✓ {nombre}: {len(features)} elementos, {ruta.stat().st_size / 1e6:.1f} MB")


# La capa del INE viene sin tildes ni eñes. La búsqueda las ignora igual; esto solo
# corrige cómo se muestran los nombres más consultados. Ampliar a medida que aparezcan.
TILDES = {
    "Potosi": "Potosí", "Nuestra Senora de La Paz": "Nuestra Señora de La Paz",
    "Cuatro Canadas": "Cuatro Cañadas", "Concepcion": "Concepción", "Pailon": "Pailón",
    "Robore": "Roboré", "Yapacani": "Yapacaní", "San Julian": "San Julián",
    "San Matias": "San Matías", "San Ramon": "San Ramón", "Puerto Suarez": "Puerto Suárez",
    "Ascencion de Guarayos": "Ascensión de Guarayos", "San Jose de Chiquitos": "San José de Chiquitos",
    "Fernandez Alonso": "Fernández Alonso", "Gutierrez": "Gutiérrez", "Colpa Belgica": "Colpa Bélgica",
    "Lago Poopo": "Lago Poopó",
    # Provincias que en el origen traen "?" en lugar de ñ o tilde.
    "?uflo De Chavez": "Ñuflo de Chávez", "Abun?": "Abuná", "Alonso de Iba?ez": "Alonso de Ibáñez",
    "Andres Iba?ez": "Andrés Ibáñez", "Mu?ecas": "Muñecas", "Zuda?ez": "Zudáñez",
}


CONECTORES = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "a"}


SIGLAS = {"UV", "OTB", "OTBS", "UPEA", "UMSA", "YPFB", "ENDE", "COTEL", "CNS", "FAB", "SRL"}
ROMANO = re.compile(r"^(X{0,3})(IX|IV|V?I{0,3})$")


def _palabra_titulo(palabra: str, primera: bool) -> str:
    base = palabra.strip(".,-")
    if base in SIGLAS or (base and ROMANO.match(base)):  # 'UV 8', 'Distrito II', 'Villa Fátima III'
        return palabra
    if not primera and palabra.lower() in CONECTORES:
        return palabra.lower()
    # str.title() rompe "1RO" en "1Ro" y "D'ORBIGNY" en "D'Orbigny" está bien; se usa
    # mayúscula solo en la primera letra alfabética.
    for i, c in enumerate(palabra):
        if c.isalpha():
            return palabra[:i] + c.upper() + palabra[i + 1:].lower()
    return palabra.lower()


def titulo(texto: str | None) -> str | None:
    """Normaliza espacios, pasa MAYÚSCULAS a título y repone tildes conocidas.

    'SANTA CRUZ DE LA SIERRA' → 'Santa Cruz de la Sierra'; '1RO DE MAYO' → '1ro de Mayo'.
    """
    if not texto:
        return texto
    texto = " ".join(str(texto).split())
    if texto.isupper():
        texto = " ".join(_palabra_titulo(p, i == 0) for i, p in enumerate(texto.split()))
    return TILDES.get(texto, texto)


# ---------------------------------------------------------------------------
# Capas administrativas (YoViajo/geodatos)
# ---------------------------------------------------------------------------
def construir_departamentos(refrescar):
    crudo = json.loads(descargar(GEODATOS + "limites/bol_lim_dpto.json", FUENTES / "bol_lim_dpto.json", refrescar))
    salida = []
    for f in crudo["features"]:
        p = f["properties"]
        geom = limpiar(shape(f["geometry"]), "departamento")
        salida.append(feature(geom, {
            "id": f"dep-{p['ID_DEP']}", "nombre": titulo(p["NOM_DEP"]), "nivel": "departamento",
            "municipio": None, "provincia": None, "departamento": titulo(p["NOM_DEP"]),
            "fuente": "YoViajo/geodatos (límites departamentales)"}))
    return salida


def construir_municipios(refrescar):
    crudo = descargar(GEODATOS + "limites/bol_municipios_339_pob2012_ed.geojson.tar.gz",
                      FUENTES / "bol_municipios.tar.gz", refrescar)
    with tarfile.open(fileobj=io.BytesIO(crudo)) as tar:
        miembro = next(m for m in tar.getmembers() if m.name.endswith(".geojson"))
        datos = json.load(tar.extractfile(miembro))

    # El archivo trae 344 polígonos para 339 municipios: algunos vienen partidos.
    por_codigo: dict[str, dict] = {}
    for f in datos["features"]:
        p = f["properties"]
        entrada = por_codigo.setdefault(p["CODIGO"], {"props": p, "geoms": []})
        entrada["geoms"].append(shape(f["geometry"]))

    salida = []
    for codigo, e in sorted(por_codigo.items()):
        p = e["props"]
        geom = limpiar(unary_union(e["geoms"]), "municipio")
        # La capa del INE incluye lagos y salares como si fueran municipios.
        if p["NOM_DEP"].strip().upper() in ("LAGO", "SALAR"):
            salida.append(feature(geom, {
                "id": f"esp-{codigo.replace(' ', '-').lower()}", "nombre": titulo(p["NOM_MUN"]),
                "nivel": "area_especial", "municipio": None, "provincia": None, "departamento": None,
                "fuente": "YoViajo/geodatos (339 municipios, INE 2012)"}))
            continue
        salida.append(feature(geom, {
            "id": f"mun-{codigo}", "nombre": titulo(p["NOM_MUN"]), "nivel": "municipio",
            "municipio": titulo(p["NOM_MUN"]), "provincia": titulo(p["NOM_PROV"]),
            "departamento": titulo(p["NOM_DEP"]), "codigo_ine": codigo,
            "poblacion_2012": p.get("pob2012_"),
            "fuente": "YoViajo/geodatos (339 municipios, INE 2012)"}))
    return salida


def construir_scz(refrescar):
    """Distritos municipales y unidades vecinales de Santa Cruz de la Sierra."""
    base = {"municipio": "Santa Cruz de la Sierra", "provincia": "Andrés Ibáñez",
            "departamento": "Santa Cruz"}

    crudo = json.loads(descargar(GEODATOS + "scz_munic/scz_distritos_municipales.geojson",
                                 FUENTES / "scz_distritos.geojson", refrescar))
    distritos = []
    for f in crudo["features"]:
        dm = f["properties"]["dm_id"]
        geom = limpiar(shape(f["geometry"]), "distrito")
        if geom is None:
            continue
        distritos.append(feature(geom, {
            "id": f"scz-dm-{dm}", "nombre": f"Distrito {dm}", "nivel": "distrito", **base,
            "distrito": str(dm), "fuente": "GAM Santa Cruz vía YoViajo/geodatos"}))

    crudo = json.loads(descargar(GEODATOS + "scz_munic/scz_unidades_vecinales.geojson",
                                 FUENTES / "scz_uv.geojson", refrescar))
    uvs = []
    for f in crudo["features"]:
        p = f["properties"]
        if not p.get("uv"):
            continue
        geom = limpiar(shape(f["geometry"]), "uv")
        if geom is None:
            continue
        uvs.append(feature(geom, {
            "id": f"scz-uv-{p['dm']}-{p['uv']}-{p['objectid']}", "nombre": f"UV {p['uv']}",
            "nivel": "uv", **base, "distrito": str(p["dm"]), "uv": str(p["uv"]),
            "fuente": "GAM Santa Cruz vía YoViajo/geodatos"}))
    return distritos, uvs


# ---------------------------------------------------------------------------
# OpenStreetMap
# ---------------------------------------------------------------------------
Q_POLIGONOS = """
[out:json][timeout:600];
area["ISO3166-1"="BO"][admin_level=2]->.b;
(
  way["place"~"^(neighbourhood|suburb|quarter)$"]["name"](area.b);
  rel["place"~"^(neighbourhood|suburb|quarter)$"]["name"](area.b);
  way["landuse"="residential"]["name"](area.b);
  rel["landuse"="residential"]["name"](area.b);
  rel["boundary"="administrative"]["admin_level"~"^(9|10)$"]["name"](area.b);
);
out geom;
"""

Q_PUNTOS = """
[out:json][timeout:300];
area["ISO3166-1"="BO"][admin_level=2]->.b;
node["place"~"^(neighbourhood|suburb|quarter|town|village)$"]["name"](area.b);
out;
"""


def geometria_osm(el):
    if el["type"] == "way":
        coords = [(n["lon"], n["lat"]) for n in el.get("geometry", [])]
        if len(coords) >= 4 and coords[0] == coords[-1]:
            return Polygon(coords)
        return None
    # Relación: ensamblar anillos exteriores e interiores a partir de sus vías.
    exteriores, interiores = [], []
    for m in el.get("members", []):
        if m["type"] != "way" or not m.get("geometry"):
            continue
        linea = [(n["lon"], n["lat"]) for n in m["geometry"]]
        (interiores if m.get("role") == "inner" else exteriores).append(linea)
    if not exteriores:
        return None
    polis_ext = list(polygonize(linemerge(exteriores)))
    if not polis_ext:
        return None
    geom = unary_union(polis_ext)
    if interiores:
        polis_int = list(polygonize(linemerge(interiores)))
        if polis_int:
            geom = geom.difference(unary_union(polis_int))
    return geom


def tipo_osm(tags):
    if tags.get("boundary") == "administrative":
        return f"admin_level {tags.get('admin_level')}"
    if tags.get("place"):
        return tags["place"]
    return "residential"


LOCALIDADES = {"village", "hamlet", "town", "isolated_dwelling", "farm", "locality"}
PRIMERA_PALABRA_LOCALIDAD = {"comunidad", "campamento", "estancia", "colonia"}
PRIMERA_PALABRA_DESCARTAR = {"edificio", "torre", "torres", "bloque"}


def nivel_osm(tags) -> str | None:
    """Traduce las etiquetas OSM a un nivel del esquema; None = no es una zona.

    En Bolivia admin_level 9 mezcla cantones rurales con macrodistritos (La Paz)
    y comunas (Cochabamba); admin_level 10 son distritos o barrios.
    """
    nombre = tags["name"].strip()
    primera = nombre.split()[0].lower()
    tipo = tipo_osm(tags)
    if tipo == "admin_level 9":
        return "canton" if primera in ("canton", "cantón") else "zona"
    if tipo == "admin_level 10":
        return "distrito" if primera == "distrito" else "barrio"
    if tipo in ("suburb", "quarter"):
        return "zona"
    if tipo == "neighbourhood":
        return "barrio"
    if tipo in LOCALIDADES or primera in PRIMERA_PALABRA_LOCALIDAD:
        return "localidad"
    if tipo != "residential" or primera in PRIMERA_PALABRA_DESCARTAR:
        return None
    return "condominio" if primera == "condominio" else "barrio"


class Ubicador:
    """Asigna municipio/departamento a una geometría por su punto representativo."""

    def __init__(self, municipios):
        municipios = [f for f in municipios if f["properties"]["nivel"] == "municipio"]
        self.props = [f["properties"] for f in municipios]
        self.arbol = STRtree([shape(f["geometry"]) for f in municipios])

    def __call__(self, punto: Point):
        for i in self.arbol.query(punto, predicate="within"):
            return self.props[i]
        return None


def construir_osm(municipios, uvs, refrescar):
    ubicar = Ubicador(municipios)
    uv_arbol = STRtree([shape(f["geometry"]) for f in uvs])

    print("  · Overpass: polígonos de barrios/zonas (puede tardar varios minutos)")
    crudo = overpass(Q_POLIGONOS, "osm_poligonos.json", refrescar)
    # Una zona puede venir en varios polígonos con el mismo nombre (partes sueltas,
    # o dibujada como vía y como relación): se agrupan y se unen.
    grupos: dict[tuple, dict] = {}
    for el in crudo["elements"]:
        tags = el.get("tags", {})
        nivel = nivel_osm(tags)
        if nivel is None:
            continue
        try:
            geom = geometria_osm(el)
        except Exception:  # noqa: BLE001 — geometrías OSM rotas se descartan
            geom = None
        if geom is None or geom.is_empty:
            continue
        mun = ubicar(geom.representative_point())
        if mun is None:  # fuera de Bolivia (las áreas de Overpass incluyen bordes)
            continue
        nombre = " ".join(tags["name"].split())
        g = grupos.setdefault((nombre.lower(), mun["id"], nivel), {
            "id": f"osm-{el['type'][0]}{el['id']}", "nombre": nombre, "nivel": nivel,
            "tipo_osm": tipo_osm(tags), "mun": mun, "geoms": []})
        g["geoms"].append(geom)

    barrios, con_poligono = [], set()
    for (nombre_min, mun_id, _), g in grupos.items():
        geom = limpiar(unary_union(g["geoms"]), "barrio")
        if geom is None:
            continue
        mun = g["mun"]
        con_poligono.add((nombre_min, mun_id))
        barrios.append(feature(geom, {
            "id": g["id"], "nombre": g["nombre"], "nivel": g["nivel"], "tipo_osm": g["tipo_osm"],
            "municipio": mun["municipio"], "provincia": mun["provincia"],
            "departamento": mun["departamento"], "fuente": "OpenStreetMap"}))

    print("  · Overpass: barrios y poblados que solo existen como punto")
    crudo = overpass(Q_PUNTOS, "osm_puntos_v2.json", refrescar)
    puntos = []
    for el in crudo["elements"]:
        tags = el["tags"]
        nombre = " ".join(tags["name"].split())
        punto = Point(el["lon"], el["lat"])
        mun = ubicar(punto)
        if mun is None or (nombre.lower(), mun["id"]) in con_poligono:
            continue  # fuera de Bolivia, o ya lo cubre un polígono con el mismo nombre
        uv = None
        for i in uv_arbol.query(punto, predicate="within"):
            uv = uvs[i]["properties"]["id"]
            break
        nivel = "localidad" if tags.get("place") in LOCALIDADES else (
            "zona" if tags.get("place") in ("suburb", "quarter") else "barrio")
        puntos.append({"type": "Feature", "geometry": mapping(punto), "properties": {
            "id": f"osm-n{el['id']}", "nombre": nombre, "nivel": nivel,
            "tipo_osm": tags.get("place"), "municipio": mun["municipio"], "provincia": mun["provincia"],
            "departamento": mun["departamento"], "uv_contenedora": uv, "fuente": "OpenStreetMap (punto)"}})
    return barrios, puntos


# ---------------------------------------------------------------------------
# Capas oficiales municipales (copias en Archive.org, ver fuentes_oficiales.py)
# ---------------------------------------------------------------------------
ARCHIVE = "https://archive.org/download/{paquete}/dataset.geojson"


def a_wgs84(geom, epsg: int):
    if epsg == 4326:
        return geom
    import numpy as np
    import shapely
    from pyproj import Transformer  # solo hace falta para construir datos, no en la API
    t = Transformer.from_crs(epsg, 4326, always_xy=True)
    return shapely.transform(geom, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))


def construir_oficiales(municipios, refrescar):
    from fuentes_oficiales import FUENTES as CAPAS_OFICIALES

    ubicar = Ubicador(municipios)
    salida = []
    for f in CAPAS_OFICIALES:
        crudo = json.loads(descargar(ARCHIVE.format(paquete=f["paquete"]),
                                     FUENTES / "oficiales" / f"{f['paquete']}.geojson", refrescar))
        grupos: dict[str, dict] = {}
        for i, feat in enumerate(crudo["features"]):
            p = feat["properties"]
            if not feat.get("geometry"):
                continue
            nombre = titulo(f["nombre"](p))
            if not nombre:
                continue
            clave_grupo = f["agrupar"](p) if f.get("agrupar") else str(i)
            if not clave_grupo:
                continue
            g = grupos.setdefault(clave_grupo, {"nombre": nombre, "props": p, "geoms": []})
            g["geoms"].append(a_wgs84(shape(feat["geometry"]), f["epsg"]))

        n = 0
        for clave_grupo, g in grupos.items():
            geom = limpiar(unary_union(g["geoms"]), f["nivel"])
            if geom is None:
                continue
            mun = ubicar(geom.representative_point())
            if mun is None:
                continue
            extra = {k: v for k, v in (f["extra"](g["props"]) if f.get("extra") else {}).items() if v not in (None, "")}
            sufijo = normalizar(clave_grupo).replace(" ", "-") if f.get("agrupar") else clave_grupo
            salida.append(feature(geom, {
                "id": f"{f['clave']}-{sufijo}", "nombre": g["nombre"], "nivel": f["nivel"],
                "municipio": mun["municipio"], "provincia": mun["provincia"], "departamento": mun["departamento"],
                "oficial": True, **extra, "fuente": f["fuente"]}))
            n += 1
        print(f"  · {f['clave']}: {n} {f['nivel']}")
    return salida


# ---------------------------------------------------------------------------
# Localidades del Censo 2024 (INE, vía mauforonda/atlasurbano)
# ---------------------------------------------------------------------------
INE_MANZANOS = "https://raw.githubusercontent.com/mauforonda/atlasurbano/main/datos/manzanos.parquet"
# Los manzanos de una localidad están separados por calles: se engrosan ~30 m para
# unirlos en una mancha continua y luego se devuelven ~15 m hacia adentro.
INE_ENGROSAR, INE_ADELGAZAR = 0.0003, 0.00015


def construir_localidades_ine(municipios, refrescar):
    import numpy as np
    import pyarrow.parquet as pq
    import shapely

    ruta = FUENTES / "ine_manzanos.parquet"
    descargar(INE_MANZANOS, ruta, refrescar)
    tabla = pq.read_table(ruta, columns=["departamento", "municipio", "nombre", "geometry"])
    geoms = shapely.from_wkb(tabla.column("geometry").to_numpy(zero_copy_only=False))
    claves = list(zip(tabla.column("departamento").to_pylist(), tabla.column("municipio").to_pylist(),
                      tabla.column("nombre").to_pylist()))
    por_localidad: dict[tuple, list[int]] = {}
    for i, k in enumerate(claves):
        if k[2]:
            por_localidad.setdefault(k, []).append(i)

    ubicar = Ubicador(municipios)
    salida = []
    for (dep, mun_ine, nombre), idx in por_localidad.items():
        partes = shapely.buffer(geoms[np.array(idx)], INE_ENGROSAR, quad_segs=2)
        mancha = shapely.union_all(partes).buffer(-INE_ADELGAZAR, quad_segs=2)
        # Plazas, parques y canchas no son manzanos y dejan huecos: una localidad los incluye.
        mancha = sin_huecos(mancha)
        geom = limpiar(mancha, "localidad")
        if geom is None:
            continue
        mun = ubicar(geom.representative_point())
        if mun is None:
            continue
        salida.append(feature(geom, {
            "id": f"ine-{normalizar(mun_ine).replace(' ', '-')}-{normalizar(nombre).replace(' ', '-')}",
            "nombre": titulo(nombre), "nivel": "localidad",
            "municipio": mun["municipio"], "provincia": mun["provincia"], "departamento": mun["departamento"],
            "manzanos": len(idx), "oficial": True,
            "fuente": "INE — Censo 2024, localidades por manzano (vía mauforonda/atlasurbano)"}))
    print(f"  · {len(salida)} localidades a partir de {len(claves)} manzanos")
    return salida


# ---------------------------------------------------------------------------
# Quitar de OSM lo que ya cubre una fuente oficial
# ---------------------------------------------------------------------------
def deduplicar_osm(oficiales: list) -> None:
    """Descarta de las capas OSM los lugares que una capa oficial ya tiene con el mismo
    nombre en el mismo municipio (p. ej. 'Cotahuma' como zona OSM y como macrodistrito)."""
    cubiertos = {(clave(f["properties"]["nombre"]), f["properties"]["municipio"]) for f in oficiales}
    for nombre in ("barrios.geojson", "barrios_puntos.geojson"):
        ruta = DATOS / nombre
        if not ruta.exists():
            continue
        fs = json.loads(ruta.read_text(encoding="utf-8"))["features"]
        quedan = [f for f in fs if (clave(f["properties"]["nombre"]), f["properties"]["municipio"]) not in cubiertos]
        print(f"  · {nombre}: {len(fs) - len(quedan)} duplicados de fuentes oficiales")
        escribir(nombre, quedan)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refrescar", action="store_true", help="ignorar la caché y descargar de nuevo")
    parser.add_argument("--sin-osm", action="store_true", help="no consultar Overpass (conserva las capas OSM actuales)")
    parser.add_argument("--sin-ine", action="store_true", help="no generar localidades del Censo 2024")
    args = parser.parse_args()

    print("Departamentos"); departamentos = construir_departamentos(args.refrescar)
    print("Municipios"); municipios = construir_municipios(args.refrescar)
    print("Santa Cruz de la Sierra"); distritos, uvs = construir_scz(args.refrescar)
    escribir("departamentos.geojson", departamentos)
    escribir("municipios.geojson", municipios)
    escribir("distritos.geojson", distritos)
    escribir("unidades_vecinales.geojson", uvs)

    print("Capas oficiales municipales"); oficiales = construir_oficiales(municipios, args.refrescar)
    escribir("oficiales.geojson", oficiales)
    if not args.sin_ine:
        print("Localidades INE (Censo 2024)"); localidades = construir_localidades_ine(municipios, args.refrescar)
        escribir("localidades.geojson", localidades)
    else:
        ruta = DATOS / "localidades.geojson"
        localidades = json.loads(ruta.read_text(encoding="utf-8"))["features"] if ruta.exists() else []

    if not args.sin_osm:
        print("OpenStreetMap"); barrios, puntos = construir_osm(municipios, uvs, args.refrescar)
        escribir("barrios.geojson", barrios)
        escribir("barrios_puntos.geojson", puntos)
    print("Deduplicación"); deduplicar_osm(oficiales + localidades)


if __name__ == "__main__":
    main()
