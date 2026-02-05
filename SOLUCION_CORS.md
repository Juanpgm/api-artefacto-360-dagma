# 🔧 Guía de Solución: Problema de CORS

## 🎯 Problema Identificado

El API funciona correctamente pero tiene **problemas de CORS** que impiden que el frontend (localhost:5174) pueda hacer peticiones.

### 📊 Estado Actual:

```
✅ API Endpoint (GET): FUNCIONANDO
   └─ URL: https://web-production-2d737.up.railway.app/init/parques
   └─ Status: 200
   └─ Data: 25 parques

❌ CORS Preflight (OPTIONS): FALLANDO
   └─ Access-Control-Allow-Origin: No configurado
   └─ Bloqueando peticiones desde localhost:5174
```

---

## ✅ Solución: 3 Pasos

### **PASO 1: Backend - Actualizar CORS** ✅ **(YA HECHO)**

El archivo `app/main.py` ya fue actualizado con:

```python
allow_origins=[
    # Desarrollo local
    "http://localhost:3000",      # React default
    "http://localhost:3001",      # React alternate
    "http://localhost:5173",      # Vite default
    "http://localhost:5174",      # Vite alternate ← NUEVO
    "http://localhost:5175",      # Vite alternate
    # Producción
    "https://web-production-2d737.up.railway.app",  # Railway API
    "https://dagma-360-capture-frontend.vercel.app",  # Frontend Vercel ← NUEVO
    "https://tu-dominio-produccion.com"
]
```

### **PASO 2: Desplegar en Railway** 🚀

```bash
# 1. Commit de los cambios
git add app/main.py
git commit -m "fix: Add localhost:5174 to CORS allowed origins for Vite development"
git push origin master

# 2. Railway detectará el push y desplegará automáticamente
# Espera 2-3 minutos para el despliegue
```

### **PASO 3: Configurar Frontend** 🌐

Tienes **2 opciones**:

---

## 🎨 **OPCIÓN A: Usar Proxy de Vite** (Recomendado para desarrollo)

### 1. Copiar configuración de Vite:

```bash
cd a:\programing_workspace\artefacto-360-dagma\frontend
copy a:\programing_workspace\api-artefacto-360-dagma\vite.config.example.js vite.config.js
```

### 2. Actualizar tu código para usar el proxy:

**Antes:**

```javascript
const API_URL = "https://web-production-2d737.up.railway.app";
fetch(`${API_URL}/init/parques`);
```

**Después:**

```javascript
// En desarrollo, usa /api que será proxy a Railway
const API_URL = import.meta.env.DEV
  ? "/api"
  : "https://web-production-2d737.up.railway.app";
fetch(`${API_URL}/init/parques`);
```

### 3. Reiniciar servidor:

```bash
# Detén el servidor (Ctrl+C)
npm run dev
```

### ✅ Ventajas del Proxy:

- ✅ No hay problemas de CORS en desarrollo
- ✅ Las peticiones pasan por localhost
- ✅ Mejor para debugging

---

## 🌍 **OPCIÓN B: Esperar despliegue de Railway** (Más simple)

### 1. Hacer push de los cambios:

```bash
git push origin master
```

### 2. Verificar despliegue:

```bash
# Espera 2-3 minutos, luego prueba:
python test_api_connection.py
```

Debe mostrar:

```
✅ CORS Preflight (OPTIONS): PASS
✅ Access-Control-Allow-Origin: http://localhost:5174
```

### 3. Refrescar frontend:

```bash
# Limpiar caché del navegador
Ctrl + Shift + Del

# O abrir en ventana de incógnito
Ctrl + Shift + N
```

---

## 🧪 Verificar que funciona

### Test desde Python:

```bash
python test_api_connection.py
```

Espera ver:

```
✅ API Endpoint (GET): PASS
✅ CORS Preflight (OPTIONS): PASS
🎉 TODO FUNCIONANDO CORRECTAMENTE
```

### Test desde Frontend:

Abre DevTools (F12) en el navegador y ejecuta:

```javascript
fetch("https://web-production-2d737.up.railway.app/init/parques")
  .then((r) => r.json())
  .then((data) => console.log("✅ Parques:", data.count))
  .catch((e) => console.error("❌ Error:", e));
```

---

## 📝 Archivo vite.config.js (Para Opción A)

Ya creé el archivo `vite.config.example.js` con esta configuración:

```javascript
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "https://web-production-2d737.up.railway.app",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        secure: false,
      },
    },
  },
});
```

**Para usarlo:**

```bash
cd a:\programing_workspace\artefacto-360-dagma\frontend
copy ..\api-artefacto-360-dagma\vite.config.example.js vite.config.js
```

---

## 🔍 Troubleshooting

### Si después del despliegue sigue sin funcionar:

```bash
# 1. Verificar que Railway desplegó correctamente
# Ve a: https://railway.app/dashboard

# 2. Ver logs de Railway:
# Railway Dashboard > tu proyecto > Logs

# 3. Probar específicamente CORS:
python test_api_connection.py

# 4. Limpiar caché del navegador completamente
# Chrome: Configuración > Privacidad > Borrar datos de navegación
```

### Si el proxy no funciona:

```bash
# 1. Verifica que vite.config.js existe
ls vite.config.js

# 2. Verifica que no haya errores de sintaxis
npm run dev

# 3. Prueba con la URL completa temporalmente
```

