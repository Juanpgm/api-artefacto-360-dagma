# Reverse Engineering: S3 Presigned URLs — Backend

**Fuente:** `api-catatrack/app/routes/artefacto_360_routes.py`
**Stack:** FastAPI + boto3 + Firebase Firestore + AWS S3
**Propósito:** Replicar la lógica exacta de almacenamiento en S3 y generación de URLs presignadas que se devuelven en el GET de requerimientos.

---

## 1. Cómo funciona el flujo completo

```
POST /registrar-requerimiento  (multipart/form-data)
        │
        ├─► sube audio  → S3: requerimientos/{vid}/{rid}/nota_voz_{uuid}.{ext}.gz
        │                    (gzip-compressed, ContentEncoding: gzip)
        │                    guarda en Firestore: nota_voz_url = URL plana S3
        │
        └─► sube fotos  → S3: requerimientos/{vid}/{rid}/{uuid}_{filename}
                              (sin comprimir, ContentType del archivo)
                              guarda en Firestore: documentos_s3 = [{filename, s3_key, content_type, size}]
                              NOTA: en Firestore solo guarda la KEY, no URLs presignadas

GET /obtener-requerimientos
        │
        ├─► lee Firestore (colección 'requerimientos')
        ├─► crea un cliente S3 UNA sola vez (reutilizado para todos los docs)
        └─► por cada requerimiento:
              llama _listar_documentos_s3(vid, rid, s3_client)
              → list_objects_v2(Bucket, Prefix="requerimientos/{vid}/{rid}/")
              → por cada objeto: genera TWO presigned URLs (descarga + visualización inline)
              → devuelve documentos_con_enlaces = [{filename, s3_url, url_visualizar, url_presigned, url_descarga, content_type, size, ...}]
```

---

## 2. Variables de entorno requeridas

| Variable | Propósito | Valor típico |
|----------|-----------|--------------|
| `AWS_ACCESS_KEY_ID` | Clave de acceso IAM | `AKIA...` |
| `AWS_SECRET_ACCESS_KEY` | Clave secreta IAM | `...` |
| `AWS_REGION` | Región del bucket | `us-east-2` |
| `S3_BUCKET_NAME` | Nombre del bucket | `catatrack-photos` |

---

## 3. Dependencias

```
boto3==1.34.0
botocore            # incluida con boto3 (BotoConfig)
python-dotenv       # para load_dotenv(override=True) en get_s3_client()
```

---

## 4. Estructura de claves S3

Todo vive bajo el mismo bucket (`catatrack-photos`). Las claves siguen este patrón:

```
requerimientos/{vid}/{rid}/nota_voz_{uuid}.{ext}.gz     ← audio comprimido
requerimientos/{vid}/{rid}/{uuid}_{filename_sanitizado}  ← fotos/docs
```

- `vid` = ID de visita (ej: `VID-001`)
- `rid` = ID de requerimiento (ej: `RID-042`)
- Los audios llevan el sufijo `.gz` porque se comprimen con gzip antes de subir

---

## 5. Código completo: inicialización del cliente S3

```python
import boto3
from botocore.config import Config as BotoConfig
import os

def get_s3_client():
    """
    Crear cliente de S3 con las credenciales del entorno.
    Usa signature_version='s3v4' (obligatorio para us-east-2 y otras regiones
    fuera de us-east-1 — la firma v2 no funciona en regiones más nuevas).
    """
    from dotenv import load_dotenv
    load_dotenv(override=True)  # override=True garantiza que .env sobreescribe variables ya seteadas

    aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    aws_region = os.getenv('AWS_REGION', 'us-east-2')

    if not aws_access_key or not aws_secret_key:
        raise ValueError("Credenciales de AWS no configuradas")

    return boto3.client(
        's3',
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region,
        config=BotoConfig(signature_version='s3v4'),
    )
```

---

## 6. Código completo: subida de audio

