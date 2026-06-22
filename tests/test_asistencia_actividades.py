"""
Tests: POST / GET / PATCH de /asistencia_actividades

Usa pytest + TestClient + mocks de Firebase Auth y Firestore.
No requiere API corriendo ni token real de Firebase.

Ejecucion:
    pytest test_asistencia_actividades.py -v
"""
import sys
import copy
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Constantes compartidas
# ---------------------------------------------------------------------------
MOCK_UID   = "test-admin-uid-asistencia"
MOCK_EMAIL = "test.admin@dagma.gov.co"

DECODED_TOKEN = {
    "uid":   MOCK_UID,
    "email": MOCK_EMAIL,
    "role":  "administrador",
    "grupo": "Flora urbana",
}

FIRESTORE_USER_DOC = {
    "uid":       MOCK_UID,
    "email":     MOCK_EMAIL,
    "full_name": "Admin Test",
    "role":      "administrador",
    "grupo":     "Cuadrilla",
}

AUTH_HEADERS = {"Authorization": "Bearer mock-token-administrador"}

TEST_ACTIVIDAD_ID = "test-actividad-asistencia-001"

# 10 integrantes: 7 validacion=True, 3 validacion=False -> 70%
PERSONAL_10 = [
    {"nombre_completo": "Ana Torres",
     "email": "ana.torres@dagma.gov.co",
     "uid": None, "grupo": "Flora urbana",
     "validacion": True,  "observacion": "Llego puntual", "alerta": None},
    {"nombre_completo": "Carlos Rios",
     "email": "carlos.rios@dagma.gov.co",
     "uid": None, "grupo": "Flora urbana",
     "validacion": True,  "observacion": None, "alerta": None},
    {"nombre_completo": "Diana Mora",
     "email": "diana.mora@dagma.gov.co",
     "uid": None, "grupo": "Vivero",
     "validacion": False, "observacion": "Incapacidad medica", "alerta": None},
    {"nombre_completo": "Eduardo Salazar",
     "email": "eduardo.salazar@dagma.gov.co",
     "uid": None, "grupo": "Vivero",
     "validacion": True,  "observacion": None, "alerta": None},
    {"nombre_completo": "Fernanda Lozano",
     "email": "fernanda.lozano@dagma.gov.co",
     "uid": None, "grupo": "Gobernanza",
     "validacion": False, "observacion": None,
     "alerta": "abandono_sin_justificacion"},
    {"nombre_completo": "Gustavo Pedraza",
     "email": "gustavo.pedraza@dagma.gov.co",
     "uid": None, "grupo": "Gobernanza",
     "validacion": True,  "observacion": "Llego tarde 10 min", "alerta": None},
    {"nombre_completo": "Helena Vargas",
     "email": "helena.vargas@dagma.gov.co",
     "uid": None, "grupo": "Ecosistemas",
     "validacion": True,  "observacion": None, "alerta": None},
    {"nombre_completo": "Ivan Castillo",
     "email": "ivan.castillo@dagma.gov.co",
     "uid": None, "grupo": "Ecosistemas",
     "validacion": False, "observacion": None,
     "alerta": "abandono_sin_justificacion"},
    {"nombre_completo": "Julia Mendez",
     "email": "julia.mendez@dagma.gov.co",
     "uid": None, "grupo": "UMATA",
     "validacion": True,  "observacion": None, "alerta": None},
    {"nombre_completo": "Kevin Ospina",
     "email": "kevin.ospina@dagma.gov.co",
     "uid": None, "grupo": "UMATA",
     "validacion": True,  "observacion": "Apoyo en carga de datos", "alerta": None},
]

ASISTENTES_ESPERADOS  = 7
AUSENTES_ESPERADOS    = 3
ASISTENCIA_GENERAL    = 70.0


