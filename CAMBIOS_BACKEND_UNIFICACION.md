# CAMBIOS BACKEND — Unificación de colecciones `reportes_intervenciones`

**Fecha:** Abril 2026  
**Branch:** `master`  
**Archivo principal modificado:** `app/routes/artefacto_360_routes.py`

---

## 1. Contexto y problema

El backend tenía **5 colecciones Firestore separadas** para los reportes de intervención, una por grupo operativo:

| Grupo | Colección anterior |
|---|---|
| Cuadrilla | `reportes_cuadrilla` |
| Vivero | `reportes_vivero` |
| Gobernanza | `reportes_gobernanza` |
| Ecosistemas | `reportes_ecosistemas` |
| UMATA | `reportes_umata` |

Esto implicaba:
- Índices duplicados en Firestore × 5
- Lógica de handler duplicada con ligeras variaciones
- Imposibilidad de hacer consultas cruzadas por actividad
- Migración de colección difícil al agregar nuevos grupos

---

## 2. Solución implementada

### Colección unificada

Todos los grupos ahora escriben y leen de una única colección:

```
reportes_intervenciones
```

El campo **`grupo`** actúa como discriminador. Su valor es siempre el `grupo_key` canónico en minúsculas proveniente de la URL (p.ej. `"cuadrilla"`, `"vivero"`), **no** el valor que el frontend pueda enviar en el form body.

### Constante global

```python
COLLECTION_REPORTES_INTERVENCIONES = "reportes_intervenciones"
```

Usada en todos los accesos a Firestore. Elimina magic strings.

---

## 3. Cambios en `GRUPOS_CONFIG`

**Antes** — cada grupo declaraba su propia colección:
```python
GRUPOS_CONFIG = {
    "cuadrilla": {
        "display_name": "Cuadrilla",
        "s3_prefix": "cuadrilla",
        "collection": "reportes_cuadrilla",    # ← eliminado
    },
    ...
}
```

**Después** — sin clave `collection`:
```python
GRUPOS_CONFIG = {
    "cuadrilla": {"display_name": "Cuadrilla", "s3_prefix": "cuadrilla"},
    "vivero":    {"display_name": "Vivero",    "s3_prefix": "vivero"},
    "gobernanza":{"display_name": "Gobernanza","s3_prefix": "gobernanza"},
    "ecosistemas":{"display_name": "Ecosistemas","s3_prefix":"ecosistemas"},
    "umata":     {"display_name": "UMATA",     "s3_prefix": "umata"},
}
```

---

## 4. Cambios en `_post_reporte_intervencion`

Handler unificado para todos los grupos. Cambios clave:

### 4a. Colección de escritura
```python
# Antes (por grupo)
db.collection(config["collection"]).document(reporte_id).set(reporte_data)

# Después (unificada)
db.collection(COLLECTION_REPORTES_INTERVENCIONES).document(reporte_id).set(reporte_data)
```

### 4b. Campo `grupo` canónico
```python
reporte_data = {
    ...
    "grupo": grupo_key,  # siempre desde la URL, nunca del form body
    ...
}
```

### 4c. Parámetros con `= None` por defecto
Todos los parámetros del handler tienen `= None` para que las rutas legacy (que no pasan `registrado_por`, `current_user`, etc.) no fallen con `TypeError: missing required argument`.

```python
async def _post_reporte_intervencion(
    grupo_key: str,
    tipo_intervencion: Optional[str],
    ...
    registrado_por: Optional[str] = None,      # ← antes sin default
    current_user: Optional[CurrentUser] = None, # ← antes sin default
    ...
)
```

### 4d. Bug fix: `upload_photos_to_s3` no retornaba el resultado

```python
# Antes — la función terminaba sin return (retornaba None implícitamente)
tasks = [upload_single_photo(i, photo) for i, photo in enumerate(photos)]
documentos = await asyncio.gather(*tasks)
# Fin función   ← bug: no hay return

# Después
tasks = [upload_single_photo(i, photo) for i, photo in enumerate(photos)]
documentos = await asyncio.gather(*tasks)
return list(documentos)   # ← fix
```

