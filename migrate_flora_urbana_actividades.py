"""
Migrates the `plan_distrito_verde` Firestore collection by normalizing the
`grupos_requeridos` list field: any Flora urbana variant ("cuadrilla",
"Flora Urbana", etc.) is replaced with the canonical value "flora_urbana",
and duplicate occurrences are removed (first-occurrence position wins).

Usage:
    python migrate_flora_urbana_actividades.py             # dry-run
    python migrate_flora_urbana_actividades.py --dry-run   # explicit dry-run
    python migrate_flora_urbana_actividades.py --apply     # write to Firestore
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


def transform_grupos_requeridos(grupos: list[str]) -> tuple[list[str], bool]:
    """Return (new_list, changed) where changed=True if any element was modified.

    Processing rules:
    - Iterate in order.
    - If an element canonicalizes to flora_urbana:
        - Emit "flora_urbana" the FIRST time a flora_urbana variant is seen.
        - Skip all subsequent flora_urbana variants (deduplicate).
    - Non-flora_urbana elements pass through unchanged.
    - changed=True if any element was replaced or removed.
    """
    result: list[str] = []
    flora_urbana_emitted = False
    changed = False

    for item in grupos:
        if canonical_grupo_key(item) == CANONICAL_GRUPO:
            if not flora_urbana_emitted:
                result.append(CANONICAL_GRUPO)
                flora_urbana_emitted = True
                if item != CANONICAL_GRUPO:
                    changed = True
            else:
                # Subsequent duplicate — drop it
                changed = True
        else:
            result.append(item)

    return result, changed


# ---------------------------------------------------------------------------
# Firestore operations (inside run() to allow test imports without Firebase)
# ---------------------------------------------------------------------------

def run(dry_run: bool) -> None:
    from app.firebase_config import db  # noqa: E402

    col_ref = db.collection("plan_distrito_verde")
    docs = list(col_ref.stream())

    written = 0
    skipped = 0

    for snap in docs:
        data = snap.to_dict() or {}
        grupos_requeridos = data.get("grupos_requeridos")

        if not isinstance(grupos_requeridos, list):
            skipped += 1
            continue

        new_grupos, changed = transform_grupos_requeridos(grupos_requeridos)

        if not changed:
            skipped += 1
            continue

        print(
            f"  {'[DRY RUN] Would update' if dry_run else 'Updating'}: "
            f"id={snap.id!r}\n"
            f"    before: {grupos_requeridos}\n"
            f"    after:  {new_grupos}"
        )

        if not dry_run:
            col_ref.document(snap.id).update({"grupos_requeridos": new_grupos})
        written += 1

    label = "would write" if dry_run else "written"
    print(f"\n{written} {label}, {skipped} skipped (already canonical or no grupos_requeridos).")
    if dry_run:
        print("DRY RUN: nothing was written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Migrate grupos_requeridos field in `plan_distrito_verde` collection "
            "to use canonical flora_urbana."
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
    if args.apply:
        from _migration_guard import confirm_apply
        confirm_apply("plan_distrito_verde")
    run(dry_run=not args.apply)
