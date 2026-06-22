"""
Tests for PATCH /grupos/{grupo_key}/reporte_intervencion/{reporte_id}/coordenadas.

Regression: an operador editing the coordinates of their OWN report used to get a
500 because the authorship check accessed CurrentUser.nombre_completo /
CurrentUser.displayName — attributes that do not exist on the model — raising
AttributeError that the generic handler turned into a 500.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch

from app.main import app

client = TestClient(app)

OPERADOR_EMAIL = "operador@dagma.gov.co"
OPERADOR_TOKEN = {
    "uid": "op-uid-1",
    "email": OPERADOR_EMAIL,
    "role": "operador",
    "grupo": "flora_urbana",
}
AUTH = {"Authorization": "Bearer mock-op"}


@pytest.fixture
def mock_auth_operador():
    with patch("app.deps.authz.auth_client") as mock_ac:
        mock_ac.verify_id_token.return_value = OPERADOR_TOKEN
        yield mock_ac


@pytest.fixture
def mock_db_existing_report():
    """A report owned by the operador, in the flora_urbana group."""
    with patch("app.routes.artefacto_360_routes.db") as mock_db:
        col = Mock()
        doc_ref = Mock()
        snap = Mock()
        snap.exists = True
        snap.to_dict.return_value = {
            "id": "REP-1",
            "grupo": "flora_urbana",
            "registrado_por": OPERADOR_EMAIL,
        }
        doc_ref.get.return_value = snap
        doc_ref.update = Mock()
        col.document.return_value = doc_ref
        mock_db.collection.return_value = col
        yield mock_db, doc_ref


def test_operador_puede_editar_coordenadas_de_su_reporte(
    mock_auth_operador, mock_db_existing_report
):
    mock_db, doc_ref = mock_db_existing_report
    with patch(
        "app.routes.artefacto_360_routes.get_location_from_coordinates",
        return_value=("Comuna 19", "El Lido"),
    ):
        r = client.patch(
            "/grupos/flora_urbana/reporte_intervencion/REP-1/coordenadas",
            json={"coordinates_data": "[-76.5225, 3.4516]", "coordinates_type": "Point"},
            headers=AUTH,
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["coordinates"]["coordinates"] == [-76.5225, 3.4516]
    doc_ref.update.assert_called_once()


def test_operador_no_puede_editar_coordenadas_de_otro(
    mock_auth_operador, mock_db_existing_report
):
    """An operador editing someone else's report must get 403, not 500."""
    mock_db, doc_ref = mock_db_existing_report
    doc_ref.get.return_value.to_dict.return_value = {
        "id": "REP-1",
        "grupo": "flora_urbana",
        "registrado_por": "otra.persona@dagma.gov.co",
    }
    with patch(
        "app.routes.artefacto_360_routes.get_location_from_coordinates",
        return_value=("Comuna 19", "El Lido"),
    ):
        r = client.patch(
            "/grupos/flora_urbana/reporte_intervencion/REP-1/coordenadas",
            json={"coordinates_data": "[-76.5225, 3.4516]", "coordinates_type": "Point"},
            headers=AUTH,
        )

    assert r.status_code == 403, r.text