Esto causaba `TypeError: object of type 'NoneType' has no len()` en cualquier POST con fotos adjuntas.

---

## 5. Cambios en `_get_reportes_intervenciones`

### 5a. Colección de lectura
```python
# Antes
query = db.collection(config["collection"])

# Después
query = db.collection(COLLECTION_REPORTES_INTERVENCIONES)
query = query.where("grupo", "==", grupo_key)  # siempre filtrar por grupo
```

### 5b. Aislamiento por grupo al buscar por ID
```python
# Al buscar por ?id=..., verificar que el doc pertenece al grupo del URL
doc_data = doc_ref.get().to_dict()
if doc_data.get("grupo") != grupo_key:
    return {"success": True, "total": 0, "data": [], ...}
```

Esto previene que un cliente autenticado del grupo `vivero` pueda obtener documentos de `cuadrilla` si conoce el ID.

---

## 6. Nuevos endpoints (requieren autenticación)

```
POST /grupos/{grupo_key}/reporte_intervencion
GET  /grupos/{grupo_key}/reportes_intervenciones
```

`grupo_key` acepta: `cuadrilla`, `vivero`, `gobernanza`, `ecosistemas`, `umata`.

Requieren header `Authorization: Bearer <firebase_id_token>`.

Roles permitidos: `administrador`, `lider`, `operador` (los dos últimos solo pueden operar en su propio grupo).

---

## 7. Rutas legacy (sin cambios de contrato)

Las siguientes rutas siguen funcionando **sin autenticación** y sin cambios en su contrato de request/response:

```
POST /grupo-cuadrilla/reporte_intervencion
POST /grupo-vivero/reporte_intervencion
POST /grupo-gobernanza/reporte_intervencion
POST /grupo-ecosistemas/reporte_intervencion
POST /grupo-umata/reporte_intervencion

GET  /grupo-cuadrilla/reportes_intervenciones
GET  /grupo-vivero/reportes_intervenciones
GET  /grupo-gobernanza/reportes_intervenciones
GET  /grupo-ecosistemas/reportes_intervenciones
GET  /grupo-umata/reportes_intervenciones
```

Internamente delegan al mismo handler unificado. Están marcadas con `include_in_schema=False` en OpenAPI.

---

## 8. Nuevos índices Firestore (`firestore.indexes.json`)

Se añadieron 3 índices compuestos para la colección `reportes_intervenciones`:

```json
{
  "collectionGroup": "reportes_intervenciones",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "grupo",     "order": "ASCENDING" },
    { "fieldPath": "timestamp", "order": "DESCENDING" }
  ]
},
{
  "collectionGroup": "reportes_intervenciones",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "grupo",       "order": "ASCENDING" },
    { "fieldPath": "id_actividad","order": "ASCENDING" }
  ]
},
{
  "collectionGroup": "reportes_intervenciones",
  "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "grupo","order": "ASCENDING" },
    { "fieldPath": "id",   "order": "ASCENDING" }
  ]
}
```

Para desplegar los índices:
```bash
firebase deploy --only firestore:indexes
```

---

## 9. Migración de datos existentes

Los documentos en las colecciones anteriores (`reportes_cuadrilla`, `reportes_vivero`, etc.) **no se migran automáticamente**. Para migrar:

1. Leer todos los docs de cada colección legacy.
2. Añadir el campo `"grupo": "<nombre>"` si no existe.
3. Escribir cada doc en `reportes_intervenciones` con el mismo ID.
4. (Opcional) Eliminar las colecciones legacy una vez validada la migración.

---

## 10. Resumen de archivos modificados

| Archivo | Cambio |
|---|---|
| `app/routes/artefacto_360_routes.py` | Constante global, GRUPOS_CONFIG sin `collection`, handlers unificados, fix `return list(documentos)` |
| `firestore.indexes.json` | +3 índices compuestos para `reportes_intervenciones` |
| `test_unified_endpoints.py` | Reescrito con mocks de auth correctos (62 tests → 62 passing) |
| `test_registros_reales_coleccion_unificada.py` | Script nuevo de integración real contra Firestore |
