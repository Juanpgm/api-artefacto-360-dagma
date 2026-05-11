"""
Tests de integración RBAC: matriz endpoint × rol × grupo.

Estrategia:
- Se mockean Firebase Admin SDK (verify_id_token, get_user) y Firestore (db).
- Se inyectan claims distintos por usuario para simular los 4 roles.
- Se verifica que cada endpoint devuelve 200 o 403 según el rol.

Usuarios de prueba:
  - operador_cuadrilla: role=operador, grupo=cuadrilla
  - lider_cuadrilla:    role=lider,    grupo=cuadrilla
  - administrador:      role=administrador, grupo=cuadrilla
  - desarrollador:      role=desarrollador, grupo=None
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures: usuarios y mocks de Firebase
# ---------------------------------------------------------------------------

USERS = {
    "operador": {
        "uid": "uid-operador",
        "email": "operador@dagma.gov.co",
        "role": "operador",
        "grupo": "cuadrilla",
    },
    "lider": {
        "uid": "uid-lider",
        "email": "lider@dagma.gov.co",
        "role": "lider",
        "grupo": "cuadrilla",
    },
    "admin": {
        "uid": "uid-admin",
        "email": "admin@dagma.gov.co",
        "role": "administrador",
        "grupo": "cuadrilla",
    },
    "dev": {
        "uid": "uid-dev",
        "email": "dev@dagma.gov.co",
        "role": "desarrollador",
        "grupo": None,
    },
}


def _make_decoded_token(user: dict) -> dict:
    """Construye un decoded_token al estilo Firebase con custom claims."""
    return {
        "uid": user["uid"],
        "email": user["email"],
        "name": user["email"].split("@")[0],
        "role": user["role"],
        "grupo": user.get("grupo"),
    }


def _make_firebase_user(user: dict):
    mock_user = MagicMock()
    mock_user.uid = user["uid"]
    mock_user.email = user["email"]
    mock_user.display_name = user["email"].split("@")[0]
    mock_user.email_verified = True
    mock_user.disabled = False
    mock_user.user_metadata = MagicMock()
    mock_user.user_metadata.creation_timestamp = 0
    return mock_user


def _make_firestore_doc(user: dict):
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {
        "uid": user["uid"],
        "email": user["email"],
        "full_name": user["email"].split("@")[0],
        "role": user["role"],
        "grupo": user.get("grupo"),
    }
    return doc


@pytest.fixture(autouse=True)
def mock_firebase(monkeypatch):
    """
    Mock global de Firebase Admin SDK y Firestore para todos los tests.
    Cada test puede override `current_test_user` para cambiar el usuario.
    """
    with (
        patch("app.firebase_config.auth") as mock_auth,
        patch("app.firebase_config.db") as mock_db,
        patch("app.firebase_config.auth_client") as mock_auth_client,
        patch("app.deps.authz.auth_client") as mock_authz_auth,
        patch("app.deps.authz.db") as mock_authz_db,
        patch("app.routes.auth_routes.auth_client") as mock_auth_routes_auth,
        patch("app.routes.auth_routes.db") as mock_auth_routes_db,
    ):
        yield {
            "auth_client": mock_auth_client,
            "db": mock_db,
            "authz_auth": mock_authz_auth,
            "authz_db": mock_authz_db,
            "auth_routes_auth": mock_auth_routes_auth,
            "auth_routes_db": mock_auth_routes_db,
        }


def _get_client_for_user(user_key: str, mocks: dict) -> TestClient:
    """
    Configura los mocks para que verify_id_token devuelva los claims del usuario indicado
    y Firestore devuelva el documento correspondiente.
    """
    from app.main import app

    user = USERS[user_key]
    decoded_token = _make_decoded_token(user)
    firebase_user = _make_firebase_user(user)
    firestore_doc = _make_firestore_doc(user)

    # authz.py
    mocks["authz_auth"].verify_id_token.return_value = decoded_token
    mocks["authz_auth"].get_user.return_value = firebase_user
    mocks["authz_db"].collection.return_value.document.return_value.get.return_value = firestore_doc

    # auth_routes.py
    mocks["auth_routes_auth"].verify_id_token.return_value = decoded_token
    mocks["auth_routes_auth"].get_user.return_value = firebase_user
    mocks["auth_routes_db"].collection.return_value.document.return_value.get.return_value = firestore_doc

    return TestClient(app, raise_server_exceptions=False)


FAKE_TOKEN = "Bearer fake-token-for-testing"


# ---------------------------------------------------------------------------
# Tests: DELETE /auth/user/{uid}
# ---------------------------------------------------------------------------

class TestDeleteUser:
    def test_operador_cannot_delete_user(self, mock_firebase):
        client = _get_client_for_user("operador", mock_firebase)
        r = client.delete("/auth/user/some-uid", headers={"Authorization": FAKE_TOKEN})
        assert r.status_code == 403

    def test_lider_cannot_delete_user(self, mock_firebase):
        client = _get_client_for_user("lider", mock_firebase)
        r = client.delete("/auth/user/some-uid", headers={"Authorization": FAKE_TOKEN})
        assert r.status_code == 403

    def test_admin_can_delete_user(self, mock_firebase):
        mock_firebase["auth_routes_auth"].delete_user.return_value = None
        mock_firebase["auth_routes_db"].collection.return_value.document.return_value.delete.return_value = None
        client = _get_client_for_user("admin", mock_firebase)
        r = client.delete("/auth/user/some-uid", headers={"Authorization": FAKE_TOKEN})
        # 200 o 400 (el uid no existe en el mock) pero no 403
        assert r.status_code != 403

    def test_dev_can_delete_user(self, mock_firebase):
        mock_firebase["auth_routes_auth"].delete_user.return_value = None
        mock_firebase["auth_routes_db"].collection.return_value.document.return_value.delete.return_value = None
        client = _get_client_for_user("dev", mock_firebase)
        r = client.delete("/auth/user/some-uid", headers={"Authorization": FAKE_TOKEN})
        assert r.status_code != 403


# ---------------------------------------------------------------------------
# Tests: GET /admin/users (listado de usuarios)
# ---------------------------------------------------------------------------

class TestListUsers:
    def test_operador_cannot_list_users(self, mock_firebase):
        client = _get_client_for_user("operador", mock_firebase)
        r = client.get("/admin/users", headers={"Authorization": FAKE_TOKEN})
        assert r.status_code == 403

    def test_lider_can_list_users(self, mock_firebase):
        # Lider puede ver su propio grupo
        mock_firebase["auth_routes_db"].collection.return_value.stream.return_value = []
        client = _get_client_for_user("lider", mock_firebase)
        r = client.get("/admin/users", headers={"Authorization": FAKE_TOKEN})
        assert r.status_code == 200

    def test_admin_can_list_all_users(self, mock_firebase):
        mock_firebase["auth_routes_db"].collection.return_value.stream.return_value = []
        client = _get_client_for_user("admin", mock_firebase)
        r = client.get("/admin/users", headers={"Authorization": FAKE_TOKEN})
        assert r.status_code == 200


class TestLeaderCatalog:
    def test_lider_can_list_all_lideres(self, mock_firebase):
        leader_docs = []
        for uid, full_name, grupo in [
            ("uid-lider-1", "Ana Perez", "cuadrilla"),
            ("uid-lider-2", "Bruno Ruiz", "vivero"),
        ]:
            doc = MagicMock()
            doc.id = uid
            doc.to_dict.return_value = {
                "uid": uid,
                "email": f"{uid}@dagma.gov.co",
                "full_name": full_name,
                "role": "lider",
                "grupo": grupo,
            }
            leader_docs.append(doc)

        mock_firebase["auth_routes_db"].collection.return_value.stream.return_value = leader_docs
        # El endpoint ahora hace where("role"|"rol", "==", "lider").limit(500).stream(),
        # ejecutando ambas queries en paralelo y dedup por doc.id.
        # Devolvemos los mismos docs en la query principal y una lista vacia en la legacy.
        collection_mock = mock_firebase["auth_routes_db"].collection.return_value
        primary_where = MagicMock()
        primary_where.limit.return_value.stream.return_value = leader_docs
        legacy_where = MagicMock()
        legacy_where.limit.return_value.stream.return_value = []
        def _where_side_effect(field, op, value):
            if field == "role":
                return primary_where
            if field == "rol":
                return legacy_where
            return MagicMock()
        collection_mock.where.side_effect = _where_side_effect
        client = _get_client_for_user("lider", mock_firebase)
        r = client.get("/admin/users/lideres", headers={"Authorization": FAKE_TOKEN})

        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        grupos = {item["grupo"] for item in data["data"]}
        assert grupos == {"cuadrilla", "vivero"}


# ---------------------------------------------------------------------------
# Tests: PATCH /admin/users/{uid}/role — reglas de escalamiento
# ---------------------------------------------------------------------------

class TestChangeRole:
    def _setup_target_user(self, mocks, target_uid: str, target_role: str, target_grupo: str = "cuadrilla"):
        """Configura Firestore para que el usuario objetivo tenga los datos indicados."""
        target_doc = MagicMock()
        target_doc.exists = True
        target_doc.to_dict.return_value = {
            "uid": target_uid,
            "email": "target@dagma.gov.co",
            "role": target_role,
            "grupo": target_grupo,
        }

        def side_effect_collection(collection_name):
            mock_col = MagicMock()
            if collection_name == "users":
                def side_effect_document(uid):
                    mock_doc_ref = MagicMock()
                    mock_doc_ref.get.return_value = target_doc
                    mock_doc_ref.update.return_value = None
                    return mock_doc_ref
                mock_col.document.side_effect = side_effect_document
                mock_col.stream.return_value = []
            elif collection_name == "audit_role_changes":
                mock_col.add.return_value = (None, MagicMock())
            return mock_col

        mocks["auth_routes_db"].collection.side_effect = side_effect_collection

    def test_operador_cannot_change_roles(self, mock_firebase):
        self._setup_target_user(mock_firebase, "uid-target", "operador")
        client = _get_client_for_user("operador", mock_firebase)
        r = client.patch(
            "/admin/users/uid-target/role",
            json={"role": "lider"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 403

    def test_lider_cannot_change_roles(self, mock_firebase):
        self._setup_target_user(mock_firebase, "uid-target", "operador")
        client = _get_client_for_user("lider", mock_firebase)
        r = client.patch(
            "/admin/users/uid-target/role",
            json={"role": "administrador"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 403

    def test_admin_can_promote_to_lider(self, mock_firebase):
        self._setup_target_user(mock_firebase, "uid-target", "operador")
        mock_firebase["auth_routes_auth"].set_custom_user_claims.return_value = None
        mock_firebase["auth_routes_auth"].revoke_refresh_tokens.return_value = None
        client = _get_client_for_user("admin", mock_firebase)
        r = client.patch(
            "/admin/users/uid-target/role",
            json={"role": "lider"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["new_role"] == "lider"
        assert data["requires_relogin"] is True

    def test_admin_cannot_assign_desarrollador(self, mock_firebase):
        """Administrador no puede crear/asignar el rol desarrollador."""
        self._setup_target_user(mock_firebase, "uid-target", "lider")
        client = _get_client_for_user("admin", mock_firebase)
        r = client.patch(
            "/admin/users/uid-target/role",
            json={"role": "desarrollador"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 403

    def test_dev_can_assign_desarrollador(self, mock_firebase):
        """Desarrollador puede asignar el rol desarrollador."""
        self._setup_target_user(mock_firebase, "uid-other-dev", "administrador")
        mock_firebase["auth_routes_auth"].set_custom_user_claims.return_value = None
        mock_firebase["auth_routes_auth"].revoke_refresh_tokens.return_value = None
        client = _get_client_for_user("dev", mock_firebase)
        r = client.patch(
            "/admin/users/uid-other-dev/role",
            json={"role": "desarrollador"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 200

    def test_nobody_can_change_own_role(self, mock_firebase):
        """Nadie puede cambiar su propio rol."""
        self._setup_target_user(mock_firebase, USERS["admin"]["uid"], "administrador")
        client = _get_client_for_user("admin", mock_firebase)
        r = client.patch(
            f"/admin/users/{USERS['admin']['uid']}/role",
            json={"role": "desarrollador"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 403

    def test_invalid_role_returns_400(self, mock_firebase):
        client = _get_client_for_user("admin", mock_firebase)
        r = client.patch(
            "/admin/users/uid-target/role",
            json={"role": "superuser"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Tests: GET /grupos/{grupo_key}/reportes_intervenciones — aislamiento de grupo
# ---------------------------------------------------------------------------

class TestReportesGrupoScope:
    def test_operador_cuadrilla_cannot_access_vivero(self, mock_firebase):
        """Operador de cuadrilla no puede ver reportes de vivero."""
        client = _get_client_for_user("operador", mock_firebase)
        r = client.get(
            "/grupos/vivero/reportes_intervenciones",
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 403

    def test_lider_cuadrilla_cannot_access_gobernanza(self, mock_firebase):
        """Líder de cuadrilla no puede ver reportes de gobernanza."""
        client = _get_client_for_user("lider", mock_firebase)
        r = client.get(
            "/grupos/gobernanza/reportes_intervenciones",
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 403

    def test_admin_can_access_any_grupo(self, mock_firebase):
        """Administrador puede ver reportes de cualquier grupo."""
        # Mock para que Firestore devuelva lista vacía
        mock_col = MagicMock()
        mock_col.stream.return_value = []
        mock_col.document.return_value.get.return_value = MagicMock(exists=False)
        mock_col.where.return_value = mock_col

        from app.firebase_config import db as real_db  # needed to patch in artefacto routes
        with patch("app.routes.artefacto_360_routes.db") as mock_ar_db:
            mock_ar_db.collection.return_value = mock_col
            mock_ar_db.collection.return_value.document.return_value.get.return_value = MagicMock(exists=False)

            client = _get_client_for_user("admin", mock_firebase)
            r = client.get(
                "/grupos/vivero/reportes_intervenciones",
                headers={"Authorization": FAKE_TOKEN},
            )
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Seguimiento — prioridad requiere administrador
# ---------------------------------------------------------------------------

class TestSeguimientoPermisos:
    def test_operador_cannot_change_prioridad(self, mock_firebase):
        client = _get_client_for_user("operador", mock_firebase)
        r = client.patch(
            "/api/v1/reportes/rep-123/prioridad",
            json={"prioridad": "urgente"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 403

    def test_lider_cannot_change_prioridad(self, mock_firebase):
        client = _get_client_for_user("lider", mock_firebase)
        r = client.patch(
            "/api/v1/reportes/rep-123/prioridad",
            json={"prioridad": "alta"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 403

    def test_operador_cannot_assign_encargado(self, mock_firebase):
        client = _get_client_for_user("operador", mock_firebase)
        r = client.patch(
            "/api/v1/reportes/rep-123/encargado",
            json={"encargado": "Juan", "centro_gestor": "DAGMA"},
            headers={"Authorization": FAKE_TOKEN},
        )
        assert r.status_code == 403

    def test_lider_can_assign_encargado(self, mock_firebase):
        # Lider puede asignar encargado
        with patch("app.routes.seguimiento_routes.db") as mock_seg_db:
            reporte_doc = MagicMock()
            reporte_doc.exists = True
            reporte_doc.to_dict.return_value = {"id": "rep-123", "nombre_parque": "Parque X"}
            seg_doc = MagicMock()
            seg_doc.exists = False

            mock_seg_db.collection.return_value.document.return_value.get.side_effect = [
                reporte_doc, seg_doc
            ]
            mock_seg_db.collection.return_value.document.return_value.set.return_value = None

            client = _get_client_for_user("lider", mock_firebase)
            r = client.patch(
                "/api/v1/reportes/rep-123/encargado",
                json={"encargado": "Juan Pérez", "centro_gestor": "DAGMA"},
                headers={"Authorization": FAKE_TOKEN},
            )
            assert r.status_code == 200

    def test_unauthenticated_cannot_access_historial(self):
        """Sin token, debe devolver 403 o 422."""
        from app.main import app
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/v1/reportes/rep-123/historial")
        assert r.status_code in (401, 403, 422)
