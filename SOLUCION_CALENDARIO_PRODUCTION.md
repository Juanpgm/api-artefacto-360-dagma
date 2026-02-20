# 🔴 Diagnostic: Por Qué Falta Google Calendar en Production

## El Problema Actual

Estás viendo en Firebase:

```json
{
  "calendar_event_error": "[Errno 2] No such file or directory: '/app/dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json'"
}
```

## Diagnosis

✅ **Backend está actualizado** (el código ya está en `artefacto_360_routes.py`)
❌ **Variable de entorno NO está configurada** en Railway/tu servidor

```
┌─────────────────────────────────┐
│  Backend (Local - FUNCIONA)      │
│  ✅ Tiene archivos locales       │
│  ✅ FIREBASE_SERVICE_ACCOUNT_... │
│     (pero no es env var)         │
└──────────────┬──────────────────┘
               │ Deploy
               ↓
┌─────────────────────────────────┐
│  Backend (Production - FALLA)    │
│  ❌ No tiene archivos en /app    │
│  ❌ FIREBASE_SERVICE_ACCOUNT...  │
│     no está en env vars          │
└─────────────────────────────────┘
```

## Solución: 3 Pasos

### Paso 1: Prepara el Archivo JSON

**Opción A: Script Automático (RECOMENDADO)**

```powershell
cd a:\programing_workspace\api-artefacto-360-dagma
.\setup_calendar_railway.ps1
```

**Opción B: Manual**

```powershell
$json = Get-Content "dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json" -Raw
$json | Set-Clipboard
# ✅ Contenido copiado al portapapeles
```

### Paso 2: Configura en Railway

1. Abre [Railway Dashboard](https://railway.app/project)
2. Selecciona tu proyecto
3. Dropdown → Selecciona **Environment**
4. Click en **Variables** o **Environment Variables**
5. Haz click en **+ Add Variable** o **+ New Variable**
6. Llena los campos:
   ```
   Name: FIREBASE_SERVICE_ACCOUNT_JSON
   Value: [Pega Ctrl+V - el JSON está en portapapeles]
   ```
7. Click **Save** o **Save Variables**
8. **NO hagas commit** - la app se redepliegue automática

### Paso 3: Verifica que Funciona

**A. Revisa Logs en Railway**

1. Click en **Logs**
2. Busca el mensaje:
   ```
   ✅ Firebase inicializado exitosamente con FIREBASE_SERVICE_ACCOUNT_JSON
   ```

**B. Prueba del Endpoint**

```bash
# Envía petición POST a:
/convocar_actividad

# Con payload como siempre
{
  "fecha_actividad": "28/02/2026",
  "hora_encuentro": "14:34",
  ...
}

# Respuesta esperada:
{
  "success": true,
  "calendar_event_id": "xxxxx",
  "calendar_event_link": "https://www.google.com/calendar/event?eid=..."
}
```

**C. Verifica en Firebase**
El documento guardado debe tener:

```
✅ calendar_event_id: "xxxxx"
✅ calendar_event_link: "https://www.google.com/calendar/..."
❌ calendar_event_error: NO DEBE APARECER
```

## ¿Qué Cambió en el Backend?

**Archivo:** `app/routes/artefacto_360_routes.py` (línea 966-1015)

Antes (FALLA en production):

```python
SERVICE_ACCOUNT_FILE = os.path.join(
    os.path.dirname(...),
    'dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json'  # Ruta hardcodeada
)
```

Ahora (FUNCIONA en producción):

```python
# Intenta en este orden:
1. FIREBASE_SERVICE_ACCOUNT_JSON (env var) ← **CONFIGURA ESTO EN RAILWAY**
2. GOOGLE_APPLICATION_CREDENTIALS (path env var)
3. Archivos locales (desarrollo)
```

## Troubleshooting

### "Calendar API error: Could not deserialize..."

→ JSON inválido en la variable de entorno
→ Verifica que no tenga saltos de línea extra
→ Usa el script `setup_calendar_railway.ps1`

### "Errno 2: No such file"

→ Variable `FIREBASE_SERVICE_ACCOUNT_JSON` no existe
→ Sigue Paso 2 arriba

### "Railway keeps deploying but no change"

→ Haz hardrefresh: `Ctrl+Shift+Delete` en Railway
→ O espera 2-3 minutos

### El JSON se ve truncado en Railway UI

→ Normal, Railway oculta valores largos por seguridad
→ Pero está completo guardado internamente

## ✅ Checklist Final

- [ ] Ejecuté `setup_calendar_railway.ps1` O copié el JSON manual
- [ ] Config en Railway: `FIREBASE_SERVICE_ACCOUNT_JSON` = JSON completo
- [ ] App se redesplegó (verifica Railway status)
- [ ] Logs muestran: "✅ Firebase inicializado exitosamente"
- [ ] Prueba POST /convocar_actividad da respuesta con `calendar_event_id`
- [ ] Firebase documento tiene `calendar_event_link` (NO `calendar_event_error`)

## 📞 Si Aún No Funciona

1. Ejecuta localmente: `python verify_calendar_config.py`
2. Verifica logs en Railway (Dashboard → Logs)
3. Revisa que la variable esté exactamente así:
   ```
   Name (exacto): FIREBASE_SERVICE_ACCOUNT_JSON
   Value (válido): JSON completo sin espacios extras
   ```

---

**Estado:** 🔴 Se requiere configuración en Railway  
**Backend:** ✅ Actualizado  
**Falta:** 🔑 Variable de entorno en Railway
