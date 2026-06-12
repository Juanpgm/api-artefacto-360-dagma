"""
API Artefacto 360 DAGMA - Main Application
Configuración basada en gestor_proyecto_api
"""
import logging
import os
import time
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.limiter import limiter

# Configurar logging de auditoría
import sys
_log_stream = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1, closefd=False)
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('audit.log', encoding='utf-8'),
        logging.StreamHandler(stream=_log_stream),
    ]
)
logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("dagma.perf")
_SLOW_REQUEST_MS = float(os.getenv("SLOW_REQUEST_MS", "1000"))

# Importar configuración de Firebase
from app.firebase_config import db, auth_client

# Importar routers
from app.routes import (
    default_routes,
    general_routes,
    monitoring_routes,
    firebase_routes,
    artefacto_360_routes,
    auth_routes,
    seguimiento_routes,
    notifications_routes
)

# Crear aplicación FastAPI
app = FastAPI(
    title="API Artefacto 360 DAGMA",
    description="API para gestión de artefacto de captura 360 con Firebase/Firestore - Soporte completo UTF-8 🇪🇸",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Wire the shared limiter into the app state so SlowAPIMiddleware can find it
# and @limiter.limit() decorators in routers work correctly.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Middleware stack — Starlette processes add_middleware in LIFO order,
# so register from innermost to outermost:
#   1. Timing (innermost — closest to the handler)
#   2. SlowAPI rate limiting
#   3. GZip compression
#   4. CORS (outermost — must wrap everything, including error responses)
# ---------------------------------------------------------------------------

class _TimingMiddleware(BaseHTTPMiddleware):
    """Adds X-Process-Time response header and logs slow requests."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        response.headers["X-Process-Time"] = f"{elapsed_ms:.1f}"
        if elapsed_ms >= _SLOW_REQUEST_MS:
            _perf_logger.warning(
                "slow_request method=%s path=%s status=%s elapsed_ms=%.1f",
                request.method, request.url.path, response.status_code, elapsed_ms,
            )
        return response


app.add_middleware(_TimingMiddleware)           # innermost
app.add_middleware(SlowAPIMiddleware)           # rate limiting
app.add_middleware(GZipMiddleware, minimum_size=1000)  # compression
app.add_middleware(                             # outermost — CORS must be last
    CORSMiddleware,
    allow_origins=[
        # Desarrollo local
        "http://localhost:3000",      # React default
        "http://localhost:3001",      # React alternate
        "http://localhost:5173",      # Vite default
        "http://localhost:5174",      # Vite alternate
        "http://localhost:5175",      # Vite alternate
        # Producción
        "https://web-production-2d737.up.railway.app",  # Railway API
        "https://artefacto-calitrack-360-frontend-pr.vercel.app",  # Frontend Vercel
        "https://dagma-360-capture-frontend.vercel.app",  # Frontend Vercel (legacy)
        "https://tu-dominio-produccion.com",   # Dominio custom adicional
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time", "X-Request-Id"],
)

# Registrar routers
app.include_router(default_routes.router)
app.include_router(general_routes.router)
app.include_router(monitoring_routes.router)
app.include_router(firebase_routes.router)
app.include_router(artefacto_360_routes.router)
app.include_router(auth_routes.router)
app.include_router(seguimiento_routes.router)
app.include_router(notifications_routes.router)

# Manejador de errores global
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": str(exc),
            "type": type(exc).__name__
        }
    )




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
