"""
Script para crear o verificar el primer usuario con rol `desarrollador`.

No disponible como endpoint de API por seguridad — solo ejecutable con
credenciales de servicio de Firebase en el entorno local o en CI.

Uso:
  # Promover por email (el usuario debe existir en Firebase Auth):
  python scripts/seed_developer.py --email dev@dagma.gov.co

  # Promover por UID directamente:
  python scripts/seed_developer.py --uid abc123xyz

  # Listar todos los desarrolladores actuales:
  python scripts/seed_developer.py --list

Nota: si el usuario ya tiene role=desarrollador, el script es idempotente (no hace nada extra).
"""
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

from app.firebase_config import auth_client, db
from app.models.roles import Role


def promote_to_developer(uid: str = None, email: str = None) -> None:
    """
    Asigna role=desarrollador en Firestore y como custom claim en Firebase Auth.
    Idempotente: si ya lo es, no hace nada extra.
    """
    if not uid and not email:
        raise ValueError("Debes proporcionar uid o email.")

    # Resolver UID
    if not uid:
        try:
            user = auth_client.get_user_by_email(email)
            uid = user.uid
            logger.info(f"Usuario encontrado: email={email} uid={uid}")
        except Exception as e:
            logger.error(f"No se encontró usuario con email '{email}': {e}")
            sys.exit(1)
    else:
        try:
            user = auth_client.get_user(uid)
            email = user.email
            logger.info(f"Usuario encontrado: uid={uid} email={email}")
        except Exception as e:
            logger.error(f"No se encontró usuario con uid '{uid}': {e}")
            sys.exit(1)

    # Verificar estado actual en Firestore
    doc_ref = db.collection("users").document(uid)
    doc = doc_ref.get()

    current_role = None
    if doc.exists:
        data = doc.to_dict() or {}
        current_role = data.get("role") or data.get("rol")
        if current_role == Role.DESARROLLADOR:
            logger.info(f"✅ El usuario {uid} ya tiene role=desarrollador. Sin cambios.")
            return

    # Aplicar cambios
    doc_ref.set(
        {
            "uid": uid,
            "email": email,
            "role": Role.DESARROLLADOR,
            "needs_review": False,
        },
        merge=True,
    )

    # Obtener grupo actual para preservarlo en claims (si existe)
    grupo = None
    if doc.exists:
        grupo = (doc.to_dict() or {}).get("grupo")

    auth_client.set_custom_user_claims(
        uid, {"role": Role.DESARROLLADOR, "grupo": grupo}
    )

    # Revocar tokens activos para que el usuario obtenga claims frescos
    auth_client.revoke_refresh_tokens(uid)

    logger.info(
        f"✅ Usuario {uid} ({email}) promovido a role=desarrollador.\n"
        f"   Su token actual fue revocado — debe re-autenticarse para obtener los nuevos claims."
    )
    if current_role:
        logger.info(f"   Rol anterior: {current_role}")


def list_developers() -> None:
    """Lista todos los usuarios con role=desarrollador en Firestore."""
    logger.info("=== Usuarios con role=desarrollador ===")
    docs = db.collection("users").where("role", "==", Role.DESARROLLADOR).stream()
    found = 0
    for doc in docs:
        data = doc.to_dict() or {}
        print(f"  uid={doc.id} email={data.get('email', 'N/A')} grupo={data.get('grupo', 'N/A')}")
        found += 1

    if found == 0:
        logger.info("  No se encontraron usuarios con role=desarrollador.")
    else:
        logger.info(f"  Total: {found}")


def main():
    parser = argparse.ArgumentParser(
        description="Gestión del primer usuario desarrollador (fuera del API)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email", type=str, help="Email del usuario a promover a desarrollador.")
    group.add_argument("--uid", type=str, help="UID de Firebase del usuario a promover.")
    group.add_argument(
        "--list", action="store_true", help="Lista todos los usuarios con role=desarrollador."
    )

    args = parser.parse_args()

    if args.list:
        list_developers()
    else:
        promote_to_developer(uid=args.uid, email=args.email)


if __name__ == "__main__":
    main()
