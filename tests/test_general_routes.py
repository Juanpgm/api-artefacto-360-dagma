"""Tests for general utility routes, including auth on the debug endpoint."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app

client = TestClient(app)

ADMIN_TOKEN = {"uid": "admin-uid", "email": "admin@dagma.gov.co", "role": "administrador", "grupo": None}
OPERADOR_TOKEN = {"uid": "op-uid", "email": "op@dagma.gov.co", "role": "operador", "grupo": "flora_urbana"}


def test_ping_is_public():
    assert client.get("/ping").status_code == 200


def test_debug_railway_requires_auth():
    # Missing Authorization header → HTTPBearer rejects with 401 (Unauthorized)
    assert client.get("/debug/railway").status_code == 401


def test_debug_railway_forbidden_for_operador():
    with patch("app.deps.authz.auth_client") as mock_ac:
        mock_ac.verify_id_token.return_value = OPERADOR_TOKEN
        r = client.get("/debug/railway", headers={"Authorization": "Bearer x"})
    assert r.status_code == 403, r.text


def test_debug_railway_allowed_for_admin():
    with patch("app.deps.authz.auth_client") as mock_ac:
        mock_ac.verify_id_token.return_value = ADMIN_TOKEN
        r = client.get("/debug/railway", headers={"Authorization": "Bearer x"})
    assert r.status_code == 200, r.text
    assert "environment" in r.json()
