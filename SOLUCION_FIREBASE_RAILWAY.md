# 🔧 Solución: Error de Firebase en Producción (Railway)

## ❌ Error Actual

```
File "/app/.venv/lib/python3.12/site-packages/google/oauth2/service_account.py", 
line 457, in _perform_refresh_token
```

**Causa**: Las credenciales de Firebase no están correctamente configuradas en Railway.

---

## ✅ Solución Paso a Paso

### Paso 1: Obtener el JSON de Credenciales

1. Ve a [Firebase Console](https://console.firebase.google.com/)
2. Selecciona tu proyecto: **dagma-85aad**
3. Ve a **Configuración del proyecto** (⚙️) > **Cuentas de servicio**
4. Click en **Generar nueva clave privada**
5. Se descargará un archivo JSON (ej: `dagma-85aad-firebase-adminsdk-xxxxx.json`)

### Paso 2: Preparar el JSON para Railway

El JSON debe estar en **una sola línea** sin saltos de línea:

#### Opción A: Usando PowerShell (Windows)
```powershell
# Leer el archivo y convertirlo a una línea
$json = Get-Content "dagma-85aad-firebase-adminsdk-xxxxx.json" -Raw | ConvertFrom-Json | ConvertTo-Json -Compress

# Copiar al portapapeles
$json | Set-Clipboard

# Mostrar en pantalla (verifica que se vea en una línea)
Write-Host $json
```

#### Opción B: Usando Python
```python
import json

# Leer el archivo
with open('dagma-85aad-firebase-adminsdk-xxxxx.json', 'r') as f:
    data = json.load(f)

# Convertir a string comprimido (sin espacios ni saltos de línea)
compressed_json = json.dumps(data, separators=(',', ':'))

# Copiar o imprimir
print(compressed_json)
```

#### Opción C: Manualmente
```bash
# En Linux/Mac
cat dagma-85aad-firebase-adminsdk-xxxxx.json | jq -c
```

El resultado debe verse así (todo en una línea):
```
{"type":"service_account","project_id":"dagma-85aad","private_key_id":"xxx","private_key":"-----BEGIN PRIVATE KEY-----\nMIIE...","client_email":"firebase-adminsdk@dagma-85aad.iam.gserviceaccount.com","client_id":"xxx","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk%40dagma-85aad.iam.gserviceaccount.com"}
```

### Paso 3: Configurar en Railway

1. Ve a tu proyecto en [Railway.app](https://railway.app)
2. Selecciona tu servicio de API
3. Ve a la pestaña **Variables**
4. Click en **+ New Variable**
5. Agrega:
   - **Nombre**: `FIREBASE_SERVICE_ACCOUNT_JSON`
   - **Valor**: Pega el JSON comprimido del Paso 2
6. Click en **Add** o **Save**

### Paso 4: Verificar la Variable

**IMPORTANTE**: El JSON debe tener estos campos obligatorios:

```json
{
  "type": "service_account",
  "project_id": "dagma-85aad",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "...@dagma-85aad.iam.gserviceaccount.com",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "...",
  "client_x509_cert_url": "..."
}
```

⚠️ **Puntos críticos**:
- El campo `private_key` debe mantener los saltos de línea como `\n`
- No debe tener comillas adicionales al principio o final
- Debe ser JSON válido

### Paso 5: Re-desplegar

1. En Railway, click en **Deploy** > **Redeploy**
2. O haz un nuevo commit al repositorio conectado
3. Espera a que el despliegue termine

### Paso 6: Verificar en Logs

Ve a la pestaña **Deployments** > **View Logs** y busca:

✅ **Correcto**:
```
🔥 Intentando inicializar Firebase con FIREBASE_SERVICE_ACCOUNT_JSON
✅ Firebase inicializado exitosamente con SERVICE_ACCOUNT_JSON
✅ Firebase Admin SDK configurado correctamente
```

❌ **Incorrecto**:
```
❌ Error parseando FIREBASE_SERVICE_ACCOUNT_JSON
❌ ERROR CRÍTICO: No se pudo inicializar Firebase
```

---

## 🔍 Verificación Local (Antes de Desplegar)

### Script de Verificación

Ejecuta en tu terminal local:

```powershell
# Verificar que la variable esté configurada
python verify_config.py
```

O verifica manualmente:

```python
import os
import json
from dotenv import load_dotenv

load_dotenv()

# Verificar que existe
json_str = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
if not json_str:
    print("❌ Variable no configurada")
else:
    print("✅ Variable encontrada")
    
    # Verificar que es JSON válido
    try:
        data = json.loads(json_str)
        print(f"✅ JSON válido")
        print(f"   - project_id: {data.get('project_id')}")
        print(f"   - client_email: {data.get('client_email')}")
        
        # Verificar campos obligatorios
        required_fields = ['type', 'project_id', 'private_key', 'client_email']
        missing = [f for f in required_fields if f not in data]
        
        if missing:
            print(f"❌ Faltan campos: {missing}")
        else:
            print("✅ Todos los campos requeridos presentes")
            
    except json.JSONDecodeError as e:
        print(f"❌ JSON inválido: {e}")
```

---

## 🚨 Errores Comunes

### Error 1: "Invalid JSON"
**Síntoma**: `Error parseando FIREBASE_SERVICE_ACCOUNT_JSON`

**Solución**: 
- Verifica que el JSON no tenga saltos de línea
- Usa un validador: https://jsonlint.com/
- No agregues comillas extra al principio/final

### Error 2: "private_key format error"
**Síntoma**: Error en `_perform_refresh_token`

**Solución**:
- El `private_key` debe mantener `\n` para saltos de línea
- Debe empezar con `-----BEGIN PRIVATE KEY-----\n`
- Debe terminar con `\n-----END PRIVATE KEY-----\n`

### Error 3: "Permission denied"
**Síntoma**: Error 403 o "insufficient permissions"

**Solución**:
- Verifica que la cuenta de servicio tenga permisos de **Firebase Admin SDK**
- En Firebase Console > IAM > Verifica roles
- El email debe ser: `firebase-adminsdk-xxxxx@dagma-85aad.iam.gserviceaccount.com`

### Error 4: Variable no se carga
**Síntoma**: `FIREBASE_SERVICE_ACCOUNT_JSON no está configurada`

**Solución**:
- Verifica que la variable se guardó en Railway
- Verifica el nombre exacto: `FIREBASE_SERVICE_ACCOUNT_JSON`
- Re-despliega la aplicación después de agregar variables

---

## 🔒 Seguridad

⚠️ **IMPORTANTE**:

1. **NUNCA** subas el archivo JSON al repositorio
2. **NUNCA** lo pongas en el código fuente
3. Solo úsalo como variable de entorno
4. Agrega al `.gitignore`:
   ```
   *firebase*.json
   dagma-*.json
   ```

---

## 📋 Checklist de Verificación

Antes de desplegar, verifica:

- [ ] JSON descargado desde Firebase Console
- [ ] JSON comprimido a una sola línea
- [ ] Variable `FIREBASE_SERVICE_ACCOUNT_JSON` configurada en Railway
- [ ] Campos obligatorios presentes (type, project_id, private_key, client_email)
- [ ] `private_key` mantiene los `\n`
- [ ] Railway re-desplegado después de agregar variable
- [ ] Logs muestran "✅ Firebase inicializado exitosamente"

---

## 🆘 Si Sigue Sin Funcionar

1. **Copia los logs completos** de Railway
2. **Verifica el contenido** de la variable (primeros 50 caracteres):
   ```
   Railway Dashboard > Variables > Click en FIREBASE_SERVICE_ACCOUNT_JSON
   ```
3. **Prueba localmente** primero:
   ```powershell
   # Agrega la variable a tu .env local
   FIREBASE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
   
   # Ejecuta la API
   uvicorn app.main:app --reload
   ```

---

## 📞 Contacto

Si el problema persiste después de seguir todos los pasos, proporciona:
- Screenshot de las variables en Railway
- Primeros 100 caracteres del JSON (sin el private_key)
- Logs completos del error

---

**Última actualización**: Febrero 11, 2026  
**Estado**: Configuración actualizada con manejo robusto de errores
