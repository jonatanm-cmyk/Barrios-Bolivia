"""API GeoUbicación Bolivia.

- Coordenada → departamento, municipio, distrito, UV, zona y barrio que la contienen.
- Nombre de zona → polígono, y qué coordenadas caen dentro de ese polígono.
- Registro de las zonas buscadas que no existen o no tienen polígono.
"""
import logging
from pathlib import Path
from typing import Literal, Optional

import shapely
from fastapi import Body, FastAPI, HTTPException, Path as PathParam, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import Point

from . import nominatim, registro
from .motor import UMBRAL_BUSQUEDA, Motor, formatear_poligono

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

DESCRIPCION = """
Servicio geográfico para **toda Bolivia**. Hace dos cosas:

### 1. ¿En qué zona cae esta coordenada?
`GET /ubicar?lat=-17.7694&lon=-63.1950` devuelve departamento, municipio, localidad,
distrito, zona, barrio y calle. Para muchas coordenadas a la vez: `POST /ubicar/lote`.

### 2. ¿Qué coordenadas están dentro de esta zona?
1. `GET /zonas/buscar?nombre=equipetrol` encuentra la zona y devuelve su polígono y su `id_poligono`.
2. `POST /zonas/{id_poligono}/contiene` recibe una lista de coordenadas y dice cuáles caen dentro.

El nombre puede escribirse como lo diría una persona: sin tildes, con errores leves o
indicando la ciudad ("sopocachi en la paz", "uv 45 montero").

---

**Antes de usar el resultado de una búsqueda, revisar `estado`:**

| estado | significado | qué hacer |
|---|---|---|
| `encontrado` | la zona tiene polígono | usarlo |
| `aproximado` | el barrio no tiene polígono propio; se da la UV que lo contiene | usarlo sabiendo que es aproximado |
| `sin_poligono` | el barrio existe solo como un punto | no hay polígono; decidir otra estrategia (p. ej. un radio) |
| `no_encontrado` | no existe con ese nombre | ver `sugerencias` |

Si `ambiguo` es `true`, el nombre existe en varias ciudades: repetir la búsqueda indicando la ciudad.

Las búsquedas que no terminan en `encontrado` quedan registradas (ver **Reportes**).
"""

SECCIONES = [
    {"name": "1. Coordenada → zona", "description": "Saber a qué barrio, zona y municipio pertenece una coordenada."},
    {"name": "2. Zona → polígono", "description": "Buscar una zona por nombre, obtener su polígono y comprobar qué coordenadas caen dentro."},
    {"name": "Reportes", "description": "Qué zonas se buscaron y no existen, y qué cobertura hay en cada municipio."},
    {"name": "Estado del servicio", "description": "Comprobar que la API está en marcha y cuántas zonas tiene cargadas."},
]

app = FastAPI(
    title="GeoUbicación Bolivia",
    summary="Coordenada → zona, y zona → polígono, para toda Bolivia.",
    description=DESCRIPCION,
    version="4.1.0",
    openapi_tags=SECCIONES,
    swagger_ui_parameters={"docExpansion": "list", "defaultModelsExpandDepth": 0, "tryItOutEnabled": True},
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

motor = Motor()
ESTATICOS = Path(__file__).resolve().parent / "static"

Formato = Literal["geojson", "lista", "ambos"]
DESC_FORMATO = ("Cómo devolver el polígono: `geojson` (estándar, para mapas y bases de datos), "
                "`lista` (partes con puntos `[lat, lon]`) o `ambos`.")


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class CoordenadaRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="Latitud en grados (WGS84)", examples=[-17.7694])
    lon: float = Field(..., ge=-180, le=180, description="Longitud en grados (WGS84)", examples=[-63.1950])


