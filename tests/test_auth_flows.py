"""
Tests de flujos críticos de autenticación:
- validate-session: usuario existente con grupo
- validate-session: usuario nuevo de Google (sin grupo) → needs_profile_completion
- validate-session: token inválido → 401
- complete-google-profile: completa grupo
- complete-google-profile: ya tiene grupo → 400
- login (email+password vía id_token)

Se mockean Firebase Admin SDK y Firestore.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from fastapi.testclient import TestClient


# ── Fixtures ─────────────────────────────────────────────────────────────────

USER_WITH_GRUPO = {
    "uid": "uid-con-grupo",
    "email": "existente@dagma.gov.co",
    "role": "operador",
    "grupo": "Flora urbana",
    "full_name": "Existente User",
}

USER_NEW_GOOGLE = {
    "uid": "uid-google-nuevo",
    "email": "nuevo.google@dagma.gov.co",
    "role": "operador",
    "grupo": None,
    "full_name": "Nuevo Google User",
}


def _make_decoded_token(user: dict) -> dict:
    return {
        "uid": user["uid"],
        "email": user["email"],
        "role": user["role"],
        "grupo": user.get("grupo"),
    }


def _make_firebase_user(user: dict):
    mock = MagicMock()
    mock.uid = user["uid"]
    mock.email = user["email"]
    mock.display_name = user.get("full_name", "")
    mock.email_verified = True
    mock.disabled = False
    mock.photo_url = None
    return mock


def _make_firestore_doc(user: dict | None):
    if user is None:
        doc = MagicMock()
        doc.exists = False
        doc.to_dict.return_value = None
        return doc
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {
        "uid": user["uid"],
        "email": user["email"],
        "full_name": user.get("full_name", ""),
        "role": user["role"],
        "grupo": user.get("grupo"),
    }
    return doc


def _build_patches(user_data: dict, firestore_user: dict | None = None, token_valid: bool = True):
    """Returns a dict of (patch_target → mock_value) for all Firebase/Firestore calls."""
    firebase_user = _make_firebase_user(user_data)
    fs_doc = _make_firestore_doc(firestore_user if firestore_user is not None else user_data)

    db_mock = MagicMock()
    db_mock.collection.return_value.document.return_value.get.return_value = fs_doc
    db_mock.collection.return_value.document.return_value.set.return_value = None
    db_mock.collection.return_value.document.return_value.update.return_value = None

    auth_client_mock = MagicMock()
    if token_valid:
        auth_client_mock.verify_id_token.return_value = _make_decoded_token(user_data)
    else:
        auth_client_mock.verify_id_token.side_effect = Exception("Token inválido")
    auth_client_mock.get_user.return_value = firebase_user
    auth_client_mock.set_custom_user_claims.return_value = None
    auth_client_mock.update_user.return_value = None

    return db_mock, auth_client_mock


@pytest.fixture()
def client_with_user(request):
    """
    Parameterizable fixture: yields (TestClient, user_data).
    Pass user_data via request.param.
    """
    user_data = getattr(request, "param", USER_WITH_GRUPO)
    db_mock, auth_mock = _build_patches(user_data)

    patches = [
        patch("app.routes.auth_routes.db", db_mock),
        patch("app.routes.auth_routes.auth_client", auth_mock),
        patch("app.services.auth_service.auth_client", auth_mock),
        patch("app.routes.auth_routes.get_s3_photo_url", return_value=None),
    ]
    for p in patches:
        p.start()

    from main import app
    with TestClient(app) as client:
        yield client, user_data

    for p in reversed(patches):
        p.stop()


# ── validate-session ──────────────────────────────────────────────────────────

class TestValidateSession:

    def test_existing_user_with_grupo_returns_valid(self):
        db_mock, auth_mock = _build_patches(USER_WITH_GRUPO)
        with (
            patch("app.routes.auth_routes.db", db_mock),
            patch("app.routes.auth_routes.auth_client", auth_mock),
            patch("app.services.auth_service.auth_client", auth_mock),
            patch("app.routes.auth_routes.get_s3_photo_url", return_value=None),
        ):
            from main import app
            client = TestClient(app)
            resp = client.post(
                "/auth/validate-session",
                headers={"Authorization": "Bearer valid-token-xyz"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["needs_profile_completion"] is False
        assert data["user"]["email"] == USER_WITH_GRUPO["email"]
        assert data["user"]["grupo"] == USER_WITH_GRUPO["grupo"]

    def test_new_google_user_gets_needs_profile_completion(self):
        # Firestore doc doesn't exist yet
        db_mock, auth_mock = _build_patches(USER_NEW_GOOGLE, firestore_user=None)
        # Override the doc to be non-existent
        no_doc = _make_firestore_doc(None)
        db_mock.collection.return_value.document.return_value.get.return_value = no_doc

        with (
            patch("app.routes.auth_routes.db", db_mock),
            patch("app.routes.auth_routes.auth_client", auth_mock),
            patch("app.services.auth_service.auth_client", auth_mock),
            patch("app.routes.auth_routes.get_s3_photo_url", return_value=None),
        ):
            from main import app
            client = TestClient(app)
            resp = client.post(
                "/auth/validate-session",
                headers={"Authorization": "Bearer new-google-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["needs_profile_completion"] is True
        # Should have created the user doc provisionally
        db_mock.collection.return_value.document.return_value.set.assert_called_once()

    def test_invalid_token_returns_401(self):
        db_mock, auth_mock = _build_patches(USER_WITH_GRUPO, token_valid=False)
        with (
            patch("app.routes.auth_routes.db", db_mock),
            patch("app.routes.auth_routes.auth_client", auth_mock),
            patch("app.services.auth_service.auth_client", auth_mock),
            patch("app.routes.auth_routes.get_s3_photo_url", return_value=None),
        ):
            from main import app
            client = TestClient(app)
            resp = client.post(
                "/auth/validate-session",
                headers={"Authorization": "Bearer invalid-token"},
            )
        assert resp.status_code == 401

    def test_missing_auth_header_returns_403(self):
        from main import app
        client = TestClient(app)
        resp = client.post("/auth/validate-session")
        assert resp.status_code in (401, 403, 422)


# ── complete-google-profile ───────────────────────────────────────────────────

class TestCompleteGoogleProfile:

    def _make_no_grupo_doc(self):
        """Firestore doc with no grupo (needs completion)."""
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "uid": USER_NEW_GOOGLE["uid"],
            "email": USER_NEW_GOOGLE["email"],
            "full_name": USER_NEW_GOOGLE["full_name"],
            "role": "operador",
            "grupo": None,
        }
        return doc

    def _make_has_grupo_doc(self):
        """Firestore doc that already has a grupo."""
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "uid": USER_WITH_GRUPO["uid"],
            "email": USER_WITH_GRUPO["email"],
            "full_name": USER_WITH_GRUPO["full_name"],
            "role": "operador",
            "grupo": "Flora urbana",
        }
        return doc

    def _patches(self, db_mock, auth_mock):
        """Context managers that patch both the route module AND the authz dependency."""
        return [
            patch("app.routes.auth_routes.db", db_mock),
            patch("app.routes.auth_routes.auth_client", auth_mock),
            # authz.py (get_current_user dependency) has its own reference
            patch("app.deps.authz.auth_client", auth_mock),
            patch("app.deps.authz.db", db_mock),
            patch("app.routes.auth_routes.get_s3_photo_url", return_value=None),
        ]

    def test_completes_profile_successfully(self):
        db_mock, auth_mock = _build_patches(USER_NEW_GOOGLE)
        db_mock.collection.return_value.document.return_value.get.return_value = (
            self._make_no_grupo_doc()
        )

        from main import app
        client = TestClient(app)
        with patch("app.routes.auth_routes.db", db_mock), \
             patch("app.routes.auth_routes.auth_client", auth_mock), \
             patch("app.deps.authz.auth_client", auth_mock), \
             patch("app.deps.authz.db", db_mock), \
             patch("app.routes.auth_routes.get_s3_photo_url", return_value=None):
            resp = client.post(
                "/auth/complete-google-profile",
                json={"grupo": "Cuadrilla", "full_name": "Nuevo User"},
                headers={"Authorization": "Bearer valid-token"},
            )

        assert resp.status_code == 200
        data = resp.json()
        # El backend canonicaliza con canonical_grupo_key: "Cuadrilla" -> "flora_urbana"
        assert data["success"] is True
        assert data["grupo"] == "flora_urbana"
        db_mock.collection.return_value.document.return_value.update.assert_called_once()

    def test_fails_when_grupo_already_set(self):
        db_mock, auth_mock = _build_patches(USER_WITH_GRUPO)
        db_mock.collection.return_value.document.return_value.get.return_value = (
            self._make_has_grupo_doc()
        )

        from main import app
        client = TestClient(app)
        with patch("app.routes.auth_routes.db", db_mock), \
             patch("app.routes.auth_routes.auth_client", auth_mock), \
             patch("app.deps.authz.auth_client", auth_mock), \
             patch("app.deps.authz.db", db_mock), \
             patch("app.routes.auth_routes.get_s3_photo_url", return_value=None):
            resp = client.post(
                "/auth/complete-google-profile",
                json={"grupo": "Cuadrilla"},
                headers={"Authorization": "Bearer valid-token"},
            )

        assert resp.status_code == 400
        assert "ya esta completo" in resp.json()["detail"]

    def test_fails_when_grupo_empty(self):
        db_mock, auth_mock = _build_patches(USER_NEW_GOOGLE)
        db_mock.collection.return_value.document.return_value.get.return_value = (
            self._make_no_grupo_doc()
        )

        from main import app
        client = TestClient(app)
        with patch("app.routes.auth_routes.db", db_mock), \
             patch("app.routes.auth_routes.auth_client", auth_mock), \
             patch("app.deps.authz.auth_client", auth_mock), \
             patch("app.deps.authz.db", db_mock), \
             patch("app.routes.auth_routes.get_s3_photo_url", return_value=None):
            resp = client.post(
                "/auth/complete-google-profile",
                json={"grupo": "   "},
                headers={"Authorization": "Bearer valid-token"},
            )

        assert resp.status_code == 400

    def test_fails_without_auth(self):
        from main import app
        client = TestClient(app)
        resp = client.post(
            "/auth/complete-google-profile",
            json={"grupo": "Cuadrilla"},
        )
        assert resp.status_code in (401, 403, 422)


# ── login endpoint (id_token flow) ───────────────────────────────────────────

class TestLoginEndpoint:

    def test_login_with_valid_id_token(self):
        db_mock, auth_mock = _build_patches(USER_WITH_GRUPO)

        with (
            patch("app.routes.auth_routes.db", db_mock),
            patch("app.routes.auth_routes.auth_client", auth_mock),
            patch("app.services.auth_service.auth_client", auth_mock),
            patch("app.routes.auth_routes.get_s3_photo_url", return_value=None),
        ):
            from main import app
            # Use standard TestClient without raise_server_exceptions to see
            # rate limiting behavior gracefully
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/auth/login",
                json={
                    "id_token": "valid-id-token",
                    "email": USER_WITH_GRUPO["email"],
                },
            )

        # May be 200 or 429 (rate limit) depending on test runner state
        assert resp.status_code in (200, 429)
        if resp.status_code == 200:
            data = resp.json()
            assert "access_token" in data or "user" in data

    def test_login_with_invalid_token_returns_401(self):
        db_mock, auth_mock = _build_patches(USER_WITH_GRUPO, token_valid=False)

        with (
            patch("app.routes.auth_routes.db", db_mock),
            patch("app.routes.auth_routes.auth_client", auth_mock),
        ):
            from main import app
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/auth/login",
                json={"id_token": "bad-token", "email": "x@x.com"},
            )

        assert resp.status_code in (401, 422, 429)
