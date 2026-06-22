"""Tests for the pure transform functions in the flora_urbana migration scripts.

No Firestore or Firebase credentials required — pure logic only.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from migrate_flora_urbana_grupos_collection import merge_flora_urbana_docs
from migrate_flora_urbana_users import (
    needs_migration as users_needs_migration,
    transform_doc as users_transform,
)
from migrate_flora_urbana_reportes import (
    needs_migration as reportes_needs_migration,
    transform_doc as reportes_transform,
)
from migrate_flora_urbana_actividades import transform_grupos_requeridos
from migrate_flora_urbana_personal_operativo import (
    needs_migration as po_needs_migration,
    transform_doc as po_transform,
)


# ---------------------------------------------------------------------------
# merge_flora_urbana_docs
# ---------------------------------------------------------------------------

class TestMergeFloraUrbanaDocs:
    def test_two_variant_docs_merged_into_canonical(self):
        docs = [
            {
                "nombre": "Cuadrilla",
                "email": "flora.coordinacion@cali.gov.co",
                "lider": {"nombre": "Ana Gomez", "email": "ana@cali.gov.co", "numero_contacto": None},
            },
            {
                "nombre": "Flora Urbana",
                "email": "flora.coordinacion@cali.gov.co",
                "lider": {"nombre": None, "email": None, "numero_contacto": None},
            },
        ]
        result = merge_flora_urbana_docs(docs)
        assert result is not None
        assert result["nombre"] == "Flora urbana"
        assert result["email"] == "flora.coordinacion@cali.gov.co"
        # lider nombre comes from the first doc that has it
        assert result["lider"]["nombre"] == "Ana Gomez"
        assert result["lider"]["email"] == "ana@cali.gov.co"

    def test_single_canonical_doc_returned_as_is(self):
        docs = [
            {
                "nombre": "Flora urbana",
                "email": "flora.coordinacion@cali.gov.co",
                "lider": {"nombre": "Luis Torres", "email": "luis@cali.gov.co", "numero_contacto": None},
            }
        ]
        result = merge_flora_urbana_docs(docs)
        assert result is not None
        assert result["nombre"] == "Flora urbana"
        assert result["lider"]["nombre"] == "Luis Torres"

    def test_empty_list_returns_none(self):
        assert merge_flora_urbana_docs([]) is None

    def test_lider_nombre_from_first_non_null_source(self):
        """Cuadrilla doc has lider.nombre, Flora Urbana doc has None — pick Cuadrilla's."""
        docs = [
            {
                "nombre": "Flora Urbana",
                "email": "flora.coordinacion@cali.gov.co",
                "lider": {"nombre": None, "email": None, "numero_contacto": None},
            },
            {
                "nombre": "Cuadrilla",
                "email": "flora.coordinacion@cali.gov.co",
                "lider": {"nombre": "Carlos Rios", "email": None, "numero_contacto": None},
            },
        ]
        result = merge_flora_urbana_docs(docs)
        assert result is not None
        # First doc has no nombre, second doc has it — should pick Carlos Rios
        assert result["lider"]["nombre"] == "Carlos Rios"

    def test_canonical_nombre_always_set(self):
        """Regardless of input nombre variants, output is always 'Flora urbana'."""
        docs = [
            {"nombre": "CUADRILLA", "email": "x@x.com", "lider": {}},
        ]
        result = merge_flora_urbana_docs(docs)
        assert result["nombre"] == "Flora urbana"

    def test_canonical_email_always_set(self):
        """Regardless of input emails, output email is always the canonical one."""
        docs = [
            {"nombre": "cuadrilla", "email": "other@cali.gov.co", "lider": {}},
        ]
        result = merge_flora_urbana_docs(docs)
        assert result["email"] == "flora.coordinacion@cali.gov.co"


# ---------------------------------------------------------------------------
# users_needs_migration / users_transform
# ---------------------------------------------------------------------------

class TestUsersNeedsMigration:
    def test_cuadrilla_needs_migration(self):
        assert users_needs_migration({"grupo": "cuadrilla"}) is True

    def test_flora_urbana_parenthetical_needs_migration(self):
        assert users_needs_migration({"grupo": "Flora Urbana (cuadrilla)"}) is True

    def test_already_canonical_skipped(self):
        assert users_needs_migration({"grupo": "flora_urbana"}) is False

    def test_other_group_skipped(self):
        assert users_needs_migration({"grupo": "vivero"}) is False

    def test_none_grupo_skipped(self):
        assert users_needs_migration({"grupo": None}) is False

    def test_missing_grupo_key_skipped(self):
        assert users_needs_migration({}) is False

    def test_flora_urbana_mixed_case_needs_migration(self):
        assert users_needs_migration({"grupo": "Flora Urbana"}) is True

    def test_Cuadrilla_capitalized_needs_migration(self):
        assert users_needs_migration({"grupo": "Cuadrilla"}) is True


class TestUsersTransformDoc:
    def test_transform_sets_canonical_grupo(self):
        doc = {"uid": "abc", "email": "x@x.com", "grupo": "cuadrilla"}
        result = users_transform(doc)
        assert result["grupo"] == "flora_urbana"

    def test_transform_preserves_other_fields(self):
        doc = {"uid": "abc", "email": "x@x.com", "grupo": "cuadrilla", "role": "operador"}
        result = users_transform(doc)
        assert result["uid"] == "abc"
        assert result["email"] == "x@x.com"
        assert result["role"] == "operador"

    def test_transform_does_not_mutate_original(self):
        doc = {"grupo": "cuadrilla"}
        _ = users_transform(doc)
        assert doc["grupo"] == "cuadrilla"


