"""
Carga centralizada de credenciales de Google para Calendar API.
"""
import os
import json
from google.oauth2 import service_account

CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar']


def _load_service_account_info() -> dict:
    """
    Carga el JSON del service account desde múltiples fuentes en orden de prioridad:
    1. Variable de entorno FIREBASE_SERVICE_ACCOUNT_JSON (Railway/producción)
    2. Variable de entorno GOOGLE_APPLICATION_CREDENTIALS (ruta de archivo)
    3. Archivos locales conocidos (desarrollo)
    Retorna el dict del service account o lanza ValueError.
    """
    raw_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
    if raw_json:
        return json.loads(raw_json)

    creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if creds_path and os.path.exists(creds_path):
        with open(creds_path, 'r') as f:
            return json.load(f)

    local_paths = [
        'dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json',
        'env/dagma-85aad-b7afe1c0f77f.json',
        os.path.join(os.path.dirname(__file__), '..', '..', 'dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json'),
    ]
    for path in local_paths:
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)

    raise ValueError(
        "No se encontraron credenciales de Google. "
        "Configura FIREBASE_SERVICE_ACCOUNT_JSON o GOOGLE_APPLICATION_CREDENTIALS."
    )


def get_calendar_credentials() -> service_account.Credentials:
    """Credenciales para Calendar API (sin impersonación)."""
    info = _load_service_account_info()
    return service_account.Credentials.from_service_account_info(
        info, scopes=CALENDAR_SCOPES
    )
