"""
Tests unitarios para la capa de modelos y helpers de roles.
No requieren Firebase ni Firestore — solo lógica pura.
"""
import pytest
from app.models.roles import (
    Role,
    ROLE_HIERARCHY,
    can_assign_role,
    normalize_role,
    role_at_least,
    CurrentUser,
)


# ---------------------------------------------------------------------------
# normalize_role
# ---------------------------------------------------------------------------

class TestNormalizeRole:
    def test_canonical_values(self):
        assert normalize_role("operador") == Role.OPERADOR
        assert normalize_role("lider") == Role.LIDER
        assert normalize_role("administrador") == Role.ADMINISTRADOR
        assert normalize_role("desarrollador") == Role.DESARROLLADOR

    def test_legacy_admin(self):
        assert normalize_role("admin") == Role.ADMINISTRADOR
        assert normalize_role("administrator") == Role.ADMINISTRADOR

    def test_legacy_super_admin(self):
        assert normalize_role("super_admin") == Role.DESARROLLADOR
        assert normalize_role("superadmin") == Role.DESARROLLADOR

    def test_legacy_coordinador(self):
        assert normalize_role("coordinador") == Role.LIDER

    def test_case_insensitive(self):
        assert normalize_role("ADMIN") == Role.ADMINISTRADOR
        assert normalize_role("Lider") == Role.LIDER
        assert normalize_role("  OPERADOR  ") == Role.OPERADOR

    def test_unknown_returns_none(self):
        assert normalize_role("superuser") is None
        assert normalize_role("root") is None

    def test_empty_and_none(self):
        assert normalize_role(None) is None
        assert normalize_role("") is None
        assert normalize_role("  ") is None


# ---------------------------------------------------------------------------
# role_at_least (jerarquía)
# ---------------------------------------------------------------------------

class TestRoleAtLeast:
    @pytest.mark.parametrize("user_role,min_role,expected", [
        # Igual nivel → True
        (Role.OPERADOR, Role.OPERADOR, True),
        (Role.LIDER, Role.LIDER, True),
        (Role.ADMINISTRADOR, Role.ADMINISTRADOR, True),
        (Role.DESARROLLADOR, Role.DESARROLLADOR, True),
        # Mayor nivel → True
        (Role.LIDER, Role.OPERADOR, True),
        (Role.ADMINISTRADOR, Role.LIDER, True),
        (Role.DESARROLLADOR, Role.ADMINISTRADOR, True),
        (Role.DESARROLLADOR, Role.OPERADOR, True),
        # Menor nivel → False
        (Role.OPERADOR, Role.LIDER, False),
        (Role.OPERADOR, Role.ADMINISTRADOR, False),
        (Role.LIDER, Role.ADMINISTRADOR, False),
        (Role.LIDER, Role.DESARROLLADOR, False),
        (Role.ADMINISTRADOR, Role.DESARROLLADOR, False),
    ])
    def test_role_hierarchy(self, user_role, min_role, expected):
        assert role_at_least(user_role, min_role) == expected


# ---------------------------------------------------------------------------
# can_assign_role (reglas de escalamiento)
# ---------------------------------------------------------------------------

class TestCanAssignRole:
    def test_desarrollador_can_assign_any_role(self):
        for target in Role:
            assert can_assign_role(Role.DESARROLLADOR, target) is True

    def test_administrador_can_assign_up_to_administrador(self):
        assert can_assign_role(Role.ADMINISTRADOR, Role.OPERADOR) is True
        assert can_assign_role(Role.ADMINISTRADOR, Role.LIDER) is True
        assert can_assign_role(Role.ADMINISTRADOR, Role.ADMINISTRADOR) is True

    def test_administrador_cannot_assign_desarrollador(self):
        assert can_assign_role(Role.ADMINISTRADOR, Role.DESARROLLADOR) is False

    def test_lider_cannot_assign_any_role(self):
        for target in Role:
            assert can_assign_role(Role.LIDER, target) is False

    def test_operador_cannot_assign_any_role(self):
        for target in Role:
            assert can_assign_role(Role.OPERADOR, target) is False


# ---------------------------------------------------------------------------
# CurrentUser model helpers
# ---------------------------------------------------------------------------

class TestCurrentUser:
    def _make_user(self, role: str, grupo: str = "cuadrilla") -> CurrentUser:
        return CurrentUser(uid="uid-test", email="test@dagma.gov.co", role=role, grupo=grupo)

    def test_level_property(self):
        assert self._make_user(Role.OPERADOR).level == 1
        assert self._make_user(Role.LIDER).level == 2
        assert self._make_user(Role.ADMINISTRADOR).level == 3
        assert self._make_user(Role.DESARROLLADOR).level == 4

    def test_has_role(self):
        u = self._make_user(Role.LIDER)
        assert u.has_role(Role.LIDER)
        assert u.has_role(Role.OPERADOR, Role.LIDER)
        assert not u.has_role(Role.ADMINISTRADOR)

    def test_at_least(self):
        u = self._make_user(Role.ADMINISTRADOR)
        assert u.at_least(Role.OPERADOR)
        assert u.at_least(Role.LIDER)
        assert u.at_least(Role.ADMINISTRADOR)
        assert not u.at_least(Role.DESARROLLADOR)

    def test_optional_grupo(self):
        u = CurrentUser(uid="x", email="x@x.com", role=Role.OPERADOR)
        assert u.grupo is None
