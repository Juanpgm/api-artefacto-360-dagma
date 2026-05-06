"""
Script de migración: normaliza el campo `grupo` de todos los usuarios de Firestore a minúsculas.

Cambios:
  - Convierte grupo "Cuadrilla" → "cuadrilla", "Vivero" → "vivero", etc.
  - Elimina espacios extra (strip)
  - Actualiza custom claims en Firebase Auth
  - Usuarios con grupo=None o grupo="" no se tocan

Uso:
  # Ver qué se haría sin aplicar cambios:
  python scripts/normalize_grupos.py --dry-run

  # Aplicar cambios:
  python scripts/normalize_grupos.py
"""
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.firebase_config import auth_client, db


VALID_GRUPOS = {"cuadrilla", "vivero", "gobernanza", "ecosistemas", "umata"}


def main(dry_run: bool = False):
    mode = "[DRY-RUN] " if dry_run else ""
    print(f"{mode}Normalizando grupos de usuarios en Firestore...\n")

    users_ref = db.collection("users").stream()
    updated = 0
    skipped = 0
    errors = 0

    for doc in users_ref:
        data = doc.to_dict() or {}
        uid = data.get("uid") or doc.id
        raw_grupo = data.get("grupo")

        if not raw_grupo or not str(raw_grupo).strip():
            skipped += 1
            continue

        normalized = str(raw_grupo).strip().lower()

        if normalized == raw_grupo:
            # Already correct
            skipped += 1
            continue

        print(f"{mode}  uid={uid} grupo: '{raw_grupo}' → '{normalized}'", end="")

        if normalized not in VALID_GRUPOS:
            print(f"  ⚠ grupo no reconocido (se normaliza de todos modos)")
        else:
            print()

        if not dry_run:
            try:
                doc.reference.update({"grupo": normalized})
                # Update custom claims preserving existing role
                claims = auth_client.get_user(uid).custom_claims or {}
                role = data.get("role") or claims.get("role", "operador")
                auth_client.set_custom_user_claims(uid, {"role": role, "grupo": normalized})
                updated += 1
            except Exception as e:
                print(f"    ERROR: {e}")
                errors += 1
        else:
            updated += 1

    print(f"\n{mode}Listo — actualizados: {updated}, sin cambios: {skipped}, errores: {errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Normaliza grupos a minúsculas")
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar cambios, no aplicar")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