```python
import gzip, uuid, os

async def _subir_audio_s3(nota_voz, vid: str, rid: str) -> tuple[str | None, list]:
    """
    Sube el archivo de audio a S3 comprimido con gzip.
    Retorna (nota_voz_url, transcripciones).
    """
    nota_voz_url = None
    transcripciones = []

    allowed_audio_types = ['audio/webm', 'audio/mp4', 'audio/mpeg', 'audio/wav',
                           'audio/ogg', 'audio/x-m4a', 'video/webm']
    if nota_voz.content_type not in allowed_audio_types:
        raise ValueError(f"Tipo no permitido: {nota_voz.content_type}")

    audio_content = await nota_voz.read()
    audio_extension = os.path.splitext(nota_voz.filename)[1] or '.mp3'

    # Transcribir ANTES de comprimir (Whisper necesita el audio original)
    transcripcion = _transcribir_audio_whisper(audio_bytes=audio_content, filename=nota_voz.filename)
    if transcripcion:
        transcripciones.append(transcripcion)

    # Clave S3: nota_voz_{uuid}.{ext}.gz — el .gz es parte del nombre, no solo metadata
    audio_filename = f"requerimientos/{vid}/{rid}/nota_voz_{uuid.uuid4().hex}{audio_extension}.gz"
    compressed_content = gzip.compress(audio_content)

    s3_client = get_s3_client()
    bucket_name = os.getenv('S3_BUCKET_NAME', 'catatrack-photos')

    s3_client.put_object(
        Bucket=bucket_name,
        Key=audio_filename,
        Body=compressed_content,
        ContentType=nota_voz.content_type,  # content_type del audio original (ej: audio/webm)
        ContentEncoding='gzip',             # indica al cliente que descomprima al descargar
    )

    # URL plana (sin presign) — se guarda en Firestore
    nota_voz_url = f"https://{bucket_name}.s3.amazonaws.com/{audio_filename}"
    return nota_voz_url, transcripciones
```

**Por qué gzip:** reduce el tamaño del audio entre 30-60%. S3 recibe el binario comprimido y lo sirve con `Content-Encoding: gzip`, lo cual hace que los navegadores y clientes HTTP lo descompriman automáticamente al descargarlo.

---

## 7. Código completo: subida de fotos/documentos

```python
import re, uuid, os, mimetypes

async def _subir_fotos_s3(fotos: list, vid: str, rid: str) -> list:
    """
    Sube cada foto/documento a S3. Retorna lista de metadatos.
    """
    documentos_urls = []
    allowed_extensions = {
        ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
        ".pdf", ".gif", ".bmp", ".tiff", ".tif",
    }

    s3_client = get_s3_client()
    bucket_name = os.getenv('S3_BUCKET_NAME', 'catatrack-photos')

    for foto in fotos:
        if not foto or not foto.filename:
            continue

        ext = os.path.splitext(foto.filename)[1].lower()
        if ext not in allowed_extensions:
            continue  # silently skip, not raise

        file_content = await foto.read()
        if len(file_content) == 0:
            continue

        # Determinar content_type: preferir extensión sobre lo que manda el cliente
        guessed_type, _ = mimetypes.guess_type(foto.filename)
        content_type = foto.content_type
        if content_type in (None, "", "application/octet-stream") and guessed_type:
            content_type = guessed_type

        # Sanitizar nombre: solo alfanuméricos, puntos, guiones
        safe_name = re.sub(r'[^\w.\-]', '_', foto.filename)
        s3_key = f"requerimientos/{vid}/{rid}/{uuid.uuid4().hex}_{safe_name}"

        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=file_content,
            ContentType=content_type or "application/octet-stream",
            # SIN ContentEncoding — archivos normales sin comprimir
        )

        documentos_urls.append({
            "filename": foto.filename,
            "s3_key": s3_key,
            "s3_url": f"https://{bucket_name}.s3.amazonaws.com/{s3_key}",
            "content_type": content_type or "application/octet-stream",
            "size": len(file_content),
        })

    return documentos_urls
```

**Lo que se guarda en Firestore (documentos_s3):**
```python
# En Firestore SOLO se guarda key + metadata, SIN URLs presignadas
# (las presigned expiran; se generan en cada GET)
"documentos_s3": [
    {
        "filename": "foto_campo.jpg",
        "s3_key": "requerimientos/VID-001/RID-042/abc123_foto_campo.jpg",
        "content_type": "image/jpeg",
        "size": 245000,
        # SIN s3_url, SIN url_presigned — se generan en el GET
    }
]
```

---

## 8. Código completo: `_listar_documentos_s3` — el corazón del GET

