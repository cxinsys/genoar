"""Every way of being complete and wrong still produces a BAM.

Reads handed over as the wrong mates, a reference for the wrong species, an
input truncated before it arrived -- Cell Ranger runs, exits 0, and writes a
BAM with almost nothing in it. Until now that BAM was the entire definition of
success: `metrics_summary.csv` appeared in this codebase only as a name in a
retention keep-list, and nothing read it.

It is read now, recorded beside the output by the job that produced it, and
compared against floors set far below anything a real experiment reaches. The
point is not to judge the science. It is to notice that there isn't any.
"""

import json
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_SH = REPO / "srr_pipeline_package" / "pipeline_next" / "run_docker_pipeline.sh"
SNAKEFILE = REPO / "srr_pipeline_package" / "pipeline_next" / "Snakefile_hs.smk"


def _provenance():
    lines = RUN_SH.read_text().split("\n")
    start = 'python3 - "$@" << \'PY\''
    i = next(n for n, l in enumerate(lines) if l.strip() == start)
    j = next(n for n in range(i + 1, len(lines)) if lines[n] == "PY")
    body = "\n".join(lines[i + 1:j]) + "\n"
    body = body[:body.index("\nsub = sys.argv[1]")]
    mod = types.ModuleType("genoar_provenance")
    exec(compile(body, str(RUN_SH), "exec"), mod.__dict__)
    return mod


def _metrics_reader():
    source = SNAKEFILE.read_text()
    block = source[source.index("METRIC_KEYS = ("):
                   source.index("def record_cellranger_completed")]
    ns = {}
    exec("import csv, os\n" + block, ns)
    return ns


prov = _provenance()
snake = _metrics_reader()

FLOORS = {"min_cells": 100, "min_valid_barcodes": 0.10, "min_transcriptome": 0.05}
GOOD = {"Estimated Number of Cells": 12630.0, "Valid Barcodes": 0.937,
        "Reads Mapped Confidently to Transcriptome": 0.499}


class TestReadingCellRangersOwnSummary:

    def _write(self, tmp_path, text):
        outs = tmp_path / "outs"
        outs.mkdir(exist_ok=True)
        (outs / "metrics_summary.csv").write_text(text)
        return str(outs / "possorted_genome_bam.bam")

    def test_the_pilots_numbers_come_back(self, tmp_path):
        bam = self._write(tmp_path,
            'Estimated Number of Cells,Mean Reads per Cell,Valid Barcodes,'
            'Reads Mapped Confidently to Transcriptome\n'
            '"12,630","4,262",93.7%,49.9%\n')
        found = snake["read_metrics"](bam)
        assert found["Estimated Number of Cells"] == 12630
        assert found["Valid Barcodes"] == pytest.approx(0.937)
        assert found["Reads Mapped Confidently to Transcriptome"] == pytest.approx(0.499)

    def test_quoted_thousands_separators_are_numbers(self, tmp_path):
        bam = self._write(tmp_path, 'Number of Reads\n"53,845,176"\n')
        assert snake["read_metrics"](bam)["Number of Reads"] == 53845176

    def test_percentages_become_fractions(self, tmp_path):
        bam = self._write(tmp_path, 'Valid Barcodes\n0.4%\n')
        assert snake["read_metrics"](bam)["Valid Barcodes"] == pytest.approx(0.004)

    @pytest.mark.parametrize("text", ["", "only a header\n", "not,a,csv",
                                      'Valid Barcodes\nnot-a-number\n'])
    def test_anything_unreadable_says_nothing(self, tmp_path, text):
        assert snake["read_metrics"](self._write(tmp_path, text)) in ({}, {})

    def test_a_missing_file_says_nothing(self, tmp_path):
        assert snake["read_metrics"](str(tmp_path / "outs" / "x.bam")) == {}


