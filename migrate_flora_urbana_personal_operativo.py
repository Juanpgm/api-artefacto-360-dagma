"""
Migrates the `personal_operativo` Firestore collection by updating the
`grupo` field from any Flora urbana variant to the canonical value
"flora_urbana".

Usage:
    python migrate_flora_urbana_personal_operativo.py             # dry-run
    python migrate_flora_urbana_personal_operativo.py --dry-run   # explicit dry-run
    python migrate_flora_urbana_personal_operativo.py --apply     # write to Firestore
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
    """True if this personal_operativo doc needs its grupo field migrated.

    A doc needs migration when its grupo canonicalizes to flora_urbana
    but the stored value is not already the canonical string.
    """
    grupo = doc.get("grupo")
    if grupo is None:
        return False
    return canonical_grupo_key(grupo) == CANONICAL_GRUPO and grupo != CANONICAL_GRUPO


def transform_doc(doc: dict) -> dict:
    """Return a new dict with grupo set to 'flora_urbana'.

    Only call this when needs_migration(doc) is True.
    Returns the full document with the grupo field replaced.
    """
    return {**doc, "grupo": CANONICAL_GRUPO}


# ---------------------------------------------------------------------------
# Firestore operations (inside run() to allow test imports without Firebase)
# ---------------------------------------------------------------------------

def run(dry_run: bool) -> None:
    from app.firebase_config import db  # noqa: E402

    col_ref = db.collection("personal_operativo")
    docs = list(col_ref.stream())

    written = 0
    skipped = 0

    for snap in docs:
        data = snap.to_dict() or {}
        if not needs_migration(data):
            skipped += 1
            continue

        new_data = transform_doc(data)
        print(
            f"  {'[DRY RUN] Would update' if dry_run else 'Updating'}: "
            f"id={snap.id!r}  grupo: {data.get('grupo')!r} -> {CANONICAL_GRUPO!r}"
        )

        if not dry_run:
            col_ref.document(snap.id).update({"grupo": CANONICAL_GRUPO})
        written += 1

    label = "would write" if dry_run else "written"
    print(f"\n{written} {label}, {skipped} skipped (already canonical or other group).")
    if dry_run:
        print("DRY RUN: nothing was written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate grupo field in `personal_operativo` collection to canonical flora_urbana."
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
    if args.apply:
        from _migration_guard import confirm_apply
        confirm_apply("personal_operativo")
    run(dry_run=not args.apply)
