"""Tests de la API contra los datos reales de datos/. No consultan Nominatim (calle=false).

    python -m pytest tests -q
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import registro
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def registro_temporal(tmp_path, monkeypatch):
    ruta = tmp_path / "registro.jsonl"
    monkeypatch.setattr(registro, "RUTA", ruta)
    return ruta


def ubicar(lat, lon, **extra):
    r = client.get("/ubicar", params={"lat": lat, "lon": lon, "calle": False, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def zona(q, **extra):
    r = client.get("/zonas/buscar", params={"nombre": q, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def eventos(ruta):
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()] if ruta.exists() else []


# -- coordenada → zonas ------------------------------------------------------
def test_santa_cruz_tiene_distrito_y_uv():
    d = ubicar(-17.7694, -63.1950)
    assert d["departamento"] == "Santa Cruz"
    assert d["municipio_ciudad"] == "Santa Cruz de la Sierra"
    assert d["distrito_municipal"].startswith("Distrito")
    assert d["unidad_vecinal"].startswith("UV")
    niveles = [z["nivel"] for z in d["jerarquia"]]
    assert niveles.index("departamento") < niveles.index("municipio") < niveles.index("uv")


def test_la_paz_no_se_confunde_con_santa_cruz():
    d = ubicar(-16.5113, -68.1287)
    assert d["departamento"] == "La Paz"
    assert "La Paz" in d["municipio_ciudad"]


def test_zona_rural_no_inventa_ciudad():
    d = ubicar(-19.5, -67.0)  # Altiplano de Potosí
    assert d["departamento"] == "Potosí"
    assert d["municipio_ciudad"] != "Santa Cruz de la Sierra"


def test_salar_es_area_especial():
    d = ubicar(-20.2, -67.6)
    assert d["status"] == "success"
    assert d["municipio_ciudad"] == "Salar de Uyuni"
    assert "Salar" not in (d.get("departamento") or "")


def test_fuera_de_bolivia_se_registra(registro_temporal):
    d = ubicar(40.4, -3.7)
    assert d["status"] == "fuera_de_bolivia"
    assert eventos(registro_temporal)[-1]["tipo"] == "fuera_de_bolivia"


def test_coordenada_invalida_es_422():
    assert client.get("/ubicar", params={"lat": 999, "lon": 0}).status_code == 422


def test_poligono_de_la_zona_mas_especifica():
    d = ubicar(-17.7694, -63.1950, incluir_poligono=True, formato="ambos")
    pol = d["zona_mas_especifica"]["poligono"]
    assert pol["geojson"]["type"] in ("Polygon", "MultiPolygon")
    assert isinstance(pol["lista"][0][0], list) and len(pol["lista"][0][0]) == 2


def test_lote():
    r = client.post("/ubicar/lote", json={"coordenadas": [
        {"lat": -17.7694, "lon": -63.1950}, {"lat": -16.5113, "lon": -68.1287}, {"lat": 40.4, "lon": -3.7}]})
    assert r.status_code == 200
    assert [x["status"] for x in r.json()] == ["success", "success", "fuera_de_bolivia"]


# -- nombre → polígono -------------------------------------------------------
def test_municipio_por_nombre_sin_tildes():
    d = zona("cuatro canadas")
    assert d["estado"] == "encontrado"
    assert d["zona"]["nivel"] == "municipio"
    assert d["zona"]["poligono"]["geojson"]["type"] in ("Polygon", "MultiPolygon")


def test_uv_por_codigo():
    d = zona("UV 45")
    assert d["estado"] == "encontrado"
    assert d["zona"]["nivel"] == "uv" and d["zona"]["uv"] == "45"


def test_lugar_acota_la_busqueda():
    d = zona("distrito 12 en santa cruz")
    assert d["lugar_interpretado"] == "santa cruz"
    assert d["zona"]["nombre"] == "Distrito 12"
    assert d["zona"]["municipio"] == "Santa Cruz de la Sierra"


def test_nombre_repetido_en_dos_departamentos_es_ambiguo():
    d = zona("San Ramón")
    assert d["ambiguo"] is True
    deptos = {d["zona"]["departamento"], *(a["departamento"] for a in d["alternativas"])}
    assert {"Santa Cruz", "Beni"} <= deptos


def test_formato_lista():
    d = zona("Warnes", formato="lista")
    lista = d["zona"]["poligono"]["lista"]
    assert "geojson" not in d["zona"]["poligono"]
    lat, lon = lista[0][0]
    assert -23 < lat < -9 and -70 < lon < -57  # [lat, lon], dentro de Bolivia


def test_zona_inexistente_se_registra(registro_temporal):
    d = zona("barrio xyzzy inventado")
    assert d["estado"] == "no_encontrado"
    ultimo = eventos(registro_temporal)[-1]
    assert ultimo["tipo"] == "no_encontrada" and ultimo["consulta"] == "barrio xyzzy inventado"
    resumen = client.get("/reportes/no-encontradas").json()
    assert resumen["consultas"][0]["consulta"] == "barrio xyzzy inventado"


def test_zona_por_id_inexistente_es_404():
    assert client.get("/zonas/no-existe").status_code == 404


def test_salud():
    d = client.get("/estado").json()
    assert d["zonas_cargadas"]["municipio"] >= 339


# -- casos con datos de OpenStreetMap y alias.json ---------------------------
def test_equipetrol_es_zona_compuesta_por_uvs():
    d = zona("Equipetrol")
    assert d["estado"] == "encontrado"
    assert d["zona"]["precision"] == "poligono_compuesto"
    assert all(p.startswith("scz-uv-") for p in d["zona"]["compuesta_por"])
    # y la coordenada de Equipetrol cae dentro de esa zona
    assert ubicar(-17.7694, -63.1950)["barrio_real"] == "Equipetrol"


def test_compuesta_se_puede_pedir_por_id():
    zid = zona("Equipetrol")["zona"]["id"]
    assert client.get(f"/zonas/{zid}").status_code == 200


def test_homonimo_prefiere_ciudad_grande():
    d = zona("Miraflores")
    assert d["zona"]["municipio"] == "Nuestra Señora de La Paz"
    assert d["ambiguo"] is True


def test_barrio_sin_poligono_se_registra(registro_temporal):
    d = zona("Achumani en La Paz")
    assert d["estado"] in ("sin_poligono", "encontrado", "aproximado")
    if d["estado"] == "sin_poligono":
        assert "punto" in d["zona"]
        assert eventos(registro_temporal)[-1]["tipo"] == "sin_poligono"


def test_nombre_parecido_no_se_da_por_encontrado():
    # En OSM no hay "Cala Cala" dentro de Cochabamba; no debe devolver "Viloma Cala Cala".
    d = zona("Cala Cala cochabamba")
    assert d["estado"] == "no_encontrado" or d["zona"]["nombre"].lower() == "cala cala"


def test_localidad_sin_nominatim():
    # El Paso (Quillacollo): en OSM es solo un punto, pero el Censo 2024 le da polígono.
    d = ubicar(-17.330064, -66.260691)
    assert d["municipio_ciudad"] == "Quillacollo"
    assert d["localidad"] == "El Paso"
    assert d["precision_localidad"] in ("poligono", "punto_cercano")


# -- fuentes oficiales municipales ---------------------------------------------
def test_la_paz_tiene_macrodistrito_y_zona_oficial():
    d = ubicar(-16.5113, -68.1287)  # Sopocachi
    assert d["macrodistrito"] == "Cotahuma"
    assert d["precision_barrio"] == "poligono"
    assert d["distrito_municipal"].startswith("Distrito")


def test_cochabamba_tiene_otb_y_comuna():
    d = ubicar(-17.3732, -66.1601)  # Cala Cala
    assert d["municipio_ciudad"] == "Cochabamba"
    assert d["precision_barrio"] == "poligono"
    assert d["macrodistrito"].startswith("Comuna")


def test_el_alto_urbanizacion():
    d = ubicar(-16.505, -68.163)
    assert d["municipio_ciudad"] == "El Alto"
    assert d["precision_barrio"] == "poligono"


def test_busqueda_prefiere_poligono_oficial():
    d = zona("Sopocachi en La Paz")
    assert d["estado"] == "encontrado"
    assert d["zona"]["oficial"] is True


def test_uv_de_montero_no_se_confunde_con_santa_cruz():
    d = zona("UV 34 en Montero")
    assert d["zona"]["municipio"] == "Montero"
    assert zona("Equipetrol")["zona"]["municipio"] == "Santa Cruz de la Sierra"


def test_subdivisiones_catastrales_incluyen_polígonos_locales():
    # Sin Nominatim, las subdivisiones salen de los polígonos (OTB, zona, macrodistrito, distrito).
    d = ubicar(-16.5113, -68.1287)
    subdiv = d["sub_divisiones_encontradas"]
    assert "Cotahuma" in subdiv and any(s.startswith("Distrito") for s in subdiv)
    assert "La Paz" not in subdiv  # municipio y localidad van en sus propios campos


# -- ¿qué coordenadas caen dentro de una zona? --------------------------------
def test_contiene_separa_dentro_y_fuera():
    zid = zona("Equipetrol")["zona"]["id_poligono"]
    r = client.post(f"/zonas/{zid}/contiene", json={"coordenadas": [
        {"id": "dentro", "lat": -17.7694, "lon": -63.1950},
        {"id": "fuera", "lat": -16.5113, "lon": -68.1287},
        {"lat": -17.7694, "lon": -63.1950}]})
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 3 and d["total_dentro"] == 2
    assert [c["id"] for c in d["fuera"]] == ["fuera"]


def test_contiene_usa_la_uv_cuando_el_barrio_es_aproximado():
    d = zona("Hamacas")
    assert d["estado"] == "aproximado"
    assert d["zona"]["id_poligono"].startswith("scz-uv-")
    assert client.post(f"/zonas/{d['zona']['id_poligono']}/contiene", json={"coordenadas": []}).status_code == 200


def test_contiene_zona_inexistente_es_404():
    assert client.post("/zonas/no-existe/contiene", json={"coordenadas": []}).status_code == 404


def test_sin_poligono_no_tiene_id_poligono():
    d = zona("Achumani en La Paz")
    if d["estado"] == "sin_poligono":
        assert d["zona"].get("id_poligono") is None


def test_rutas_anteriores_siguen_funcionando():
    assert client.get("/ubicacion", params={"lat": -17.7694, "lon": -63.195, "calle": False}).status_code == 200
    assert client.get("/zona", params={"q": "equipetrol"}).json()["estado"] == "encontrado"
    assert client.get("/salud").status_code == 200
    rutas_docs = client.get("/openapi.json").json()["paths"]
    assert "/ubicacion" not in rutas_docs and "/ubicar" in rutas_docs
