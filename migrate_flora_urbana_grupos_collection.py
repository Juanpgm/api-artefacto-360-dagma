"""
Migrates the `grupos` Firestore collection by collapsing all Flora urbana
variant documents (e.g., "Cuadrilla", "Flora Urbana") into a single canonical
document with id `flora_urbana`.

Usage:
    python migrate_flora_urbana_grupos_collection.py             # dry-run
    python migrate_flora_urbana_grupos_collection.py --dry-run   # explicit dry-run
    python migrate_flora_urbana_grupos_collection.py --apply     # write to Firestore
"""

import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Pure transform functions (no Firestore — importable in unit tests)
# ---------------------------------------------------------------------------

CANONICAL_ID = "flora_urbana"
CANONICAL_NOMBRE = "Flora urbana"
CANONICAL_EMAIL = "flora.coordinacion@cali.gov.co"


def merge_flora_urbana_docs(docs: list[dict]) -> dict | None:
    """Given a list of raw doc dicts (each has 'nombre', 'email', 'lider'),
    return the merged canonical doc or None if the list is empty.

    Merge strategy:
    - nombre is always CANONICAL_NOMBRE
    - email is always CANONICAL_EMAIL
    - lider.nombre: first non-null value found across all docs
    - lider.email: first non-null value found across all docs
    """
    if not docs:
        return None

    merged_lider_nombre = None
    merged_lider_email = None

    for doc in docs:
        lider = doc.get("lider") or {}
        if merged_lider_nombre is None:
            merged_lider_nombre = lider.get("nombre") or None
        if merged_lider_email is None:
            merged_lider_email = lider.get("email") or None
        if merged_lider_nombre and merged_lider_email:
            break

    return {
        "nombre": CANONICAL_NOMBRE,
        "email": CANONICAL_EMAIL,
        "lider": {
            "nombre": merged_lider_nombre,
            "email": merged_lider_email,
            "numero_contacto": None,
        },
    }


# ---------------------------------------------------------------------------
# Firestore operations (imported lazily inside run() to allow test imports)
# ---------------------------------------------------------------------------

def run(dry_run: bool) -> None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app.firebase_config import db  # noqa: E402
    from app.utils.text_utils import canonical_grupo_key  # noqa: E402

    col_ref = db.collection("grupos")
    all_docs = list(col_ref.stream())

    variant_docs: list[tuple] = []  # (doc_id, doc_data)
    canonical_doc_snap = None

    for snap in all_docs:
        data = snap.to_dict() or {}
        nombre = data.get("nombre", "")
        if canonical_grupo_key(nombre) == CANONICAL_ID:
            variant_docs.append((snap.id, data))
            if snap.id == CANONICAL_ID:
                canonical_doc_snap = (snap.id, data)

    if not variant_docs:
        print("No Flora urbana variant documents found. Nothing to do.")
        return

    non_canonical = [(doc_id, data) for doc_id, data in variant_docs if doc_id != CANONICAL_ID]
    all_data = [data for _, data in variant_docs]

    merged = merge_flora_urbana_docs(all_data)

    print("\n-- Flora urbana docs found --")
    for doc_id, data in variant_docs:
        print(f"  id={doc_id!r}  nombre={data.get('nombre')!r}")

    print(f"\n-- Merged canonical doc (id={CANONICAL_ID!r}) --")
    print(f"  nombre={merged['nombre']!r}")
    print(f"  email={merged['email']!r}")
    print(f"  lider={merged['lider']!r}")
    print(f"\n-- Non-canonical docs to delete: {[doc_id for doc_id, _ in non_canonical]}")

    if dry_run:
        print("\nDRY RUN: nothing was written.")
        return

    # Write canonical doc
    col_ref.document(CANONICAL_ID).set(merged)
    print(f"\nWritten: {CANONICAL_ID!r}")

    # Delete non-canonical variant docs
    deleted = 0
    for doc_id, _ in non_canonical:
        col_ref.document(doc_id).delete()
        print(f"Deleted: {doc_id!r}")
        deleted += 1

    written = 1
    print(f"\n{written} written, {deleted} deleted, {len(all_docs) - len(variant_docs)} skipped (other groups).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Collapse Flora urbana variants in the `grupos` collection."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would change without writing (default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes to Firestore.",
    )
    args = parser.parse_args()
    apply_mode = args.apply
    if apply_mode:
        from _migration_guard import confirm_apply
        confirm_apply("grupos")
    run(dry_run=not apply_mode)