```python
def _listar_documentos_s3(vid: str, rid: str, s3_client=None, expiration: int = 3600) -> list:
    """
    Dado un vid+rid, lista TODOS los objetos en S3 bajo requerimientos/{vid}/{rid}/
    y genera dos URLs presignadas por archivo:
      - url_descarga: fuerza descarga (Content-Disposition: attachment)
      - url_visualizar: muestra inline en navegador (imágenes, audio, PDFs)

    expiration=3600 → las URLs expiran en 1 hora
    """
    bucket_name = os.getenv('S3_BUCKET_NAME', 'catatrack-photos')
    prefix = f"requerimientos/{vid}/{rid}/"

    if s3_client is None:
        try:
            s3_client = get_s3_client()
        except Exception:
            return []  # si no hay credenciales, devuelve vacío (no explota)

    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
    except Exception:
        return []

    # Mapa de extensiones a content_type (S3 no siempre devuelve ContentType correcto)
    ct_map = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
        '.webp': 'image/webp', '.heic': 'image/heic',
        '.pdf': 'application/pdf',
        '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
        '.ogg': 'audio/ogg', '.webm': 'audio/webm', '.m4a': 'audio/mp4',
        '.gz': 'application/gzip',
    }

    documentos = []
    for obj in response.get('Contents', []):
        key = obj['Key']
        filename = key.rsplit('/', 1)[-1] if '/' in key else key
        ext = os.path.splitext(filename)[1].lower()
        content_type = ct_map.get(ext, 'application/octet-stream')
        s3_url = f"https://{bucket_name}.s3.amazonaws.com/{key}"

        # URL para descarga (Content-Disposition: attachment)
        try:
            url_descarga = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': key,
                    'ResponseContentDisposition': f'attachment; filename="{filename}"',
                },
                ExpiresIn=expiration,
            )
        except Exception:
            url_descarga = s3_url  # fallback a URL plana

        # URL para visualización inline (imágenes, audio, PDFs en el browser)
        try:
            url_visualizar = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': key,
                    'ResponseContentDisposition': 'inline',
                },
                ExpiresIn=expiration,
            )
        except Exception:
            url_visualizar = s3_url  # fallback a URL plana

        documentos.append({
            "filename": filename,
            "s3_key": key,
            "s3_url": s3_url,                    # URL plana (sin autenticación, puede ser pública o privada)
            "content_type": content_type,
            "size": obj.get('Size', 0),
            "upload_date": obj['LastModified'].isoformat() if obj.get('LastModified') else None,
            "url_descarga": url_descarga,         # presigned, fuerza descarga
            "url_visualizar": url_visualizar,     # presigned, muestra inline
            "url_presigned": url_visualizar,      # alias de url_visualizar (compatibilidad)
            "url_expiration_seconds": expiration,
        })

    return documentos
```

---

## 9. Código completo: GET `/obtener-requerimientos`

