"""
Tests para los endpoints unificados de reportes de intervención.
Verifica que tanto las rutas nuevas (/grupos/{grupo}/...) como las legacy
(/grupo-{name}/...) funcionan correctamente con el handler unificado.
"""
import pytest
import json
import io
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, MagicMock

from app.main import app

client = TestClient(app)

GRUPOS_VALIDOS = ["cuadrilla", "vivero", "gobernanza", "ecosistemas", "umata"]


# ==================== FIXTURES ====================#

@pytest.fixture
def mock_firebase_db():
    """Mock de Firebase Firestore - patches at route module level"""
    with patch('app.routes.artefacto_360_routes.db') as mock_db:
        mock_collection = Mock()
        mock_doc_ref = Mock()
        mock_doc_ref.set = Mock()

        mock_doc_snapshot = Mock()
        mock_doc_snapshot.exists = False

        mock_collection.document.return_value = mock_doc_ref
        mock_doc_ref.get.return_value = mock_doc_snapshot
        mock_collection.stream.return_value = []
        mock_collection.where.return_value = mock_collection
        mock_db.collection.return_value = mock_collection
        yield mock_db


@pytest.fixture
def mock_s3():
    """Mock de S3 client"""
    with patch('app.routes.artefacto_360_routes.get_s3_client') as mock_get_s3:
        mock_client = Mock()
        mock_client.upload_fileobj = Mock()
        mock_client.generate_presigned_url.return_value = "https://fake-presigned-url.com"
        mock_client.head_object.return_value = {
            "ContentType": "image/jpeg",
            "ContentLength": 1234,
        }
        mock_get_s3.return_value = mock_client
        yield mock_client


# ==================== TEST: GRUPOS_CONFIG ====================#

class TestGruposConfig:
    """Verificar que la configuración de grupos es correcta"""

    def test_grupos_config_import(self):
        from app.routes.artefacto_360_routes import GRUPOS_CONFIG, GRUPOS_VALIDOS
        assert len(GRUPOS_CONFIG) == 5
        assert set(GRUPOS_VALIDOS) == {"cuadrilla", "vivero", "gobernanza", "ecosistemas", "umata"}

    def test_grupo_config_has_required_fields(self):
        from app.routes.artefacto_360_routes import GRUPOS_CONFIG
        for grupo, config in GRUPOS_CONFIG.items():
            assert "collection" in config, f"Missing 'collection' for {grupo}"
            assert "display_name" in config, f"Missing 'display_name' for {grupo}"
            assert "s3_prefix" in config, f"Missing 's3_prefix' for {grupo}"
            assert config["collection"].startswith("reportes_intervenciones_grupo_")

    def test_get_grupo_config_valid(self):
        from app.routes.artefacto_360_routes import get_grupo_config
        config = get_grupo_config("cuadrilla")
        assert config["collection"] == "reportes_intervenciones_grupo_cuadrilla"
        assert config["display_name"] == "Cuadrilla"

    def test_get_grupo_config_invalid(self):
        from app.routes.artefacto_360_routes import get_grupo_config
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_grupo_config("invalido")
        assert exc_info.value.status_code == 404
        assert "invalido" in str(exc_info.value.detail)


# ==================== TEST: validate_grupo_specific_fields ====================#

