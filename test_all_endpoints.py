"""
Test completo para todos los endpoints de la API Artefacto 360 DAGMA
"""
import pytest
import json
import io
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

# Importar la aplicación
from app.main import app

# Cliente de prueba
client = TestClient(app)


# ==================== FIXTURES ====================#
@pytest.fixture
def mock_firebase_db():
    """Mock de Firebase Firestore"""
    with patch('app.firebase_config.db') as mock_db:
        # Mock de colección y documentos
        mock_collection = Mock()
        mock_doc = Mock()
        mock_doc.to_dict.return_value = {
            'id': 'test-id',
            'name': 'Test Parque',
            'location': 'Test Location'
        }
        mock_doc.id = 'test-id'
        
        mock_collection.stream.return_value = [mock_doc]
        mock_collection.document.return_value = mock_doc
        mock_db.collection.return_value = mock_collection
        yield mock_db


@pytest.fixture
def mock_firebase_auth():
    """Mock de Firebase Auth"""
    with patch('app.firebase_config.auth_client') as mock_auth:
        # Mock de usuario
        mock_user = Mock()
        mock_user.uid = 'test-uid-123'
        mock_user.email = 'test@example.com'
        mock_user.display_name = 'Test User'
        mock_user.email_verified = True
        mock_user.disabled = False
        mock_user.user_metadata = Mock()
        mock_user.user_metadata.creation_timestamp = 1609459200000
        
        # Mock de token decodificado
        mock_decoded_token = {
            'uid': 'test-uid-123',
            'email': 'test@example.com'
        }
        
        mock_auth.verify_id_token.return_value = mock_decoded_token
        mock_auth.get_user.return_value = mock_user
        mock_auth.create_user.return_value = mock_user
        mock_auth.create_custom_token.return_value = b'mock-custom-token'
        mock_auth.list_users.return_value.iterate_all.return_value = [mock_user]
        
        yield mock_auth


@pytest.fixture
def mock_s3_client():
    """Mock de cliente S3"""
    with patch('app.routes.artefacto_360_routes.get_s3_client') as mock_get_client:
        mock_client = Mock()
        mock_client.upload_fileobj.return_value = None
        mock_client.delete_object.return_value = None
        mock_client.list_objects_v2.return_value = {
            'Contents': [
                {'Key': 'reconocimientos/test-id/photo1.jpg'}
            ]
        }
        mock_get_client.return_value = mock_client
        yield mock_client


@pytest.fixture
def valid_auth_token():
    """Token de autenticación válido para pruebas"""
    return "Bearer mock-valid-token-123"


@pytest.fixture
def sample_image_file():
    """Archivo de imagen de muestra"""
    # Crear un archivo de imagen falso
    image_content = b"fake-image-content"
    return ("test_photo.jpg", io.BytesIO(image_content), "image/jpeg")


# ==================== TESTS: DEFAULT ROUTES ====================#
class TestDefaultRoutes:
    """Tests para rutas por defecto"""
    
    def test_root_endpoint(self):
        """Test GET /"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "API Artefacto 360 DAGMA"
        assert data["version"] == "1.0.0"
        assert data["status"] == "active"
        assert data["documentation"] == "/docs"


# ==================== TESTS: GENERAL ROUTES ====================#
class TestGeneralRoutes:
    """Tests para rutas generales"""
    
    def test_ping_endpoint(self):
        """Test GET /ping"""
        response = client.get("/ping")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "Pong" in data["message"]
        assert "timestamp" in data
        assert "utf8_test" in data
    
    def test_cors_test_endpoint(self):
        """Test GET /cors-test"""
        response = client.get("/cors-test")
        assert response.status_code == 200
        data = response.json()
        assert data["cors"] == "enabled"
        assert data["message"] == "CORS configurado correctamente"
    
    def test_cors_test_options(self):
        """Test OPTIONS /cors-test"""
        response = client.options("/cors-test")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "OPTIONS request successful"
    
    def test_utf8_test_endpoint(self):
        """Test GET /test/utf8"""
        response = client.get("/test/utf8")
        assert response.status_code == 200
        data = response.json()
        assert data["test"] == "UTF-8"
        assert "español" in data
        assert "symbols" in data
    
    def test_railway_debug_endpoint(self):
        """Test GET /debug/railway"""
        response = client.get("/debug/railway")
        assert response.status_code == 200
        data = response.json()
        assert "platform" in data
        assert "python_version" in data
        assert "environment" in data
    
    def test_health_check_endpoint(self):
        """Test GET /health"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "checks" in data
        assert data["checks"]["api"] == "ok"


