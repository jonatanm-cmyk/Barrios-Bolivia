"""Capas oficiales de barrios, OTB, distritos y macrodistritos.

Son capas publicadas por gobiernos municipales y ministerios en sus geoservidores.
Muchas de esas capas se retiran o los servidores dejan de responder, así que se
descargan de las copias de preservación que mantiene mauforonda/geodatos en
Archive.org (https://archive.org/details/<paquete>, archivo dataset.geojson).

Para agregar una capa: buscarla en
https://github.com/mauforonda/geodatos (descubrir/capas.csv y archivar/paquetes.csv),
copiar su `archive_item` y declarar aquí cómo leer el nombre.

Campos de cada fuente:
  clave      identificador corto, prefijo de los ids
  paquete    archive_item del paquete en Archive.org
  epsg       sistema de coordenadas del dataset.geojson
  nivel      nivel del esquema (barrio, uv, distrito, macrodistrito, zona, localidad)
  nombre     función props -> nombre, o None para descartar el elemento
  extra      función props -> dict de propiedades adicionales (opcional)
  agrupar    función props -> nombre de grupo; si está, se unen los polígonos del grupo
  fuente     texto que se muestra en las respuestas
"""
import re


def _txt(v):
    if v is None:
        return None
    v = " ".join(str(v).split())
    return v or None


def _distrito(v):
    """'4' -> 'Distrito 4'; 'D.R. Lava Lava' -> 'Distrito Lava Lava'; 'DISTRITO D' -> 'DISTRITO D'."""
    v = _txt(v)
    if not v or v.lower().startswith("distrito"):
        return v
    # Prefijos 'D.R. ', 'D.M. ' y 'D'/'D-' seguido de número ('D9', 'D-01'); no toca 'Duraznal'.
    v = re.sub(r"^(d\.\s?[rm]\.\s*|d-?(?=\d))", "", v, flags=re.I)
    return f"Distrito {v.lstrip('0') or v}"