# ---------------------------------------------------------------------------
# reportes_needs_migration / reportes_transform
# ---------------------------------------------------------------------------

class TestReportesNeedsMigration:
    def test_cuadrilla_needs_migration(self):
        doc = {
            "grupo": "cuadrilla",
            "photosUrl": ["s3://bucket/reportes/cuadrilla/img.jpg"],
            "s3_key": "reportes/cuadrilla/abc.jpg",
        }
        assert reportes_needs_migration(doc) is True

    def test_already_canonical_skipped(self):
        assert reportes_needs_migration({"grupo": "flora_urbana"}) is False

    def test_other_group_skipped(self):
        assert reportes_needs_migration({"grupo": "acustica"}) is False

    def test_none_grupo_skipped(self):
        assert reportes_needs_migration({"grupo": None}) is False


class TestReportesTransformDoc:
    def test_transform_returns_only_grupo_field(self):
        doc = {
            "grupo": "cuadrilla",
            "photosUrl": ["s3://bucket/reportes/cuadrilla/img.jpg"],
            "s3_key": "reportes/cuadrilla/abc.jpg",
        }
        result = reportes_transform(doc)
        assert result == {"grupo": "flora_urbana"}

    def test_transform_does_not_include_photosUrl(self):
        doc = {
            "grupo": "cuadrilla",
            "photosUrl": ["s3://bucket/x.jpg"],
            "s3_key": "reportes/cuadrilla/x.jpg",
            "fecha": "2025-01-01",
        }
        result = reportes_transform(doc)
        assert "photosUrl" not in result

    def test_transform_does_not_include_s3_key(self):
        doc = {"grupo": "cuadrilla", "s3_key": "reportes/cuadrilla/x.jpg"}
        result = reportes_transform(doc)
        assert "s3_key" not in result

    def test_transform_canonical_grupo_value(self):
        result = reportes_transform({"grupo": "Flora Urbana"})
        assert result["grupo"] == "flora_urbana"


# ---------------------------------------------------------------------------
# transform_grupos_requeridos
# ---------------------------------------------------------------------------

class TestTransformGruposRequeridos:
    def test_cuadrilla_replaced_by_canonical(self):
        result, changed = transform_grupos_requeridos(["cuadrilla", "vivero"])
        assert result == ["flora_urbana", "vivero"]
        assert changed is True

    def test_two_variants_deduplicated(self):
        result, changed = transform_grupos_requeridos(["cuadrilla", "flora_urbana"])
        assert result == ["flora_urbana"]
        assert changed is True

    def test_no_flora_urbana_group_unchanged(self):
        result, changed = transform_grupos_requeridos(["vivero"])
        assert result == ["vivero"]
        assert changed is False

    def test_empty_list_unchanged(self):
        result, changed = transform_grupos_requeridos([])
        assert result == []
        assert changed is False

    def test_already_canonical_not_changed(self):
        result, changed = transform_grupos_requeridos(["flora_urbana"])
        assert result == ["flora_urbana"]
        assert changed is False

    def test_flora_urbana_mixed_case_normalized(self):
        result, changed = transform_grupos_requeridos(["Flora Urbana", "vivero"])
        assert result == ["flora_urbana", "vivero"]
        assert changed is True

    def test_position_of_first_variant_preserved(self):
        """First occurrence of a flora_urbana variant keeps its position."""
        result, changed = transform_grupos_requeridos(["vivero", "cuadrilla", "acustica"])
        assert result == ["vivero", "flora_urbana", "acustica"]
        assert changed is True

    def test_three_variants_collapsed_to_one(self):
        result, changed = transform_grupos_requeridos(
            ["cuadrilla", "Flora Urbana", "flora_urbana"]
        )
        assert result == ["flora_urbana"]
        assert changed is True

    def test_non_flora_urbana_entries_preserved_order(self):
        result, changed = transform_grupos_requeridos(
            ["acustica", "cuadrilla", "vivero", "gobernanza"]
        )
        assert result == ["acustica", "flora_urbana", "vivero", "gobernanza"]
        assert changed is True


# ---------------------------------------------------------------------------
# personal_operativo needs_migration / transform
# ---------------------------------------------------------------------------

class TestPersonalOperativoNeedsMigration:
    def test_cuadrilla_needs_migration(self):
        assert po_needs_migration({"grupo": "cuadrilla"}) is True

    def test_flora_urbana_parenthetical_needs_migration(self):
        assert po_needs_migration({"grupo": "Flora Urbana (cuadrilla)"}) is True

    def test_already_canonical_skipped(self):
        assert po_needs_migration({"grupo": "flora_urbana"}) is False

    def test_other_group_skipped(self):
        assert po_needs_migration({"grupo": "vivero"}) is False

    def test_none_grupo_skipped(self):
        assert po_needs_migration({"grupo": None}) is False

    def test_Cuadrilla_capitalized_needs_migration(self):
        assert po_needs_migration({"grupo": "Cuadrilla"}) is True


class TestPersonalOperativoTransformDoc:
    def test_transform_sets_canonical_grupo(self):
        doc = {"id": "po-001", "nombre": "Juan", "grupo": "cuadrilla"}
        result = po_transform(doc)
        assert result["grupo"] == "flora_urbana"

    def test_transform_preserves_other_fields(self):
        doc = {"id": "po-001", "nombre": "Juan", "grupo": "Cuadrilla", "cargo": "operador"}
        result = po_transform(doc)
        assert result["nombre"] == "Juan"
        assert result["cargo"] == "operador"

    def test_transform_does_not_mutate_original(self):
        doc = {"grupo": "cuadrilla"}
        _ = po_transform(doc)
        assert doc["grupo"] == "cuadrilla"
