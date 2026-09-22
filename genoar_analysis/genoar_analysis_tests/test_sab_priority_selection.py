#!/usr/bin/env python3
"""
SAB Priority Selection Regression Tests
=======================================

Regression cover for UMLSCSVReader.get_sab_priority_selection().

Written as::

    df.groupby('STR').apply(select_priority_sab)

the callback reaches for ``group['STR']`` when a term has more than one row
under the winning SAB. From pandas 3.0 onwards DataFrameGroupBy.apply hands the
callback a frame with the grouping column removed, so that line raises
KeyError: 'STR' - but only for data that actually contains such a duplicate.
On a corpus without one the function returns normally, which is why the fault
looks data-dependent (cell_type fails, tissue and disease_state_modified do
not) rather than like a plain breakage.

These tests use small synthetic frames, no crawl output and no UMLS files.

Usage:
    pytest genoar_analysis_tests/test_sab_priority_selection.py
    python genoar_analysis_tests/test_sab_priority_selection.py
"""

import sys
import warnings
from pathlib import Path

import pandas as pd

# Add parent directory to path. Inserted at the front, not appended: the image
# also carries an installed copy of the package, and an appended path would let
# that copy shadow the tree under test when this file is run as a script.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from genoar_analysis.io.umls_readers import UMLSCSVReader

from conftest import run_test_suite


# CUI, STR, SAB, STY behind an unnamed index column: the shape of a pandas
# to_csv() export, which is what the shipped tables have and the reader
# drops if present.
UMLS_COLUMNS = ['Unnamed: 0', 'CUI', 'STR', 'SAB', 'STY']


def _frame(rows):
    """Build a UMLS-shaped frame from (CUI, STR, SAB, STY) tuples."""
    return pd.DataFrame(
        [(i,) + row for i, row in enumerate(rows)],
        columns=UMLS_COLUMNS,
    )


def _reader():
    """A reader instance; these tests never touch its data directory."""
    return UMLSCSVReader('all_query_results')


def test_duplicate_priority_sab_selects_first_and_warns():
    """
    A term with two rows under the same winning SAB must not raise.

    'WT' below is the shape that broke the 919-series run: both of its rows
    carry SAB 'NCI', the highest-priority SAB present for that term, so the
    "multiple entries" branch runs. It has to name the term in its warning
    without reading it back out of the group frame.
    """
    df = _frame([
        ('C0000001', 'WT', 'NCI', 'Fish'),
        ('C0000002', 'WT', 'NCI', 'Gene or Genome'),
    ])

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        result = _reader().get_sab_priority_selection(df, 'cell_type')

    assert len(result) == 1, f"expected one row per term, got {len(result)}"
    assert result['STR'].tolist() == ['WT'], "grouping column lost from the result"
    # The first of the tied rows wins, as it always did.
    assert result['CUI'].tolist() == ['C0000001'], f"wrong row selected: {result['CUI'].tolist()}"

    messages = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
    assert any("'WT'" in m for m in messages), \
        f"warning should name the term, got: {messages}"
    assert any("'NCI'" in m for m in messages), \
        f"warning should name the chosen SAB, got: {messages}"


def test_priority_order_is_respected_across_terms():
    """Higher-priority SAB wins; a term with no priority SAB keeps its first row."""
    # cell_type priority: MSH, NCI, SNOMEDCT_US, SNM, MEDCIN, CHV, ...
    df = _frame([
        ('C0000010', 'B cell', 'NCI', 'Cell'),
        ('C0000011', 'B cell', 'MSH', 'Cell'),          # MSH outranks NCI
        ('C0000012', 'oddball', 'NOTAPRIORITY', 'Cell'),  # no priority SAB at all
        ('C0000013', 'oddball', 'ALSONOT', 'Cell'),
        ('C0000014', 'WT', 'NCI', 'Fish'),
        ('C0000015', 'WT', 'NCI', 'Gene or Genome'),
    ])

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        result = _reader().get_sab_priority_selection(df, 'cell_type')

    # Groups come back in sorted STR order, one row per term.
    selected = dict(zip(result['STR'], result['CUI']))
    assert selected == {
        'B cell': 'C0000011',
        'oddball': 'C0000012',
        'WT': 'C0000014',
    }, f"unexpected selection: {selected}"
    assert result['STR'].tolist() == sorted(result['STR'].tolist()), \
        "result should be ordered by term"


def test_result_keeps_the_input_columns_in_order():
    """
    The selected frame must carry every input column, in the input order.

    This is what makes the result independent of the pandas version: the rows
    are taken from the input frame rather than rebuilt out of whatever the
    groupby machinery chose to hand back.
    """
    df = _frame([
        ('C0000020', 'liver', 'MSH', 'Body Part'),
        ('C0000021', 'liver', 'FMA', 'Body Part'),
        ('C0000022', 'lung', 'SNOMEDCT_US', 'Body Part'),
    ])

    result = _reader().get_sab_priority_selection(df, 'tissue')

    assert list(result.columns) == UMLS_COLUMNS, \
        f"columns changed: {list(result.columns)}"
    assert result.index.tolist() == list(range(len(result))), \
        "result index should be a clean range"
    assert result['CUI'].tolist() == ['C0000020', 'C0000022']


def test_empty_input_is_returned_unchanged():
    """An empty match set is a normal outcome, not an error."""
    empty = _frame([])

    result = _reader().get_sab_priority_selection(empty, 'cell_type')

    assert result.empty
    assert list(result.columns) == UMLS_COLUMNS


def main():
    tests = [
        ("Duplicate priority SAB selects first and warns",
         test_duplicate_priority_sab_selects_first_and_warns),
        ("Priority order respected across terms",
         test_priority_order_is_respected_across_terms),
        ("Result keeps input columns in order",
         test_result_keeps_the_input_columns_in_order),
        ("Empty input returned unchanged",
         test_empty_input_is_returned_unchanged),
    ]
    return run_test_suite("SAB PRIORITY SELECTION TEST SUMMARY", tests)


if __name__ == "__main__":
    sys.exit(main())
