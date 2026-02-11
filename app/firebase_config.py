"""
Configuración de Firebase Admin SDK
"""
import os
import json
import firebase_admin
from firebase_admin import credentials, firestore, auth
from dotenv import load_dotenv
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

def initialize_firebase():
    """
    Inicializa Firebase Admin SDK con manejo robusto de errores
    Intenta múltiples métodos de autenticación en orden de prioridad
    """
    try:
        # Método 1: Usar JSON desde variable de entorno (Railway, Heroku)
        SERVICE_ACCOUNT_JSON = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
        
        if SERVICE_ACCOUNT_JSON:
            logger.info("🔥 Intentando inicializar Firebase con FIREBASE_SERVICE_ACCOUNT_JSON")
            try:
                service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
                cred = credentials.Certificate(service_account_info)
                firebase_admin.initialize_app(cred)
                logger.info("✅ Firebase inicializado exitosamente con SERVICE_ACCOUNT_JSON")
                return True
            except json.JSONDecodeError as e:
                logger.error(f"❌ Error parseando FIREBASE_SERVICE_ACCOUNT_JSON: {e}")
                logger.info("💡 Verifica que la variable contenga JSON válido")
                raise ValueError(f"FIREBASE_SERVICE_ACCOUNT_JSON no es un JSON válido: {e}")
            except Exception as e:
                logger.error(f"❌ Error inicializando Firebase con SERVICE_ACCOUNT_JSON: {e}")
                raise
        
        # Método 2: Usar ruta de archivo (GOOGLE_APPLICATION_CREDENTIALS)
        GOOGLE_CREDS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if GOOGLE_CREDS_PATH and os.path.exists(GOOGLE_CREDS_PATH):
            logger.info(f"🔥 Intentando inicializar Firebase con archivo: {GOOGLE_CREDS_PATH}")
            cred = credentials.Certificate(GOOGLE_CREDS_PATH)
            firebase_admin.initialize_app(cred)
            logger.info("✅ Firebase inicializado exitosamente con GOOGLE_APPLICATION_CREDENTIALS")
            return True
        
        # Método 3: Buscar archivos JSON en directorios comunes
        possible_paths = [
            'dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json',
            'env/dagma-85aad-b7afe1c0f77f.json',
            '/app/firebase-credentials.json',  # Path común en Railway
            '/etc/secrets/firebase-credentials.json'  # Path común en Kubernetes
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"🔥 Intentando inicializar Firebase con archivo encontrado: {path}")
                try:
                    cred = credentials.Certificate(path)
                    firebase_admin.initialize_app(cred)
                    logger.info(f"✅ Firebase inicializado exitosamente con archivo: {path}")
                    return True
                except Exception as e:
                    logger.warning(f"⚠️ No se pudo usar {path}: {e}")
                    continue
        
        # Si no se encuentra ninguna credencial
        error_msg = """
❌ ERROR CRÍTICO: No se pudo inicializar Firebase Admin SDK
        
No se encontraron credenciales válidas. Verifica que exista una de estas opciones:
        
1. Variable de entorno FIREBASE_SERVICE_ACCOUNT_JSON con el contenido JSON completo
2. Variable de entorno GOOGLE_APPLICATION_CREDENTIALS con ruta al archivo JSON
3. Archivo de credenciales en alguna de estas rutas:
   - dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json
   - env/dagma-85aad-b7afe1c0f77f.json
   - /app/firebase-credentials.json (Railway)
   
📝 Para configurar en Railway/Heroku:
   - Ve a Variables de entorno
   - Agrega FIREBASE_SERVICE_ACCOUNT_JSON
   - Pega el contenido completo del archivo JSON (sin saltos de línea)
   
📝 Para verificar localmente:
   - Ejecuta: python verify_config.py
"""
        logger.error(error_msg)
        raise ValueError(error_msg)
        
    except Exception as e:
        if "Firebase app named" in str(e) and "already exists" in str(e):
            # Firebase ya está inicializado (probablemente en tests)
            logger.info("⚠️ Firebase ya estaba inicializado")
            return True
        else:
            logger.error(f"❌ Error fatal inicializando Firebase: {e}")
            raise

# Inicializar Firebase
try:
    initialize_firebase()
    
    # Obtener referencias a los servicios
    db = firestore.client()
    auth_client = auth
    
    logger.info("✅ Firebase Admin SDK configurado correctamente")
    
except Exception as e:
    logger.error(f"❌ ERROR CRÍTICO: No se pudo inicializar Firebase: {e}")
    # En producción, queremos que la app falle rápido si Firebase no está disponible
    # En lugar de continuar con un servicio mal configurado
    raise