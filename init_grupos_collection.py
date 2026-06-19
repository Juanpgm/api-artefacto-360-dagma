"""
Inicializa la colección `grupos` en Firestore combinando:
  - Nombres de grupo y correos institucionales (definidos aquí)
  - Líderes de la colección existente `lideres_grupos` (matched por campo `grupo`)

Uso:
    python init_grupos_collection.py
    python init_grupos_collection.py --dry-run       # solo muestra lo que se crearía
    python init_grupos_collection.py --overwrite      # sobreescribe docs existentes
"""

import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.firebase_config import db  # noqa: E402

# ── Tabla de grupos y correos institucionales (extraída de la imagen) ──────────
GRUPOS_EMAILS: dict[str, str] = {
    "Acustica":               "acustica.dagma@cali.gov.co",
    "Tramites":               "coordinacion.tramite@cali.gov.co",
    "Calidad del Aire":       "admin.calidadaire@cali.gov.co",
    "Sancionatorio":          "admin.sancionatorio@cali.gov.co",
    "Laboratorio":            "laboratoriodagma@cali.gov.co",
    "Urbanistica":            "coordinacion.gua@cali.gov.co",
    "Residuos Solidos":       "operativo.residuos@cali.gov.co",
    "Flora Silvestre":        "flora.silvestre@cali.gov.co",
    "Fauna":                  "faunasilvestre@cali.gov.co",
    "Recurso Hídrico":        "coordinacion.hidrico@cali.gov.co",
    "Vivero":                 "vivero.dagma@cali.gov.co",
    "PSA":                    "psadagmacali@cali.gov.co",
    "Ecourbanismo":           "ecourbanismo.dagma@cali.gov.co",
    "Flora urbana":           "flora.coordinacion@cali.gov.co",
    "Umata":                  "umatacali@cali.gov.co",
    "Cambio Climatico":       "marcela.villa@cali.gov.co",
    "Gobernanza":             "viviana.ospina@cali.gov.co",
    "Huertas Urbanas":        "huertas@cali.gov.co",
    "Central Social":         "Centralsocialdagma@cali.gov.co",
    "Reacción":               "reaccion.dagma@cali.gov.co",
    "Ecosistemas":            "ecosistemas.dagma@cali.gov.co",
}


def _normalize(text: str) -> str:
    """Normalización básica para comparar nombres de grupo sin importar mayúsculas/tildes."""
    return text.strip().lower()


def fetch_lideres() -> dict[str, str]:
    """
    Lee la colección `lideres_grupos` y devuelve un dict:
        clave   → nombre normalizado del grupo
        valor   → nombre_completo del líder
    Si un grupo tiene más de un líder se usa el primero encontrado.
    Campos reconocidos para el nombre del líder: nombre_completo, nombre, lider.
    Devuelve dict vacío si la colección no existe o no es accesible.
    """
    lideres: dict[str, str] = {}
    try:
        docs = db.collection("lideres_grupos").stream()
        for doc in docs:
            data = doc.to_dict() or {}
            grupo_raw = data.get("grupo") or data.get("Grupo") or ""
            if not grupo_raw:
                continue
            grupo_key = _normalize(str(grupo_raw))
            if grupo_key in lideres:
                continue  # ya tenemos un líder para este grupo
            nombre = (
                data.get("nombre_completo")
                or data.get("Nombre completo")
                or data.get("nombre")
                or data.get("Nombre")
                or data.get("lider")
                or data.get("Lider")
            )
            lideres[grupo_key] = {
                "nombre": str(nombre).strip() if nombre else None,
                "email": None,
                "numero_contacto": None,
            }
    except Exception as e:
        print(f"⚠️  No se pudo leer `lideres_grupos`: {e}")
        print("   Los grupos se crearán sin líder asignado.")
    return lideres


LIDER_VACIO: dict = {"nombre": None, "email": None, "numero_contacto": None}


def build_grupos_docs(lideres: dict[str, dict]) -> list[dict]:
    """Construye los documentos a insertar en `grupos`."""
    docs = []
    for nombre, email in GRUPOS_EMAILS.items():
        lider = lideres.get(_normalize(nombre), LIDER_VACIO.copy())
        doc = {
            "nombre": nombre,
            "email": email,
            "lider": lider,
        }
        docs.append(doc)
    return docs


def run(dry_run: bool, overwrite: bool, skip_lideres: bool) -> None:
    if skip_lideres:
        print("⏭  Omitiendo lectura de `lideres_grupos` (--skip-lideres).")
        lideres: dict[str, str] = {}
    else:
        print("🔍 Leyendo líderes desde `lideres_grupos`...")
        lideres = fetch_lideres()
        print(f"   → {len(lideres)} grupo(s) con líder encontrado(s): {list(lideres.keys())}")

    grupos_docs = build_grupos_docs(lideres)
    col_ref = db.collection("grupos")

    sin_lider = [d["nombre"] for d in grupos_docs if d["lider"] is None]
    if sin_lider:
        print(f"⚠️  Sin líder coincidente: {sin_lider}")

    if dry_run:
        print("\n── DRY RUN ─────────────────────────────────────────────")
        for d in grupos_docs:
            print(f"  {d['nombre']!r:35} | {d['email']!r:45} | lider={d['lider']!r}")
        print(f"\nTotal: {len(grupos_docs)} documento(s). Nada fue escrito.")
        return

    escritos = 0
    omitidos = 0
    for doc_data in grupos_docs:
        doc_id = _normalize(doc_data["nombre"]).replace(" ", "_")
        doc_ref = col_ref.document(doc_id)
        if not overwrite:
            snap = doc_ref.get()
            if snap.exists:
                print(f"  ⏭  Omitiendo (ya existe): {doc_data['nombre']!r}")
                omitidos += 1
                continue
        doc_ref.set(doc_data)
        print(f"  ✅ Guardado: {doc_data['nombre']!r} → lider={doc_data['lider']!r}")
        escritos += 1

    print(f"\n🎉 Listo: {escritos} escrito(s), {omitidos} omitido(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inicializa la colección `grupos` en Firestore")
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra lo que se crearía, sin escribir")
    parser.add_argument("--overwrite", action="store_true", help="Sobreescribe documentos existentes")
    parser.add_argument("--skip-lideres", action="store_true", help="No leer lideres_grupos; crea grupos sin líder")
    args = parser.parse_args()
    run(dry_run=args.dry_run, overwrite=args.overwrite, skip_lideres=args.skip_lideres)
