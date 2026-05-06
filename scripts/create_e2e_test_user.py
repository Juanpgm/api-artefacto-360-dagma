"""  
Crea (o resetea) el usuario de test E2E en Firebase Auth + Firestore.

Uso:
  cd back
  python scripts/create_e2e_test_user.py

El usuario es:
  email:    test.e2e@dagma.local
  password: TestDagma2026!
  role:     operador
  grupo:    cuadrilla
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from firebase_admin import auth
from app.firebase_config import auth_client, db

EMAIL = "test.e2e@dagma.local"
PASSWORD = "TestDagma2026!"
DISPLAY_NAME = "Usuario E2E Test"
GRUPO = "cuadrilla"
ROLE = "operador"


def main():
    # 1. Crear o actualizar en Firebase Auth
    try:
        user = auth_client.get_user_by_email(EMAIL)
        print(f"Usuario existente uid={user.uid}. Actualizando contraseña y display_name...")
        auth_client.update_user(user.uid, password=PASSWORD, display_name=DISPLAY_NAME, email_verified=True)
    except auth.UserNotFoundError:
        print(f"Creando usuario {EMAIL} en Firebase Auth...")
        user = auth_client.create_user(
            email=EMAIL,
            password=PASSWORD,
            display_name=DISPLAY_NAME,
            email_verified=True,
        )
        print(f"Usuario creado uid={user.uid}")

    uid = user.uid

    # 2. Custom claims
    auth_client.set_custom_user_claims(uid, {"role": ROLE, "grupo": GRUPO})

    # 3. Firestore doc
    doc_ref = db.collection("users").document(uid)
    doc_ref.set({
        "uid": uid,
        "email": EMAIL,
        "full_name": DISPLAY_NAME,
        "role": ROLE,
        "grupo": GRUPO,
        "needs_review": False,
        "created_at": datetime.now(timezone.utc),
        "e2e_test_user": True,
    }, merge=True)

    print(f"OK — usuario E2E listo:")
    print(f"  uid:      {uid}")
    print(f"  email:    {EMAIL}")
    print(f"  password: {PASSWORD}")
    print(f"  grupo:    {GRUPO}")
    print(f"  role:     {ROLE}")


if __name__ == "__main__":
    main()
