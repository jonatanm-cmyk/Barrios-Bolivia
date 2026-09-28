"""Consulta a Nominatim solo para calle y dirección postal.

Los límites (departamento, municipio, distrito, UV, barrio) salen de los polígonos
locales; Nominatim es un complemento opcional y su caída no rompe la respuesta.
Política de uso: máximo 1 petición por segundo y User-Agent identificable.
"""
import functools
import logging
import os
import threading
import time

from geopy.exc import GeopyError
from geopy.geocoders import Nominatim

log = logging.getLogger("barrios_bolivia.nominatim")

_geo = Nominatim(user_agent=os.environ.get("NOMINATIM_USER_AGENT", "barrios-bolivia/4.1"), timeout=6)
_lock = threading.Lock()
_ultima = 0.0


@functools.lru_cache(maxsize=4096)
def direccion(lat: float, lon: float) -> dict | None:
    """Devuelve {'calle', 'direccion_completa', 'address'} o None si Nominatim no responde."""
    global _ultima
    with _lock:
        espera = 1.0 - (time.monotonic() - _ultima)
        if espera > 0:
            time.sleep(espera)
        _ultima = time.monotonic()
        try:
            r = _geo.reverse((lat, lon), language="es", addressdetails=True, zoom=18)
        except (GeopyError, OSError) as e:
            log.warning("Nominatim no disponible: %s", e)
            raise _FalloTransitorio from e  # lru_cache no guarda excepciones: se reintenta
    if not r:
        return None
    a = r.raw.get("address", {})
    return {"calle": a.get("road") or a.get("pedestrian") or a.get("footway"),
            "direccion_completa": r.address, "address": a}


class _FalloTransitorio(Exception):
    pass


def direccion_segura(lat: float, lon: float) -> dict | None:
    """Como direccion(), pero un fallo de red devuelve None sin quedar en caché."""
    try:
        return direccion(round(lat, 5), round(lon, 5))
    except _FalloTransitorio:
        return None