---

## 📁 Archivos Modificados

1. ✅ `app/main.py` - CORS actualizado
2. ✅ `vite.config.example.js` - Configuración de proxy creada
3. ✅ `test_api_connection.py` - Script de diagnóstico creado

---

## 🚀 Comandos Rápidos

```bash
# Backend: Desplegar cambios
cd a:\programing_workspace\api-artefacto-360-dagma
git add app/main.py
git commit -m "fix: Add CORS support for Vite dev server (localhost:5174)"
git push origin master

# Frontend: Opción A - Con Proxy
cd a:\programing_workspace\artefacto-360-dagma\frontend
copy ..\api-artefacto-360-dagma\vite.config.example.js vite.config.js
npm run dev

# Verificar
cd ..\api-artefacto-360-dagma
python test_api_connection.py
```

---

**Tiempo estimado:** 5-10 minutos (incluyendo despliegue de Railway)

**¿Cuál opción prefieres?**
- 🎨 Opción A: Configurar proxy local (más rápido, sin esperar despliegue)
- 🌍 Opción B: Esperar despliegue en Railway (más simple)

---

## 🚀 Producción en Vercel

### 📍 URL de Frontend en Producción:
```
https://dagma-360-capture-frontend.vercel.app
```

### ✅ Configuración de CORS para Producción

El backend ya está configurado para aceptar peticiones desde Vercel:

```python
allow_origins=[
    # Desarrollo
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    # Producción
    "https://dagma-360-capture-frontend.vercel.app",  # ✅ Frontend Vercel
    "https://web-production-2d737.up.railway.app"     # ✅ Railway API
]
```

### 🧪 Verificar CORS en Producción

```bash
# Test desde Python con origin de Vercel
python -c "
import requests
url = 'https://web-production-2d737.up.railway.app/init/parques'
headers = {'Origin': 'https://dagma-360-capture-frontend.vercel.app'}
r = requests.options(url, headers=headers)
print('Status:', r.status_code)
print('CORS Origin:', r.headers.get('Access-Control-Allow-Origin', 'No configurado'))
"
```

Debe mostrar:
```
Status: 200
CORS Origin: https://dagma-360-capture-frontend.vercel.app
```

### 🌐 Verificar en el Frontend de Producción

1. **Abre tu app en Vercel:**
   ```
   https://dagma-360-capture-frontend.vercel.app
   ```

2. **Abre DevTools (F12) > Consola**

3. **Ejecuta este test:**
   ```javascript
   fetch('https://web-production-2d737.up.railway.app/init/parques')
     .then(r => r.json())
     .then(data => {
       console.log('✅ Parques cargados:', data.count);
       console.log('Primer parque:', data.data[0]);
     })
     .catch(e => console.error('❌ Error CORS:', e));
   ```

### 📊 Troubleshooting Producción

#### Si no se ven los parques en producción:

1. **Verificar variable de entorno en Vercel:**
   ```bash
   # En Vercel Dashboard > tu proyecto > Settings > Environment Variables
   # Debe tener:
   VITE_API_URL=https://web-production-2d737.up.railway.app
   ```

2. **Verificar CORS desde consola del navegador:**
   ```javascript
   // En https://dagma-360-capture-frontend.vercel.app
   console.log('API URL:', import.meta.env.VITE_API_URL);
   ```

3. **Ver errores de red en DevTools:**
   - F12 > Network
   - Filtrar por "parques"
   - Verificar status code y headers

4. **Si hay error 503 o 504:**
   - La API en Railway puede estar dormida (cold start)
   - Espera 10-15 segundos y recarga la página
   - Verifica logs de Railway

#### Logs de Railway:

```bash
# Accede a:
https://railway.app/dashboard
# > Tu proyecto > Logs

# Busca:
- "Firebase inicializado correctamente"
- Errores de CORS
- Errores de conexión a Firestore
```

### 🔄 Redesplegar si es necesario:

```bash
# Si cambias algo en el backend
git add app/main.py
git commit -m "fix: Add Vercel frontend to CORS allowed origins"
git push origin master

# Railway desplegará automáticamente en 2-3 minutos
```

### ✅ Checklist de Producción:

- [ ] CORS incluye `https://dagma-360-capture-frontend.vercel.app`
- [ ] Variable `VITE_API_URL` configurada en Vercel
- [ ] API responde en Railway (no dormida)
- [ ] Firebase conectado correctamente
- [ ] No hay errores en DevTools del navegador
- [ ] Los 25 parques se cargan en el mapa

---

## 🎯 Resumen de URLs

| Entorno | Frontend | Backend |
|---------|----------|---------|
| **Desarrollo** | `http://localhost:5174` | `/api` (proxy) o Railway |
| **Producción** | `https://dagma-360-capture-frontend.vercel.app` | `https://web-production-2d737.up.railway.app` |

---

## 📞 Comandos de Verificación Rápida

```bash
# Desarrollo
python test_api_connection.py

# Producción - Test CORS desde terminal
curl -I -X OPTIONS \
  -H "Origin: https://dagma-360-capture-frontend.vercel.app" \
  -H "Access-Control-Request-Method: GET" \
  https://web-production-2d737.up.railway.app/init/parques
```



- 🎨 Opción A: Configurar proxy local (más rápido, sin esperar despliegue)
- 🌍 Opción B: Esperar despliegue en Railway (más simple)
