"""
Tests para los endpoints unificados de reportes de intervencion.
Coleccion unificada: reportes_intervenciones (discriminador: campo "grupo").
Ejecucion: pytest test_unified_endpoints.py -v
"""
import pytest
import io
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch

DECODED_TOKEN = {
    "uid": "test-admin-uid",
    "email": "admin@dagma.gov.co",
    "role": "administrador",
    "grupo": None,
}
AUTH_HEADERS = {"Authorization": "Bearer mock-token-admin"}
GRUPOS_VALIDOS = ["cuadrilla", "vivero", "gobernanza", "ecosistemas", "umata"]

from app.main import app
client = TestClient(app)


@pytest.fixture
def mock_firebase_db():
    with patch("app.routes.artefacto_360_routes.db") as mock_db:
        mc = Mock()
        dr = Mock()
        dr.set = Mock()
        snap = Mock()
        snap.exists = False
        mc.document.return_value = dr
        dr.get.return_value = snap
        mc.stream.return_value = []
        mc.where.return_value = mc
        mock_db.collection.return_value = mc
        yield mock_db


@pytest.fixture
def mock_s3():
    with patch("app.routes.artefacto_360_routes.get_s3_client") as mock_get:
        mc = Mock()
        mc.upload_fileobj = Mock()
        mc.generate_presigned_url.return_value = "https://fake-presigned-url.com"
        mc.head_object.return_value = {"ContentType": "image/jpeg", "ContentLength": 1234}
        mock_get.return_value = mc
        yield mc


@pytest.fixture
def mock_auth():
    with patch("app.deps.authz.auth_client") as mock_ac:
        mock_ac.verify_id_token.return_value = DECODED_TOKEN
        yield mock_ac


class TestGruposConfig:

    def test_grupos_config_import(self):
        from app.routes.artefacto_360_routes import GRUPOS_CONFIG, GRUPOS_VALIDOS
        assert len(GRUPOS_CONFIG) == 5
        assert set(GRUPOS_VALIDOS) == {"cuadrilla", "vivero", "gobernanza", "ecosistemas", "umata"}

    def test_grupo_config_has_required_fields(self):
        from app.routes.artefacto_360_routes import GRUPOS_CONFIG
        for grupo, config in GRUPOS_CONFIG.items():
            assert "display_name" in config, f"Missing display_name for {grupo}"
            assert "s3_prefix" in config, f"Missing s3_prefix for {grupo}"
            assert "collection" not in config, f"collection ya no debe estar en GRUPOS_CONFIG ({grupo})"

    def test_get_grupo_config_valid(self):
        from app.routes.artefacto_360_routes import get_grupo_config, COLLECTION_REPORTES_INTERVENCIONES
        config = get_grupo_config("cuadrilla")
        assert config["display_name"] == "Cuadrilla"
        assert config["s3_prefix"] == "cuadrilla"
        assert COLLECTION_REPORTES_INTERVENCIONES == "reportes_intervenciones"

    def test_get_grupo_config_invalid(self):
        from app.routes.artefacto_360_routes import get_grupo_config
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            get_grupo_config("invalido")
        assert exc.value.status_code == 404

    def test_collection_constant_value(self):
        from app.routes.artefacto_360_routes import COLLECTION_REPORTES_INTERVENCIONES
        assert COLLECTION_REPORTES_INTERVENCIONES == "reportes_intervenciones"