FUENTES = [
    # --- La Paz (GAMLP, Sistema de Información Territorial) ---------------------
    dict(clave="lpz-otb", paquete="geodatosbolivia_lapaz_sit_alt_sit_otbdpe2021", epsg=32719, nivel="barrio",
         nombre=lambda p: _txt(p.get("nombre_zona")),
         extra=lambda p: {"codigo": _txt(p.get("cod_zona")), "estado": _txt(p.get("estado_zona_otb"))},
         fuente="GAM La Paz — Zonas/OTB 2021 (SIT)"),
    dict(clave="lpz-zona", paquete="geodatosbolivia_lapaz_sit_sit_zonasgu2016", epsg=32719, nivel="zona",
         nombre=lambda p: _txt(p.get("zona") or p.get("zonaref")),
         fuente="GAM La Paz — Zonas Guía Urbana 2016 (SIT)"),
    dict(clave="lpz-macro", paquete="geodatosbolivia_planificacion_geobolivia_geonode_macrodistrito_gamlp_2019_0f3732dd",
         epsg=4326, nivel="macrodistrito", nombre=lambda p: _txt(p.get("macro_vige")),
         fuente="GAM La Paz — Macrodistritos 2020 (GeoBolivia)"),
    dict(clave="lpz-dist", paquete="geodatosbolivia_planificacion_geobolivia_geonode_distrito_gamlp_2019_486c0e79",
         epsg=4326, nivel="distrito", nombre=lambda p: _distrito(p.get("distrito")),
         fuente="GAM La Paz — Distritos 2020 (GeoBolivia)"),
    dict(clave="lpz-rural", paquete="geodatosbolivia_lapaz_sit_sit_otbrural", epsg=32719, nivel="localidad",
         nombre=lambda p: _txt(p.get("nombre")),
         fuente="GAM La Paz — OTB rurales (SIT)"),
    # --- El Alto --------------------------------------------------------------
    dict(clave="ea-urb", paquete="geodatosbolivia_presidencia_geobolivia_geonode_elalto_urbanizaciones", epsg=4326,
         nivel="barrio", nombre=lambda p: _txt(p.get("nombre")),
         extra=lambda p: {"poblacion": p.get("poblacion")},
         fuente="GAM El Alto — Urbanizaciones (GeoBolivia)"),
    dict(clave="ea-dist", paquete="geodatosbolivia_planificacion_geobolivia_geonode_elalto_distritos_919a7cdf",
         epsg=4326, nivel="distrito", nombre=lambda p: _distrito(p.get("c_dist_mun")),
         fuente="GAM El Alto — Distritos 2015 (GeoBolivia)"),
    # --- Cochabamba -----------------------------------------------------------
    dict(clave="cbba-otb",
         paquete="geodatosbolivia_planificacion_geoinfo_infospie_layer_organizaciones_territoriales_de_ba_150db1d134e4",
         epsg=4326, nivel="barrio", nombre=lambda p: _txt(p.get("otb")),
         extra=lambda p: {"distrito": _txt(p.get("distrito"))},
         fuente="GAM Cochabamba — OTB, PTDI (Ministerio de Planificación)"),
    dict(clave="cbba-comuna",
         paquete="geodatosbolivia_planificacion_geoinfo_infospie_layer_organizaciones_territoriales_de_ba_150db1d134e4",
         epsg=4326, nivel="macrodistrito",
         nombre=lambda p: (f"Comuna {_txt(re.sub(r'[(].*', '', p['comunas'])).title()}" if _txt(p.get("comunas")) else None),
         agrupar=lambda p: _txt(re.sub(r"[(].*", "", p.get("comunas") or "")),
         fuente="GAM Cochabamba — Comunas (unión de OTB, PTDI)"),
    dict(clave="cbba-dist",
         paquete="geodatosbolivia_planificacion_geoinfo_infospie_layer_distritos_en_el_municipio_de_cocha_cb40f736fbd1",
         epsg=4326, nivel="distrito", nombre=lambda p: _distrito(p.get("distritos")),
         fuente="GAM Cochabamba — Distritos, PTDI (Ministerio de Planificación)"),
    dict(clave="sacaba-dist",
         paquete="geodatosbolivia_planificacion_geobolivia_geonode_distrito_municipal_sacaba_cba_28ce2bca",
         epsg=4326, nivel="distrito", nombre=lambda p: _distrito(p.get("distritos") or p.get("distrito")),
         fuente="GAM Sacaba — Distritos (GeoBolivia)"),
    dict(clave="colca-dist",
         paquete="geodatosbolivia_planificacion_geoinfo_infospie_layer_distritos_del_municipio_de_colcapi_96b980ee9833",
         epsg=4326, nivel="distrito", nombre=lambda p: _distrito(p.get("nombre")),
         fuente="GAM Colcapirhua — Distritos, PTDI (Ministerio de Planificación)"),
    # --- Santa Cruz (fuera de la capital) ---------------------------------------
    dict(clave="montero-uv",
         paquete="geodatosbolivia_minplanifica_infocapa_layer_unidades_vecinales_del_municipio_de_montero_ptdi_uhzqk",
         epsg=4326, nivel="uv", nombre=lambda p: _txt(p.get("uv")),
         extra=lambda p: {"uv": re.sub(r"(?i)^uv\s*", "", _txt(p.get("uv")) or "")},
         fuente="GAM Montero — Unidades vecinales, PTDI (Ministerio de Planificación)"),
    dict(clave="montero-dist",
         paquete="geodatosbolivia_minplanifica_infocapa_layer_distritos_del_municipio_de_montero_ptdi_bkom1",
         epsg=4326, nivel="distrito", nombre=lambda p: _distrito(p.get("nombre_dis")),
         fuente="GAM Montero — Distritos, PTDI (Ministerio de Planificación)"),
    dict(clave="camiri-otb",
         paquete="geodatosbolivia_planificacion_geoinfo_infospie_layer_organizaciones_territoriales_de_ba_d38b722492b7",
         epsg=4326, nivel="barrio", nombre=lambda p: _txt(p.get("otb")),
         fuente="GAM Camiri — OTB, PTDI (Ministerio de Planificación)"),
    dict(clave="camiri-dist",
         paquete="geodatosbolivia_minplanifica_infocapa_layer_distritos_del_municipio_de_camiri_977mx",
         epsg=4326, nivel="distrito", nombre=lambda p: _distrito(p.get("distrito")),
         fuente="GAM Camiri — Distritos, PTDI (Ministerio de Planificación)"),
    # --- Pando y Beni -----------------------------------------------------------
    dict(clave="cobija-barrio", paquete="geodatosbolivia_cobija_siscat_cobija_barrio", epsg=32719, nivel="barrio",
         nombre=lambda p: _txt(p.get("nombre")),
         fuente="GAM Cobija — Barrios (SISCAT)"),
    dict(clave="cobija-dist", paquete="geodatosbolivia_cobija_siscat_cobija_distritos_municipales", epsg=32719,
         nivel="distrito", nombre=lambda p: _distrito(p.get("nombre")),
         fuente="GAM Cobija — Distritos (SISCAT)"),
    dict(clave="beni-barrio", paquete="geodatosbolivia_mhe_geoportal_geonode_beni_barrios", epsg=4326, nivel="barrio",
         nombre=lambda p: _txt(p.get("BARRIO")),
         fuente="ENDE / Min. Hidrocarburos y Energías — Barrios del Beni 2018"),
    dict(clave="beni-dist", paquete="geodatosbolivia_mhe_geoportal_geonode_beni_distritos", epsg=4326, nivel="distrito",
         nombre=lambda p: _distrito(p.get("Nombre")),
         fuente="ENDE / Min. Hidrocarburos y Energías — Distritos de Trinidad"),
]