# ---------------------------------------------------------------------------
# Helper: construir mock de Firestore
# ---------------------------------------------------------------------------
def make_firestore_mock(doc_data=None, doc_exists=True):
    """Retorna (mock_db, mock_doc_ref) con el comportamiento deseado."""
    mock_db      = MagicMock()
    mock_doc_ref = MagicMock()

    # Snapshot del documento de asistencia
    mock_snap = MagicMock()
    mock_snap.exists = doc_exists
    mock_snap.to_dict.return_value = doc_data or {}
    mock_doc_ref.get.return_value = mock_snap

    # Snapshot del usuario en Firestore (para authz)
    mock_user_snap = MagicMock()
    mock_user_snap.exists = True
    mock_user_snap.to_dict.return_value = FIRESTORE_USER_DOC
    mock_user_ref = MagicMock()
    mock_user_ref.get.return_value = mock_user_snap

    def collection_side_effect(name):
        col = MagicMock()
        if name == "users":
            col.document.return_value = mock_user_ref
        else:
            col.document.return_value = mock_doc_ref
        return col

    mock_db.collection.side_effect = collection_side_effect
    return mock_db, mock_doc_ref


# ---------------------------------------------------------------------------
# Context manager para monkeypatching completo de Firebase
# ---------------------------------------------------------------------------
from contextlib import contextmanager

@contextmanager
def firebase_mocked(mock_db):
    """Parchea db y auth_client en todos los modulos que los usan."""
    with (
        patch("app.firebase_config.db",              mock_db),
        patch("app.routes.artefacto_360_routes.db",  mock_db),
        patch("app.deps.authz.db",                   mock_db),
        patch("app.firebase_config.auth_client")     as mock_auth,
        patch("app.deps.authz.auth_client",          mock_auth),
    ):
        mock_auth.verify_id_token.return_value = DECODED_TOKEN
        yield mock_auth


