"""
Auth business logic — token verification and S3 profile photo utilities.

These functions are extracted from auth_routes.py to keep route handlers thin.
All Firebase Admin SDK interactions (Firestore writes, claims updates) that are
tightly interleaved with routing logic remain in auth_routes.py.
"""
import logging
import os
from typing import Optional

from app.firebase_config import auth_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def verify_firebase_token(token: str) -> dict:
    """Verify a Firebase ID token and return its decoded claims.

    The signature is ALWAYS verified — there is no unsigned fallback. For local
    development without outbound access to Google's certificate endpoint, run the
    Firebase Auth Emulator and set FIREBASE_AUTH_EMULATOR_HOST; the Admin SDK then
    validates emulator-issued tokens through this same call. See back/README.md.
    """
    return auth_client.verify_id_token(token, check_revoked=True)


# Backwards-compatible alias (the old name implied an insecure fallback that no
# longer exists). Prefer verify_firebase_token in new code.
verify_token_with_fallback = verify_firebase_token


# ---------------------------------------------------------------------------
# S3 profile photo utilities
# ---------------------------------------------------------------------------

# Module-level S3 client cache — avoids recreation cost per request.
_S3_CLIENT = None
_S3_INIT_FAILED = False


def get_s3_client():
    """Return a cached boto3 S3 client, or None if credentials are missing."""
    global _S3_CLIENT, _S3_INIT_FAILED
    if _S3_CLIENT is not None or _S3_INIT_FAILED:
        return _S3_CLIENT
    try:
        import boto3
        aws_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_REGION", "us-east-1")
        if not aws_key or not aws_secret:
            _S3_INIT_FAILED = True
            return None
        _S3_CLIENT = boto3.client(
            "s3",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region,
        )
        return _S3_CLIENT
    except Exception as e:
        logger.warning(f"No se pudo inicializar cliente S3: {e}")
        _S3_INIT_FAILED = True
        return None


def get_s3_photo_url(s3_key: str) -> Optional[str]:
    """Generate a 7-day presigned S3 URL for a profile photo.

    Returns None if S3 is not configured or the URL cannot be generated.
    """
    try:
        bucket = os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_S3_BUCKET_NAME")
        if not bucket:
            return None
        s3 = get_s3_client()
        if s3 is None:
            return None
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=604800,  # 7 days
        )
    except Exception as e:
        logger.warning(f"No se pudo generar presigned URL para {s3_key}: {e}")
        return None
