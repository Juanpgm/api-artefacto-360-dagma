"""
Script de migración: normaliza el campo de rol en la colección `users` de Firestore.

Cambios:
  - Renombra `rol` → `role` (si no existe `role` todavía)
  - Normaliza valores legacy: admin → administrador, super_admin → desarrollador, etc.
  - Usuarios sin rol conocido → role="operador", needs_review=True
  - Usuarios sin grupo → grupo permanece None, needs_review=True
  - Propaga Firebase custom claims (role + grupo) para cada usuario

Uso:
  # Ver qué se haría sin aplicar cambios:
  python scripts/migrate_users_role_field.py --dry-run

  # Aplicar cambios en Firestore + custom claims:
  python scripts/migrate_users_role_field.py --apply

  # Limitar a N usuarios (útil para pruebas):
  python scripts/migrate_users_role_field.py --apply --limit 10

Requiere: credenciales de Firebase Admin en el entorno (mismas que la API).
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Agregar el directorio raíz del proyecto al path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Cargar .env si existe
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("migration_users_role.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Importar Firebase DESPUÉS de ajustar el path
from app.firebase_config import auth_client, db
from app.models.roles import LEGACY_ROLE_MAP, Role, normalize_role


def migrate(dry_run: bool = True, limit: int = None) -> dict:
    """
    Itera la colección `users`, normaliza el campo de rol y opcionalmente
    aplica los cambios en Firestore y Firebase Auth custom claims.

    Devuelve un resumen con estadísticas de la migración.
    """
    mode_label = "DRY-RUN" if dry_run else "APPLY"
    logger.info(f"=== Migración de roles [{mode_label}] iniciada ===")
    if limit:
        logger.info(f"Limitando a {limit} documentos")

    docs = list(db.collection("users").stream())
    total = len(docs)
    logger.info(f"Total documentos en colección 'users': {total}")

    stats = {
        "total": total,
        "migrated": 0,
        "already_ok": 0,
        "needs_review": 0,
        "errors": 0,
        "changes": [],
    }

    if limit:
        docs = docs[:limit]

    for doc in docs:
        uid = doc.id
        data = doc.to_dict() or {}

        try:
            changes = {}
            flags = {}

            # ── 1. Resolver el rol ──────────────────────────────────────────
            current_role = data.get("role")
            legacy_rol = data.get("rol")

            if current_role:
                # Ya tiene `role`; normalizar si es valor legacy
                normalized = normalize_role(current_role)
                if normalized and normalized != current_role:
                    changes["role"] = normalized
                    logger.info(
                        f"  [{uid}] role: '{current_role}' → '{normalized}' (normalización)"
                    )
                elif not normalized:
                    # Valor desconocido → operador
                    changes["role"] = Role.OPERADOR
                    flags["needs_review"] = True
                    logger.warning(
                        f"  [{uid}] role '{current_role}' desconocido → operador (needs_review)"
                    )
            elif legacy_rol:
                # Tiene `rol` (legacy) pero no `role`
                normalized = normalize_role(legacy_rol)
                if normalized:
                    changes["role"] = normalized
                    changes["_delete_rol"] = True  # marcar para borrar campo legacy
                    logger.info(
                        f"  [{uid}] rol: '{legacy_rol}' → role: '{normalized}' (migración)"
                    )
                else:
                    changes["role"] = Role.OPERADOR
                    changes["_delete_rol"] = True
                    flags["needs_review"] = True
                    logger.warning(
                        f"  [{uid}] rol '{legacy_rol}' desconocido → operador (needs_review)"
                    )
            else:
                # Sin ningún campo de rol
                if not current_role:
                    changes["role"] = Role.OPERADOR
                    flags["needs_review"] = True
                    logger.warning(f"  [{uid}] sin rol → operador (needs_review)")

            # ── 2. Resolver grupo ───────────────────────────────────────────
            grupo = data.get("grupo")
            if not grupo:
                flags["needs_review"] = True
                logger.warning(f"  [{uid}] sin grupo asignado (needs_review)")

            # ── 3. Agregar flags si aplica ──────────────────────────────────
            if flags:
                changes.update(flags)
                stats["needs_review"] += 1

            # ── 4. Determinar si hay algo que hacer ─────────────────────────
            has_firestore_changes = bool(
                {k: v for k, v in changes.items() if not k.startswith("_")}
            )
            delete_rol = changes.pop("_delete_rol", False)

            effective_role = changes.get("role") or data.get("role") or Role.OPERADOR
            effective_grupo = grupo  # puede ser None

            if not has_firestore_changes and not delete_rol:
                stats["already_ok"] += 1
                logger.debug(f"  [{uid}] ya normalizado, sin cambios")
                continue

            # Registrar cambio para el resumen
            stats["changes"].append(
                {
                    "uid": uid,
                    "email": data.get("email", ""),
                    "before": {
                        "role": data.get("role"),
                        "rol": data.get("rol"),
                        "grupo": grupo,
                    },
                    "after": {
                        "role": effective_role,
                        "grupo": effective_grupo,
                        "needs_review": changes.get("needs_review", False),
                    },
                    "dry_run": dry_run,
                }
            )

            if not dry_run:
                # Escribir en Firestore
                update_payload = {k: v for k, v in changes.items() if not k.startswith("_")}
                db.collection("users").document(uid).update(update_payload)

                if delete_rol:
                    from google.cloud import firestore as _fs
                    db.collection("users").document(uid).update(
                        {"rol": _fs.DELETE_FIELD}
                    )

                # Propagar custom claims en Firebase Auth
                try:
                    auth_client.set_custom_user_claims(
                        uid,
                        {"role": effective_role, "grupo": effective_grupo},
                    )
                    logger.info(
                        f"  [{uid}] claims actualizados → role={effective_role}, grupo={effective_grupo}"
                    )
                except Exception as claims_err:
                    logger.warning(
                        f"  [{uid}] error al setear claims (puede que el uid no exista en Auth): {claims_err}"
                    )

            stats["migrated"] += 1

        except Exception as doc_err:
            logger.error(f"  [{uid}] ERROR inesperado: {doc_err}", exc_info=True)
            stats["errors"] += 1

    # ── Resumen final ───────────────────────────────────────────────────────
    logger.info("=== Resumen de migración ===")
    logger.info(f"  Total docs procesados : {len(docs)}")
    logger.info(f"  Ya normalizados       : {stats['already_ok']}")
    logger.info(f"  Migrados/pendientes   : {stats['migrated']}")
    logger.info(f"  Requieren revisión    : {stats['needs_review']}")
    logger.info(f"  Errores               : {stats['errors']}")
    if dry_run:
        logger.info("  ⚠️  Modo DRY-RUN: ningún cambio fue aplicado.")
    else:
        logger.info("  ✅ Cambios aplicados en Firestore y Firebase Auth.")

    # Escribir resumen JSON
    summary_path = ROOT / "migration_users_role_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        summary = {k: v for k, v in stats.items() if k != "changes"}
        summary["dry_run"] = dry_run
        summary["timestamp"] = datetime.now(timezone.utc).isoformat()
        summary["first_50_changes"] = stats["changes"][:50]
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info(f"  Resumen guardado en: {summary_path}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Migra el campo de rol en Firestore users (rol → role, normaliza valores)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra qué cambiaría sin aplicar nada (por defecto).",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Aplica los cambios en Firestore y actualiza custom claims.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Procesar solo los primeros N documentos (útil para pruebas).",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    stats = migrate(dry_run=dry_run, limit=args.limit)

    if stats["errors"] > 0:
        logger.error(f"Migración completada con {stats['errors']} error(es).")
        sys.exit(1)


if __name__ == "__main__":
    main()