class LoteRequest(BaseModel):
    coordenadas: list[CoordenadaRequest] = Field(..., max_length=200, description="Hasta 200 coordenadas")

    model_config = ConfigDict(json_schema_extra={"example": {"coordenadas": [
        {"lat": -17.7694, "lon": -63.1950}, {"lat": -16.5113, "lon": -68.1287}]}})


class CoordenadaConId(CoordenadaRequest):
    id: Optional[str | int] = Field(None, description="Identificador propio (p. ej. el id del inmueble); se devuelve tal cual")


class ContieneRequest(BaseModel):
    coordenadas: list[CoordenadaConId] = Field(..., max_length=5000, description="Hasta 5000 coordenadas")

    model_config = ConfigDict(json_schema_extra={"example": {"coordenadas": [
        {"id": "inmueble-101", "lat": -17.7694, "lon": -63.1950},
        {"id": "inmueble-102", "lat": -17.7900, "lon": -63.1600}]}})


class ContieneResponse(BaseModel):
    zona_id: str
    zona_nombre: str
    municipio: Optional[str] = None
    total: int = Field(description="Coordenadas recibidas")
    total_dentro: int = Field(description="Cuántas caen dentro del polígono (el borde cuenta como dentro)")
    dentro: list[CoordenadaConId]
    fuera: list[CoordenadaConId]


class UbicacionResponse(BaseModel):
    status: Literal["success", "fuera_de_bolivia"] = Field(description="`fuera_de_bolivia` si la coordenada no cae en Bolivia")
    latitud: float
    longitud: float
    barrio_real: Optional[str] = Field(None, description="Barrio, OTB o urbanización")
    precision_barrio: Literal["poligono", "punto_cercano", "nominatim", "sin_datos"] = Field(
        "sin_datos", description="De dónde sale el barrio: `poligono` (el más confiable), `nominatim`, `punto_cercano` o `sin_datos`")
    distancia_barrio_m: Optional[int] = Field(None, description=(
        "Metros al barrio cuando no contiene el punto: barrio-punto cercano, o polígono vecino a menos de 45 m"))
    zona_o_sector: Optional[str] = Field(None, description="Zona o sector, más amplio que el barrio")
    macrodistrito: Optional[str] = Field(None, description="Macrodistrito (La Paz) o comuna (Cochabamba)")
    distrito_municipal: Optional[str] = None
    unidad_vecinal: Optional[str] = Field(None, description="UV (Santa Cruz de la Sierra, Montero)")
    condominio: Optional[str] = None
    localidad: Optional[str] = Field(None, description="Pueblo o ciudad a la que pertenece el punto")
    precision_localidad: Literal["poligono", "nominatim", "punto_cercano", "sin_datos"] = "sin_datos"
    canton: Optional[str] = None
    calle_avenida: Optional[str] = Field(None, description="Solo si `calle=true`")
    municipio_ciudad: Optional[str] = None
    provincia: Optional[str] = None
    departamento: Optional[str] = None
    pais: str = "Bolivia"
    sub_divisiones_encontradas: list[str] = Field(default_factory=list, description="Todos los nombres de subdivisiones detectados")
    jerarquia: list[dict] = Field(default_factory=list, description="Zonas con polígono que contienen el punto, de general a específica, con su `id`")
    zona_mas_especifica: Optional[dict] = Field(None, description="Datos de la zona más chica; incluye el polígono si `incluir_poligono=true`")
    fuente_informacion: str
    direccion_completa: Optional[str] = Field(None, description="Solo si `calle=true`")


