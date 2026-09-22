"""Unit tests for genoar_crawler.parsing.

The crawler runs as a flat script (its directory is on sys.path at runtime), so
the test adds that directory to sys.path and imports ``parsing`` directly,
keeping these tests free of the Selenium dependency pulled in by
``genoar_crawler.py``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parsing import parse_grep_count  # noqa: E402


@pytest.mark.parametrize(
    "output, expected",
    [
        # The exact regression: `grep -c` printed "0" then `|| echo 0` added another.
        ("0\n0", 0),
        ("0\n0\n", 0),
        # Normal single-count outputs.
        ("0", 0),
        ("5", 5),
        ("5\n", 5),
        ("42\n", 42),
        # Defensive cases: empty / whitespace / non-numeric noise.
        ("", 0),
        ("   \n  ", 0),
        ("\n\n", 0),
        # First integer token wins even with trailing junk.
        ("7\n0", 7),
        ("  12  \n0\n", 12),
    ],
)
def test_parse_grep_count(output, expected):
    assert parse_grep_count(output) == expected


def test_parse_grep_count_does_not_raise_on_legacy_double_zero():
    """The original bug raised ValueError: invalid literal for int() ... '0\\n0'."""
    # Must not raise; this is the whole point of the fix.
    assert parse_grep_count("0\n0") == 0
