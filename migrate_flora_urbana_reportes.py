"""
Migrates the `reportes_intervenciones` Firestore collection by updating the
`grupo` field from any Flora urbana variant to the canonical value
"flora_urbana".

IMPORTANT: Only the `grupo` field is updated. Fields like `photosUrl` and
`s3_key` are intentionally left untouched — S3 paths must not change because
they point to existing photo objects.

Usage:
    python migrate_flora_urbana_reportes.py             # dry-run
    python migrate_flora_urbana_reportes.py --dry-run   # explicit dry-run
    python migrate_flora_urbana_reportes.py --apply     # write to Firestore
"""

import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Pure transform functions (no Firestore — importable in unit tests)
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.utils.text_utils import canonical_grupo_key  # noqa: E402

CANONICAL_GRUPO = "flora_urbana"


def needs_migration(doc: dict) -> bool:
    """True if this reporte doc needs its grupo field migrated.

    A doc needs migration when its grupo canonicalizes to flora_urbana
    but the stored value is not already the canonical string.
    """
    grupo = doc.get("grupo")
    if grupo is None:
        return False
    return canonical_grupo_key(grupo) == CANONICAL_GRUPO and grupo != CANONICAL_GRUPO


def transform_doc(doc: dict) -> dict:
    """Return a dict with ONLY the fields to update: {'grupo': 'flora_urbana'}.

    Intentionally does NOT include photosUrl, s3_key, or any other field.
    Only call this when needs_migration(doc) is True.
    """
    return {"grupo": CANONICAL_GRUPO}


# ---------------------------------------------------------------------------
# Firestore operations (inside run() to allow test imports without Firebase)
# ---------------------------------------------------------------------------

def run(dry_run: bool) -> None:
    from app.firebase_config import db  # noqa: E402

    col_ref = db.collection("reportes_intervenciones")
    docs = list(col_ref.stream())

    written = 0
    skipped = 0

    for snap in docs:
        data = snap.to_dict() or {}
        if not needs_migration(data):
            skipped += 1
            continue

        update_payload = transform_doc(data)
        print(
            f"  {'[DRY RUN] Would update' if dry_run else 'Updating'}: "
            f"id={snap.id!r}  grupo: {data.get('grupo')!r} -> {CANONICAL_GRUPO!r}"
        )

        if not dry_run:
            col_ref.document(snap.id).update(update_payload)
        written += 1

    label = "would write" if dry_run else "written"
    print(f"\n{written} {label}, {skipped} skipped (already canonical or other group).")
    if dry_run:
        print("DRY RUN: nothing was written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Migrate grupo field in `reportes_intervenciones` collection to "
            "canonical flora_urbana. Does NOT touch photosUrl or s3_key."
        )
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
    run(dry_run=not args.apply)
