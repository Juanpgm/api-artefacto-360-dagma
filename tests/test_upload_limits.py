"""Tests for per-photo upload size limit (DoS/OOM guard)."""
import io
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch

from app.main import app

client = TestClient(app)

DECODED_TOKEN = {"uid": "u", "email": "admin@dagma.gov.co", "role": "administrador", "grupo": None}
AUTH = {"Authorization": "Bearer mock"}


@pytest.fixture
def mock_firebase_db():
    with patch("app.routes.artefacto_360_routes.db") as mock_db:
        mc = Mock()
        dr = Mock()
        snap = Mock()
        snap.exists = False
        mc.document.return_value = dr
        dr.get.return_value = snap
        mc.stream.return_value = []
        mc.where.return_value = mc
        mc.limit.return_value = mc
        mock_db.collection.return_value = mc
        yield mock_db


@pytest.fixture
def mock_s3():
    with patch("app.routes.artefacto_360_routes.get_s3_client") as mock_get:
        mc = Mock()
        mc.upload_fileobj = Mock()
        mc.delete_object = Mock()
        mc.generate_presigned_url.return_value = "https://fake-presigned-url.com"
        mc.head_object.return_value = {"ContentType": "image/jpeg", "ContentLength": 1234}
        mock_get.return_value = mc
        yield mc


@pytest.fixture
def mock_auth():
    with patch("app.deps.authz.auth_client") as mock_ac:
        mock_ac.verify_id_token.return_value = DECODED_TOKEN
        yield mock_ac


def test_oversized_photo_rejected_with_413(mock_firebase_db, mock_s3, mock_auth):
    # Shrink the limit so a tiny file trips it without sending megabytes.
    with patch("app.routes.artefacto_360_routes.MAX_PHOTO_BYTES", 10):
        files = [("photos", ("big.jpg", io.BytesIO(b"x" * 50), "image/jpeg"))]
        r = client.post(
            "/grupos/flora_urbana/reporte_intervencion",
            data={"tipo_intervencion": "T"},
            files=files,
            headers=AUTH,
        )
    assert r.status_code == 413, r.text


def test_normal_photo_still_accepted(mock_firebase_db, mock_s3, mock_auth):
    files = [("photos", ("ok.jpg", io.BytesIO(b"small content"), "image/jpeg"))]
    r = client.post(
        "/grupos/flora_urbana/reporte_intervencion",
        data={"tipo_intervencion": "T"},
        files=files,
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
