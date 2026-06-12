"""
Rutas generales - Health checks y endpoints de utilidad
"""
from fastapi import APIRouter
from datetime import datetime, timezone
import platform
import os

router = APIRouter(tags=["General"])

@router.get("/ping")
async def ping():
    """
    🔵 GET | ❤️ Health Check | Health check super simple para Railway con soporte UTF-8
    """
    return {
        "status": "ok",
        "message": "¡Pong! 🏓",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "utf8_test": "Funciona correctamente con caracteres especiales: á é í ó ú ñ"
    }

@router.get("/cors-test")
async def cors_test():
    """
    Endpoint específico para probar configuración CORS
    """
    return {
        "cors": "enabled",
        "message": "CORS configurado correctamente",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@router.options("/cors-test")
async def cors_test_options():
    """
    OPTIONS handler específico para CORS test
    """
    return {"message": "OPTIONS request successful"}

@router.get("/test/utf8")
async def test_utf8():
    """
    Endpoint de prueba específico para caracteres UTF-8 en español
    """
    return {
        "test": "UTF-8",
        "español": "Caracteres especiales: á é í ó ú ñ Ñ",
        "symbols": "© ® ™ € £ ¥",
        "message": "Todos los caracteres UTF-8 funcionan correctamente ✓"
    }

@router.get("/debug/railway")
async def railway_debug():
    """
    Debug específico para Railway - Diagnóstico simplificado
    """
    return {
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "environment": os.environ.get("RAILWAY_ENVIRONMENT", "local"),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@router.get("/health")
async def health_check():
    """
    🔵 GET | ❤️ Health Check | Verificar estado de salud de la API
    
    Endpoint completo de health check con información del sistema
    """
    from app.firebase_config import db
    from app.utils.firestore_async import run_blocking
    from unittest.mock import Mock
    
    # 1. Verificar base de datos (Firestore)
    db_status = "ok"
    if not isinstance(db, Mock):
        try:
            # Intentar listar colecciones de forma no bloqueante
            await run_blocking(lambda: list(db.collections()))
        except Exception as e:
            db_status = f"error: {str(e)}"
            
    # 2. Verificar almacenamiento (AWS S3)
    s3_status = "ok"
    aws_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    bucket = os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_S3_BUCKET_NAME") or "360-dagma-photos"
    
    if not aws_key or not aws_secret:
        s3_status = "unconfigured"
    else:
        try:
            import boto3
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
                region_name=os.getenv("AWS_REGION", "us-east-1"),
            )
            # Intentar listado mínimo de objetos para probar conexión
            await run_blocking(lambda: s3_client.list_objects_v2(Bucket=bucket, MaxKeys=1))
        except Exception as e:
            s3_status = f"error: {str(e)}"
            
    is_healthy = db_status == "ok" and s3_status in ("ok", "unconfigured")
    
    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "API Artefacto 360 DAGMA",
        "version": "1.0.0",
        "uptime": "OK",
        "checks": {
            "api": "ok",
            "database": db_status,
            "storage": s3_status
        }
    }
