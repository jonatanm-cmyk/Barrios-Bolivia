import sys
import time
from geopy.geocoders import Nominatim

# Forzar codificación UTF-8 para evitar errores de caracteres en Windows consola
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# Inicializar geocodificador
geolocalizador = Nominatim(user_agent="test_bolivia_locations_v2", timeout=10)

def probar_coordenada(nombre, lat, lon):
    print("\n" + "=" * 45)
    print(f"[*] Probando: {nombre} ({lat}, {lon})")
    print("=" * 45)
    try:
        ubicacion = geolocalizador.reverse((lat, lon), language="es", addressdetails=True)
        if not ubicacion:
            print("[-] No se encontro informacion para estas coordenadas.")
            return

        detalles = ubicacion.raw.get("address", {})
        
        barrio = (
            detalles.get("neighbourhood")
            or detalles.get("suburb")
            or detalles.get("quarter")
            or detalles.get("residential")
            or detalles.get("city_district")
            or detalles.get("commercial")
            or detalles.get("industrial")
            or "Barrio/Zona no identificada"
        )
        calle = detalles.get("road") or "No identificada"
        municipio = detalles.get("city") or detalles.get("town") or detalles.get("municipality") or detalles.get("village") or "No identificado"
        departamento = detalles.get("state") or "No identificado"

        print(f"-> Barrio / Zona : {barrio}")
        print(f"-> Calle / Av    : {calle}")
        print(f"-> Ciudad/Muni   : {municipio}")
        print(f"-> Departamento  : {departamento}")
        print(f"-> Dir. Completa : {ubicacion.address}")
    except Exception as e:
        print(f"[!] Error: {e}")

if __name__ == "__main__":
    puntos_bolivia = [
        ("Santa Cruz - Equipetrol", -17.7694, -63.1950),
        ("Santa Cruz - Plan 3000", -17.8285, -63.1412),
        ("La Paz - Sopocachi", -16.5113, -68.1287),
        ("Cochabamba - Cala Cala", -17.3732, -66.1601),
    ]

    for nombre, lat, lon in puntos_bolivia:
        probar_coordenada(nombre, lat, lon)
        time.sleep(1) # Respetar limite de cortesia de OpenStreetMap
