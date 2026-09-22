"""Unit tests for normalization utilities."""

from app.utils.normalization import escape_like, normalize_text


class TestNormalizeText:
    def test_strip_whitespace(self):
        assert normalize_text("  hello  ") == "hello"

    def test_collapse_internal_spaces(self):
        assert normalize_text("bone   marrow") == "bone marrow"

    def test_tabs_and_newlines(self):
        assert normalize_text("bone\t\nmarrow") == "bone marrow"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_single_word(self):
        assert normalize_text("blood") == "blood"


class TestEscapeLike:
    def test_no_special_chars(self):
        assert escape_like("bone marrow") == "bone marrow"

    def test_percent_escaped(self):
        assert escape_like("50%") == "50\\%"

    def test_underscore_escaped(self):
        assert escape_like("cell_type") == "cell\\_type"

    def test_backslash_escaped(self):
        assert escape_like("path\\to") == "path\\\\to"

    def test_multiple_specials(self):
        assert escape_like("%_\\") == "\\%\\_\\\\"

    def test_empty_string(self):
        assert escape_like("") == ""