class TestValidateGrupoSpecificFields:

    def test_cuadrilla_valid_arboles(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        result = validate_grupo_specific_fields("cuadrilla", '[{"especie": "Ceiba", "cantidad": 5}]', None, None, None)
        assert len(result["arboles"]) == 1
        assert result["arboles"][0]["especie"] == "Ceiba"

    def test_cuadrilla_invalid_arboles(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            validate_grupo_specific_fields("cuadrilla", "invalid json", None, None, None)
        assert exc.value.status_code == 400

    def test_cuadrilla_no_arboles(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        assert validate_grupo_specific_fields("cuadrilla", None, None, None, None)["arboles"] is None

    def test_vivero_valid_tipos_plantas(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        result = validate_grupo_specific_fields("vivero", None, '{"Guayacan": 10, "Ceiba": 5}', None, None)
        assert result["cantidad_total_plantas"] == 15

    def test_vivero_invalid_tipos_plantas_json(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            validate_grupo_specific_fields("vivero", None, "not json", None, None)
        assert exc.value.status_code == 400

    def test_vivero_invalid_tipos_plantas_not_dict(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            validate_grupo_specific_fields("vivero", None, '[1, 2, 3]', None, None)
        assert exc.value.status_code == 400

    def test_vivero_negative_cantidad(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            validate_grupo_specific_fields("vivero", None, '{"A": -5}', None, None)
        assert exc.value.status_code == 400

    def test_gobernanza_fields(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        assert validate_grupo_specific_fields("gobernanza", None, None, 42, None) == {"unidades_impactadas": 42}

    def test_ecosistemas_fields(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        result = validate_grupo_specific_fields("ecosistemas", None, None, 10, "hectareas")
        assert result["unidad_medida"] == "hectareas"
        assert result["unidades_impactadas"] == 10

    def test_umata_fields(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        assert validate_grupo_specific_fields("umata", None, None, 100, None) == {"unidades_impactadas": 100}


class TestUnifiedPostEndpoint:

    def test_post_grupo_invalido(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/invalido/reporte_intervencion", data={"tipo_intervencion": "T"}, headers=AUTH_HEADERS)
        assert r.status_code == 404

    @pytest.mark.parametrize("grupo", GRUPOS_VALIDOS)
    def test_post_reporte_basico_todos_los_grupos(self, grupo, mock_firebase_db, mock_s3, mock_auth):
        r = client.post(f"/grupos/{grupo}/reporte_intervencion", data={"tipo_intervencion": "Mant"}, headers=AUTH_HEADERS)
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True

    def test_post_cuadrilla_con_arboles(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/cuadrilla/reporte_intervencion",
            data={"tipo_intervencion": "Poda", "arboles_data": '[{"especie": "Ceiba", "cantidad": 3}]'},
            headers=AUTH_HEADERS)
        assert r.status_code == 200
        saved = mock_firebase_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert saved["arboles"][0]["especie"] == "Ceiba"
        assert saved["grupo"] == "cuadrilla"

    def test_post_cuadrilla_arboles_invalidos(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/cuadrilla/reporte_intervencion",
            data={"tipo_intervencion": "Poda", "arboles_data": "no-es-json"},
            headers=AUTH_HEADERS)
        assert r.status_code == 400

    def test_post_vivero_con_tipos_plantas(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/vivero/reporte_intervencion",
            data={"tipo_intervencion": "Siembra", "tipos_plantas": '{"Guayacan": 10, "Ceiba": 5}'},
            headers=AUTH_HEADERS)
        assert r.status_code == 200
        saved = mock_firebase_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert saved["cantidad_total_plantas"] == 15
        assert saved["grupo"] == "vivero"

    def test_post_ecosistemas_con_unidad_medida(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/ecosistemas/reporte_intervencion",
            data={"tipo_intervencion": "Monitoreo", "unidad_medida": "individuos", "unidades_impactadas": "25"},
            headers=AUTH_HEADERS)
        assert r.status_code == 200
        saved = mock_firebase_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert saved["unidad_medida"] == "individuos"
        assert saved["unidades_impactadas"] == 25

    def test_post_con_coordenadas_point(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/cuadrilla/reporte_intervencion",
            data={"tipo_intervencion": "Insp", "coordinates_type": "Point", "coordinates_data": "[-76.5225, 3.4516]"},
            headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["coordinates"]["type"] == "Point"

    def test_post_geometria_invalida(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/cuadrilla/reporte_intervencion",
            data={"tipo_intervencion": "T", "coordinates_type": "InvalidType", "coordinates_data": "[-76.5, 3.4]"},
            headers=AUTH_HEADERS)
        assert r.status_code == 400
        assert "geomet" in r.json()["detail"].lower()

    def test_post_con_fotos(self, mock_firebase_db, mock_s3, mock_auth):
        files = [
            ("photos", ("foto1.jpg", io.BytesIO(b"fake"), "image/jpeg")),
            ("photos", ("foto2.png", io.BytesIO(b"fake"), "image/png")),
        ]
        r = client.post("/grupos/umata/reporte_intervencion",
            data={"tipo_intervencion": "Doc"}, files=files, headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["photos_uploaded"] == 2

    def test_post_firebase_save_collection(self, mock_firebase_db, mock_s3, mock_auth):
        for grupo in GRUPOS_VALIDOS:
            mock_firebase_db.reset_mock()
            mock_firebase_db.collection.return_value.document.return_value.set = Mock()
            mock_firebase_db.collection.return_value.document.return_value.get.return_value = Mock(exists=False)
            mock_firebase_db.collection.return_value.stream.return_value = []
            mock_firebase_db.collection.return_value.where.return_value = mock_firebase_db.collection.return_value
            r = client.post(f"/grupos/{grupo}/reporte_intervencion", data={"tipo_intervencion": "T"}, headers=AUTH_HEADERS)
            assert r.status_code == 200, f"{grupo}: {r.text}"
            mock_firebase_db.collection.assert_called_with("reportes_intervenciones")

    def test_post_grupo_key_prevalece_sobre_form(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/cuadrilla/reporte_intervencion",
            data={"tipo_intervencion": "T", "grupo": "Cuadrilla Verde A"},
            headers=AUTH_HEADERS)
        assert r.status_code == 200
        saved = mock_firebase_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert saved["grupo"] == "cuadrilla"

    def test_post_sin_auth_retorna_401(self):
        r = client.post("/grupos/cuadrilla/reporte_intervencion", data={"tipo_intervencion": "T"})
        assert r.status_code == 401


class TestUnifiedGetEndpoint:

    def test_get_grupo_invalido(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.get("/grupos/invalido/reportes_intervenciones", headers=AUTH_HEADERS)
        assert r.status_code == 404

    @pytest.mark.parametrize("grupo", GRUPOS_VALIDOS)
    def test_get_reportes_vacio(self, grupo, mock_firebase_db, mock_s3, mock_auth):
        r = client.get(f"/grupos/{grupo}/reportes_intervenciones", headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["total"] == 0
        assert data["data"] == []

    def test_get_filtro_id_actividad(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.get("/grupos/cuadrilla/reportes_intervenciones?id_actividad=ACT-123", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["filters"]["id_actividad"] == "ACT-123"

    def test_get_filtro_grupo_retorna_grupo_key(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.get("/grupos/vivero/reportes_intervenciones?grupo=Vivero%20A", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["filters"]["grupo"] == "vivero"

    def test_get_con_id_documento_mismo_grupo(self, mock_firebase_db, mock_s3, mock_auth):
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.id = "doc-001"
        mock_doc.to_dict.return_value = {"id": "doc-001", "tipo_intervencion": "Poda", "timestamp": "2026-01-01", "grupo": "cuadrilla"}
        mock_firebase_db.collection.return_value.document.return_value.get.return_value = mock_doc
        r = client.get("/grupos/cuadrilla/reportes_intervenciones?id=doc-001", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_get_con_id_de_otro_grupo_retorna_vacio(self, mock_firebase_db, mock_s3, mock_auth):
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.id = "doc-vivero"
        mock_doc.to_dict.return_value = {"id": "doc-vivero", "tipo_intervencion": "S", "timestamp": "2026-01-01", "grupo": "vivero"}
        mock_firebase_db.collection.return_value.document.return_value.get.return_value = mock_doc
        r = client.get("/grupos/cuadrilla/reportes_intervenciones?id=doc-vivero", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_get_firebase_collection(self, mock_firebase_db, mock_s3, mock_auth):
        for grupo in GRUPOS_VALIDOS:
            mock_firebase_db.reset_mock()
            mock_firebase_db.collection.return_value.stream.return_value = []
            mock_firebase_db.collection.return_value.where.return_value = mock_firebase_db.collection.return_value
            r = client.get(f"/grupos/{grupo}/reportes_intervenciones", headers=AUTH_HEADERS)
            assert r.status_code == 200, f"{grupo}: {r.text}"
            mock_firebase_db.collection.assert_called_with("reportes_intervenciones")

    def test_get_sin_auth_retorna_401(self):
        r = client.get("/grupos/cuadrilla/reportes_intervenciones")
        assert r.status_code == 401


class TestLegacyRoutes:

    @pytest.mark.parametrize("grupo", GRUPOS_VALIDOS)
    def test_legacy_post_routes(self, grupo, mock_firebase_db, mock_s3):
        r = client.post(f"/grupo-{grupo}/reporte_intervencion", data={"tipo_intervencion": "Test"})
        assert r.status_code == 200
        assert r.json()["success"] is True

    @pytest.mark.parametrize("grupo", GRUPOS_VALIDOS)
    def test_legacy_get_routes(self, grupo, mock_firebase_db, mock_s3):
        r = client.get(f"/grupo-{grupo}/reportes_intervenciones")
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert "data" in r.json()

    def test_legacy_cuadrilla_con_arboles(self, mock_firebase_db, mock_s3):
        r = client.post("/grupo-cuadrilla/reporte_intervencion",
            data={"tipo_intervencion": "Poda", "arboles_data": '[{"especie": "Ceiba", "cantidad": 5}]'})
        assert r.status_code == 200
        saved = mock_firebase_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert saved["arboles"][0]["especie"] == "Ceiba"

    def test_legacy_vivero_con_tipos_plantas(self, mock_firebase_db, mock_s3):
        r = client.post("/grupo-vivero/reporte_intervencion",
            data={"tipo_intervencion": "Siembra", "tipos_plantas": '{"Saman": 8}'})
        assert r.status_code == 200
        saved = mock_firebase_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert saved["tipos_plantas"] == {"Saman": 8}
        assert saved["cantidad_total_plantas"] == 8

    def test_legacy_ecosistemas_con_unidad_medida(self, mock_firebase_db, mock_s3):
        r = client.post("/grupo-ecosistemas/reporte_intervencion",
            data={"tipo_intervencion": "Monitoreo", "unidad_medida": "individuos", "unidades_impactadas": "25"})
        assert r.status_code == 200
        saved = mock_firebase_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert saved["unidad_medida"] == "individuos"
        assert saved["unidades_impactadas"] == 25

    def test_legacy_guarda_grupo_key_canonico(self, mock_firebase_db, mock_s3):
        r = client.post("/grupo-gobernanza/reporte_intervencion",
            data={"tipo_intervencion": "Cap", "grupo": "Gobernanza Ambiental 1"})
        assert r.status_code == 200
        saved = mock_firebase_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert saved["grupo"] == "gobernanza"

    def test_legacy_y_unified_misma_coleccion(self, mock_firebase_db, mock_s3, mock_auth):
        mock_firebase_db.reset_mock()
        mock_firebase_db.collection.return_value.document.return_value.set = Mock()
        mock_firebase_db.collection.return_value.document.return_value.get.return_value = Mock(exists=False)
        mock_firebase_db.collection.return_value.stream.return_value = []
        mock_firebase_db.collection.return_value.where.return_value = mock_firebase_db.collection.return_value
        client.post("/grupos/gobernanza/reporte_intervencion", data={"tipo_intervencion": "A"}, headers=AUTH_HEADERS)
        unified_col = mock_firebase_db.collection.call_args[0][0]

        mock_firebase_db.reset_mock()
        mock_firebase_db.collection.return_value.document.return_value.set = Mock()
        client.post("/grupo-gobernanza/reporte_intervencion", data={"tipo_intervencion": "B"})
        legacy_col = mock_firebase_db.collection.call_args[0][0]

        assert unified_col == "reportes_intervenciones"
        assert legacy_col == "reportes_intervenciones"


class TestResponseStructure:

    def test_post_response_fields(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/cuadrilla/reporte_intervencion",
            data={"tipo_intervencion": "Test"}, headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()
        for field in ["success", "id", "message", "coordinates", "photosUrl", "photos_uploaded", "timestamp"]:
            assert field in data, f"Missing field: {field}"

    def test_get_response_fields(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.get("/grupos/vivero/reportes_intervenciones", headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()
        for field in ["success", "total", "data", "filters", "timestamp"]:
            assert field in data, f"Missing field: {field}"
        assert isinstance(data["data"], list)

    def test_get_filters_returned(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.get("/grupos/umata/reportes_intervenciones?id_actividad=ACT-1&grupo=G1", headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["filters"]["id_actividad"] == "ACT-1"
        assert data["filters"]["grupo"] == "umata"

    def test_post_documento_campos_minimos(self, mock_firebase_db, mock_s3, mock_auth):
        r = client.post("/grupos/gobernanza/reporte_intervencion",
            data={"tipo_intervencion": "Reunion", "id_actividad": "ACT-001", "unidades_impactadas": "50"},
            headers=AUTH_HEADERS)
        assert r.status_code == 200
        saved = mock_firebase_db.collection.return_value.document.return_value.set.call_args[0][0]
        for field in ["id", "grupo", "tipo_intervencion", "timestamp", "registrado_por"]:
            assert field in saved, f"Campo faltante en Firestore: {field}"
        assert saved["grupo"] == "gobernanza"
        assert saved["unidades_impactadas"] == 50