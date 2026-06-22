# API Artefacto 360 DAGMA

API para gestión de artefacto de captura DAGMA (Departamento Administrativo de Gestión del Medio Ambiente) basada en la arquitectura de gestor_proyecto_api.

## 🚀 Características

- **FastAPI**: Framework moderno y de alto rendimiento
- **Firebase/Firestore**: Base de datos en tiempo real
- **Amazon S3**: Almacenamiento de fotos de reconocimientos
- **Captura GPS**: Registro de coordenadas geoespaciales
- **Soporte UTF-8**: Caracteres especiales en español
- **Monitoreo**: Métricas de Prometheus
- **Documentación**: Swagger UI automática

## 📋 Endpoints Principales

### Default

- `GET /` - Endpoint raíz con información básica

### General

- `GET /ping` - Health check simple
- `GET /health` - Health check completo
- `GET /cors-test` - Prueba de CORS
- `GET /test/utf8` - Prueba de caracteres UTF-8
- `GET /debug/railway` - Debug para Railway
- `GET /centros-gestores/nombres-unicos` - Listado de centros gestores

### Monitoring

- `GET /metrics` - Métricas de Prometheus

### Firebase

- `GET /firebase/status` - Estado de conexión Firebase
- `GET /firebase/collections` - Información de colecciones
- `GET /firebase/collections/summary` - Resumen de colecciones

### Artefacto de Captura DAGMA

- `GET /init/parques` - Inicialización de parques para DAGMA
- `POST /grupo-operativo/reconocimiento` - Registrar reconocimiento del grupo operativo
- `GET /grupo-operativo/reportes` - Obtener reportes del grupo operativo
- `GET /lideres_grupo` - Obtener líderes de grupo (todos o filtrado por `grupo`)
- `DELETE /grupo-operativo/eliminar-reporte` - Eliminar reporte del grupo operativo

### Administración y Control de Accesos

- `POST /auth/validate-session` - Validar sesión
- `POST /auth/login` - Login de usuario
- `POST /auth/register` - Registro de usuario (acepta `grupo` y `rol` como texto)
- `POST /auth/change-password` - Cambiar contraseña
- `POST /auth/google` - Autenticación con Google
- `DELETE /auth/user/{uid}` - Eliminar usuario
- `GET /admin/users` - Listar usuarios desde Firestore (con filtros por query params)
- `GET /auth/config` - Configuración de Firebase

## 🛠️ Instalación

1. Clonar el repositorio
2. Crear entorno virtual:

   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   ```

3. Instalar dependencias:

   ```bash
   pip install -r requirements.txt
   ```

4. Configurar variables de entorno:

   ```bash
   cp .env.example .env
   # Editar .env con tus credenciales
   ```

5. Ejecutar la aplicación:
   ```bash
   uvicorn app.main:app --reload
   ```

## 📚 Documentación

Una vez iniciada la aplicación, accede a:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## 🔧 Configuración de Firebase

1. Crear proyecto en Firebase Console
2. Descargar archivo de credenciales (serviceAccountKey.json)
3. Configurar ruta en `.env`:
   ```
   FIREBASE_CREDENTIALS_PATH=path/to/serviceAccountKey.json
   ```

## ☁️ Configuración de AWS S3 (para fotos)

1. Crear bucket S3 llamado `360-dagma-photos`
2. Configurar credenciales AWS en `.env`:
   ```
   AWS_ACCESS_KEY_ID=tu_access_key
   AWS_SECRET_ACCESS_KEY=tu_secret_key
   AWS_REGION=us-east-1
   S3_BUCKET_NAME=360-dagma-photos
   ```

## 🔐 Autenticación en desarrollo local (Firebase Auth Emulator)

La API **siempre** verifica la firma de los Firebase ID tokens. No existe ningún
modo que acepte tokens sin verificar (el antiguo "fallback" inseguro fue
eliminado por motivos de seguridad).

Si tu entorno local no tiene salida al endpoint de certificados de Google
(puerto 443 bloqueado), usá el **Firebase Auth Emulator** en lugar de saltarte la
verificación:

1. Instalá las Firebase CLI tools y arrancá el emulador de Auth:
   ```bash
   npm install -g firebase-tools
   firebase emulators:start --only auth
   ```
   Por defecto queda escuchando en `localhost:9099`.

2. Exportá la variable de entorno antes de levantar la API (el Admin SDK la
   detecta automáticamente y valida los tokens emitidos por el emulador):
   ```bash
   export FIREBASE_AUTH_EMULATOR_HOST=localhost:9099   # PowerShell: $env:FIREBASE_AUTH_EMULATOR_HOST="localhost:9099"
   uvicorn app.main:app --reload
   ```

3. En el frontend, apuntá el SDK de Firebase al emulador con
   `connectAuthEmulator(auth, "http://localhost:9099")` (bajo un flag de entorno).

Con esto los tokens locales se validan de verdad contra el emulador, sin aceptar
firmas sin verificar. En Railway/producción no se define
`FIREBASE_AUTH_EMULATOR_HOST` y la verificación se hace contra Google.

## 📝 Próximos Pasos (TODO)

- [ ] Implementar conexión con Firebase (colección: reconocimientos_dagma)
- [ ] Implementar subida de fotos a Amazon S3
- [ ] Implementar eliminación de fotos en S3
- [ ] Implementar consulta de parques desde Firebase
- [ ] Implementar autenticación completa
- [ ] Implementar validaciones de datos geoespaciales
- [ ] Agregar tests unitarios para endpoints DAGMA
- [ ] Configurar CI/CD
- [ ] Deploy en Railway o AWS

## 📄 Licencia

Apache License 2.0 (basado en gestor_proyecto_api)
