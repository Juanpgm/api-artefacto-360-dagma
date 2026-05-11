"""
Rutas de Firebase - Status y gestión de colecciones
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
from app.firebase_config import db
from app.utils.firestore_async import run_blocking

router = APIRouter(tags=["Firebase"])

@router.get("/firebase/status")
async def firebase_status():
    """
    Verificar estado de la conexión con Firebase
    """
    try:
        # Verificar conexión intentando acceder a Firestore
        collection_names = await run_blocking(lambda: [col.id for col in db.collections()])
        return {
            "status": "connected",
            "firestore": "available",
            "storage": "available",  # TODO: Verificar storage si es necesario
            "project_id": "dagma-85aad",
            "collections_count": len(collection_names),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Firebase no disponible: {str(e)}")

@router.get("/firebase/collections")
async def get_firebase_collections():
    """
    Obtener información completa de todas las colecciones de Firestore
    """
    try:
        collections = await run_blocking(lambda: list(db.collections()))
        collections_info = []
        for col in collections:
            doc_count = await run_blocking(lambda c=col: sum(1 for _ in c.stream()))
            collections_info.append({
                "name": col.id,
                "document_count": doc_count,
                "size_bytes": 0  # TODO: Calcular tamaño si es necesario
            })
        return {
            "success": True,
            "collections": collections_info,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo colecciones: {str(e)}")

@router.get("/firebase/collections/summary")
async def get_firebase_collections_summary():
    """
    Obtener resumen estadístico de las colecciones
    """
    try:
        collections = await run_blocking(lambda: list(db.collections()))
        total_collections = 0
        total_documents = 0
        for col in collections:
            total_collections += 1
            total_documents += await run_blocking(lambda c=col: sum(1 for _ in c.stream()))
        return {
            "success": True,
            "total_collections": total_collections,
            "total_documents": total_documents,
            "total_size_mb": 0.0,  # TODO: Calcular tamaño total
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo resumen: {str(e)}")
