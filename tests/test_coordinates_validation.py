"""
Unit tests for coordinate validation (current API: validate_coordinates returns
True for valid input and raises ValueError for invalid input).

Regression: under Pydantic v2, the CoordinatesModel validator read
`values.get('geometry_type')` while validating `coordinates`, but fields are
validated in declaration order so geometry_type was not yet available — silently
skipping all range checks and accepting out-of-range coordinates.
"""
import pytest

from app.routes.artefacto_360_routes import validate_coordinates


def test_valid_point_passes():
    assert validate_coordinates([-76.5225, 3.4516], "Point") is True


def test_invalid_longitude_raises():
    with pytest.raises(ValueError):
        validate_coordinates([-200, 3.4516], "Point")


def test_invalid_latitude_raises():
    with pytest.raises(ValueError):
        validate_coordinates([-76.5225, 100], "Point")


def test_point_wrong_length_raises():
    with pytest.raises(ValueError):
        validate_coordinates([-76.5225, 3.4516, 10], "Point")


def test_boundary_values_accepted():
    assert validate_coordinates([-180, -90], "Point") is True
    assert validate_coordinates([180, 90], "Point") is True
