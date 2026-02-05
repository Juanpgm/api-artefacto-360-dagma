"""
Configuración de Firebase Admin SDK
"""
import os
import firebase_admin
from firebase_admin import credentials, firestore, auth
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Obtener la ruta de la clave de servicio
SERVICE_ACCOUNT_KEY_PATH = os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY_PATH')

if not SERVICE_ACCOUNT_KEY_PATH:
    raise ValueError("FIREBASE_SERVICE_ACCOUNT_KEY_PATH no está configurada en el archivo .env")

# Verificar que el archivo existe
if not os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
    raise FileNotFoundError(f"El archivo de clave de servicio no se encuentra en: {SERVICE_ACCOUNT_KEY_PATH}")

# Inicializar Firebase Admin SDK
cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
firebase_admin.initialize_app(cred)

# Obtener referencias a los servicios
db = firestore.client()
auth_client = auth

print("Firebase inicializado correctamente")