class TestValidateGrupoSpecificFields:

    def test_cuadrilla_valid_arboles(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        result = validate_grupo_specific_fields(
            "cuadrilla",
            arboles_data='[{"especie": "Ceiba", "cantidad": 5}]',
            tipos_plantas=None,
            unidades_impactadas=None,
            unidad_medida=None,
        )
        assert "arboles" in result
        assert len(result["arboles"]) == 1
        assert result["arboles"][0]["especie"] == "Ceiba"
        assert result["arboles"][0]["cantidad"] == 5

    def test_cuadrilla_invalid_arboles(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_grupo_specific_fields(
                "cuadrilla",
                arboles_data="invalid json",
                tipos_plantas=None,
                unidades_impactadas=None,
                unidad_medida=None,
            )
        assert exc_info.value.status_code == 400

    def test_cuadrilla_no_arboles(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        result = validate_grupo_specific_fields("cuadrilla", None, None, None, None)
        assert result["arboles"] is None

    def test_vivero_valid_tipos_plantas(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        result = validate_grupo_specific_fields(
            "vivero",
            arboles_data=None,
            tipos_plantas='{"Guayacán": 10, "Ceiba": 5}',
            unidades_impactadas=None,
            unidad_medida=None,
        )
        assert result["tipos_plantas"] == {"Guayacán": 10, "Ceiba": 5}
        assert result["cantidad_total_plantas"] == 15

    def test_vivero_invalid_tipos_plantas_json(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_grupo_specific_fields("vivero", None, "not json", None, None)
        assert exc_info.value.status_code == 400

    def test_vivero_invalid_tipos_plantas_not_dict(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_grupo_specific_fields("vivero", None, '[1, 2, 3]', None, None)
        assert exc_info.value.status_code == 400

    def test_vivero_negative_cantidad(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_grupo_specific_fields("vivero", None, '{"A": -5}', None, None)
        assert exc_info.value.status_code == 400

    def test_gobernanza_fields(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        result = validate_grupo_specific_fields("gobernanza", None, None, 42, None)
        assert result == {"unidades_impactadas": 42}

    def test_ecosistemas_fields(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        result = validate_grupo_specific_fields("ecosistemas", None, None, 10, "hectáreas")
        assert result == {"unidad_medida": "hectáreas", "unidades_impactadas": 10}

    def test_umata_fields(self):
        from app.routes.artefacto_360_routes import validate_grupo_specific_fields
        result = validate_grupo_specific_fields("umata", None, None, 5, None)
        assert result == {"unidades_impactadas": 5}


# ==================== TEST: Unified POST Endpoint ====================#

class TestUnifiedPostEndpoint:
    """Tests para POST /grupos/{grupo}/reporte_intervencion"""

    def test_post_grupo_invalido(self):
        """Grupo inválido debe retornar 404"""
        response = client.post(
            "/grupos/invalido/reporte_intervencion",
            data={"tipo_intervencion": "Test"},
        )
        assert response.status_code == 404
        assert "invalido" in response.json()["detail"]

    @pytest.mark.parametrize("grupo", GRUPOS_VALIDOS)
    def test_post_reporte_basico_todos_los_grupos(self, grupo, mock_firebase_db, mock_s3):
        """POST básico sin fotos funciona para cada grupo"""
        form_data = {
            "tipo_intervencion": "Mantenimiento",
            "descripcion_intervencion": "Test",
            "registrado_por": "Tester",
            "grupo": grupo.capitalize(),
        }
        response = client.post(f"/grupos/{grupo}/reporte_intervencion", data=form_data)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "id" in data
        assert grupo.capitalize() in data["message"] or grupo.upper() in data["message"]

    def test_post_cuadrilla_con_arboles(self, mock_firebase_db, mock_s3):
        """POST cuadrilla con arboles_data"""
        form_data = {
            "tipo_intervencion": "Poda",
            "arboles_data": '[{"especie": "Ceiba", "cantidad": 5}, {"especie": "Guayacán", "cantidad": 3}]',
        }
        response = client.post("/grupos/cuadrilla/reporte_intervencion", data=form_data)
        assert response.status_code == 200
        # Verificar que se guardó con los árboles
        call_args = mock_firebase_db.collection.return_value.document.return_value.set.call_args
        saved_data = call_args[0][0]
        assert saved_data["arboles"] is not None
        assert len(saved_data["arboles"]) == 2

    def test_post_cuadrilla_arboles_invalidos(self, mock_firebase_db, mock_s3):
        """POST cuadrilla con arboles_data inválidos da 400"""
        form_data = {
            "tipo_intervencion": "Poda",
            "arboles_data": "not valid json",
        }
        response = client.post("/grupos/cuadrilla/reporte_intervencion", data=form_data)
        assert response.status_code == 400
        assert "arboles_data" in response.json()["detail"]

    def test_post_vivero_con_tipos_plantas(self, mock_firebase_db, mock_s3):
        """POST vivero con tipos_plantas"""
        form_data = {
            "tipo_intervencion": "Siembra",
            "tipos_plantas": '{"Guayacán": 10, "Ceiba": 5}',
        }
        response = client.post("/grupos/vivero/reporte_intervencion", data=form_data)
        assert response.status_code == 200
        call_args = mock_firebase_db.collection.return_value.document.return_value.set.call_args
        saved_data = call_args[0][0]
        assert saved_data["tipos_plantas"] == {"Guayacán": 10, "Ceiba": 5}
        assert saved_data["cantidad_total_plantas"] == 15

    def test_post_ecosistemas_con_unidad_medida(self, mock_firebase_db, mock_s3):
        """POST ecosistemas con unidad_medida + unidades_impactadas"""
        form_data = {
            "tipo_intervencion": "Restauración",
            "unidad_medida": "hectáreas",
            "unidades_impactadas": "10",
        }
        response = client.post("/grupos/ecosistemas/reporte_intervencion", data=form_data)
        assert response.status_code == 200
        call_args = mock_firebase_db.collection.return_value.document.return_value.set.call_args
        saved_data = call_args[0][0]
        assert saved_data["unidad_medida"] == "hectáreas"
        assert saved_data["unidades_impactadas"] == 10

    def test_post_con_coordenadas_point(self, mock_firebase_db, mock_s3):
        """POST con coordenadas de tipo Point"""
        form_data = {
            "tipo_intervencion": "Test",
            "coordinates_type": "Point",
            "coordinates_data": "[-76.5225, 3.4516]",
        }
        response = client.post("/grupos/gobernanza/reporte_intervencion", data=form_data)
        assert response.status_code == 200
        data = response.json()
        assert data["coordinates"] is not None
        assert data["coordinates"]["type"] == "Point"

    def test_post_geometria_invalida(self, mock_firebase_db, mock_s3):
        """POST con tipo de geometría inválido da 400"""
        form_data = {
            "tipo_intervencion": "Test",
            "coordinates_type": "InvalidType",
            "coordinates_data": "[-76.5225, 3.4516]",
        }
        response = client.post("/grupos/cuadrilla/reporte_intervencion", data=form_data)
        assert response.status_code == 400
        assert "geometría" in response.json()["detail"].lower()

    def test_post_con_fotos(self, mock_firebase_db, mock_s3):
        """POST con archivos de fotos"""
        form_data = {"tipo_intervencion": "Test"}
        files = [
            ("photos", ("foto1.jpg", io.BytesIO(b"fake-jpeg-content"), "image/jpeg")),
            ("photos", ("foto2.png", io.BytesIO(b"fake-png-content"), "image/png")),
        ]
        response = client.post(
            "/grupos/umata/reporte_intervencion", data=form_data, files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["photos_uploaded"] == 2

    def test_post_firebase_save_collection(self, mock_firebase_db, mock_s3):
        """Verificar que se guarda en la colección correcta de Firebase"""
        for grupo, expected_collection in [
            ("cuadrilla", "reportes_intervenciones_grupo_cuadrilla"),
            ("vivero", "reportes_intervenciones_grupo_vivero"),
            ("gobernanza", "reportes_intervenciones_grupo_gobernanza"),
            ("ecosistemas", "reportes_intervenciones_grupo_ecosistemas"),
            ("umata", "reportes_intervenciones_grupo_umata"),
        ]:
            mock_firebase_db.reset_mock()
            response = client.post(
                f"/grupos/{grupo}/reporte_intervencion",
                data={"tipo_intervencion": "Test"},
            )
            assert response.status_code == 200
            mock_firebase_db.collection.assert_called_with(expected_collection)


# ==================== TEST: Unified GET Endpoint ====================#

class TestUnifiedGetEndpoint:
    """Tests para GET /grupos/{grupo}/reportes_intervenciones"""

    def test_get_grupo_invalido(self):
        """Grupo inválido debe retornar 404"""
        response = client.get("/grupos/invalido/reportes_intervenciones")
        assert response.status_code == 404

    @pytest.mark.parametrize("grupo", GRUPOS_VALIDOS)
    def test_get_reportes_vacio(self, grupo, mock_firebase_db, mock_s3):
        """GET retorna lista vacía cuando no hay reportes"""
        response = client.get(f"/grupos/{grupo}/reportes_intervenciones")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["total"] == 0
        assert data["data"] == []

    def test_get_reportes_con_filtro_id_actividad(self, mock_firebase_db, mock_s3):
        """GET con filtro id_actividad"""
        response = client.get(
            "/grupos/cuadrilla/reportes_intervenciones?id_actividad=ACT-123"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filters"]["id_actividad"] == "ACT-123"

    def test_get_reportes_con_filtro_grupo(self, mock_firebase_db, mock_s3):
        """GET con filtro grupo"""
        response = client.get(
            "/grupos/vivero/reportes_intervenciones?grupo=Vivero A"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filters"]["grupo"] == "Vivero A"

    def test_get_con_id_documento(self, mock_firebase_db, mock_s3):
        """GET con id específico busca por documento"""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.id = "test-doc-id"
        mock_doc.to_dict.return_value = {
            "id": "test-doc-id",
            "tipo_intervencion": "Poda",
            "timestamp": "2026-01-01T00:00:00",
        }
        mock_firebase_db.collection.return_value.document.return_value.get.return_value = mock_doc

        response = client.get(
            "/grupos/cuadrilla/reportes_intervenciones?id=test-doc-id"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["id"] == "test-doc-id"

    def test_get_firebase_collection(self, mock_firebase_db, mock_s3):
        """Verificar que consulta la colección correcta"""
        for grupo, expected_collection in [
            ("cuadrilla", "reportes_intervenciones_grupo_cuadrilla"),
            ("vivero", "reportes_intervenciones_grupo_vivero"),
            ("gobernanza", "reportes_intervenciones_grupo_gobernanza"),
            ("ecosistemas", "reportes_intervenciones_grupo_ecosistemas"),
            ("umata", "reportes_intervenciones_grupo_umata"),
        ]:
            mock_firebase_db.reset_mock()
            response = client.get(f"/grupos/{grupo}/reportes_intervenciones")
            assert response.status_code == 200
            mock_firebase_db.collection.assert_called_with(expected_collection)


# ==================== TEST: Legacy Routes ====================#

class TestLegacyRoutes:
    """Verificar que las rutas legacy siguen funcionando idénticas"""

    @pytest.mark.parametrize("grupo", GRUPOS_VALIDOS)
    def test_legacy_post_routes(self, grupo, mock_firebase_db, mock_s3):
        """POST /grupo-{name}/reporte_intervencion sigue funcionando"""
        response = client.post(
            f"/grupo-{grupo}/reporte_intervencion",
            data={"tipo_intervencion": "Test legacy"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.parametrize("grupo", GRUPOS_VALIDOS)
    def test_legacy_get_routes(self, grupo, mock_firebase_db, mock_s3):
        """GET /grupo-{name}/reportes_intervenciones sigue funcionando"""
        response = client.get(f"/grupo-{grupo}/reportes_intervenciones")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "total" in data

    def test_legacy_cuadrilla_con_arboles(self, mock_firebase_db, mock_s3):
        """Legacy cuadrilla POST con arboles_data"""
        form_data = {
            "tipo_intervencion": "Poda",
            "arboles_data": '[{"especie": "Ceiba", "cantidad": 5}]',
        }
        response = client.post("/grupo-cuadrilla/reporte_intervencion", data=form_data)
        assert response.status_code == 200
        call_args = mock_firebase_db.collection.return_value.document.return_value.set.call_args
        saved_data = call_args[0][0]
        assert saved_data["arboles"] is not None

    def test_legacy_vivero_con_tipos_plantas(self, mock_firebase_db, mock_s3):
        """Legacy vivero POST con tipos_plantas"""
        form_data = {
            "tipo_intervencion": "Siembra",
            "tipos_plantas": '{"Samán": 8}',
        }
        response = client.post("/grupo-vivero/reporte_intervencion", data=form_data)
        assert response.status_code == 200
        call_args = mock_firebase_db.collection.return_value.document.return_value.set.call_args
        saved_data = call_args[0][0]
        assert saved_data["tipos_plantas"] == {"Samán": 8}
        assert saved_data["cantidad_total_plantas"] == 8

    def test_legacy_ecosistemas_con_unidad_medida(self, mock_firebase_db, mock_s3):
        """Legacy ecosistemas POST con unidad_medida + unidades_impactadas"""
        form_data = {
            "tipo_intervencion": "Monitoreo",
            "unidad_medida": "individuos",
            "unidades_impactadas": "25",
        }
        response = client.post("/grupo-ecosistemas/reporte_intervencion", data=form_data)
        assert response.status_code == 200
        call_args = mock_firebase_db.collection.return_value.document.return_value.set.call_args
        saved_data = call_args[0][0]
        assert saved_data["unidad_medida"] == "individuos"
        assert saved_data["unidades_impactadas"] == 25

    def test_legacy_and_unified_same_collection(self, mock_firebase_db, mock_s3):
        """Legacy y unificado escriben en la misma colección"""
        # POST via unified
        mock_firebase_db.reset_mock()
        client.post("/grupos/gobernanza/reporte_intervencion", data={"tipo_intervencion": "A"})
        unified_collection = mock_firebase_db.collection.call_args[0][0]

        # POST via legacy
        mock_firebase_db.reset_mock()
        client.post("/grupo-gobernanza/reporte_intervencion", data={"tipo_intervencion": "B"})
        legacy_collection = mock_firebase_db.collection.call_args[0][0]

        assert unified_collection == legacy_collection == "reportes_intervenciones_grupo_gobernanza"


# ==================== TEST: Response Structure ====================#

class TestResponseStructure:
    """Verificar que la estructura de respuesta es correcta"""

    def test_post_response_fields(self, mock_firebase_db, mock_s3):
        """POST response tiene todos los campos esperados"""
        response = client.post(
            "/grupos/cuadrilla/reporte_intervencion",
            data={"tipo_intervencion": "Test"},
        )
        data = response.json()
        assert "success" in data
        assert "id" in data
        assert "message" in data
        assert "coordinates" in data
        assert "photosUrl" in data
        assert "photos_uploaded" in data
        assert "timestamp" in data

    def test_get_response_fields(self, mock_firebase_db, mock_s3):
        """GET response tiene todos los campos esperados"""
        response = client.get("/grupos/vivero/reportes_intervenciones")
        data = response.json()
        assert "success" in data
        assert "total" in data
        assert "data" in data
        assert "filters" in data
        assert "timestamp" in data
        assert isinstance(data["data"], list)

    def test_get_filters_returned(self, mock_firebase_db, mock_s3):
        """GET retorna los filtros aplicados"""
        response = client.get(
            "/grupos/umata/reportes_intervenciones?id_actividad=ACT-1&grupo=G1"
        )
        data = response.json()
        assert data["filters"]["id_actividad"] == "ACT-1"
        assert data["filters"]["grupo"] == "G1"
