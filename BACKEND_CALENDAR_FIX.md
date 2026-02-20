# 🔧 Backend Calendar Fix - Configuración de Credenciales Firebase para Google Calendar

## Problema Original

El endpoint `POST /convocar_actividad` fallaba en producción con el error:

```
[Errno 2] No such file or directory: '/app/dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json'
```

Esto ocurría porque el código intentaba cargar las credenciales de una ruta hardcodeada que no existe en entornos de producción (Docker, Railway, Heroku).

## Solución Implementada

El backend ahora carga credenciales desde **múltiples fuentes en orden de prioridad**:

### 1. **Variable de Entorno (Recomendado para Producción)**

```python
FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
```

- El JSON completo se pasa como una variable de entorno
- Válido en: Railway, Heroku, AWS, GCP, Kubernetes

### 2. **Ruta desde Variable de Entorno**

```python
GOOGLE_APPLICATION_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
```

- Referencias una ruta al archivo JSON
- Útil en servidores con sistema de archivos persistente

### 3. **Rutas Locales (Desarrollo)**

```
- dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json
- env/dagma-85aad-b7afe1c0f77f.json
```

- Funciona en desarrollo local
- No requiere configuración

## 📋 ¿Por Qué Falla en Producción?

Probablemente estás viendo este error:

```
calendar_event_error: "[Errno 2] No such file or directory: '/app/dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json'"
```

**Razón:** La variable de entorno `FIREBASE_SERVICE_ACCOUNT_JSON` **no está configurada** en tu servidor (Railway, Heroku, etc.).

El backend intenta cargar credenciales en este orden:

1. ❌ `FIREBASE_SERVICE_ACCOUNT_JSON` (env var) - **NO CONFIGURADA**
2. ❌ `GOOGLE_APPLICATION_CREDENTIALS` (ruta env var) - NO CONFIGURADA
3. ❌ `/app/dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json` - NO EXISTE en Docker
4. ❌ Rutas locales - NO EXISTEN en Docker

Resultado: El error no detiene la actividad (se guarda igual), pero sin evento en Google Calendar.

## 📋 Configuración en Railway (Producción)

### OPCIÓN A: Usar el Script PowerShell (RECOMENDADO)

```powershell
# En PowerShell dentro de la carpeta del proyecto:
.\setup_calendar_railway.ps1
```

El script:

- ✅ Valida que el JSON existe y es válido
- ✅ Lo copia automáticamente al portapapeles
- ✅ Te muestra instrucciones paso a paso

### OPCIÓN B: Forma Manual

#### Paso 1: Obtener el Contenido del JSON

```powershell
# En Windows PowerShell
$json = Get-Content "dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json" -Raw
$json | Set-Clipboard
Write-Host "✅ Contenido copiado al portapapeles"
```

#### Paso 2: Agregar Variable en Railway

1. Ve a tu proyecto en [Railway.app](https://railway.app)
2. Click en **Variables** (o **Environment**)
3. Click en **Add Variable**
4. Rellena:
   - **Nombre:** `FIREBASE_SERVICE_ACCOUNT_JSON`
   - **Valor:** [Pega aquí - `Ctrl+V`]
5. Click **Save**
6. La app se redesplegará automáticamente

#### Paso 3: Verificar

Después del redeploy, busca en **Logs**:

```
✅ Firebase inicializado exitosamente con FIREBASE_SERVICE_ACCOUNT_JSON
```

### Verificación

El backend imprimirá en los logs:

```
✅ Firebase inicializado exitosamente con FIREBASE_SERVICE_ACCOUNT_JSON
⚠️ Intentando crear evento en Google Calendar...
```

## 📝 Variables de Entorno Necesarias

| Variable                         | Valor                               | Requerida   | Ambiente          |
| -------------------------------- | ----------------------------------- | ----------- | ----------------- |
| `FIREBASE_SERVICE_ACCOUNT_JSON`  | Contenido JSON completo             | ✅ Sí       | Producción        |
| `GOOGLE_APPLICATION_CREDENTIALS` | Ruta al archivo JSON                | ⚠️ Opcional | Servidores con FS |
| (Archivos locales)               | `env/dagma-85aad-b7afe1c0f77f.json` | ✅ Sí       | Desarrollo        |

## 🧪 Test Local

Para verificar que funcione localmente:

```bash
python test_runner_convocar.py
```

Salida esperada:

```
Status Code: 200
Test Validated Successfully!
"calendar_event_id": "..." # Evento creado exitosamente
"calendar_event_link": "https://www.google.com/calendar/event?eid=..."
```

## ⚠️ Troubleshooting

### Error: "No se encontraron credenciales para Google Calendar"

**Solución:** Configura `FIREBASE_SERVICE_ACCOUNT_JSON` en variables de entorno

### Error: "JSON inválido en FIREBASE_SERVICE_ACCOUNT_JSON"

**Solución:** Verifica que el JSON sea válido (sin saltos de línea adicionales)

```powershell
# Verificar JSON válido
$json = Get-Content "archivo.json" -Raw
$obj = $json | ConvertFrom-Json  # Si funciona, está bien
```

### Error: "Calendar API error"

**Solución:** Verifica que el `calendar_id` sea correcto:

```python
calendar_id = '19c263371dc17e144c9ee0b12ac40c28339cb20c259f528d348730d98e193eb9@group.calendar.google.com'
```

## 📚 Cambios en el Código

**Archivo:** `app/routes/artefacto_360_routes.py`

El endpoint `POST /convocar_actividad` ahora:

1. ✅ Lee credenciales desde `FIREBASE_SERVICE_ACCOUNT_JSON`
2. ✅ Parsea JSON correctamente
3. ✅ Crea eventos en Google Calendar exitosamente
4. ✅ Manejea errores de forma graceful (no detiene la creación de la actividad)

## ✅ Estado Actual

- ✅ Backend actualizado y testeado
- ✅ Compatible con desarrollo local
- ✅ Compatible con producción (Railway, Heroku, etc.)
- ✅ Manejo robusto de credenciales con múltiples fallbacks
- ✅ Logs detallados para debugging

---

**Fecha:** 19/02/2026  
**Autor:** Sistema de Configuración  
**Estado:** ✅ Implementado y Validado
