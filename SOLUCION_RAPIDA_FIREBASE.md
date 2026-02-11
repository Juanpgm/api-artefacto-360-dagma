# 🚨 SOLUCIÓN RÁPIDA - Firebase Error en Railway

## ❌ Error que estás viendo:
```
File "/app/.venv/lib/python3.12/site-packages/google/oauth2/service_account.py", 
line 457, in _perform_refresh_token
```

## ✅ Solución en 5 Minutos

### 1️⃣ Descarga Credenciales (2 min)
1. Ve a https://console.firebase.google.com/
2. Selecciona proyecto: **dagma-85aad**
3. **⚙️ Configuración** > **Cuentas de servicio**
4. **Generar nueva clave privada**
5. Descarga el archivo JSON

### 2️⃣ Prepara el JSON (1 min)
```powershell
# En tu terminal local
.\prepare_firebase_json.ps1 -InputFile "dagma-85aad-firebase-xxxxx.json"

# El script:
# - Valida el JSON
# - Lo comprime a una línea
# - Lo copia al portapapeles
```

### 3️⃣ Configura Railway (1 min)
1. Ve a https://railway.app
2. Tu proyecto > **Variables**
3. **+ New Variable**
4. Nombre: `FIREBASE_SERVICE_ACCOUNT_JSON`
5. Valor: **Ctrl+V** (pegar)
6. **Save**

### 4️⃣ Re-despliega (1 min)
1. Click en **Deploy** > **Redeploy**
2. Espera ~2-3 minutos

### 5️⃣ Verifica (30 seg)
Ve a **Deployments** > **View Logs**

Busca:
```
✅ Firebase inicializado exitosamente con SERVICE_ACCOUNT_JSON
```

---

## 🎯 Comandos Rápidos

```powershell
# 1. Preparar JSON
.\prepare_firebase_json.ps1 -InputFile "tu-archivo.json"

# 2. Verificar localmente antes de subir
python verify_config.py

# 3. Probar localmente
uvicorn app.main:app --reload
```

---

## ⚠️ Errores Comunes

| Error | Causa | Solución |
|-------|-------|----------|
| `Invalid JSON` | JSON tiene saltos de línea | Usa `prepare_firebase_json.ps1` |
| `private_key format` | `\n` no preservados | Verifica que `\n` estén en el JSON |
| `Variable not found` | Nombre incorrecto | Usa exactamente: `FIREBASE_SERVICE_ACCOUNT_JSON` |
| `Permission denied` | Cuenta sin permisos | Verifica roles en Firebase Console |

---

## 📋 Checklist

- [ ] JSON descargado desde Firebase Console
- [ ] JSON procesado con `prepare_firebase_json.ps1`
- [ ] Variable `FIREBASE_SERVICE_ACCOUNT_JSON` en Railway
- [ ] Railway re-desplegado
- [ ] Logs muestran "✅ Firebase inicializado"

---

## 📚 Documentación Completa

Si necesitas más detalles: **[SOLUCION_FIREBASE_RAILWAY.md](SOLUCION_FIREBASE_RAILWAY.md)**

---

## 🆘 ¿Aún no funciona?

1. **Copia el JSON** que pusiste en Railway (primeros 100 chars)
2. **Copia los logs** completos del error
3. **Ejecuta localmente**: `python verify_config.py`
4. Reporta los resultados

---

**⏱️ Tiempo total estimado: 5 minutos**