class TestWhatCountsAsNotAResult:

    def _below(self, metrics, **floors):
        environment = dict(FLOORS)
        environment.update(floors)
        return prov.below_the_floor({"metrics": metrics}, environment)

    def test_a_good_run_is_not_flagged(self):
        assert self._below(GOOD) is None

    def test_the_wrong_mates_show_up_as_barcodes(self):
        """R1 and R2 swapped: Cell Ranger finds almost no valid barcodes."""
        swapped = dict(GOOD, **{"Valid Barcodes": 0.004,
                                "Estimated Number of Cells": 3.0})
        complaint = self._below(swapped)
        assert complaint and "valid barcodes" in complaint and "cells" in complaint

    def test_the_wrong_species_shows_up_as_mapping(self):
        mouse = dict(GOOD, **{"Reads Mapped Confidently to Transcriptome": 0.011})
        complaint = self._below(mouse)
        assert complaint and "transcriptome" in complaint

    def test_the_numbers_are_in_the_complaint(self):
        complaint = self._below(dict(GOOD, **{"Estimated Number of Cells": 7.0}))
        assert "7" in complaint and "100" in complaint

    def test_a_floor_of_zero_is_off(self):
        assert self._below(dict(GOOD, **{"Estimated Number of Cells": 1.0}),
                           min_cells=0) is None

    def test_no_metrics_recorded_is_not_a_failure(self):
        """Output made before this existed says nothing either way."""
        assert prov.below_the_floor({"metrics": {}}, FLOORS) is None
        assert prov.below_the_floor({}, FLOORS) is None

    def test_no_floors_recorded_is_not_a_failure(self):
        assert prov.below_the_floor({"metrics": {"Valid Barcodes": 0.0}},
                                    {"cellranger_version": "8.0.1"}) is None

    def test_a_metric_cell_ranger_did_not_write_is_not_a_failure(self):
        assert self._below({"Estimated Number of Cells": 12630.0}) is None


class TestTheStatusIsItsOwnThing:
    """`suspect` is not `missing`, and the remedy is not the same."""

    def test_it_is_not_counted_as_complete(self):
        source = RUN_SH.read_text()
        assert 'counts["completed"] = (counts["fresh"] + counts["cache_hit"]' in source
        completed = source[source.index('counts["completed"] = ('):]
        completed = completed[:completed.index("\n\n")]
        assert "suspect" not in completed

    def test_it_is_counted_and_reported(self):
        source = RUN_SH.read_text()
        assert "suspect=0" in source
        assert '"suspect")' in source or '"suspect",' in source

    def test_the_reason_says_where_to_look(self):
        source = RUN_SH.read_text()
        assert "which reads were given as R1" in source
        assert "which reference was used" in source

    def test_the_reconciliation_does_not_count_it_as_done(self):
        report = (REPO / "srr_pipeline_package" / "hpc"
                  / "corpus_report.py").read_text()
        complete = report[report.index("COMPLETE = ("):]
        complete = complete[:complete.index(")") + 1]
        assert "suspect" not in complete


class TestTheEndingSaysWhatHappened:
    """`processed 0 samples` is the one thing that is not true of a suspect run.

    Cell Ranger ran. It produced output. What it produced is not a result, and
    the thing to look at is the input -- so sending the reader to the front of
    the pipeline is sending them to the wrong end.
    """

    def _final_block(self):
        source = RUN_SH.read_text()
        start = source.index("case \"$CELLRANGER_STAGE\" in")
        return source[start:]

    def test_a_suspect_run_does_not_claim_nothing_was_processed(self):
        block = self._final_block()
        assert 'if [[ "${PROV_SUSPECT:-0}" -gt 0 ]]; then' in block
        suspect = block[block.index('if [[ "${PROV_SUSPECT:-0}" -gt 0 ]]; then'):]
        suspect = suspect[:suspect.index("\n    fi\n")]
        assert "PROCESSED 0 SAMPLES" not in suspect
        assert "PRODUCED NOTHING USABLE" in suspect

    def test_it_points_at_the_input(self):
        block = self._final_block()
        assert "it is the input that is wrong" in block
        assert "which reads were given as R1 and R2" in block

    def test_it_still_exits_non_zero(self):
        block = self._final_block()
        suspect = block[block.index('if [[ "${PROV_SUSPECT:-0}" -gt 0 ]]; then'):]
        suspect = suspect[:suspect.index("\n    fi\n")]
        assert 'exit "$EXIT_NOTHING_PROCESSED"' in suspect

    def test_the_summary_line_counts_them(self):
        source = RUN_SH.read_text()
        assert "produced but not a result:" in source
