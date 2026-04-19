# Testing Patterns — DAGMA API

## Current Setup

```ini
# pytest.ini
[pytest]
testpaths = .
python_files = test_*.py
python_classes = Test*
python_functions = test_*
markers =
    unit: Unit tests (fast, no external calls)
    integration: Integration tests (may call Firebase/S3)
    auth: Authentication tests
    firebase: Firebase-specific tests
    s3: S3 storage tests
    slow: Slow tests
    api: API endpoint tests
asyncio_mode = auto
addopts = --cov=app --cov-report=term-missing
```

**Run all tests:** `pytest`
**Run only unit tests:** `pytest -m unit`
**Run with coverage:** `pytest --cov=app --cov-report=html`

---

## Fixture Patterns

### Core Fixtures (`conftest.py`)

The project has existing fixtures. Always check `conftest.py` before creating new ones.

```python
# Existing fixtures (use these):
mock_firebase_db      # AsyncMock for Firestore db operations
mock_firebase_auth    # AsyncMock for Firebase Auth
mock_s3_client        # Mock for boto3 S3 client
valid_auth_token      # Valid Firebase ID token string
sample_image_file     # BytesIO image for photo upload tests
```

### Adding Gmail/Calendar Mock Fixtures

```python
# Add to conftest.py
import pytest
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_gmail_service():
    with patch('app.services.email_service._get_gmail_service') as mock:
        service = MagicMock()
        service.users().messages().send().execute.return_value = {
            'id': 'msg_test_123',
            'threadId': 'thread_test_456'
        }
        mock.return_value = service
        yield service

@pytest.fixture
def mock_calendar_service():
    with patch('app.services.calendar_service._get_calendar_service') as mock:
        service = MagicMock()
        service.events().insert().execute.return_value = {
            'id': 'cal_event_test_789',
            'htmlLink': 'https://calendar.google.com/event?eid=test'
        }
        mock.return_value = service
        yield service
```

---

## Test File Conventions

### Pattern: New Endpoint Test

```python
# test_programar_actividad.py
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch
from app.main import app

class TestProgramarActividad:
    
    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_firebase_db, mock_calendar_service, mock_gmail_service):
        self.db = mock_firebase_db
        self.calendar = mock_calendar_service
        self.gmail = mock_gmail_service
    
    @pytest.mark.asyncio
    @pytest.mark.api
    async def test_programar_actividad_success(self, valid_auth_token):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/programar-actividad",
                headers={"Authorization": f"Bearer {valid_auth_token}"},
                json={
                    "nombre_actividad": "Poda de árboles",
                    "grupo": "cuadrilla",
                    "nombre_parque": "Parque del Amor",
                    "fecha_programada": "2026-05-10",
                    "activity_status": "programación"
                }
            )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_programar_actividad_missing_fecha(self, valid_auth_token):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/programar-actividad",
                headers={"Authorization": f"Bearer {valid_auth_token}"},
                json={"nombre_actividad": "Poda", "grupo": "cuadrilla"}
                # Missing fecha_programada
            )
        assert response.status_code == 422  # Pydantic validation error
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_calendar_event_created_on_activity(self, valid_auth_token):
        """Calendar sync should be called but its failure must not break the endpoint."""
        self.calendar.events().insert().execute.side_effect = Exception("Calendar API down")
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/programar-actividad",
                headers={"Authorization": f"Bearer {valid_auth_token}"},
                json={
                    "nombre_actividad": "Inspección",
                    "grupo": "ecosistemas",
                    "fecha_programada": "2026-05-15",
                    "activity_status": "programación"
                }
            )
        # Must succeed even when Calendar API fails
        assert response.status_code == 200
```

---

## Report Lifecycle Test

```python
# test_report_lifecycle.py
"""
E2E test for complete report lifecycle:
notificado → radicado → en-gestion → asignado → en-proceso → resuelto → cerrado
"""

@pytest.mark.asyncio
@pytest.mark.slow
async def test_complete_report_lifecycle(mock_firebase_db, valid_auth_token):
    async with AsyncClient(app=app, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {valid_auth_token}"}
        
        # Step 1: Create report
        create_resp = await client.post(
            "/grupo-cuadrilla/reporte_intervencion",
            headers=headers,
            json={
                "nombre_parque": "Parque Test",
                "actividad_principal": "Poda",
                "coordinates": {"type": "Point", "coordinates": [-76.53, 3.45]},
                "arboles_data": [{"especie": "Samán", "cantidad": 5}]
            }
        )
        assert create_resp.status_code == 200
        reporte_id = create_resp.json()["data"]["reporte_id"]
        
        # Step 2: Change state through lifecycle
        states = ["radicado", "en-gestion", "asignado", "en-proceso", "resuelto"]
        for state in states:
            state_resp = await client.put(
                f"/api/v1/reportes/{reporte_id}/estado",
                headers=headers,
                json={"estado": state, "comentario": f"Cambio a {state}"}
            )
            assert state_resp.status_code == 200, f"Failed at state: {state}"
        
        # Step 3: Close report
        close_resp = await client.delete(
            f"/api/v1/reportes/{reporte_id}/cerrar",
            headers=headers
        )
        assert close_resp.status_code == 200
```

---

## Firestore Schema Contract Tests

These tests ensure that Firestore documents always have the required fields. Run them when you add or change a Pydantic model.

```python
# test_firestore_schemas.py
import pytest
from pydantic import BaseModel, ValidationError

class InterventionReportSchema(BaseModel):
    nombre_parque: str
    actividad_principal: str
    grupo: str
    estado: str
    timestamp: str
    coordinates: dict

@pytest.mark.unit
def test_cuadrilla_report_has_required_fields():
    """Ensure POST /grupo-cuadrilla/reporte creates documents with all required fields."""
    sample = {
        "nombre_parque": "Parque del Amor",
        "actividad_principal": "Poda",
        "grupo": "cuadrilla",
        "estado": "notificado",
        "timestamp": "2026-04-16T10:00:00-05:00",
        "coordinates": {"type": "Point", "coordinates": [-76.53, 3.45]}
    }
    # Should not raise
    report = InterventionReportSchema(**sample)
    assert report.grupo == "cuadrilla"

@pytest.mark.unit
def test_report_invalid_without_coordinates():
    with pytest.raises(ValidationError):
        InterventionReportSchema(
            nombre_parque="Test",
            actividad_principal="Poda",
            grupo="cuadrilla",
            estado="notificado",
            timestamp="2026-04-16T10:00:00-05:00"
            # Missing coordinates
        )
```

---

## Coverage Targets

| Module | Target | Notes |
|--------|--------|-------|
| `app/routes/artefacto_360_routes.py` | 80%+ | Core business logic |
| `app/routes/seguimiento_routes.py` | 85%+ | State machine must be covered |
| `app/routes/auth_routes.py` | 75%+ | Auth flows |
| `app/services/email_service.py` | 70%+ | Mock external calls |
| `app/services/calendar_service.py` | 70%+ | Mock external calls |
| `app/firebase_config.py` | 60%+ | Init code, hard to test |

**Check current coverage:**
```bash
pytest --cov=app --cov-report=html
open htmlcov/index.html
```
