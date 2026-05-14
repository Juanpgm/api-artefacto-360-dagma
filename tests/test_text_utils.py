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
