"""Tests de la utilidad de normalización de nombres de grupo."""
from app.utils.text_utils import normalize_grupo, grupos_match, strip_accents


class TestNormalizeGrupo:
    def test_acustica_with_accent_matches_without_accent(self):
        assert normalize_grupo("Acústica") == "acustica"
        assert normalize_grupo("acustica") == "acustica"
        assert normalize_grupo("ACÚSTICA") == "acustica"
        assert normalize_grupo("  Acústica  ") == "acustica"

    def test_handles_none_and_empty(self):
        assert normalize_grupo(None) == ""
        assert normalize_grupo("") == ""
        assert normalize_grupo("   ") == ""

    def test_keeps_multiword_groups(self):
        assert normalize_grupo("Recurso Hídrico") == "recurso hidrico"
        assert normalize_grupo("Residuos Sólidos") == "residuos solidos"
        assert normalize_grupo("Flora Silvestre") == "flora silvestre"

    def test_underscore_equivalent_to_space(self):
        # Variantes con "_" deben normalizarse igual que con espacio.
        assert normalize_grupo("Central_Social") == "central social"
        assert normalize_grupo("Central Social") == "central social"
        assert normalize_grupo("central_social") == normalize_grupo("Central Social")
        assert normalize_grupo("Fauna_Silvestre") == normalize_grupo("Fauna Silvestre")
        assert normalize_grupo("Flora_Urbana") == normalize_grupo("Flora Urbana")

    def test_collapses_repeated_whitespace(self):
        assert normalize_grupo("Central   Social") == "central social"
        assert normalize_grupo("Central_ _Social") == "central social"
        assert normalize_grupo("  Flora__Urbana  ") == "flora urbana"

    def test_accents_are_stripped_for_spanish_groups(self):
        assert normalize_grupo("Reacción") == "reaccion"
        assert normalize_grupo("REACCIÓN") == "reaccion"
        assert normalize_grupo("reaccion") == "reaccion"

    def test_strip_accents_preserves_letters(self):
        assert strip_accents("ñoño") == "nono"
        assert strip_accents("Cañón") == "Canon"

    def test_grupos_match_is_symmetric_and_case_insensitive(self):
        assert grupos_match("Acústica", "acustica")
        assert grupos_match("Acustica", "ACÚSTICA")
        assert grupos_match("cuadrilla", "Cuadrilla")
        assert not grupos_match("Acústica", "Cuadrilla")
        assert not grupos_match(None, "Acustica")
        # Both None/empty count as same (empty grupo)
        assert grupos_match(None, None)
        assert grupos_match("", None)

    def test_grupos_match_treats_underscore_and_accents_as_equivalent(self):
        # Casos reportados por el frontend (issue: grupos con "_" o tildes
        # no listaban todos sus integrantes).
        assert grupos_match("Central Social", "Central_Social")
        assert grupos_match("central_social", "Central Social")
        assert grupos_match("Fauna Silvestre", "fauna_silvestre")
        assert grupos_match("Flora Urbana", "FLORA_URBANA")
        assert grupos_match("Reacción", "reaccion")
        assert grupos_match("Reacción", "REACCION")
