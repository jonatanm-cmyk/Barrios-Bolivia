"""Normalización de texto para comparar nombres de lugares bolivianos."""
import re
import unicodedata

# Palabras que describen el tipo de lugar o son relleno de la petición, no parte
# del nombre. Se quitan de la consulta y de los nombres por igual, así "Barrio
# Hamacas" y "hamacas" comparan como iguales.
RELLENO = {
    "barrio", "barrios", "zona", "zonas", "sector", "urbanizacion", "urb", "condominio",
    "poligono", "poligonos", "limites", "limite", "area", "dame", "quiero", "busca", "buscar",
    "mostrar", "muestra", "donde", "esta", "queda", "cual", "es", "el", "la", "los", "las",
    "de", "del", "en", "y", "por", "favor", "ciudad",
}

# Formas cortas con que la gente nombra las capitales y ciudades grandes.
CIUDADES_COLOQUIALES = {
    "santa cruz": "santa cruz de la sierra",
    "scz": "santa cruz de la sierra",
    "lpz": "la paz",
    "cbba": "cochabamba",
    "cocha": "cochabamba",
}


def normalizar(texto: str | None) -> str:
    """Minúsculas, sin tildes ni signos, espacios colapsados."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto.lower())
    return " ".join(texto.split())


def clave(texto: str | None) -> str:
    """Forma normalizada sin palabras de relleno, para comparar nombres."""
    palabras = [p for p in normalizar(texto).split() if p not in RELLENO]
    return " ".join(palabras) or normalizar(texto)


def contiene_frase(texto_norm: str, frase_norm: str) -> bool:
    return bool(frase_norm) and re.search(rf"(^| ){re.escape(frase_norm)}( |$)", texto_norm) is not None


def quitar_frase(texto_norm: str, frase_norm: str) -> str:
    """Quita la frase y el conector que la precede ('en', 'de', ',')."""
    patron = rf"(^| )((en|de|del) )?{re.escape(frase_norm)}( |$)"
    return " ".join(re.sub(patron, " ", texto_norm, count=1).split())