# ==================== TESTS: MONITORING ROUTES ====================#
class TestMonitoringRoutes:
    """Tests para rutas de monitoreo"""
    
    def test_metrics_endpoint(self):
        """Test GET /metrics"""
        response = client.get("/metrics")
        assert response.status_code == 200
        # Verificar que retorna contenido de tipo Prometheus
        assert "text/plain" in response.headers.get("content-type", "")


# ==================== TESTS: FIREBASE ROUTES ====================#
class TestFirebaseRoutes:
    """Tests para rutas de Firebase"""
    
    def test_firebase_status(self, mock_firebase_db):
        """Test GET /firebase/status"""
        # Mock de colecciones
        mock_firebase_db.collections.return_value = [
            Mock(id='collection1'),
            Mock(id='collection2')
        ]
        
        response = client.get("/firebase/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "connected"
        assert data["firestore"] == "available"
        assert data["project_id"] == "dagma-85aad"
    
    def test_firebase_collections(self, mock_firebase_db):
        """Test GET /firebase/collections"""
        # Mock de colecciones
        mock_col = Mock()
        mock_col.id = 'test_collection'
        mock_col.stream.return_value = [Mock(), Mock()]
        mock_firebase_db.collections.return_value = [mock_col]
        
        response = client.get("/firebase/collections")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "collections" in data
    
    def test_firebase_collections_summary(self, mock_firebase_db):
        """Test GET /firebase/collections/summary"""
        # Mock de colecciones
        mock_col = Mock()
        mock_col.stream.return_value = [Mock(), Mock(), Mock()]
        mock_firebase_db.collections.return_value = [mock_col, mock_col]
        
        response = client.get("/firebase/collections/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "total_collections" in data
        assert "total_documents" in data


# ==================== TESTS: ARTEFACTO 360 ROUTES ====================#
class TestArtefacto360Routes:
    """Tests para rutas de Artefacto 360"""
    
    def test_init_parques(self, mock_firebase_db):
        """Test GET /init/parques"""
        response = client.get("/init/parques")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "count" in data
        assert isinstance(data["data"], list)
    
    @patch.dict('os.environ', {
        'AWS_ACCESS_KEY_ID': 'test-key',
        'AWS_SECRET_ACCESS_KEY': 'test-secret',
        'S3_BUCKET_NAME': '360-dagma-photos'
    })
    def test_post_reconocimiento_success(self, mock_firebase_db, mock_s3_client):
        """Test POST /grupo-operativo/reconocimiento - Éxito"""
        # Preparar datos del formulario
        form_data = {
            'tipo_intervencion': 'Mantenimiento',
            'descripcion_intervencion': 'Poda de árboles',
            'direccion': 'Calle 5 #10-20',
            'observaciones': 'Trabajo completado',
            'coordinates_type': 'Point',
            'coordinates_data': '[-76.5225, 3.4516]'
        }
        
        # Crear archivo de imagen falso
        files = {
            'photos': ('test_photo.jpg', io.BytesIO(b'fake-image-content'), 'image/jpeg')
        }
        
        response = client.post(
            "/grupo-operativo/reconocimiento",
            data=form_data,
            files=files
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "id" in data
        assert data["message"] == "Reconocimiento registrado exitosamente"
        assert "coordinates" in data
        assert "photosUrl" in data
        assert data["photos_uploaded"] == 1
    
    def test_post_reconocimiento_invalid_geometry_type(self):
        """Test POST /grupo-operativo/reconocimiento - Tipo de geometría inválido"""
        form_data = {
            'tipo_intervencion': 'Mantenimiento',
            'descripcion_intervencion': 'Poda de árboles',
            'direccion': 'Calle 5 #10-20',
            'coordinates_type': 'InvalidType',
            'coordinates_data': '[-76.5225, 3.4516]'
        }
        
        files = {
            'photos': ('test.jpg', io.BytesIO(b'fake-image'), 'image/jpeg')
        }
        
        response = client.post(
            "/grupo-operativo/reconocimiento",
            data=form_data,
            files=files
        )
        
        assert response.status_code == 400
        assert "Tipo de geometría inválido" in response.json()["detail"]
    
    def test_post_reconocimiento_no_photos(self):
        """Test POST /grupo-operativo/reconocimiento - Sin fotos"""
        form_data = {
            'tipo_intervencion': 'Mantenimiento',
            'descripcion_intervencion': 'Poda de árboles',
            'direccion': 'Calle 5 #10-20',
            'coordinates_type': 'Point',
            'coordinates_data': '[-76.5225, 3.4516]'
        }
        
        response = client.post(
            "/grupo-operativo/reconocimiento",
            data=form_data
        )
        
        assert response.status_code == 422  # FastAPI validation error
    
    def test_post_reconocimiento_invalid_coordinates_format(self):
        """Test POST /grupo-operativo/reconocimiento - Formato de coordenadas inválido"""
        form_data = {
            'tipo_intervencion': 'Mantenimiento',
            'descripcion_intervencion': 'Poda de árboles',
            'direccion': 'Calle 5 #10-20',
            'coordinates_type': 'Point',
            'coordinates_data': 'invalid-json'
        }
        
        files = {
            'photos': ('test.jpg', io.BytesIO(b'fake-image'), 'image/jpeg')
        }
        
        response = client.post(
            "/grupo-operativo/reconocimiento",
            data=form_data,
            files=files
        )
        
        assert response.status_code == 400
        assert "Formato de coordenadas inválido" in response.json()["detail"]
    
    def test_post_reconocimiento_invalid_file_type(self):
        """Test POST /grupo-operativo/reconocimiento - Tipo de archivo inválido"""
        form_data = {
            'tipo_intervencion': 'Mantenimiento',
            'descripcion_intervencion': 'Poda de árboles',
            'direccion': 'Calle 5 #10-20',
            'coordinates_type': 'Point',
            'coordinates_data': '[-76.5225, 3.4516]'
        }
        
        files = {
            'photos': ('test.txt', io.BytesIO(b'not-an-image'), 'text/plain')
        }
        
        response = client.post(
            "/grupo-operativo/reconocimiento",
            data=form_data,
            files=files
        )
        
        assert response.status_code == 400
        assert "Tipo de archivo no permitido" in response.json()["detail"]
    
    def test_get_reportes(self, mock_firebase_db):
        """Test GET /grupo-operativo/reportes"""
        response = client.get("/grupo-operativo/reportes")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "count" in data
        assert isinstance(data["data"], list)
    
    def test_delete_reporte(self):
        """Test DELETE /grupo-operativo/eliminar-reporte"""
        response = client.delete(
            "/grupo-operativo/eliminar-reporte",
            params={"reporte_id": "test-reporte-id"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["id"] == "test-reporte-id"
        assert "message" in data
        assert "photos_deleted" in data


# ==================== TESTS: AUTH ROUTES ====================#
class TestAuthRoutes:
    """Tests para rutas de autenticación"""
    
    @patch('app.routes.auth_routes.auth_client')
    def test_validate_session_success(self, mock_auth):
        """Test POST /auth/validate-session - Éxito"""
        # Configurar mock
        mock_user = Mock()
        mock_user.uid = 'test-uid-123'
        mock_user.email = 'test@example.com'
        mock_user.display_name = 'Test User'
        mock_user.email_verified = True
        mock_user.disabled = False
        
        mock_auth.verify_id_token.return_value = {'uid': 'test-uid-123'}
        mock_auth.get_user.return_value = mock_user
        
        headers = {"Authorization": "Bearer mock-token"}
        response = client.post("/auth/validate-session", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert "user" in data
        assert data["user"]["uid"] == "test-uid-123"
    
    @patch('app.routes.auth_routes.auth_client')
    def test_validate_session_invalid_token(self, mock_auth):
        """Test POST /auth/validate-session - Token inválido"""
        mock_auth.verify_id_token.side_effect = Exception("Invalid token")
        headers = {"Authorization": "Bearer invalid-token"}
        response = client.post("/auth/validate-session", headers=headers)
        assert response.status_code == 401
    
    @patch('app.routes.auth_routes.auth_client')
    def test_login_user_success(self, mock_auth):
        """Test POST /auth/login - Éxito"""
        # Configurar mock
        mock_user = Mock()
        mock_user.uid = 'test-uid-123'
        mock_user.email = 'test@example.com'
        mock_user.display_name = 'Test User'
        mock_user.email_verified = True
        
        mock_auth.verify_id_token.return_value = {'uid': 'test-uid-123'}
        mock_auth.get_user.return_value = mock_user
        
        login_data = {
            "id_token": "mock-valid-token"
        }
        response = client.post("/auth/login", json=login_data)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "user" in data
        assert data["user"]["email"] == "test@example.com"
    
    @patch('app.routes.auth_routes.auth_client')
    def test_login_user_invalid_token(self, mock_auth):
        """Test POST /auth/login - Token inválido"""
        mock_auth.verify_id_token.side_effect = Exception("Invalid token")
        login_data = {
            "id_token": "invalid-token"
        }
        response = client.post("/auth/login", json=login_data)
        assert response.status_code == 401
    
    def test_register_health_check(self):
        """Test GET /auth/register/health-check"""
        response = client.get("/auth/register/health-check")
        assert response.status_code == 200
        data = response.json()
        assert data["firebase_auth"] == "available"
        assert data["firestore"] == "available"
    
    @patch('app.routes.auth_routes.db')
    @patch('app.routes.auth_routes.auth_client')
    def test_register_user_success(self, mock_auth, mock_db):
        """Test POST /auth/register - Éxito"""
        # Configurar mock
        mock_user = Mock()
        mock_user.uid = 'test-uid-new'
        mock_user.email = 'newuser@example.com'
        
        mock_auth.create_user.return_value = mock_user
        
        # Mock de Firestore
        mock_doc = Mock()
        mock_collection = Mock()
        mock_collection.document.return_value = mock_doc
        mock_db.collection.return_value = mock_collection
        
        register_data = {
            "email": "newuser@example.com",
            "password": "SecurePass123!",
            "full_name": "New User",
            "cellphone": "1234567890",
            "nombre_centro_gestor": "Centro Test"
        }
        response = client.post("/auth/register", json=register_data)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Usuario registrado exitosamente"
        assert "uid" in data
    
    def test_register_user_invalid_email(self):
        """Test POST /auth/register - Email inválido"""
        register_data = {
            "email": "invalid-email",
            "password": "SecurePass123!",
            "full_name": "New User",
            "cellphone": "1234567890",
            "nombre_centro_gestor": "Centro Test"
        }
        response = client.post("/auth/register", json=register_data)
        assert response.status_code == 422  # Validation error
    
    @patch('app.routes.auth_routes.auth_client')
    def test_change_password_success(self, mock_auth):
        """Test POST /auth/change-password - Éxito"""
        mock_auth.update_user.return_value = None
        
        form_data = {
            "uid": "test-uid-123",
            "new_password": "NewSecurePass123!"
        }
        response = client.post("/auth/change-password", data=form_data)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Contraseña actualizada exitosamente"
    
    def test_workload_identity_status(self):
        """Test GET /auth/workload-identity/status"""
        response = client.get("/auth/workload-identity/status")
        assert response.status_code == 200
        data = response.json()
        assert data["workload_identity"] == "configured"
        assert data["status"] == "active"
    
    @patch('app.routes.auth_routes.auth_client')
    def test_google_auth_success(self, mock_auth):
        """Test POST /auth/google - Éxito"""
        # Configurar mock
        mock_user = Mock()
        mock_user.uid = 'test-uid-google'
        mock_user.email = 'google@example.com'
        mock_user.display_name = 'Google User'
        
        mock_auth.verify_id_token.return_value = {'uid': 'test-uid-google'}
        mock_auth.get_user.return_value = mock_user
        mock_auth.create_custom_token.return_value = b'mock-custom-token'
        
        form_data = {
            "google_token": "mock-google-token"
        }
        response = client.post("/auth/google", data=form_data)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "token" in data
        assert "user" in data
    
    @patch('app.routes.auth_routes.auth_client')
    def test_google_auth_invalid_token(self, mock_auth):
        """Test POST /auth/google - Token inválido"""
        mock_auth.verify_id_token.side_effect = Exception("Invalid token")
        form_data = {
            "google_token": "invalid-token"
        }
        response = client.post("/auth/google", data=form_data)
        assert response.status_code == 401
    
    @patch('app.routes.auth_routes.db')
    @patch('app.routes.auth_routes.auth_client')
    def test_delete_user_success(self, mock_auth, mock_db):
        """Test DELETE /auth/user/{uid} - Éxito"""
        mock_auth.delete_user.return_value = None
        
        # Mock de Firestore
        mock_doc = Mock()
        mock_collection = Mock()
        mock_collection.document.return_value = mock_doc
        mock_db.collection.return_value = mock_collection
        
        response = client.delete("/auth/user/test-uid-123")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Usuario" in data["message"]
    
    def test_list_system_users(self, mock_firebase_db):
        """Test GET /admin/users"""
        response = client.get("/admin/users")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "count" in data
    
    @patch('app.routes.auth_routes.auth_client')
    def test_list_users_from_auth(self, mock_auth):
        """Test GET /auth/admin/users"""
        # Configurar mock
        mock_user = Mock()
        mock_user.uid = 'test-uid-123'
        mock_user.email = 'test@example.com'
        mock_user.display_name = 'Test User'
        mock_user.email_verified = True
        mock_user.disabled = False
        mock_user.user_metadata = Mock()
        mock_user.user_metadata.creation_timestamp = 1609459200000
        
        mock_list = Mock()
        mock_list.iterate_all.return_value = [mock_user]
        mock_auth.list_users.return_value = mock_list
        
        response = client.get("/auth/admin/users")
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert isinstance(data["users"], list)
    
    def test_list_super_admins(self):
        """Test GET /auth/admin/users/super-admins"""
        response = client.get("/auth/admin/users/super-admins")
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
    
    def test_get_user_details(self):
        """Test GET /auth/admin/users/{uid}"""
        response = client.get("/auth/admin/users/test-uid-123")
        assert response.status_code == 200
        data = response.json()
        assert "uid" in data
    
    def test_update_user_info(self):
        """Test PUT /auth/admin/users/{uid}"""
        response = client.put("/auth/admin/users/test-uid-123")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
    
    def test_assign_roles_to_user(self):
        """Test POST /auth/admin/users/{uid}/roles"""
        role_data = {
            "roles": ["admin", "editor"]
        }
        response = client.post("/auth/admin/users/test-uid-123/roles", json=role_data)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "roles" in data
    
    def test_grant_temporary_permission(self):
        """Test POST /auth/admin/users/{uid}/temporary-permissions"""
        permission_data = {
            "permission": "edit:documents",
            "expires_at": "2026-12-31T23:59:59Z"
        }
        response = client.post(
            "/auth/admin/users/test-uid-123/temporary-permissions",
            json=permission_data
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
    
    def test_revoke_temporary_permission(self):
        """Test DELETE /auth/admin/users/{uid}/temporary-permissions/{permission}"""
        response = client.delete(
            "/auth/admin/users/test-uid-123/temporary-permissions/edit:documents"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
    
    def test_list_roles(self):
        """Test GET /auth/admin/roles"""
        response = client.get("/auth/admin/roles")
        assert response.status_code == 200
        data = response.json()
        assert "roles" in data
    
    def test_get_role_details(self):
        """Test GET /auth/admin/roles/{role_id}"""
        response = client.get("/auth/admin/roles/admin")
        assert response.status_code == 200
        data = response.json()
        assert "role_id" in data
    
    def test_get_audit_logs(self):
        """Test GET /auth/admin/audit-logs"""
        response = client.get("/auth/admin/audit-logs")
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert "total" in data
    
    def test_get_system_stats(self):
        """Test GET /auth/admin/system/stats"""
        response = client.get("/auth/admin/system/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_users" in data
        assert "total_roles" in data
    
    @patch('app.routes.auth_routes.auth_client')
    def test_get_firebase_config_with_valid_token(self, mock_auth):
        """Test GET /auth/config - Con token válido"""
        mock_auth.verify_id_token.return_value = {'uid': 'test-uid-123'}
        
        headers = {"Authorization": "Bearer valid-token"}
        response = client.get("/auth/config", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "apiKey" in data
        assert "projectId" in data
        assert data["projectId"] == "dagma-85aad"
    
    def test_get_firebase_config_without_token(self):
        """Test GET /auth/config - Sin token"""
        response = client.get("/auth/config")
        assert response.status_code == 403  # Forbidden without auth


# ==================== TESTS DE VALIDACIÓN ====================#
class TestValidations:
    """Tests de validaciones de datos"""
    
    def test_validate_point_coordinates_valid(self):
        """Test validación de coordenadas Point - Válidas"""
        from app.routes.artefacto_360_routes import validate_coordinates
        coordinates = [-76.5225, 3.4516]
        assert validate_coordinates(coordinates, "Point") is True
    
    def test_validate_point_coordinates_invalid_longitude(self):
        """Test validación de coordenadas Point - Longitud inválida"""
        from app.routes.artefacto_360_routes import validate_coordinates
        coordinates = [-200, 3.4516]  # Longitud fuera de rango
        with pytest.raises(ValueError, match="Longitud inválida"):
            validate_coordinates(coordinates, "Point")
    
    def test_validate_point_coordinates_invalid_latitude(self):
        """Test validación de coordenadas Point - Latitud inválida"""
        from app.routes.artefacto_360_routes import validate_coordinates
        coordinates = [-76.5225, 100]  # Latitud fuera de rango
        with pytest.raises(ValueError, match="Latitud inválida"):
            validate_coordinates(coordinates, "Point")
    
    def test_validate_linestring_coordinates_valid(self):
        """Test validación de coordenadas LineString - Válidas"""
        from app.routes.artefacto_360_routes import validate_coordinates
        coordinates = [[-76.5225, 3.4516], [-76.5226, 3.4517]]
        assert validate_coordinates(coordinates, "LineString") is True
    
    def test_validate_linestring_coordinates_insufficient_points(self):
        """Test validación de coordenadas LineString - Puntos insuficientes"""
        from app.routes.artefacto_360_routes import validate_coordinates
        coordinates = [[-76.5225, 3.4516]]  # Solo un punto
        with pytest.raises(ValueError, match="debe tener al menos 2 puntos"):
            validate_coordinates(coordinates, "LineString")
    
    def test_validate_polygon_coordinates_valid(self):
        """Test validación de coordenadas Polygon - Válidas"""
        from app.routes.artefacto_360_routes import validate_coordinates
        coordinates = [[
            [-76.5225, 3.4516],
            [-76.5226, 3.4517],
            [-76.5227, 3.4518],
            [-76.5225, 3.4516]  # Cerrar el polígono
        ]]
        assert validate_coordinates(coordinates, "Polygon") is True


# ==================== TESTS DE UTILIDADES ====================#
class TestUtilities:
    """Tests de funciones auxiliares"""
    
    def test_clean_nan_values_with_nan(self):
        """Test limpiar valores NaN"""
        from app.routes.artefacto_360_routes import clean_nan_values
        import math
        
        data = {
            "valid": 123,
            "invalid_nan": float('nan'),
            "invalid_inf": float('inf'),
            "nested": {
                "value": float('nan')
            }
        }
        
        cleaned = clean_nan_values(data)
        assert cleaned["valid"] == 123
        assert cleaned["invalid_nan"] is None
        assert cleaned["invalid_inf"] is None
        assert cleaned["nested"]["value"] is None
    
    def test_clean_nan_values_with_list(self):
        """Test limpiar valores NaN en listas"""
        from app.routes.artefacto_360_routes import clean_nan_values
        import math
        
        data = [1, float('nan'), 3, float('inf')]
        cleaned = clean_nan_values(data)
        assert cleaned == [1, None, 3, None]
    
    def test_validate_photo_file_valid_jpeg(self):
        """Test validar archivo de foto - JPEG válido"""
        from app.routes.artefacto_360_routes import validate_photo_file
        
        mock_file = Mock()
        mock_file.content_type = "image/jpeg"
        mock_file.filename = "test.jpg"
        
        assert validate_photo_file(mock_file) is True
    
    def test_validate_photo_file_valid_png(self):
        """Test validar archivo de foto - PNG válido"""
        from app.routes.artefacto_360_routes import validate_photo_file
        
        mock_file = Mock()
        mock_file.content_type = "image/png"
        mock_file.filename = "test.png"
        
        assert validate_photo_file(mock_file) is True
    
    def test_validate_photo_file_invalid_type(self):
        """Test validar archivo de foto - Tipo inválido"""
        from app.routes.artefacto_360_routes import validate_photo_file
        
        mock_file = Mock()
        mock_file.content_type = "application/pdf"
        mock_file.filename = "test.pdf"
        
        with pytest.raises(ValueError, match="Tipo de archivo no permitido"):
            validate_photo_file(mock_file)
    
    def test_validate_photo_file_invalid_extension(self):
        """Test validar archivo de foto - Extensión inválida"""
        from app.routes.artefacto_360_routes import validate_photo_file
        
        mock_file = Mock()
        mock_file.content_type = "image/jpeg"
        mock_file.filename = "test.txt"  # Extensión no permitida
        
        with pytest.raises(ValueError, match="Extensión no permitida"):
            validate_photo_file(mock_file)


# ==================== TESTS DE INTEGRACIÓN ====================#
class TestIntegration:
    """Tests de integración entre endpoints"""
    
    @patch('app.routes.auth_routes.db')
    @patch('app.routes.auth_routes.auth_client')
    def test_full_user_registration_and_login_flow(self, mock_auth, mock_db):
        """Test flujo completo: Registro + Login"""
        # Configurar mocks
        mock_user = Mock()
        mock_user.uid = 'test-uid-integration'
        mock_user.email = 'integration@example.com'
        mock_user.display_name = 'Integration Test User'
        mock_user.email_verified = False
        
        mock_auth.create_user.return_value = mock_user
        mock_auth.verify_id_token.return_value = {'uid': 'test-uid-integration'}
        mock_auth.get_user.return_value = mock_user
        
        # Mock de Firestore
        mock_doc = Mock()
        mock_collection = Mock()
        mock_collection.document.return_value = mock_doc
        mock_db.collection.return_value = mock_collection
        
        # 1. Registrar usuario
        register_data = {
            "email": "integration@example.com",
            "password": "SecurePass123!",
            "full_name": "Integration Test User",
            "cellphone": "9876543210",
            "nombre_centro_gestor": "Centro Integration"
        }
        register_response = client.post("/auth/register", json=register_data)
        assert register_response.status_code == 200
        uid = register_response.json()["uid"]
        
        # 2. Login con el usuario
        login_data = {
            "id_token": "mock-token-for-new-user"
        }
        login_response = client.post("/auth/login", json=login_data)
        assert login_response.status_code == 200
        assert login_response.json()["success"] is True
    
    @patch.dict('os.environ', {
        'AWS_ACCESS_KEY_ID': 'test-key',
        'AWS_SECRET_ACCESS_KEY': 'test-secret',
        'S3_BUCKET_NAME': '360-dagma-photos'
    })
    def test_full_reconocimiento_flow(self, mock_firebase_db, mock_s3_client):
        """Test flujo completo: Crear reconocimiento + Obtener reportes + Eliminar"""
        # 1. Crear reconocimiento
        form_data = {
            'tipo_intervencion': 'Mantenimiento',
            'descripcion_intervencion': 'Test integración',
            'direccion': 'Calle Test',
            'coordinates_type': 'Point',
            'coordinates_data': '[-76.5225, 3.4516]'
        }
        files = {
            'photos': ('test.jpg', io.BytesIO(b'fake-image'), 'image/jpeg')
        }
        
        post_response = client.post(
            "/grupo-operativo/reconocimiento",
            data=form_data,
            files=files
        )
        assert post_response.status_code == 200
        reporte_id = post_response.json()["id"]
        
        # 2. Obtener reportes
        get_response = client.get("/grupo-operativo/reportes")
        assert get_response.status_code == 200
        
        # 3. Eliminar reporte
        delete_response = client.delete(
            "/grupo-operativo/eliminar-reporte",
            params={"reporte_id": reporte_id}
        )
        assert delete_response.status_code == 200


# ==================== CONFIGURACIÓN DE PYTEST ====================#
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
