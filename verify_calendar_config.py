#!/usr/bin/env python3
"""
Script de verificación para configuración de credenciales de Google Calendar
Ayuda a diagnosticar problemas de carga de credenciales en producción
"""

import os
import json
import sys
from pathlib import Path

def check_firebase_service_account_json():
    """Verifica FIREBASE_SERVICE_ACCOUNT_JSON"""
    print("\n📋 Verificando FIREBASE_SERVICE_ACCOUNT_JSON...")
    
    env_value = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
    
    if not env_value:
        print("   ❌ FIREBASE_SERVICE_ACCOUNT_JSON no está configurada")
        return False
    
    print("   ✅ FIREBASE_SERVICE_ACCOUNT_JSON está configurada")
    
    try:
        data = json.loads(env_value)
        print(f"   ✅ JSON válido con campos: {list(data.keys())}")
        
        required_fields = ['type', 'project_id', 'private_key', 'client_email']
        missing = [f for f in required_fields if f not in data]
        if missing:
            print(f"   ⚠️  Campos faltantes: {missing}")
            return False
        
        print("   ✅ Contiene todos los campos requeridos")
        return True
    except json.JSONDecodeError as e:
        print(f"   ❌ JSON inválido: {e}")
        return False

def check_google_application_credentials():
    """Verifica GOOGLE_APPLICATION_CREDENTIALS"""
    print("\n📋 Verificando GOOGLE_APPLICATION_CREDENTIALS...")
    
    env_value = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    
    if not env_value:
        print("   ⚠️  GOOGLE_APPLICATION_CREDENTIALS no está configurada (opcional)")
        return None
    
    if os.path.exists(env_value):
        print(f"   ✅ Archivo existe: {env_value}")
        return True
    else:
        print(f"   ❌ Archivo no encontrado: {env_value}")
        return False

def check_local_files():
    """Verifica archivos locales"""
    print("\n📋 Verificando archivos de credenciales locales...")
    
    possible_paths = [
        'dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json',
        'env/dagma-85aad-b7afe1c0f77f.json',
        '/app/dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json',
    ]
    
    found = []
    for path in possible_paths:
        if os.path.exists(path):
            print(f"   ✅ Encontrado: {path}")
            found.append(path)
        else:
            print(f"   ❌ No encontrado: {path}")
    
    return len(found) > 0

def check_environment():
    """Resumen del ambiente"""
    print("\n" + "="*60)
    print("🔍 VERIFICACIÓN DE CONFIGURACIÓN DE GOOGLE CALENDAR")
    print("="*60)
    
    # Check methods in order of priority
    method1 = check_firebase_service_account_json()
    method2 = check_google_application_credentials()
    method3 = check_local_files()
    
    print("\n" + "="*60)
    print("💡 RESUMEN Y RECOMENDACIONES")
    print("="*60)
    
    if method1:
        print("✅ CONFIGURACIÓN LISTA - Google Calendar debería funcionar")
        return True
    
    if method2:
        print("✅ CONFIGURACIÓN LISTA - Google Calendar debería funcionar")
        return True
    
    if method3:
        print("✅ CONFIGURACIÓN LISTA (desarrollo) - Google Calendar debería funcionar")
        return True
    
    print("""
❌ ERROR: No se encontraron credenciales configuradas
    
Para PRODUCCIÓN (Railway, Heroku, etc.):
    
1. Obtén el archivo JSON de credenciales
2. Convierte a string (PowerShell):
   $json = Get-Content "dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json" -Raw
   $json | Set-Clipboard
   
3. En tu plataforma (Railway/Heroku), agrega variable de entorno:
   Nombre: FIREBASE_SERVICE_ACCOUNT_JSON
   Valor: [pega el JSON sin saltos de línea]
4. Redeploy la aplicación

Para DESARROLLO LOCAL:
    
Coloca el archivo en alguna de estas rutas:
  - dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json
  - env/dagma-85aad-b7afe1c0f77f.json
""")
    
    return False

def show_instructions():
    """Muestra instrucciones detalladas"""
    print("\n" + "="*60)
    print("📚 INSTRUCCIONES PARA CONFIGURAR EN RAILWAY")
    print("="*60)
    
    instructions = """
PASO 1: Preparar el JSON en Windows
────────────────────────────────────
PowerShell:
  $filePath = "A:\\programing_workspace\\api-artefacto-360-dagma\\dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json"
  $json = Get-Content $filePath -Raw
  $json | Set-Clipboard
  Write-Host "✅ Contenido copiado al portapapeles"

PASO 2: Configurar en Railway Dashboard
────────────────────────────────────────
1. Ve a: https://railway.app/project/[TU_PROYECTO]
2. Click en "Variables"
3. Click en "Add variable"
4. Nombre: FIREBASE_SERVICE_ACCOUNT_JSON
5. Valor: [Pega el contenido que copiaste]
6. Click "Save"

PASO 3: Redeploy
────────────────
1. La aplicación se redesplegará automáticamente
2. Verifica logs: Click en "Logs"
3. Busca: "✅ Firebase inicializado exitosamente"

PASO 4: Prueba del Endpoint
────────────────────────────
Envía una petición a POST /convocar_actividad

Respuesta esperada:
  "success": true,
  "calendar_event_id": "xxxxx",
  "calendar_event_link": "https://www.google.com/calendar/..."
"""
    
    print(instructions)

if __name__ == "__main__":
    # Run checks
    config_ok = check_environment()
    
    if "--help" in sys.argv or "-h" in sys.argv or not config_ok:
        show_instructions()
    
    # Exit with error code if not configured
    sys.exit(0 if config_ok else 1)