class ZonaResultado(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    id_poligono: Optional[str] = Field(None, description="Id a usar en `/zonas/{id}` y `/zonas/{id}/contiene`; vacío si no hay polígono")
    nombre: str
    nivel: Optional[str] = Field(None, description="departamento, municipio, macrodistrito, distrito, zona, localidad, uv, barrio…")
    municipio: Optional[str] = None
    departamento: Optional[str] = None
    precision: str = Field(description="`poligono`, `poligono_compuesto`, `aproximado_uv` o `solo_punto`")
    puntaje: Optional[float] = Field(None, description="Parecido con lo buscado, de 0 a 100")
    poligono: Optional[dict] = Field(None, description="`centroide`, `bbox` [oeste, sur, este, norte] y la geometría en el formato pedido")
    punto: Optional[dict] = Field(None, description="Ubicación, cuando el barrio existe solo como punto")


class BusquedaResponse(BaseModel):
    consulta: str
    estado: Literal["encontrado", "aproximado", "sin_poligono", "no_encontrado"]
    ambiguo: bool = Field(False, description="Hay otra zona con el mismo nombre en otra ciudad")
    nombre_interpretado: Optional[str] = Field(None, description="Lo que se entendió como nombre de la zona")
    lugar_interpretado: Optional[str] = Field(None, description="Ciudad o departamento que acotó la búsqueda, si se indicó")
    zona: Optional[ZonaResultado] = None
    alternativas: list[dict] = Field(default_factory=list, description="Otras zonas que también coinciden")
    sugerencias: list[dict] = Field(default_factory=list, description="Solo con `no_encontrado`: los nombres más parecidos")


# ---------------------------------------------------------------------------
# Coordenada → zonas
# ---------------------------------------------------------------------------
def _nombre(zona) -> Optional[str]:
    return zona.props["nombre"] if zona else None


def _subdivisiones(zonas, direccion_osm: dict) -> list[str]:
    """Subdivisiones catastrales: las que ya daba la versión anterior (Nominatim) más las de
    los polígonos locales (macrodistrito, distrito, zona, UV, OTB/barrio), sin repetir."""
    nombres = [z.props["nombre"] for z in zonas
               if z.nivel not in ("departamento", "municipio", "area_especial", "localidad")]
    nombres += [direccion_osm[k] for k in ("neighbourhood", "residential", "quarter", "suburb",
                                           "city_district", "district")
                if direccion_osm.get(k)]
    vistos, salida = set(), []
    for n in nombres:
        if n.lower() not in vistos:
            vistos.add(n.lower())
            salida.append(n)
    return salida


def resolver_ubicacion(lat: float, lon: float, calle: bool, incluir_poligono: bool,
                       formato: str) -> UbicacionResponse:
    zonas = motor.zonas_en(lat, lon)
    por_nivel = {z.nivel: z for z in zonas}  # ordenadas de mayor a menor área: gana la más chica
    if not ({"departamento", "municipio", "area_especial"} & por_nivel.keys()):
        registro.registrar("fuera_de_bolivia", f"{lat},{lon}", lat=lat, lon=lon)
        return UbicacionResponse(status="fuera_de_bolivia", latitud=lat, longitud=lon,
                                 fuente_informacion="Capas de límites de Bolivia")

    mun = por_nivel.get("municipio")
    dep = por_nivel.get("departamento")
    especial = por_nivel.get("area_especial")
    nom = nominatim.direccion_segura(lat, lon) if calle else None
    direccion_osm = (nom or {}).get("address", {})

    # Barrio, de la fuente más confiable a la menos:
    #   1. un polígono local que contiene el punto;
    #   2. el barrio que Nominatim asigna a esa dirección exacta;
    #   3. el barrio-punto de OSM más cercano (cuando Nominatim no responde o calle=false);
    #   4. el sector ('suburb') de Nominatim, que es más amplio que un barrio.
    barrio, precision, distancia_barrio = None, "sin_datos", None
    fuentes = {z.props["fuente"] for z in zonas}
    uv = por_nivel.get("uv")
    nombre_mun = mun.props["municipio"] if mun else None
    barrio_nom = (direccion_osm.get("neighbourhood") or direccion_osm.get("residential")
                  or direccion_osm.get("quarter"))
    if barrio_nom and barrio_nom.lower().startswith("distrito"):  # etiqueta de distrito, no de barrio (Oruro)
        barrio_nom = None
    if "barrio" in por_nivel:
        z = por_nivel["barrio"]
        barrio, precision = motor.nombre_popular(z.props) or z.props["nombre"], "poligono"
        if not z.geom.covers(punto := Point(lon, lat)):  # tomado por tolerancia (calle o plaza vecina)
            distancia_barrio = round(z.geom.distance(punto) * 111_000)
    elif barrio_nom:
        barrio, precision = motor.nombre_popular({"municipio": nombre_mun, "nombre": barrio_nom}) or barrio_nom, "nominatim"
    elif cercano := motor.barrio_punto_cercano(lat, lon, nombre_mun, uv.id if uv else None):
        barrio, precision = motor.nombre_popular(cercano) or cercano["nombre"], "punto_cercano"
        distancia_barrio = cercano["distancia_m"]
        fuentes.add(cercano["fuente"])
    elif direccion_osm.get("suburb"):
        barrio, precision = direccion_osm["suburb"], "nominatim"
    localidad, precision_loc = _nombre(por_nivel.get("localidad")), "poligono"
    if not localidad:
        # Nominatim sabe a qué pueblo pertenece el punto aunque OSM no tenga su polígono
        # (p. ej. "El Paso", en Quillacollo). Se prefiere lo más local.
        localidad, precision_loc = (direccion_osm.get("hamlet") or direccion_osm.get("village")
                                    or direccion_osm.get("town") or direccion_osm.get("city")), "nominatim"
        if localidad and localidad.lower().startswith("municipio "):  # "Municipio Nuestra Señora de La Paz"
            localidad = localidad[len("municipio "):]
    if not localidad and (loc := motor.localidad_punto_cercana(lat, lon, mun.props["municipio"] if mun else None)):
        localidad, precision_loc = loc["nombre"], "punto_cercano"
        fuentes.add(loc["fuente"])
    if not localidad:
        precision_loc = "sin_datos"

    if precision == "sin_datos" and mun:
        registro.registrar("coordenada_sin_barrio", f"{lat},{lon}", lat=lat, lon=lon, localidad=localidad,
                           municipio=mun.props["municipio"], departamento=mun.props["departamento"])
    if nom:
        fuentes.add("Nominatim (calle y dirección)")

    zona = por_nivel.get("zona")
    distrito = por_nivel.get("distrito")
    mas_especifica = zonas[-1]
    detalle = mas_especifica.resumen()
    if incluir_poligono:
        detalle["poligono"] = formatear_poligono(mas_especifica.geom, formato)

    return UbicacionResponse(
        status="success", latitud=lat, longitud=lon,
        barrio_real=barrio, precision_barrio=precision, distancia_barrio_m=distancia_barrio,
        zona_o_sector=zona.props["nombre"] if zona else direccion_osm.get("suburb"),
        distrito_municipal=distrito.props["nombre"] if distrito else direccion_osm.get("city_district"),
        macrodistrito=_nombre(por_nivel.get("macrodistrito")),
        unidad_vecinal=uv.props["nombre"] if uv else None,
        condominio=_nombre(por_nivel.get("condominio")),
        localidad=localidad, precision_localidad=precision_loc,
        canton=_nombre(por_nivel.get("canton")),
        calle_avenida=(nom or {}).get("calle"),
        municipio_ciudad=mun.props["municipio"] if mun else (especial.props["nombre"] if especial else None),
        provincia=mun.props["provincia"] if mun else None,
        departamento=dep.props["nombre"] if dep else None,
        sub_divisiones_encontradas=_subdivisiones(zonas, direccion_osm),
        jerarquia=[{"id": z.id, "nombre": z.props["nombre"], "nivel": z.nivel} for z in zonas],
        zona_mas_especifica=detalle,
        fuente_informacion=" + ".join(sorted(fuentes)),
        direccion_completa=(nom or {}).get("direccion_completa"),
    )


# ---------------------------------------------------------------------------
# 1. Coordenada → zona
# ---------------------------------------------------------------------------
SECCION_1 = "1. Coordenada → zona"
SECCION_2 = "2. Zona → polígono"

Q_CALLE = Query(True, description="Consultar también la calle y la dirección (Nominatim, ~1 s). Con `false` responde al instante")
Q_POLIGONO = Query(False, description="Incluir el polígono de la zona más específica")


@app.get("/ubicar", tags=[SECCION_1], response_model=UbicacionResponse, response_model_exclude_none=True,
         summary="Ubicar una coordenada")
def ubicar(
    lat: float = Query(..., ge=-90, le=90, description="Latitud (WGS84)", examples=[-17.7694]),
    lon: float = Query(..., ge=-180, le=180, description="Longitud (WGS84)", examples=[-63.1950]),
    calle: bool = Q_CALLE,
    incluir_poligono: bool = Q_POLIGONO,
    formato: Formato = Query("geojson", description=DESC_FORMATO),
):
    """Devuelve a qué departamento, municipio, localidad, distrito, zona y barrio pertenece la coordenada."""
    return resolver_ubicacion(lat, lon, calle, incluir_poligono, formato)


@app.post("/ubicar/lote", tags=[SECCION_1], response_model=list[UbicacionResponse], response_model_exclude_none=True,
          summary="Ubicar varias coordenadas")
def ubicar_lote(req: LoteRequest, calle: bool = Query(False, description=(
        "Consultar calle y dirección. Nominatim admite 1 consulta por segundo: con `true` un lote de 200 tarda más de 3 minutos"))):
    """Igual que `/ubicar`, para hasta 200 coordenadas. Devuelve los resultados en el mismo orden."""
    return [resolver_ubicacion(c.lat, c.lon, calle, False, "geojson") for c in req.coordenadas]


# ---------------------------------------------------------------------------
# 2. Zona → polígono
# ---------------------------------------------------------------------------
def _id_poligono(props: dict, precision: str) -> Optional[str]:
    if precision == "aproximado_uv":
        return props["aproximado_por"]["id"]
    return None if precision == "solo_punto" else props["id"]


def _resumen_candidato(tipo, ref, puntaje) -> dict:
    props, geom, precision = motor.resolver(tipo, ref)
    return {"id": props["id"], "id_poligono": _id_poligono(props, precision), "nombre": props["nombre"],
            "nivel": props.get("nivel"), "municipio": props.get("municipio"),
            "departamento": props.get("departamento"), "puntaje": puntaje, "precision": precision}


def _zona_o_404(zona_id: str):
    z = motor.por_id.get(zona_id)
    if not z:
        raise HTTPException(404, f"No existe una zona con id '{zona_id}'. Usar el `id_poligono` que devuelve /zonas/buscar.")
    return z


@app.get("/zonas/buscar", tags=[SECCION_2], response_model=BusquedaResponse, response_model_exclude_none=True,
         summary="Buscar una zona por nombre")
def buscar_zona(
    nombre: str = Query(..., min_length=2, description="Nombre de la zona o barrio, opcionalmente con la ciudad",
                        examples=["equipetrol", "sopocachi en la paz", "uv 45", "villa adela el alto"]),
    formato: Formato = Query("geojson", description=DESC_FORMATO),
    limite: int = Query(5, ge=1, le=20, description="Cuántas alternativas devolver como máximo"),
):
    """Encuentra la zona que mejor coincide con el nombre y devuelve su polígono.

    Guardar `zona.id_poligono` para consultar luego qué coordenadas caen dentro
    (`POST /zonas/{id_poligono}/contiene`).
    """
    busqueda = motor.buscar(nombre, limite=limite + 1)
    validos = [r for r in busqueda["resultados"] if r[0] >= UMBRAL_BUSQUEDA]
    base = {"consulta": nombre, "nombre_interpretado": busqueda.get("nombre_interpretado"),
            "lugar_interpretado": busqueda.get("lugar_interpretado")}

    if not validos:
        sugerencias = [_resumen_candidato(t, r, p) for p, t, r in busqueda["resultados"][:3]]
        registro.registrar("no_encontrada", nombre, lugar=base["lugar_interpretado"],
                           sugerencias=[s["nombre"] for s in sugerencias])
        return {**base, "estado": "no_encontrado", "zona": None, "sugerencias": sugerencias}

    puntaje, tipo, ref = validos[0]
    props, geom, precision = motor.resolver(tipo, ref)
    zona = {**props, "id_poligono": _id_poligono(props, precision), "puntaje": puntaje, "precision": precision}
    if geom is not None:
        zona["poligono"] = formatear_poligono(geom, formato)
    if tipo == "punto":
        zona["punto"] = motor.punto_de(ref)

    alternativas = [_resumen_candidato(t, r, p) for p, t, r in validos[1:limite + 1]]
    # Mismo puntaje en otro lugar (p. ej. "San Ramón" existe en Santa Cruz y en Beni).
    lugar_mejor = (props.get("municipio"), props.get("departamento"))
    ambiguo = (not base["lugar_interpretado"] and any(
        a["puntaje"] == puntaje and (a["municipio"], a["departamento"]) != lugar_mejor for a in alternativas))

    estado = {"poligono": "encontrado", "poligono_compuesto": "encontrado",
              "aproximado_uv": "aproximado", "solo_punto": "sin_poligono"}[precision]
    if estado != "encontrado":
        registro.registrar(estado, nombre, zona_id=props["id"], nombre=props["nombre"],
                           municipio=props.get("municipio"), departamento=props.get("departamento"))
    return {**base, "estado": estado, "ambiguo": ambiguo, "zona": zona, "alternativas": alternativas}


@app.get("/zonas/{zona_id}", tags=[SECCION_2], summary="Obtener el polígono de una zona")
def obtener_zona(
    zona_id: str = PathParam(..., description="`id_poligono` de /zonas/buscar, o un `id` de la `jerarquia` de /ubicar",
                             examples=["alias-equipetrol"]),
    formato: Formato = Query("geojson", description=DESC_FORMATO),
):
    """Devuelve los datos y el polígono de una zona ya identificada."""
    z = _zona_o_404(zona_id)
    return {**z.resumen(), "poligono": formatear_poligono(z.geom, formato)}


@app.post("/zonas/{zona_id}/contiene", tags=[SECCION_2], response_model=ContieneResponse,
          summary="¿Qué coordenadas están dentro de la zona?")
def zona_contiene(
    zona_id: str = PathParam(..., description="`id_poligono` de /zonas/buscar", examples=["alias-equipetrol"]),
    req: ContieneRequest = Body(...),
):
    """Recibe hasta 5000 coordenadas y las separa en `dentro` y `fuera` del polígono.

    Cada coordenada puede llevar un `id` propio (por ejemplo el del inmueble), que se
    devuelve tal cual para poder cruzar el resultado.
    """
    z = _zona_o_404(zona_id)
    if not req.coordenadas:
        return ContieneResponse(zona_id=z.id, zona_nombre=z.props["nombre"], municipio=z.props.get("municipio"),
                                total=0, total_dentro=0, dentro=[], fuera=[])
    lons = [c.lon for c in req.coordenadas]
    lats = [c.lat for c in req.coordenadas]
    # Para un punto contra un polígono, intersects = dentro o sobre el borde.
    marcas = shapely.intersects_xy(z.geom, lons, lats)
    dentro = [c for c, m in zip(req.coordenadas, marcas) if m]
    fuera = [c for c, m in zip(req.coordenadas, marcas) if not m]
    return ContieneResponse(zona_id=z.id, zona_nombre=z.props["nombre"], municipio=z.props.get("municipio"),
                            total=len(req.coordenadas), total_dentro=len(dentro), dentro=dentro, fuera=fuera)


# ---------------------------------------------------------------------------
# Reportes y estado
# ---------------------------------------------------------------------------
@app.get("/reportes/no-encontradas", tags=["Reportes"], summary="Zonas buscadas que no existen o no tienen polígono")
def reporte_no_encontradas(limite: int = Query(100, ge=1, le=1000, description="Cuántas consultas devolver")):
    """Consultas registradas, de la más repetida a la menos. Sirve para saber qué polígonos conseguir.

    Solo funciona donde la API puede escribir en disco (en local). En Vercel el registro
    queda en los logs y, si se configuró, en el webhook `WEBHOOK_ZONAS_NO_ENCONTRADAS`.
    """
    return registro.resumen(limite)


@app.get("/reportes/cobertura", tags=["Reportes"], summary="Cobertura de datos por municipio")
def reporte_cobertura(
    departamento: Optional[str] = Query(None, description="Filtrar por departamento", examples=["Santa Cruz"]),
    limite: int = Query(40, ge=1, le=400, description="Cuántos municipios devolver"),
):
    """Por municipio, del más poblado al menos: cuántos polígonos hay de cada nivel, cuántos
    barrios existen solo como punto y de qué fuentes salen."""
    filas = motor.cobertura()
    if departamento:
        filas = [f for f in filas if (f["departamento"] or "").lower() == departamento.lower()]
    return filas[:limite]


@app.get("/estado", tags=["Estado del servicio"], summary="Estado de la API")
def estado():
    return {"estado": "ok", "zonas_cargadas": motor.estadisticas(), "umbral_busqueda": UMBRAL_BUSQUEDA}


# ---------------------------------------------------------------------------
# Rutas anteriores (v4.0). Siguen funcionando para no romper integraciones
# existentes, pero no aparecen en /docs. Ver README → "Rutas anteriores".
# ---------------------------------------------------------------------------
@app.get("/ubicacion", include_in_schema=False, response_model=UbicacionResponse, response_model_exclude_none=True)
def _ubicacion_v40(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
                   calle: bool = True, incluir_poligono: bool = False, formato: Formato = "geojson"):
    return resolver_ubicacion(lat, lon, calle, incluir_poligono, formato)


@app.post("/ubicacion", include_in_schema=False, response_model=UbicacionResponse, response_model_exclude_none=True)
def _ubicacion_post_v40(req: CoordenadaRequest, calle: bool = True, incluir_poligono: bool = False,
                        formato: Formato = "geojson"):
    return resolver_ubicacion(req.lat, req.lon, calle, incluir_poligono, formato)


@app.post("/ubicaciones-lote", include_in_schema=False, response_model=list[UbicacionResponse],
          response_model_exclude_none=True)
def _lote_v40(req: LoteRequest, calle: bool = False):
    return ubicar_lote(req, calle)


@app.get("/zona", include_in_schema=False, response_model=BusquedaResponse, response_model_exclude_none=True)
def _zona_v40(q: str = Query(..., min_length=2), formato: Formato = "geojson", limite: int = Query(5, ge=1, le=20)):
    return buscar_zona(q, formato, limite)


@app.get("/zona/{zona_id}", include_in_schema=False)
def _zona_id_v40(zona_id: str, formato: Formato = "geojson"):
    return obtener_zona(zona_id, formato)


@app.get("/registro/no-encontradas", include_in_schema=False)
def _registro_v40(limite: int = Query(100, ge=1, le=1000)):
    return reporte_no_encontradas(limite)


@app.get("/cobertura", include_in_schema=False)
def _cobertura_v40(departamento: Optional[str] = None, limite: int = Query(40, ge=1, le=400)):
    return reporte_cobertura(departamento, limite)


@app.get("/salud", include_in_schema=False)
def _salud_v40():
    return estado()


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ESTATICOS / "index.html")
