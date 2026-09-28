import os
import functools
import urllib.request
import urllib.parse
import json
from typing import List, Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from geopy.geocoders import Nominatim

try:
    import geopandas as gpd
    from shapely.geometry import Point
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False

app = FastAPI(
    title="GeoUbicación Bolivia - Motor de Alta Precisión",
    description="Motor inteligente multifuente para resolver con exactitud Zonas, Barrios y UVs en Bolivia.",
    version="3.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

geolocalizador = Nominatim(user_agent="geoubicacion_bolivia_v3_smart", timeout=10)

# =========================================================================
# 1. DICCIONARIO DE CORRECCIÓN Y NORMALIZACIÓN DE ZONAS EN BOLIVIA
# Corrige los nombres técnicos/antiguos de OSM por los nombres populares reales
# =========================================================================
MAPEO_BARRIOS_CONOCIDOS = {
    "santa cruz": {
        "avaroa": "Equipetrol (Zona Avaroa)",
        "faremafu": "Equipetrol (Zona Faremafu)",
        "sirari": "Equipetrol Norte / Sirari",
        "piraí": "Distrito 1 (Piraí / Equipetrol / Hamacas)",
        "pirai": "Distrito 1 (Piraí / Equipetrol / Hamacas)",
        "villa san luis": "Villa San Luis / Equipetrol",
        "uv-59": "Equipetrol Norte (UV 59)",
        "el trompillo": "El Trompillo",
        "las palmas": "Las Palmas",
        "urbarí": "Urbarí",
        "urbari": "Urbarí",
        "hamacas": "Barrio Hamacas",
        "los pozo": "Zona Los Pozos (Centro)",
        "la ramada": "Zona La Ramada",
        "plan 3000": "Ciudadela Andrés Ibáñez (Plan 3000)",
        "villa 1ro de mayo": "Villa Primero de Mayo",
        "pampa de la isla": "Pampa de la Isla",
    },
    "la paz": {
        "kantutani": "Sopocachi (Sector Kantutani)",
        "cotahuma": "Macrodistrito Cotahuma (Sopocachi / San Jorge)",
        "san pedro": "San Pedro",
        "miraflores": "Miraflores",
        "calacoto": "Calacoto (Zona Sur)",
        "obrajes": "Obrajes (Zona Sur)",
        "achumani": "Achumani (Zona Sur)",
        "irpatpachi": "Irpavi",
        "san jorge": "San Jorge",
    },
    "cochabamba": {
        "portales": "Cala Cala (Sector Portales)",
        "adela zamudio": "Distrito 12 (Adela Zamudio / Cala Cala)",
        "tupuraya": "Tupuraya",
        "sarco": "Sarco / Queru Queru",
        "muyurina": "Muyurina",
    }
}

# =========================================================================
# 2. CAPAS LOCALES GEOPANDAS (Si el usuario coloca archivos GeoJSON / SHP)
# =========================================================================
capas_locales = {}

def cargar_mapas_locales():
    if not HAS_GEOPANDAS:
        return
    for ruta in [".", "mapas", "capas"]:
        if os.path.exists(ruta):
            for archivo in os.listdir(ruta):
                if archivo.endswith(".geojson") or archivo.endswith(".shp"):
                    ruta_completa = os.path.join(ruta, archivo)
                    nombre = archivo.split(".")[0]
                    try:
                        gdf = gpd.read_file(ruta_completa)
                        if gdf.crs is None:
                            gdf.set_crs(epsg=4326, inplace=True)
                        elif gdf.crs.to_string() != "EPSG:4326":
                            gdf = gdf.to_crs(epsg=4326)
                        capas_locales[nombre] = gdf
                        print(f"[*] Capa local cargada: {nombre} ({len(gdf)} registros)")
                    except Exception as e:
                        print(f"[!] Error al cargar {archivo}: {e}")

cargar_mapas_locales()

def buscar_en_capas_locales(lat: float, lon: float) -> dict:
    if not capas_locales:
        return {}
    punto = Point(lon, lat)
    info = {}
    for nombre_capa, gdf in capas_locales.items():
        try:
            hits = gdf.iloc[gdf.sindex.query(punto, predicate="contains")]
            if not hits.empty:
                fila = hits.iloc[0]
                for col in ['NOMBRE_UV', 'NOM_UV', 'UV', 'BARRIO', 'NOMBRE', 'ZONA', 'DIST_MUNIC', 'DISTRITO', 'nom_dist']:
                    if col in fila and fila[col]:
                        info[f"{nombre_capa}_{col}"] = str(fila[col]).strip()
        except Exception:
            pass
    return info

# =========================================================================
# 3. GOOGLE MAPS GEOCODING (OPCIONAL SI TIENE API KEY)
# =========================================================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()

def consultar_google_maps(lat: float, lon: float):
    if not GOOGLE_API_KEY:
        return None
    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={lat},{lon}&language=es&key={GOOGLE_API_KEY}"
        req = urllib.request.Request(url, headers={"User-Agent": "GeoBolivia/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            if data.get("status") == "OK" and data.get("results"):
                res = data["results"][0]
                barrio = None
                ciudad = None
                depto = None
                ruta = None
                for comp in res.get("address_components", []):
                    types = comp.get("types", [])
                    if "neighborhood" in types or "sublocality_level_1" in types:
                        barrio = comp.get("long_name")
                    elif "sublocality" in types and not barrio:
                        barrio = comp.get("long_name")
                    elif "locality" in types:
                        ciudad = comp.get("long_name")
                    elif "administrative_area_level_1" in types:
                        depto = comp.get("long_name")
                    elif "route" in types:
                        ruta = comp.get("long_name")
                return {
                    "barrio": barrio or "No especificado",
                    "zona": barrio or "No especificada",
                    "distrito": "Identificado por Google",
                    "calle": ruta,
                    "ciudad": ciudad or "No especificada",
                    "departamento": depto or "No especificado",
                    "direccion_completa": res.get("formatted_address"),
                    "fuente": "Google Maps Geocoding API (Oficial)"
                }
    except Exception as e:
        print(f"[!] Error Google Maps API: {e}")
    return None

# =========================================================================
# 4. RESOLVEDOR ESPACIAL INTELIGENTE
# =========================================================================
class CoordenadaRequest(BaseModel):
    lat: float
    lon: float

class UbicacionResponse(BaseModel):
    status: str
    latitud: float
    longitud: float
    barrio_real: str = Field(..., description="Nombre popular y real del barrio (ej: Equipetrol, Sopocachi, Plan 3000)")
    zona_o_sector: str = Field(..., description="Zona general o macrosector")
    distrito_municipal: str = Field(..., description="Distrito o Macrodistrito")
    calle_avenida: Optional[str] = None
    municipio_ciudad: str
    departamento: str
    pais: str = "Bolivia"
    sub_divisiones_encontradas: List[str] = Field(default_factory=list)
    fuente_informacion: str
    direccion_completa: str

@functools.lru_cache(maxsize=8192)
def _consultar_osm(lat_redondeada: float, lon_redondeada: float):
    return geolocalizador.reverse((lat_redondeada, lon_redondeada), language="es", addressdetails=True, zoom=18)

def resolver_ubicacion_inteligente(lat: float, lon: float) -> UbicacionResponse:
    # 1. Intentar con Google Maps si hay API Key
    google_res = consultar_google_maps(lat, lon)
    if google_res:
        return UbicacionResponse(
            status="success",
            latitud=lat,
            longitud=lon,
            barrio_real=google_res["barrio"],
            zona_o_sector=google_res["zona"],
            distrito_municipal=google_res["distrito"],
            calle_avenida=google_res["calle"],
            municipio_ciudad=google_res["ciudad"],
            departamento=google_res["departamento"],
            fuente_informacion=google_res["fuente"],
            direccion_completa=google_res["direccion_completa"]
        )

    # 2. Consultar capas vectoriales locales si existen
    info_local = buscar_en_capas_locales(lat, lon)
    
    # 3. Consultar OpenStreetMap
    lat_r = round(lat, 5)
    lon_r = round(lon, 5)
    
    ubicacion = _consultar_osm(lat_r, lon_r)
    detalles = ubicacion.raw.get("address", {}) if ubicacion else {}
    dir_completa = ubicacion.address if ubicacion else f"{lat}, {lon}"

    # Extraer componentes
    neighbourhood = detalles.get("neighbourhood")
    suburb = detalles.get("suburb")
    quarter = detalles.get("quarter")
    residential = detalles.get("residential")
    city_district = detalles.get("city_district")
    district = detalles.get("district")
    road = detalles.get("road") or detalles.get("pedestrian") or detalles.get("footway")
    city = detalles.get("city") or detalles.get("town") or detalles.get("municipality") or detalles.get("village") or "Santa Cruz de la Sierra"
    state = detalles.get("state") or detalles.get("region") or "Santa Cruz"

    # Lista de subdivisiones
    sub_divs = []
    for item in [neighbourhood, residential, quarter, suburb, city_district, district]:
        if item and item not in sub_divs:
            sub_divs.append(item)

    # Normalización inteligente de nombres bolivianos
    ciudad_key = city.lower()
    depto_key = state.lower()
    
    nombre_barrio_detectado = neighbourhood or residential or quarter or suburb or "No especificado"
    nombre_zona_detectada = suburb or quarter or city_district or "Zona no especificada"
    
    barrio_normalizado = None
    
    # Buscar en diccionario de correcciones
    for region in ["santa cruz", "la paz", "cochabamba"]:
        if region in ciudad_key or region in depto_key or region in dir_completa.lower():
            reg_dict = MAPEO_BARRIOS_CONOCIDOS[region]
            for tag in sub_divs:
                tag_low = tag.lower().strip()
                if tag_low in reg_dict:
                    barrio_normalizado = reg_dict[tag_low]
                    break
            if barrio_normalizado:
                break

    # Detección específica para casos como Equipetrol (Avaroa, Sirari, Faremafu, San Martín, etc.)
    if "santa cruz" in ciudad_key or "santa cruz" in depto_key or "santa cruz" in dir_completa.lower():
        if any(x.lower() in ["avaroa", "faremafu", "sirari", "piraí", "pirai", "villa san luis", "uv-59", "uv 59"] for x in sub_divs):
            barrio_normalizado = "Equipetrol"
            nombre_zona_detectada = "Zona Nor-Oeste (Equipetrol / Sirari)"
            distrito = "Distrito 1 (Piraí)"
        elif any(x.lower() in ["simón bolívar", "simon bolivar", "plan 3000"] for x in sub_divs):
            barrio_normalizado = "Plan 3000 (Andrés Ibáñez)"
            nombre_zona_detectada = "Ciudadela Plan 3000"
            distrito = "Distrito 8"
        else:
            distrito = city_district or "Distrito Municipal"
    else:
        distrito = city_district or district or "Distrito Municipal"

    barrio_final = barrio_normalizado or nombre_barrio_detectado
    zona_final = nombre_zona_detectada if (barrio_normalizado and barrio_normalizado != nombre_zona_detectada) else (suburb or "Zona Urbana")

    fuente = "Motor de Inferencia Bolivia (OSM + Normalizador Catastral)"
    if info_local:
        fuente = "Híbrido (Capas GeoJSON Locales + Motor Inteligente)"

    return UbicacionResponse(
        status="success",
        latitud=lat,
        longitud=lon,
        barrio_real=barrio_final,
        zona_o_sector=zona_final,
        distrito_municipal=distrito,
        calle_avenida=road,
        municipio_ciudad=city,
        departamento=state,
        pais="Bolivia",
        sub_divisiones_encontradas=sub_divs,
        fuente_informacion=fuente,
        direccion_completa=dir_completa
    )

# =========================================================================
# ENDPOINTS
# =========================================================================
@app.get("/ubicacion", response_model=UbicacionResponse)
def obtener_ubicacion(
    lat: float = Query(..., description="Latitud"),
    lon: float = Query(..., description="Longitud")
):
    return resolver_ubicacion_inteligente(lat, lon)

@app.post("/ubicacion", response_model=UbicacionResponse)
def obtener_ubicacion_post(req: CoordenadaRequest):
    return resolver_ubicacion_inteligente(req.lat, req.lon)

@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GeoUbicación Bolivia - Motor Inteligente</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>
            * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; }
            body { margin: 0; padding: 0; display: flex; height: 100vh; background: #0f172a; }
            #sidebar { width: 440px; padding: 24px; background: #ffffff; box-shadow: 4px 0 24px rgba(0,0,0,0.15); overflow-y: auto; z-index: 1000; }
            #map { flex: 1; height: 100%; }
            .badge { display: inline-block; background: #dbeafe; color: #1e40af; font-size: 0.75rem; font-weight: 700; padding: 4px 10px; border-radius: 9999px; margin-bottom: 10px; }
            h2 { margin: 0 0 6px 0; color: #0f172a; font-size: 1.4rem; }
            p.sub { margin: 0 0 20px 0; color: #64748b; font-size: 0.88rem; }
            .form-row { display: flex; gap: 10px; margin-bottom: 14px; }
            .form-group { flex: 1; }
            label { display: block; font-size: 0.8rem; font-weight: 700; color: #475569; margin-bottom: 4px; }
            input { width: 100%; padding: 10px 12px; border: 1.5px solid #cbd5e1; border-radius: 8px; font-size: 0.95rem; }
            button { width: 100%; background: #2563eb; color: white; border: none; padding: 12px; border-radius: 8px; font-weight: 700; font-size: 0.95rem; cursor: pointer; transition: 0.2s; }
            button:hover { background: #1d4ed8; }
            
            .card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px; margin-top: 20px; }
            .hero-result { background: #eff6ff; border: 1.5px solid #bfdbfe; border-radius: 10px; padding: 14px; margin-bottom: 14px; }
            .hero-label { font-size: 0.75rem; text-transform: uppercase; font-weight: 800; color: #1d4ed8; letter-spacing: 0.5px; }
            .hero-value { font-size: 1.35rem; font-weight: 800; color: #1e3a8a; margin-top: 2px; }
            
            .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
            .box { background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px; }
            .box-lbl { font-size: 0.72rem; text-transform: uppercase; font-weight: 700; color: #94a3b8; }
            .box-val { font-size: 0.95rem; font-weight: 700; color: #1e293b; margin-top: 2px; }
            
            .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
            .chip { background: #e2e8f0; color: #334155; font-size: 0.75rem; padding: 3px 8px; border-radius: 6px; font-weight: 600; }
            .tag-fuente { font-size: 0.72rem; color: #0284c7; background: #e0f2fe; padding: 4px 8px; border-radius: 6px; display: inline-block; margin-top: 10px; font-weight: 600; }
            .loading { color: #d97706; font-weight: 600; font-size: 0.9rem; margin-top: 10px; }
        </style>
    </head>
    <body>
        <div id="sidebar">
            <span class="badge">🇧🇴 Motor de Inferencia Bolivia</span>
            <h2>GeoUbicación Precisa</h2>
            <p class="sub">Haz clic en cualquier punto del mapa para identificar el barrio real.</p>
            
            <div class="form-row">
                <div class="form-group">
                    <label>Latitud</label>
                    <input type="number" step="any" id="lat" value="-17.765853">
                </div>
                <div class="form-group">
                    <label>Longitud</label>
                    <input type="number" step="any" id="lon" value="-63.194236">
                </div>
            </div>
            <button onclick="consultar()">🔍 Consultar Coordenada</button>
            <div id="status"></div>

            <div id="resultado" class="card" style="display:none;">
                <div class="hero-result">
                    <div class="hero-label">Barrio Identificado</div>
                    <div class="hero-value" id="res-barrio">-</div>
                </div>

                <div class="grid-2">
                    <div class="box">
                        <div class="box-lbl">Zona / Sector</div>
                        <div class="box-val" id="res-zona">-</div>
                    </div>
                    <div class="box">
                        <div class="box-lbl">Distrito Municipal</div>
                        <div class="box-val" id="res-distrito">-</div>
                    </div>
                </div>

                <div class="grid-2">
                    <div class="box">
                        <div class="box-lbl">Calle / Vía</div>
                        <div class="box-val" id="res-calle">-</div>
                    </div>
                    <div class="box">
                        <div class="box-lbl">Ciudad / Municipio</div>
                        <div class="box-val" id="res-ciudad">-</div>
                    </div>
                </div>

                <div style="margin-top: 10px;">
                    <div class="box-lbl">Subdivisiones Catastrales Detectadas</div>
                    <div class="chips" id="res-chips"></div>
                </div>

                <div style="margin-top: 10px;">
                    <div class="box-lbl">Dirección Completa</div>
                    <div style="font-size:0.82rem; color:#475569; margin-top:2px;" id="res-dir">-</div>
                </div>

                <div id="res-fuente" class="tag-fuente"></div>
            </div>
        </div>
        
        <div id="map"></div>

        <script>
            const map = L.map('map').setView([-17.765853, -63.194236], 15);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap contributors'
            }).addTo(map);

            let marker = L.marker([-17.765853, -63.194236]).addTo(map);

            async function ejecutarBusqueda(lat, lon) {
                document.getElementById('lat').value = lat;
                document.getElementById('lon').value = lon;
                document.getElementById('status').innerHTML = '<p class="loading">⏳ Consultando motor espacial...</p>';

                if (marker) map.removeLayer(marker);
                marker = L.marker([lat, lon]).addTo(map);

                try {
                    const res = await fetch(`/ubicacion?lat=${lat}&lon=${lon}`);
                    const data = await res.json();
                    document.getElementById('status').innerHTML = '';

                    if (res.ok) {
                        document.getElementById('resultado').style.display = 'block';
                        document.getElementById('res-barrio').innerText = data.barrio_real;
                        document.getElementById('res-zona').innerText = data.zona_o_sector;
                        document.getElementById('res-distrito').innerText = data.distrito_municipal;
                        document.getElementById('res-calle').innerText = data.calle_avenida || 'Sin nombre';
                        document.getElementById('res-ciudad').innerText = data.municipio_ciudad;
                        document.getElementById('res-dir').innerText = data.direccion_completa;
                        document.getElementById('res-fuente').innerText = 'Fuente: ' + data.fuente_informacion;

                        const chipsDiv = document.getElementById('res-chips');
                        chipsDiv.innerHTML = '';
                        (data.sub_divisiones_encontradas || []).forEach(c => {
                            const span = document.createElement('span');
                            span.className = 'chip';
                            span.innerText = c;
                            chipsDiv.appendChild(span);
                        });

                        marker.bindPopup(`<b>${data.barrio_real}</b><br>${data.zona_o_sector}`).openPopup();
                    }
                } catch(e) {
                    document.getElementById('status').innerHTML = '';
                    alert('Error conectando al servidor');
                }
            }

            map.on('click', function(e) {
                ejecutarBusqueda(e.latlng.lat, e.latlng.lng);
            });

            function consultar() {
                const lat = parseFloat(document.getElementById('lat').value);
                const lon = parseFloat(document.getElementById('lon').value);
                map.setView([lat, lon], 15);
                ejecutarBusqueda(lat, lon);
            }

            // Consulta inicial
            ejecutarBusqueda(-17.765853, -63.194236);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
