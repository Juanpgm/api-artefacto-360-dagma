"""
Security regression: the API must NEVER accept a Firebase ID token without
verifying its signature.

Previously, when Google's certificate endpoint was unreachable (cert fetch
error) and the process was not running in Railway "production", both
get_current_user and verify_token_with_fallback decoded the JWT payload
WITHOUT verifying the signature — letting an attacker forge any role/uid.

These tests assert that path is gone: a cert-fetch failure now yields 401.
"""
import base64
import json
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app

client = TestClient(app)


def _make_unsigned_jwt(payload: dict) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}.notarealsignature"


FORGED = _make_unsigned_jwt(
    {"user_id": "attacker-uid", "role": "administrador", "grupo": None, "email": "evil@x.com"}
)


class _CertificateFetchError(Exception):
    """Mimics firebase_admin's CertificateFetchError by class name."""


@pytest.fixture
def mock_cert_fetch_failure():
    """auth_client.verify_id_token always fails as if port 443 were blocked."""
    with patch("app.deps.authz.auth_client") as mock_ac:
        mock_ac.verify_id_token.side_effect = _CertificateFetchError("CertificateFetchError: blocked")
        yield mock_ac


def test_cert_fetch_failure_does_not_bypass_signature(mock_cert_fetch_failure):
    """A forged but well-formed JWT must be rejected with 401, never accepted."""
    r = client.get(
        "/grupos/flora_urbana/reportes_intervenciones",
        headers={"Authorization": f"Bearer {FORGED}"},
    )
    assert r.status_code == 401, r.text


def test_invalid_token_is_unauthorized():
    """A token that fails verification for any reason yields 401."""
    with patch("app.deps.authz.auth_client") as mock_ac:
        mock_ac.verify_id_token.side_effect = ValueError("invalid token")
        r = client.get(
            "/grupos/flora_urbana/reportes_intervenciones",
            headers={"Authorization": "Bearer whatever"},
        )
    assert r.status_code == 401, r.text