# ---------------------------------------------------------------------------
# Tests: POST /asistencia_actividades
# ---------------------------------------------------------------------------
class TestPostAsistenciaActividades:

    def test_01_crea_10_personas_metricas_correctas(self):
        """POST con 10 personas devuelve metricas correctas (70% asistencia)."""
        mock_db, mock_doc_ref = make_firestore_mock()

        with firebase_mocked(mock_db):
            from app.main import app
            client = TestClient(app)
            r = client.post(
                "/asistencia_actividades",
                json={"actividad_id": TEST_ACTIVIDAD_ID,
                      "personal_asignado": PERSONAL_10},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 200, r.text
        data = r.json()
        d    = data["data"]

        assert data["status"]        == "success"
        assert d["actividad_id"]     == TEST_ACTIVIDAD_ID
        assert d["total_personal"]   == 10
        assert d["asistentes"]       == ASISTENTES_ESPERADOS
        assert d["ausentes"]         == AUSENTES_ESPERADOS
        assert d["asistencia_general"] == ASISTENCIA_GENERAL

        # Firestore debe usar set(), nunca update()
        mock_doc_ref.set.assert_called_once()
        mock_doc_ref.update.assert_not_called()

    def test_02_post_sobre_existente_usa_set_no_update(self):
        """POST sobre actividad ya existente: usa set() = REEMPLAZO, no update()."""
        mock_db, mock_doc_ref = make_firestore_mock()

        with firebase_mocked(mock_db):
            from app.main import app
            client = TestClient(app)

            # Primera llamada (10 personas)
            client.post(
                "/asistencia_actividades",
                json={"actividad_id": TEST_ACTIVIDAD_ID,
                      "personal_asignado": PERSONAL_10},
                headers=AUTH_HEADERS,
            )
            # Segunda llamada con solo 3 personas -> debe REEMPLAZAR
            r = client.post(
                "/asistencia_actividades",
                json={"actividad_id": TEST_ACTIVIDAD_ID,
                      "personal_asignado": PERSONAL_10[:3]},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 200
        assert r.json()["data"]["total_personal"] == 3, (
            "POST debe REEMPLAZAR el documento (set()), no acumular"
        )
        assert mock_doc_ref.set.call_count == 2
        mock_doc_ref.update.assert_not_called()

    def test_03_post_sin_actividad_id_retorna_422(self):
        """POST sin actividad_id es rechazado por validacion de Pydantic."""
        mock_db, _ = make_firestore_mock()

        with firebase_mocked(mock_db):
            from app.main import app
            r = TestClient(app).post(
                "/asistencia_actividades",
                json={"personal_asignado": PERSONAL_10},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Tests: GET /asistencia_actividades
# ---------------------------------------------------------------------------
class TestGetAsistenciaActividades:

    def _doc_existente(self):
        return {
            "actividad_id":      TEST_ACTIVIDAD_ID,
            "personal_asignado": copy.deepcopy(PERSONAL_10),
            "total_personal":    10,
            "asistentes":        ASISTENTES_ESPERADOS,
            "ausentes":          AUSENTES_ESPERADOS,
            "asistencia_general": ASISTENCIA_GENERAL,
            "marca_temporal":    "2026-04-21T10:00:00",
        }

    def test_04_get_retorna_documento_con_metricas(self):
        """GET ?actividad_id=X retorna el documento y recalcula metricas."""
        mock_db, _ = make_firestore_mock(
            doc_data=self._doc_existente(), doc_exists=True
        )

        with firebase_mocked(mock_db):
            from app.main import app
            r = TestClient(app).get(
                "/asistencia_actividades",
                params={"actividad_id": TEST_ACTIVIDAD_ID},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "success"
        assert body["data"]["actividad_id"] == TEST_ACTIVIDAD_ID
        assert len(body["data"]["personal_asignado"]) == 10

        m = body["metricas"]
        assert m["total_personal"]     == 10
        assert m["asistentes"]         == ASISTENTES_ESPERADOS
        assert m["ausentes"]           == AUSENTES_ESPERADOS
        assert m["asistencia_general"] == ASISTENCIA_GENERAL

    def test_05_get_solo_alertas_filtra_personal(self):
        """GET ?solo_alertas=true retorna solo integrantes con alerta != None."""
        mock_db, _ = make_firestore_mock(
            doc_data=self._doc_existente(), doc_exists=True
        )

        with firebase_mocked(mock_db):
            from app.main import app
            r = TestClient(app).get(
                "/asistencia_actividades",
                params={"actividad_id": TEST_ACTIVIDAD_ID, "solo_alertas": "true"},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 200
        personal = r.json()["data"]["personal_asignado"]
        emails   = [p["email"] for p in personal]

        # Fernanda e Ivan tienen alerta en los datos originales
        assert "fernanda.lozano@dagma.gov.co" in emails
        assert "ivan.castillo@dagma.gov.co"   in emails
        assert all(p.get("alerta") for p in personal), (
            "Todos los devueltos deben tener alerta"
        )

    def test_06_get_actividad_inexistente_retorna_404(self):
        """GET con actividad_id que no existe en Firestore -> 404."""
        mock_db, _ = make_firestore_mock(doc_exists=False)

        with firebase_mocked(mock_db):
            from app.main import app
            r = TestClient(app).get(
                "/asistencia_actividades",
                params={"actividad_id": "no-existe-xyz"},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 404

    def test_07_get_sin_actividad_id_retorna_422(self):
        """GET sin actividad_id -> 422 (campo requerido)."""
        mock_db, _ = make_firestore_mock()

        with firebase_mocked(mock_db):
            from app.main import app
            r = TestClient(app).get(
                "/asistencia_actividades",
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Tests: PATCH /asistencia_actividades/{actividad_id}
# ---------------------------------------------------------------------------
class TestPatchAsistenciaActividades:

    def _doc_existente(self):
        return {
            "actividad_id":      TEST_ACTIVIDAD_ID,
            "personal_asignado": copy.deepcopy(PERSONAL_10),
            "total_personal":    10,
            "asistentes":        ASISTENTES_ESPERADOS,
            "ausentes":          AUSENTES_ESPERADOS,
            "asistencia_general": ASISTENCIA_GENERAL,
            "marca_temporal":    "2026-04-21T10:00:00",
        }

    def test_08_patch_actualiza_2_personas_recalcula_metricas(self):
        """PATCH: Diana False->True + alerta a Carlos => 8 asistentes / 80%."""
        mock_db, mock_doc_ref = make_firestore_mock(
            doc_data=self._doc_existente(), doc_exists=True
        )

        with firebase_mocked(mock_db):
            from app.main import app
            r = TestClient(app).patch(
                f"/asistencia_actividades/{TEST_ACTIVIDAD_ID}",
                json={"personal": [
                    {"email": "diana.mora@dagma.gov.co",
                     "validacion": True, "observacion": "Se recupero"},
                    {"email": "carlos.rios@dagma.gov.co",
                     "alerta": "llamado_atencion"},
                ]},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 200, r.text
        body = r.json()

        assert body["status"] == "success"
        assert set(body["actualizados"]) == {
            "diana.mora@dagma.gov.co", "carlos.rios@dagma.gov.co"
        }

        m = body["metricas"]
        assert m["asistentes"]         == 8,    f"Esperaba 8, obtuvo {m['asistentes']}"
        assert m["ausentes"]           == 2,    f"Esperaba 2, obtuvo {m['ausentes']}"
        assert m["asistencia_general"] == 80.0, f"Esperaba 80.0, obtuvo {m['asistencia_general']}"

        # Siguen siendo 10 integrantes
        assert len(body["data"]["personal_asignado"]) == 10

        # Verificar campos actualizados
        personal = {p["email"]: p for p in body["data"]["personal_asignado"]}
        assert personal["diana.mora@dagma.gov.co"]["validacion"]  is True
        assert personal["diana.mora@dagma.gov.co"]["observacion"] == "Se recupero"
        assert personal["carlos.rios@dagma.gov.co"]["alerta"]     == "llamado_atencion"
        assert personal["carlos.rios@dagma.gov.co"]["validacion"] is True  # no cambio

        # Firestore debe usar update(), nunca set()
        mock_doc_ref.update.assert_called_once()
        mock_doc_ref.set.assert_not_called()

    def test_09_patch_solo_campos_enviados_no_toca_resto(self):
        """PATCH con solo observacion no modifica validacion ni alerta."""
        mock_db, _ = make_firestore_mock(
            doc_data=self._doc_existente(), doc_exists=True
        )

        with firebase_mocked(mock_db):
            from app.main import app
            r = TestClient(app).patch(
                f"/asistencia_actividades/{TEST_ACTIVIDAD_ID}",
                json={"personal": [
                    {"email": "gustavo.pedraza@dagma.gov.co",
                     "observacion": "Actualizado: llego 20 min tarde"},
                ]},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 200
        personal = {p["email"]: p for p in r.json()["data"]["personal_asignado"]}
        gustavo  = personal["gustavo.pedraza@dagma.gov.co"]

        assert gustavo["observacion"] == "Actualizado: llego 20 min tarde"
        assert gustavo["validacion"]  is True  # no cambio
        assert gustavo["alerta"]      is None  # no cambio

        # Metricas no cambian (no se modifico validacion)
        assert r.json()["metricas"]["asistencia_general"] == ASISTENCIA_GENERAL

    def test_10_patch_email_inexistente_retorna_422(self):
        """PATCH con email que no existe en personal_asignado -> 422."""
        mock_db, _ = make_firestore_mock(
            doc_data=self._doc_existente(), doc_exists=True
        )

        with firebase_mocked(mock_db):
            from app.main import app
            r = TestClient(app).patch(
                f"/asistencia_actividades/{TEST_ACTIVIDAD_ID}",
                json={"personal": [
                    {"email": "noexiste@dagma.gov.co", "validacion": True}
                ]},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 422

    def test_11_patch_actividad_inexistente_retorna_404(self):
        """PATCH sobre actividad que no existe en Firestore -> 404."""
        mock_db, _ = make_firestore_mock(doc_exists=False)

        with firebase_mocked(mock_db):
            from app.main import app
            r = TestClient(app).patch(
                "/asistencia_actividades/no-existe",
                json={"personal": [
                    {"email": "ana.torres@dagma.gov.co", "validacion": True}
                ]},
                headers=AUTH_HEADERS,
            )

        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Modo script: lanza pytest
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short", "--no-header"],
        capture_output=False,
    )
    sys.exit(result.returncode)