```python
@router.get("/obtener-requerimientos")
async def obtener_requerimientos(vid: Optional[str] = Query(None)):
    """
    Devuelve todos los requerimientos con documentos_con_enlaces (presigned URLs).
    """
    try:
        requerimientos_ref = db.collection('requerimientos')
        docs = requerimientos_ref.where('vid', '==', vid).stream() if vid else requerimientos_ref.stream()

        # IMPORTANTE: crear el cliente S3 UNA SOLA VEZ fuera del loop
        # (crear un cliente por documento sería lento y dispararía rate limits de AWS STS)
        s3_client = None
        try:
            s3_client = get_s3_client()
        except Exception as e:
            print(f"⚠️ No se pudo crear cliente S3: {e}")
            # Continúa sin URLs presignadas — documentos_con_enlaces = []

        requerimientos = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id

            req_vid = data.get('vid', '')
            req_rid = data.get('rid', '')

            if req_vid and req_rid and s3_client:
                documentos = _listar_documentos_s3(req_vid, req_rid, s3_client=s3_client)
                data['documentos_con_enlaces'] = documentos
                data['total_documentos'] = len(documentos)
            else:
                data['documentos_con_enlaces'] = []
                data['total_documentos'] = 0

            requerimientos.append(data)  # clean_nan_values(data) en el original

        return {
            "success": True,
            "total": len(requerimientos),
            "requerimientos": requerimientos,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 10. Estructura completa de la respuesta GET

```json
{
  "success": true,
  "total": 1,
  "requerimientos": [
    {
      "id": "firestore-doc-id",
      "vid": "VID-001",
      "rid": "RID-042",
      "rid_number": 42,
      "estado": "Pendiente",
      "requerimiento": "Árbol caído...",
      "nota_voz_url": "https://catatrack-photos.s3.amazonaws.com/requerimientos/VID-001/RID-042/nota_voz_abc.webm.gz",
      "documentos_s3": [
        { "filename": "foto.jpg", "s3_key": "requerimientos/VID-001/RID-042/uuid_foto.jpg", "content_type": "image/jpeg", "size": 245000 }
      ],
      "documentos_con_enlaces": [
        {
          "filename": "nota_voz_abc.webm.gz",
          "s3_key": "requerimientos/VID-001/RID-042/nota_voz_abc.webm.gz",
          "s3_url": "https://catatrack-photos.s3.amazonaws.com/requerimientos/VID-001/RID-042/nota_voz_abc.webm.gz",
          "content_type": "application/gzip",
          "size": 18500,
          "upload_date": "2026-04-19T10:30:00+00:00",
          "url_descarga": "https://catatrack-photos.s3.amazonaws.com/...?X-Amz-Signature=...&...",
          "url_visualizar": "https://catatrack-photos.s3.amazonaws.com/...?X-Amz-Signature=...&...",
          "url_presigned": "https://catatrack-photos.s3.amazonaws.com/...?X-Amz-Signature=...&...",
          "url_expiration_seconds": 3600
        },
        {
          "filename": "uuid_foto.jpg",
          "s3_key": "requerimientos/VID-001/RID-042/uuid_foto.jpg",
          "s3_url": "https://catatrack-photos.s3.amazonaws.com/requerimientos/VID-001/RID-042/uuid_foto.jpg",
          "content_type": "image/jpeg",
          "size": 245000,
          "url_descarga": "https://...?X-Amz-Signature=...&ResponseContentDisposition=attachment...",
          "url_visualizar": "https://...?X-Amz-Signature=...&ResponseContentDisposition=inline",
          "url_presigned": "https://...?X-Amz-Signature=...&ResponseContentDisposition=inline",
          "url_expiration_seconds": 3600
        }
      ],
      "total_documentos": 2
    }
  ]
}
```

---

## 11. Permisos IAM mínimos requeridos

El usuario IAM asociado a `AWS_ACCESS_KEY_ID` necesita:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::catatrack-photos",
        "arn:aws:s3:::catatrack-photos/*"
      ]
    }
  ]
}
```

`ListBucket` es obligatorio para `list_objects_v2`. Sin él, el GET devuelve `documentos_con_enlaces = []` silenciosamente.

---

## 12. Known gotchas

1. **Firma v4 obligatoria para regiones distintas a us-east-1:** `config=BotoConfig(signature_version='s3v4')` es requerido. Sin ello, boto3 usa v2 por defecto y las URLs presignadas fallan con `AuthorizationQueryParametersError`.

2. **Los audios se guardan con `.gz` en el nombre** (ej: `nota_voz_abc.webm.gz`), no solo como metadata. `_listar_documentos_s3` los detecta por extensión `.gz` y les asigna `content_type: 'application/gzip'`. El frontend debe manejar esto — ver documento de frontend.

3. **`documentos_s3` (en Firestore) vs `documentos_con_enlaces` (en la respuesta GET):** Son distintos. `documentos_s3` se guarda en Firestore sin URLs (solo keys). `documentos_con_enlaces` se construye en cada GET listando S3 en tiempo real. Si hay objetos en S3 que no están en `documentos_s3`, igual aparecen en `documentos_con_enlaces` (porque se lista el prefix completo).

4. **Las presigned URLs expiran en 3600 segundos (1 hora).** Si el usuario deja la app abierta más de 1 hora y luego intenta ver una foto, obtendrá `403 AccessDenied`. Para apps con sesiones largas, considera aumentar `ExpiresIn` o regenerar URLs al re-abrir el panel.

5. **El cliente S3 se crea una sola vez por request** para no disparar límites de STS (el servicio de credenciales temporales de AWS). No crear un cliente por documento.

6. **`nota_voz_url` guardado en Firestore es la URL plana** (sin presign). La URL presignada correcta se encuentra dentro de `documentos_con_enlaces` buscando el doc cuyo `filename` empieza con `nota_voz_`. El frontend debe preferir `documentos_con_enlaces` sobre `nota_voz_url` para obtener una URL válida.
