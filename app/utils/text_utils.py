"""Utilidades de normalización de texto.

`normalize_grupo` se usa para comparar nombres de grupo de manera insensible a
mayúsculas/minúsculas y a tildes/diacríticos, sin perder el nombre canónico que
está guardado en la colección `grupos` de Firestore (p.ej. "Acústica").
"""
from __future__ import annotations

import unicodedata


def strip_accents(text: str) -> str:
    """Elimina marcas diacríticas (tildes, diéresis, etc.) preservando la letra base."""
    if not isinstance(text, str):
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize_grupo(text: str | None) -> str:
    """Forma canónica para comparar nombres de grupo.

    - strip()
    - lower()
    - quita tildes/diacríticos
    - trata "_" como espacio y colapsa espacios repetidos

    Esto permite que variantes como "Central Social", "Central_Social",
    "central_social" y "CENTRAL  SOCIAL" se consideren equivalentes, y que
    "Reacción" coincida con "reaccion". El nombre canónico almacenado en
    Firestore se preserva (esta función sólo se usa para comparar).

    Ejemplos:
        normalize_grupo("Acústica")        -> "acustica"
        normalize_grupo("  ACÚSTICA ")     -> "acustica"
        normalize_grupo("Cuadrilla")       -> "cuadrilla"
        normalize_grupo("Central_Social")  -> "central social"
        normalize_grupo("Central Social")  -> "central social"
        normalize_grupo("Reacción")        -> "reaccion"
    """
    if not text:
        return ""
    cleaned = strip_accents(str(text)).lower().replace("_", " ")
    # Colapsa cualquier secuencia de whitespace en un único espacio y recorta extremos.
    return " ".join(cleaned.split())


def grupos_match(a: str | None, b: str | None) -> bool:
    """True si dos nombres de grupo representan el mismo grupo."""
    return normalize_grupo(a) == normalize_grupo(b)


# Variantes conocidas del grupo "Flora urbana" en forma normalizada
# (salida de normalize_grupo: sin tildes, minúsculas, guiones→espacios).
# Se mantienen aquí para que canonical_grupo_key y los scripts de migración
# compartan la misma fuente de verdad.
VARIANTES_FLORA_URBANA: frozenset[str] = frozenset({
    "cuadrilla",
    "flora urbana",
    "flora urbana (cuadrilla)",
})


def canonical_grupo_key(text: str | None) -> str:
    """Devuelve la clave canónica de Firestore para un nombre de grupo.

    Para la familia Flora urbana / Cuadrilla y sus variantes históricas,
    devuelve siempre ``"flora_urbana"`` independientemente de mayúsculas,
    tildes o formato.  El resto de grupos se normalizan a minúsculas con
    ``normalize_grupo`` (p. ej. ``"vivero"``, ``"gobernanza"``).

    Esta función es la fuente de verdad para:
    - Lookup en GRUPOS_CONFIG del backend.
    - Scripts de migración de datos.
    - Comparaciones durante la ventana de datos mixtos.
    """
    normalizado = normalize_grupo(text)
    if normalizado in VARIANTES_FLORA_URBANA:
        return "flora_urbana"
    return normalizado
