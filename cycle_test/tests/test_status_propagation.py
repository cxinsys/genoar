"""Negative tests for outcome reporting across the orchestrator.

Each one drives a path where an incomplete or failed run could be reported as a
success, so a green suite here means the orchestrator cannot hand automation a
false success.

Testing the report aggregation alone is not enough: fed already-failed cycles it
never exercises the paths that *produce* a status. These drive
run_single_cycle() and the exit-code mapping directly instead.
"""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from cycle_test.utils.stage1_runner import (
    classify_stage1_outcome,
    COMPLETION_MANIFEST_FILE,
)
from cycle_test.utils.stage3_runner import EXIT_CONFIG_ERROR, EXIT_PARTIAL

# The crawler validates the run ids it is handed through GENOAR_RUN_ID. Stage 3
# reads the same variable, so the two are asked the same questions below.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "genoar_crawler"))
import genoar_crawler as gc  # noqa: E402


def _manifest(d: Path, **over):
    data = {
        "status": "completed",
        "requested_pages": {"start": 1, "end": 5},
        "completed_pages": {"start": 1, "end": 5},
        "items_per_page": 500,
    }
    data.update(over)
    (d / COMPLETION_MANIFEST_FILE).write_text(json.dumps(data))


class TestManifestProvesTheRequestedRange:
    """A manifest is only evidence if it is evidence of *this* request."""

    def test_matching_range_is_success(self, tmp_path):
        _manifest(tmp_path)
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=5)
        assert status == "success"

    def test_manifest_for_a_different_range_is_rejected(self, tmp_path):
        # A leftover manifest from another run, or a resume that covered other
        # pages, must not pass as completion of the pages we asked for.
        _manifest(
            tmp_path,
            requested_pages={"start": 999, "end": 999},
            completed_pages={"start": 999, "end": 999},
        )
        status, error = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=5)
        assert status == "failed"
        assert "range" in error.lower()

    def test_partially_covered_range_is_rejected(self, tmp_path):
        # Asked for 1-5, only 1-3 completed.
        _manifest(tmp_path, completed_pages={"start": 1, "end": 3})
        status, error = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=5)
        assert status == "failed"

    def test_all_pages_request_needs_the_resolved_end_recorded(self, tmp_path):
        # end_page 0 means "all pages". The crawler resolves the real end, and the
        # manifest has to say what it was, or "completed" cannot be checked at all.
        # See TestManifestSchema for the case this guards.
        _manifest(
            tmp_path,
            requested_pages={"start": 1, "end": 0},
            completed_pages={"start": 1, "end": 37},
            resolved_end_page=37,
        )
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=0)
        assert status == "success"

    def test_missing_range_fields_are_rejected(self, tmp_path):
        # An old-format manifest cannot prove the range, so it is not evidence.
        (tmp_path / COMPLETION_MANIFEST_FILE).write_text(json.dumps({"status": "completed"}))
        status, error = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=5)
        assert status == "failed"


class TestStage3FailurePropagates:
    """A Stage 3 that failed must not leave the cycle reported as success."""

    def _run_cycle(self, tmp_path, download_result, srr_ids=1):
        import logging

        from cycle_test import run_cycle_test as rct

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        prep = {"success": True, "unique_srr_ids": srr_ids, "gse_count": 1}

        # The SRR list is what Stage 3 downloads from; absent means nothing to do.
        if srr_ids:
            (dirs["stage3"] / "srr_list.txt").write_text("SRR000001\n")

        with mock.patch.object(rct, "extract_srr_from_stage2_tables", return_value=prep), \
             mock.patch.object(rct, "download_sra_files", return_value=download_result), \
             mock.patch.object(rct, "run_stage3_pipeline", return_value={"success": True}):
            # Stage 3 only, so Stage 1/2 are skipped and the cycle's outcome is
            # entirely down to Stage 3.
            return rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs,
                umls_dir=tmp_path, logger=logging.getLogger("test"),
                stage3_only=True, download_only=True,
            )

    def test_download_failure_is_a_partial_cycle(self, tmp_path):
        result = self._run_cycle(
            tmp_path, {"success": False, "total": 3, "downloaded": 1, "failed": 2}
        )
        assert result["status"] == "partial_success"

    def test_stage3_requested_with_no_ids_is_no_data(self, tmp_path):
        # Stage 3 was asked for but the data yielded nothing to download. That is
        # not a failure of anything, but it is not the requested work either.
        result = self._run_cycle(
            tmp_path, {"success": True, "total": 0, "downloaded": 0, "failed": 0}, srr_ids=0
        )
        assert result["status"] == "no_data"

    def test_a_clean_download_still_succeeds(self, tmp_path):
        result = self._run_cycle(
            tmp_path, {"success": True, "total": 1, "downloaded": 1, "failed": 0}
        )
        assert result["status"] == "success"


class TestInterruptAlwaysReachesTheExitCode:
    """A user interrupt must surface as 130 wherever it happened."""

    def test_interrupt_between_cycles_is_130(self):
        from cycle_test.run_cycle_test import _exit_code_for

        # The interrupt arrived while waiting after a successful cycle, so no
        # "interrupted" cycle was ever appended. The run was still cut short.
        assert _exit_code_for([{"status": "success"}], interrupted=True) == 130

    def test_uninterrupted_success_is_still_0(self):
        from cycle_test.run_cycle_test import _exit_code_for

        assert _exit_code_for([{"status": "success"}], interrupted=False) == 0

    def test_interrupted_cycle_is_130_without_the_flag(self):
        from cycle_test.run_cycle_test import _exit_code_for

        assert _exit_code_for([{"status": "interrupted"}], interrupted=False) == 130


class TestStage3PrepFailurePropagates:
    """A prep that errored is not the same as a prep that found nothing."""

    def _run(self, tmp_path, prep):
        import logging
        from cycle_test import run_cycle_test as rct

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        with mock.patch.object(rct, "extract_srr_from_stage2_tables", return_value=prep), \
             mock.patch.object(rct, "download_sra_files",
                               return_value={"success": True, "total": 1, "downloaded": 1, "failed": 0}), \
             mock.patch.object(rct, "run_stage3_pipeline", return_value={"success": True}):
            return rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs, umls_dir=tmp_path,
                logger=logging.getLogger("test"), stage3_only=True, download_only=True,
            )

    def test_prep_error_is_a_failure_not_an_empty_result(self, tmp_path):
        # Prep failing (disk full, unreadable tables) yielded 0 ids and was read
        # as "nothing to do", which finalized as success.
        result = self._run(tmp_path, {"success": False, "unique_srr_ids": 0, "error": "disk full"})
        assert result["status"] == "failed_stage3"

    def test_prep_finding_no_ids_is_not_an_error(self, tmp_path):
        # A clean prep over data that genuinely has no SRR ids is not a failure;
        # with Stage 3 requested it is reported as no_data, not failed_stage3.
        result = self._run(tmp_path, {"success": True, "unique_srr_ids": 0, "gse_count": 0})
        assert result["status"] == "no_data"


class TestPipelineSampleFailuresPropagate:
    """Cell Ranger failing on some samples is not a fully successful cycle."""

    def _run(self, tmp_path, pipeline_result):
        import logging
        from cycle_test import run_cycle_test as rct

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        (dirs["stage3"] / "srr_list.txt").write_text("SRR000001\n")
        with mock.patch.object(rct, "extract_srr_from_stage2_tables",
                               return_value={"success": True, "unique_srr_ids": 1, "gse_count": 1}), \
             mock.patch.object(rct, "download_sra_files",
                               return_value={"success": True, "total": 1, "downloaded": 1, "failed": 0}), \
             mock.patch.object(rct, "run_stage3_pipeline", return_value=pipeline_result):
            return rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs, umls_dir=tmp_path,
                logger=logging.getLogger("test"), stage3_only=True,
            )

    def test_some_samples_failing_is_partial(self, tmp_path):
        result = self._run(
            tmp_path, {"success": True, "successful_samples": 1, "failed_samples": 2}
        )
        assert result["status"] == "partial_success"

    def test_every_sample_failing_is_a_stage3_failure(self, tmp_path):
        result = self._run(
            tmp_path, {"success": True, "successful_samples": 0, "failed_samples": 3}
        )
        assert result["status"] == "failed_stage3"

    def test_all_samples_succeeding_is_success(self, tmp_path):
        result = self._run(
            tmp_path, {"success": True, "successful_samples": 3, "failed_samples": 0}
        )
        assert result["status"] == "success"


class TestManifestSchema:
    """The manifest is evidence only if its fields are well-formed and match."""

    def test_requested_range_must_match_the_current_request(self, tmp_path):
        # completed_pages covers 1-5, but this manifest was written for a
        # different request, so it is not evidence about ours.
        _manifest(
            tmp_path,
            requested_pages={"start": 999, "end": 999},
            completed_pages={"start": 1, "end": 5},
        )
        status, error = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=5)
        assert status == "failed"

    def test_all_pages_request_needs_the_resolved_end(self, tmp_path):
        # "All pages" with only page 1 done is not a completed crawl. The crawler
        # records the end it resolved, so a manifest claiming 1-1 for an
        # all-pages request must say that was the real last page.
        _manifest(
            tmp_path,
            requested_pages={"start": 1, "end": 0},
            completed_pages={"start": 1, "end": 1},
        )
        status, error = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=0)
        assert status == "failed"

    def test_all_pages_with_matching_resolved_end_is_success(self, tmp_path):
        _manifest(
            tmp_path,
            requested_pages={"start": 1, "end": 0},
            completed_pages={"start": 1, "end": 37},
            resolved_end_page=37,
        )
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=0)
        assert status == "success"

    def test_non_integer_page_numbers_are_reported_not_raised(self, tmp_path):
        # A malformed manifest must come back as a failure, not crash the runner.
        _manifest(tmp_path, completed_pages={"start": "x", "end": 5})
        status, error = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=5)
        assert status == "failed"


class TestUnexpectedExceptionFailsTheRun:
    def test_exception_after_a_successful_cycle_is_not_exit_0(self):
        from cycle_test.run_cycle_test import _exit_code_for

        # The run crashed partway; the cycles that did finish do not make it a
        # successful run.
        assert _exit_code_for([{"status": "success"}], run_failed=True) == 1

    def test_clean_run_is_still_0(self):
        from cycle_test.run_cycle_test import _exit_code_for

        assert _exit_code_for([{"status": "success"}], run_failed=False) == 0


class TestManifestPageValues:
    """Page numbers have to be plausible, not merely integers."""

    def test_all_pages_with_a_zero_resolved_end_is_rejected(self, tmp_path):
        # "From page 5 to the end" cannot have resolved to 0 pages.
        _manifest(
            tmp_path,
            requested_pages={"start": 5, "end": 0},
            completed_pages={"start": 5, "end": 5},
            resolved_end_page=0,
        )
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=5, end_page=0)
        assert status == "failed"

    def test_all_pages_resolved_before_the_start_is_rejected(self, tmp_path):
        _manifest(
            tmp_path,
            requested_pages={"start": 5, "end": 0},
            completed_pages={"start": 5, "end": 5},
            resolved_end_page=3,
        )
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=5, end_page=0)
        assert status == "failed"

    def test_all_pages_completed_end_must_equal_resolved(self, tmp_path):
        # Claiming more pages than the crawl resolved is not evidence either.
        _manifest(
            tmp_path,
            requested_pages={"start": 1, "end": 0},
            completed_pages={"start": 1, "end": 99},
            resolved_end_page=37,
        )
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=0)
        assert status == "failed"

    def test_page_numbers_below_one_are_rejected(self, tmp_path):
        _manifest(tmp_path, completed_pages={"start": 0, "end": 5})
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=5)
        assert status == "failed"

    def test_a_reversed_range_is_rejected(self, tmp_path):
        _manifest(tmp_path, completed_pages={"start": 5, "end": 1})
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=5)
        assert status == "failed"


class TestRequestedRangeIsValidated:
    """The request itself has to be a real range, not only match the manifest."""

    def test_reversed_fixed_range_request_is_rejected(self, tmp_path):
        _manifest(
            tmp_path,
            requested_pages={"start": 5, "end": 3},
            completed_pages={"start": 5, "end": 5},
        )
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=5, end_page=3)
        assert status == "failed"

    def test_negative_end_request_is_rejected(self, tmp_path):
        _manifest(
            tmp_path,
            requested_pages={"start": 1, "end": -1},
            completed_pages={"start": 1, "end": 1},
        )
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=-1)
        assert status == "failed"

    def test_start_page_below_one_is_rejected(self, tmp_path):
        _manifest(
            tmp_path,
            requested_pages={"start": 0, "end": 5},
            completed_pages={"start": 0, "end": 5},
        )
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=0, end_page=5)
        assert status == "failed"

    def test_a_normal_request_still_passes(self, tmp_path):
        _manifest(tmp_path)  # requested 1-5, completed 1-5
        status, _ = classify_stage1_outcome(0, tmp_path, start_page=1, end_page=5)
        assert status == "success"


class _FakeProc:
    """A container run that produced no output and exited with `returncode`."""

    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = io.StringIO("")

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode

    def kill(self):  # pragma: no cover - only reached on a timeout
        pass


def _install_cellranger(tmp_path):
    """A Cell Ranger install that satisfies the prerequisite check.

    The whole tree, not just the launcher. Cell Ranger reads .env.json,
    external/, lib/ and mro/ beside the binary and refuses to start without
    them, so a directory holding only an executable is not an install that
    passes — it is the K-BDS failure with the check looking the other way.
    """
    cellranger = tmp_path / "cellranger"
    cellranger.mkdir(exist_ok=True)
    binary = cellranger / "cellranger"
    binary.write_text('#!/bin/sh\necho "cellranger cellranger-8.0.1"\n')
    binary.chmod(0o755)
    (cellranger / ".env.json").write_text("{}\n")
    for part in ("external", "lib", "mro"):
        (cellranger / part).mkdir(exist_ok=True)
    return cellranger


def _install_reference(tmp_path, omit=()):
    """A 10x reference layout, optionally missing some of its parts."""
    ref = tmp_path / "ref"
    ref.mkdir(exist_ok=True)
    if "reference.json" not in omit:
        (ref / "reference.json").write_text('{"genomes": ["GRCh38"]}\n')
    for name in ("fasta", "genes", "star"):
        if name not in omit:
            (ref / name).mkdir(exist_ok=True)
    return ref


def _stage3_runner(tmp_path):
    """A Stage 3 runner whose prerequisites are satisfied by fixtures on disk."""
    from cycle_test.utils.stage3_runner import Stage3PipelineRunner

    sra = tmp_path / "sra" / "SRR1"
    sra.mkdir(parents=True)
    (sra / "SRR1.sra").write_bytes(b"NCBI.sra")
    cellranger = _install_cellranger(tmp_path)
    ref = _install_reference(tmp_path)

    return Stage3PipelineRunner(
        sra_dir=tmp_path / "sra",
        results_dir=tmp_path / "results",
        logs_dir=tmp_path / "logs",
        cellranger_path=cellranger,
        ref_genome_path=ref,
    )


def _fastq_conversion_succeeded(results_dir: Path, samples=("SRR1",)):
    """What the pipeline writes when FASTQ conversion worked. Says nothing about
    Cell Ranger, which is the whole point of these tests."""
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "fastq_success.txt").write_text("".join(f"{s}\n" for s in samples))


def _cellranger_output(results_dir: Path, sample="SRR1"):
    """A sample with the BAM Cell Ranger leaves behind when it really ran."""
    outs = results_dir / "success" / sample / "cellranger_output" / "outs"
    outs.mkdir(parents=True, exist_ok=True)
    (outs / "possorted_genome_bam.bam").write_bytes(b"BAM")


def _run_record(runner, samples, expected=None, others=()):
    """The outcome.json a run writes about itself, as the pipeline writes it.

    Every image that carries run records writes one on every path it can exit
    by, including the ones that analyse nothing, so this is what the runner
    reads in production. A run that leaves none is not a quieter version of
    these cases — it is a run that said nothing about itself, and it has its
    own class below.

    `samples` maps a sample name to its status, or to (status, reason).
    """
    statuses = {}
    counts = dict(fresh=0, cache_hit=0, adopted=0, ineligible=0, missing=0,
                  unverified=0, stale=0)
    for name, value in samples.items():
        status, reason = value if isinstance(value, tuple) else (value, None)
        statuses[name] = {"sample": name, "status": status}
        if reason:
            statuses[name]["reason"] = reason
        counts[status] += 1
    names = sorted(samples if expected is None else expected)
    counts["expected"] = len(names)
    counts["completed"] = counts["fresh"] + counts["cache_hit"]
    runner.run_record_dir.mkdir(parents=True, exist_ok=True)
    (runner.run_record_dir / "outcome.json").write_text(json.dumps({
        "run_id": runner.run_id,
        "expected_samples": names,
        "counts": counts,
        "samples": statuses,
        "other_samples_with_output": sorted(others),
    }))


def _step_sentinel(results_dir: Path, step: int, kind: str, message: str = ""):
    reports = results_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"step{step}.{kind}").write_text("")
    if message:
        (reports / f"step{step}.msg").write_text(message + "\n")


def _run_stage3(tmp_path, returncode, record=None, expected=None, others=()):
    """One Stage 3 run. `record` is the outcome.json the container left, if any."""
    from cycle_test.utils import stage3_runner as s3

    runner = _stage3_runner(tmp_path)
    if record is not None:
        _run_record(runner, record, expected=expected, others=others)
    with mock.patch.object(runner, "check_prerequisites",
                           return_value={"errors": [], "sra_files_count": 1}), \
         mock.patch.object(s3.subprocess, "Popen", return_value=_FakeProc(returncode)):
        return runner.run_pipeline(timeout_hours=1)


class TestStage3SuccessNeedsCellRangerOutput:
    """FASTQ conversion is not analysis.

    total_samples comes from fastq_success.txt, so "converted some FASTQs and
    the container returned 0" must not be enough for success: that grades a run
    which processed zero samples as a successful Stage 3. The sibling check on
    exit 3 catches it only on the exit-3 path.
    """

    def test_converted_fastqs_without_cellranger_output_is_not_success(self, tmp_path):
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        _step_sentinel(results, 8, "warn", "cellranger ran no jobs (0 eligible samples)")

        # Exit 0, samples in fastq_success.txt, no BAM anywhere.
        result = _run_stage3(tmp_path, returncode=0,
                             record={"SRR1": ("missing", "no Cell Ranger output")})

        assert result["total_samples"] == 1  # FASTQ conversion did happen
        assert result["cellranger_completed"] == 0
        assert result["success"] is False
        assert result["nothing_processed"] is True

    def test_cellranger_output_present_is_success(self, tmp_path):
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        _cellranger_output(results)
        _step_sentinel(results, 8, "ok")

        result = _run_stage3(tmp_path, returncode=0, record={"SRR1": "fresh"})

        assert result["cellranger_completed"] == 1
        assert result["success"] is True
        assert result["nothing_processed"] is False

    def test_an_outs_directory_without_a_bam_is_not_output(self, tmp_path):
        # A Cell Ranger run killed partway leaves the directory but no BAM. The
        # pipeline counts the BAM, so this runner must not count less.
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        (results / "success" / "SRR1" / "cellranger_output" / "outs").mkdir(parents=True)
        _step_sentinel(results, 8, "warn")

        result = _run_stage3(
            tmp_path, returncode=0,
            record={"SRR1": ("missing", "this run started Cell Ranger for this "
                                        "sample and the job did not finish")})

        assert result["cellranger_completed"] == 0
        assert result["success"] is False

    def test_step8_ok_without_a_bam_is_reported_as_an_error(self, tmp_path):
        # step8.ok claims Cell Ranger did its work; an empty success directory
        # says it did not. A contradiction is not a clean empty result.
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        _step_sentinel(results, 8, "ok")

        result = _run_stage3(tmp_path, returncode=0,
                             record={"SRR1": ("missing", "no Cell Ranger output")})

        assert result["success"] is False
        assert result["nothing_processed"] is False
        assert "step8 ok" in result["error"]

    def test_a_container_failure_is_still_a_failure(self, tmp_path):
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        _cellranger_output(results)

        result = _run_stage3(tmp_path, returncode=1)

        assert result["success"] is False
        assert "code 1" in result["error"]


class TestStage3ExitCode3:
    """Exit 3: a valid, complete run that qualified no sample."""

    INELIGIBLE = ("only 1 FASTQ file; Cell Ranger requires paired R1/R2 reads")

    def _exit3(self, tmp_path, with_census=True):
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        _step_sentinel(results, 8, "warn", "cellranger ran no jobs (0 eligible samples)")
        if with_census:
            success = results / "success"
            success.mkdir(parents=True, exist_ok=True)
            (success / "cellranger_eligibility.tsv").write_text(
                "# sample\tstatus\treason\tdetail\n"
                "SRR1\tineligible\t%s\tfastq=1\n" % self.INELIGIBLE
            )
            record = {"SRR1": ("ineligible", self.INELIGIBLE)}
        else:
            record = {"SRR1": "missing"}
        return _run_stage3(tmp_path, returncode=3, record=record)

    def test_exit_3_is_not_an_error(self, tmp_path):
        result = self._exit3(tmp_path)
        # Nothing malfunctioned, so no error is invented...
        assert result["error"] is None
        # ...and nothing was analysed, so it is not a success either.
        assert result["success"] is False
        assert result["nothing_processed"] is True

    def test_the_reason_names_why_nothing_qualified(self, tmp_path):
        result = self._exit3(tmp_path)
        assert "no sample qualified" in result["nothing_processed_reason"]
        assert "paired R1/R2" in result["nothing_processed_reason"]

    def test_the_census_is_carried_into_the_result(self, tmp_path):
        result = self._exit3(tmp_path)
        assert result["cellranger_ineligible"] == 1
        assert sum(result["cellranger_ineligible_reasons"].values()) == 1

    def test_exit_3_without_a_census_still_reports_nothing_processed(self, tmp_path):
        result = self._exit3(tmp_path, with_census=False)
        assert result["nothing_processed"] is True
        assert result["nothing_processed_reason"]


class TestStepWarnSentinel:
    """step<N>.warn means "ran, did no work" — not "never ran"."""

    def _parse(self, tmp_path, kind):
        runner = _stage3_runner(tmp_path)
        _step_sentinel(tmp_path / "results", 8, kind, "cellranger ran no jobs")
        return runner.parse_results()

    def test_warn_is_reported_as_warn(self, tmp_path):
        parsed = self._parse(tmp_path, "warn")
        assert parsed["step_status"]["step8"] == "warn"

    def test_the_warn_message_is_kept(self, tmp_path):
        parsed = self._parse(tmp_path, "warn")
        assert parsed["step_messages"]["step8"] == "cellranger ran no jobs"

    def test_an_absent_sentinel_is_still_not_run(self, tmp_path):
        runner = _stage3_runner(tmp_path)
        (tmp_path / "results").mkdir(parents=True, exist_ok=True)
        parsed = runner.parse_results()
        assert parsed["step_status"]["step8"] == "not_run"

    def test_err_still_wins_over_warn(self, tmp_path):
        runner = _stage3_runner(tmp_path)
        _step_sentinel(tmp_path / "results", 8, "warn")
        _step_sentinel(tmp_path / "results", 8, "err")
        assert runner.parse_results()["step_status"]["step8"] == "error"


class TestStage1OutcomesReachTheCycle:
    """A capped crawl and an empty range must not read as the same thing, and
    neither may read as a failure."""

    def _run(self, tmp_path, stage1_result):
        import logging
        from cycle_test import run_cycle_test as rct

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 100)
        stage2_calls = {"n": 0}

        def fake_stage2(**kw):
            stage2_calls["n"] += 1
            return {"success": True, "tables": {}}

        with mock.patch.object(rct, "run_stage1", return_value=stage1_result), \
             mock.patch.object(rct, "run_stage2", side_effect=fake_stage2), \
             mock.patch.object(rct, "extract_srr_from_stage2_tables",
                               return_value={"success": True, "unique_srr_ids": 0, "gse_count": 0}):
            result = rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=100, dirs=dirs,
                umls_dir=tmp_path, logger=logging.getLogger("test"),
            )
        return result, stage2_calls["n"]

    def test_a_capped_crawl_is_a_successful_cycle(self, tmp_path):
        # 100 pages asked for, 47 exist, all 47 crawled. The cycle did the work
        # there was to do.
        result, stage2_calls = self._run(tmp_path, {
            "success": True,
            "status": "capped",
            "capped": True,
            "capped_pages": {"requested_end": 100, "completed_end": 47, "available_pages": 47},
            "statistics": {"meta_files": 12},
        })
        assert result["status"] == "success"
        assert stage2_calls == 1  # later stages ran on real data

    def test_the_cap_is_recorded_on_the_cycle(self, tmp_path):
        result, _ = self._run(tmp_path, {
            "success": True, "status": "capped", "capped": True,
            "capped_pages": {"requested_end": 100, "completed_end": 47},
            "statistics": {"meta_files": 12},
        })
        assert result["stages"]["stage1"]["capped"] is True

    def test_an_empty_range_is_no_data_not_a_failure(self, tmp_path):
        result, stage2_calls = self._run(tmp_path, {
            "success": False,
            "status": "no_pages_in_range",
            "error": "Crawler exited 3: ... nothing was crawled.",
            "statistics": {},
        })
        assert result["status"] == "no_data"
        assert "no page" in result["no_data_reason"]
        # Nothing was crawled, so Stage 2 must not run on an empty directory.
        assert stage2_calls == 0

    def test_a_genuine_stage1_failure_is_still_failed_stage1(self, tmp_path):
        result, _ = self._run(tmp_path, {
            "success": False, "status": "failed", "error": "boom", "statistics": {},
        })
        assert result["status"] == "failed_stage1"


class TestStage3NothingProcessedReachesTheCycle:
    def _run(self, tmp_path, pipeline_result, download_result=None):
        import logging
        from cycle_test import run_cycle_test as rct

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        (dirs["stage3"] / "srr_list.txt").write_text("SRR000001\n")
        download_result = download_result or {
            "success": True, "total": 1, "downloaded": 1, "failed": 0
        }
        with mock.patch.object(rct, "extract_srr_from_stage2_tables",
                               return_value={"success": True, "unique_srr_ids": 1, "gse_count": 1}), \
             mock.patch.object(rct, "download_sra_files", return_value=download_result), \
             mock.patch.object(rct, "run_stage3_pipeline", return_value=pipeline_result):
            return rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs, umls_dir=tmp_path,
                logger=logging.getLogger("test"), stage3_only=True,
            )

    def _nothing_processed(self):
        return {
            "success": False,
            "nothing_processed": True,
            "nothing_processed_reason": "no sample qualified for Cell Ranger",
            "returncode": 3,
            "cellranger_completed": 0,
            "successful_samples": 1,
            "failed_samples": 0,
        }

    def test_a_zero_sample_run_is_no_data_not_partial_success(self, tmp_path):
        # Was reported as "Stage 3b completed with issues: Docker exited with
        # code 3" and partial_success, which reads as a defect rather than as a
        # complete run with nothing to analyse.
        result = self._run(tmp_path, self._nothing_processed())
        assert result["status"] == "no_data"
        assert "Cell Ranger" in result["no_data_reason"]

    def test_it_is_never_a_success(self, tmp_path):
        from cycle_test.run_cycle_test import _exit_code_for

        result = self._run(tmp_path, self._nothing_processed())
        assert result["status"] != "success"
        assert _exit_code_for([result]) == 1

    def test_an_earlier_failure_is_not_downgraded_to_no_data(self, tmp_path):
        # A failed download already made the cycle partial_success. "Cell Ranger
        # correctly did nothing" must not overwrite "something went wrong".
        result = self._run(
            tmp_path,
            self._nothing_processed(),
            download_result={"success": False, "total": 3, "downloaded": 1, "failed": 2},
        )
        assert result["status"] == "partial_success"

    def test_a_real_run_is_still_a_success(self, tmp_path):
        result = self._run(tmp_path, {
            "success": True, "nothing_processed": False,
            "cellranger_completed": 2, "successful_samples": 2, "failed_samples": 0,
        })
        assert result["status"] == "success"


class TestTheReportTellsTheThreeOutcomesApart:
    """did work / correctly did nothing / failed must be readable from the
    report JSON and the console, without the log."""

    def _cycle(self, status, **over):
        cyc = {
            "cycle_num": 1, "start_page": 1, "end_page": 100, "status": status,
            "start_time": "2026-08-12T10:00:00", "end_time": "2026-08-12T10:10:00",
            "stage3_requested": False,
            "stages": {
                "stage1": {"success": True, "status": "success",
                           "statistics": {"meta_files": 5}},
                "stage2": {"success": True, "tables": {"tissue": {"rows": 3}}},
                "stage3_prep": {"success": True, "unique_srr_ids": 0},
            },
        }
        cyc.update(over)
        return cyc

    def _report(self, tmp_path, cycles):
        from cycle_test.utils.report_generator import generate_cycle_summary

        return json.loads(generate_cycle_summary(cycles, tmp_path).read_text())

    def test_a_capped_stage1_is_visible_in_the_report(self, tmp_path):
        cyc = self._cycle("success")
        cyc["stages"]["stage1"].update({
            "status": "capped", "capped": True,
            "capped_pages": {"requested_end": 100, "completed_end": 47},
        })
        r = self._report(tmp_path, [cyc])
        s1 = r["cycles"][0]["stage1"]
        # It did work, so the rate is 100%, but the cap is not silent.
        assert r["success_rate"]["stage1"] == 100.0
        assert s1["capped"] is True
        assert s1["capped_pages"]["completed_end"] == 47

    def test_the_no_data_reason_reaches_the_report(self, tmp_path):
        cyc = self._cycle(
            "no_data",
            no_data_reason="Stage 1 found no page in the requested range 101-200",
        )
        r = self._report(tmp_path, [cyc])
        assert "no page" in r["cycles"][0]["no_data_reason"]

    def test_a_stage1_no_data_cycle_is_not_a_stage3_failure(self, tmp_path):
        # Stage 1 can now end a cycle as no_data on a run that never asked for
        # Stage 3. Counting that as a failed Stage 3 would report a stage that
        # was never attempted.
        cyc = self._cycle("no_data", no_data_reason="Stage 1 found no page in range")
        r = self._report(tmp_path, [cyc])
        assert r["success_rate"]["stage3"] is None

    def test_a_requested_stage3_that_found_nothing_still_counts(self, tmp_path):
        cyc = self._cycle("no_data", stage3_requested=True,
                          no_data_reason="Stage 3 prep found no SRR IDs")
        r = self._report(tmp_path, [cyc])
        assert r["success_rate"]["stage3"] == 0.0

    def test_the_console_shows_the_cellranger_count(self, tmp_path, capsys):
        cyc = self._cycle("no_data", stage3_requested=True,
                          no_data_reason="Stage 3 Cell Ranger: no sample qualified")
        cyc["stages"]["stage3_download"] = {
            "success": True, "total": 1, "downloaded": 1, "failed": 0
        }
        cyc["stages"]["stage3_pipeline"] = {
            "success": False, "nothing_processed": True,
            "nothing_processed_reason": "no sample qualified for Cell Ranger",
            "cellranger_completed": 0, "successful_samples": 1, "failed_samples": 0,
        }
        self._report(tmp_path, [cyc])
        out = capsys.readouterr().out
        assert "0 sample(s) with Cell Ranger output" in out
        assert "no sample qualified for Cell Ranger" in out
        # The status line still carries the verdict automation reads.
        assert "✗ no_data" in out


class TestPipelineAndFilesystemMustAgree:
    """Exit 3 says zero samples were analysed. BAMs on disk say otherwise. A run
    whose own two accounts disagree is not one to report as a success."""

    def test_exit_3_with_output_present_is_an_error(self, tmp_path):
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        _cellranger_output(results)

        # The run record credits the sample; the exit code says nothing was
        # analysed. The two came out of the same run and cannot both be right.
        result = _run_stage3(tmp_path, returncode=3, record={"SRR1": "fresh"})

        assert result["success"] is False
        assert result["nothing_processed"] is False
        assert "disagree" in result["error"]


class TestCompletionIsScopedToThisRunsSamples:
    """A BAM on the volume is not this run's achievement.

    results/ is persistent. Counting every possorted_genome_bam.bam under
    results/success let one leftover from an earlier run carry a run that was
    given no input at all, and analysed nothing, to a clean success.
    """

    def test_a_leftover_from_another_run_is_not_counted(self, tmp_path):
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        _cellranger_output(results, sample="SRR999999999")  # not in sra_dir
        _step_sentinel(results, 8, "warn")

        result = _run_stage3(tmp_path, returncode=3,
                             record={"SRR1": ("missing", "no Cell Ranger output")},
                             others=["SRR999999999"])

        assert result["expected_samples"] == ["SRR1"]
        assert result["cellranger_completed"] == 0
        assert result["other_samples_with_output"] == ["SRR999999999"]
        assert result["success"] is False
        assert result["nothing_processed"] is True

    def test_the_leftover_is_left_on_disk(self, tmp_path):
        results = tmp_path / "results"
        _cellranger_output(results, sample="SRR999999999")
        bam = (results / "success" / "SRR999999999" / "cellranger_output"
               / "outs" / "possorted_genome_bam.bam")
        before = bam.read_bytes()

        _run_stage3(tmp_path, returncode=3,
                    record={"SRR1": ("missing", "no Cell Ranger output")},
                    others=["SRR999999999"])

        assert bam.is_file() and bam.read_bytes() == before

    def test_output_for_the_expected_sample_is_counted(self, tmp_path):
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        _cellranger_output(results, sample="SRR1")
        _cellranger_output(results, sample="SRR999999999")
        _step_sentinel(results, 8, "ok")

        result = _run_stage3(tmp_path, returncode=0, record={"SRR1": "fresh"},
                             others=["SRR999999999"])

        assert result["cellranger_samples"] == ["SRR1"]
        assert result["success"] is True

    def test_a_run_with_no_input_cannot_succeed(self, tmp_path):
        from cycle_test.utils.stage3_runner import Stage3PipelineRunner
        from cycle_test.utils import stage3_runner as s3

        (tmp_path / "sra").mkdir()
        results = tmp_path / "results"
        _cellranger_output(results, sample="SRR999999999")
        runner = Stage3PipelineRunner(
            sra_dir=tmp_path / "sra", results_dir=results,
            logs_dir=tmp_path / "logs",
            cellranger_path=_install_cellranger(tmp_path),
            ref_genome_path=_install_reference(tmp_path),
        )
        # What the pipeline records for a run it was given nothing to do: an
        # empty expected set, and the leftover named as somebody else's.
        _run_record(runner, {}, expected=[], others=["SRR999999999"])
        with mock.patch.object(runner, "check_prerequisites",
                               return_value={"errors": [], "sra_files_count": 0}), \
             mock.patch.object(s3.subprocess, "Popen", return_value=_FakeProc(3)):
            result = runner.run_pipeline(timeout_hours=1)

        assert result["expected_count"] == 0
        assert result["success"] is False
        assert result["nothing_processed"] is True
        assert "no samples to process" in result["nothing_processed_reason"]


class TestPartialIsNeitherSuccessNorFailure:
    """Expected 2, analysed 1. The documented partial_success status, meant."""

    def _partial(self, tmp_path, returncode=4):
        sra = tmp_path / "sra"
        (sra / "SRR2").mkdir(parents=True)
        (sra / "SRR2" / "SRR2.sra").write_bytes(b"NCBI.sra")
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results, samples=("SRR1", "SRR2"))
        _cellranger_output(results, sample="SRR1")
        (results / "success").mkdir(parents=True, exist_ok=True)
        (results / "success" / "cellranger_eligibility.tsv").write_text(
            "# sample\tstatus\treason\tdetail\n"
            "SRR1\teligible\t\t\n"
            "SRR2\tineligible\tonly 1 FASTQ file; Cell Ranger requires paired R1/R2 reads\tfastq=1\n"
        )
        _step_sentinel(results, 8, "warn", "cellranger partial (1/2 expected sample(s))")
        return _run_stage3(
            tmp_path, returncode=returncode,
            record={"SRR1": "fresh",
                    "SRR2": ("ineligible", "only 1 FASTQ file; Cell Ranger "
                                           "requires paired R1/R2 reads")})

    def test_it_is_not_a_success(self, tmp_path):
        result = self._partial(tmp_path)
        assert result["expected_count"] == 2
        assert result["cellranger_completed"] == 1
        assert result["success"] is False

    def test_it_is_reported_as_partial_with_a_reason(self, tmp_path):
        result = self._partial(tmp_path)
        assert result["status"] == "partial_success"
        assert result["partial"] is True
        assert "1 of 2" in result["partial_reason"]
        assert "paired R1/R2" in result["partial_reason"]

    def test_it_is_not_nothing_processed_either(self, tmp_path):
        # Something WAS analysed, so "the run correctly did nothing" is false.
        result = self._partial(tmp_path)
        assert result["nothing_processed"] is False
        assert result["error"] is None

    def test_exit_0_with_a_missing_sample_is_still_partial(self, tmp_path):
        # Even when the container claims a clean exit, one of the two samples
        # this run was given has no output.
        result = self._partial(tmp_path, returncode=0)
        assert result["status"] == "partial_success"

    def test_the_cycle_records_partial_success(self, tmp_path):
        import logging
        from cycle_test import run_cycle_test as rct

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        (dirs["stage3"] / "srr_list.txt").write_text("SRR1\nSRR2\n")
        pipeline_result = {
            "success": False, "nothing_processed": False, "partial": True,
            "partial_reason": "1 of 2 expected sample(s) have Cell Ranger output",
            "returncode": 4, "cellranger_completed": 1, "expected_count": 2,
            "successful_samples": 2, "failed_samples": 0,
        }
        with mock.patch.object(rct, "extract_srr_from_stage2_tables",
                               return_value={"success": True, "unique_srr_ids": 2, "gse_count": 1}), \
             mock.patch.object(rct, "download_sra_files",
                               return_value={"success": True, "total": 2, "downloaded": 2, "failed": 0}), \
             mock.patch.object(rct, "run_stage3_pipeline", return_value=pipeline_result):
            result = rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs, umls_dir=tmp_path,
                logger=logging.getLogger("test"), stage3_only=True,
            )

        assert result["status"] == "partial_success"
        assert "1 of 2" in result["partial_reason"]


class TestTheRunRecordIsAuthoritative:
    """When the pipeline leaves a run record, its provenance verdict wins."""

    def _with_outcome(self, tmp_path, counts, samples, returncode=0):
        from cycle_test.utils import stage3_runner as s3

        runner = _stage3_runner(tmp_path)
        record = runner.run_record_dir
        record.mkdir(parents=True)
        (record / "outcome.json").write_text(json.dumps({
            "run_id": runner.run_id,
            "expected_samples": sorted(samples),
            "counts": counts,
            "samples": samples,
            "other_samples_with_output": [],
        }))
        _cellranger_output(tmp_path / "results", sample="SRR1")
        with mock.patch.object(runner, "check_prerequisites",
                               return_value={"errors": [], "sra_files_count": 1}), \
             mock.patch.object(s3.subprocess, "Popen", return_value=_FakeProc(returncode)):
            return runner.run_pipeline(timeout_hours=1)

    def test_a_cache_hit_completes_the_run_without_fresh_work(self, tmp_path):
        result = self._with_outcome(
            tmp_path,
            counts={"expected": 1, "completed": 1, "fresh": 0, "cache_hit": 1,
                    "adopted": 0, "ineligible": 0, "missing": 0,
                    "unverified": 0, "stale": 0},
            samples={"SRR1": {"status": "cache_hit", "sample": "SRR1",
                              "source_run_id": "run-earlier",
                              "reason": "output of run run-earlier reused"}},
        )
        assert result["success"] is True
        assert result["cellranger_cache_hits"] == 1
        assert result["cellranger_fresh"] == 0
        assert result["provenance_verified"] is True

    def test_output_whose_provenance_does_not_match_does_not_count(self, tmp_path):
        # The BAM is on disk, so counting files would call this complete. The
        # run record says it came from different input.
        result = self._with_outcome(
            tmp_path,
            counts={"expected": 1, "completed": 0, "fresh": 0, "cache_hit": 0,
                    "adopted": 0, "ineligible": 0, "missing": 0,
                    "unverified": 0, "stale": 1},
            samples={"SRR1": {"status": "stale", "sample": "SRR1",
                              "reason": "produced from a different input"}},
            returncode=3,
        )
        assert result["cellranger_completed"] == 0
        assert result["success"] is False
        assert result["nothing_processed"] is True

    def test_a_record_from_another_run_is_ignored(self, tmp_path):
        from cycle_test.utils import stage3_runner as s3

        runner = _stage3_runner(tmp_path)
        record = runner.results_dir / "runs" / "someone-elses-run"
        record.mkdir(parents=True)
        (record / "outcome.json").write_text(json.dumps({
            "run_id": "someone-elses-run",
            "counts": {"expected": 1, "completed": 1},
        }))
        assert runner.read_run_outcome() is None

    def test_the_run_id_travels_with_the_container(self, tmp_path):
        runner = _stage3_runner(tmp_path)
        config_path = runner.generate_config()
        cmd = runner.build_docker_command(config_path)
        assert f"GENOAR_RUN_ID={runner.run_id}" in cmd
        import yaml
        assert yaml.safe_load(config_path.read_text())["run_id"] == runner.run_id

    def test_an_inherited_run_id_cannot_outvote_this_runs(self, tmp_path):
        # Singularity hands the host environment to the container and drops
        # docker's -e flags. An exported GENOAR_RUN_ID from the crawl used to
        # be what the container recorded itself under, while the runner looked
        # under the id it generated.
        runner = _stage3_runner(tmp_path)
        with mock.patch.dict(os.environ, {"GENOAR_RUN_ID": "the-crawls-id"}):
            env = runner.container_env()

        assert env["GENOAR_RUN_ID"] == runner.run_id
        assert env["APPTAINERENV_GENOAR_RUN_ID"] == runner.run_id
        assert env["SINGULARITYENV_GENOAR_RUN_ID"] == runner.run_id

    def test_the_container_is_started_in_that_environment(self, tmp_path):
        from cycle_test.utils import stage3_runner as s3

        runner = _stage3_runner(tmp_path)
        _run_record(runner, {"SRR1": "fresh"})
        _cellranger_output(tmp_path / "results", sample="SRR1")
        with mock.patch.object(runner, "check_prerequisites",
                               return_value={"errors": [], "sra_files_count": 1}), \
             mock.patch.object(s3.subprocess, "Popen",
                               return_value=_FakeProc(0)) as popen:
            runner.run_pipeline(timeout_hours=1)

        assert popen.call_args.kwargs["env"]["GENOAR_RUN_ID"] == runner.run_id


class TestARunThatSaidNothingProvesNothing:
    """No run record, no verdict — and no crediting the volume in its place.

    The run record replaced "count the BAMs under results/success", and the
    counting survived directly underneath it, as the branch taken when no
    record was found. An image built before run records exists in the wild:
    running one over a results volume that held a single earlier BAM produced

        Verified output: 1 of 1 expected      success: True     status: success

    for a run whose Cell Ranger analysed nothing. The absence of a statement is
    not a statement in the run's favour.
    """

    def _no_record(self, tmp_path, returncode=0, sample="SRR1"):
        results = tmp_path / "results"
        _fastq_conversion_succeeded(results)
        _cellranger_output(results, sample=sample)
        return _run_stage3(tmp_path, returncode=returncode)

    def test_a_leftover_bam_does_not_complete_a_run_that_left_no_record(self, tmp_path):
        result = self._no_record(tmp_path)

        assert result["success"] is False
        assert result["status"] == "failed"
        assert result["cellranger_completed"] == 0
        assert result["cellranger_samples"] == []

    def test_it_is_not_a_valid_empty_run_either(self, tmp_path):
        # "nothing_processed" is a finding about the work. There is no finding.
        result = self._no_record(tmp_path)

        assert result["nothing_processed"] is False
        assert result["partial"] is False
        assert result["configuration_error"] is False

    def test_the_message_names_both_causes_and_a_fix_for_each(self, tmp_path):
        result = self._no_record(tmp_path)

        assert "outcome.json" in result["error"]
        # An image that cannot write a record, and what to do about it...
        assert "predates run records" in result["error"]
        assert "make build-srr" in result["error"]
        # ...and a run that died before writing one, and where to look.
        assert "ended before it could write its record" in result["error"]
        assert "docker_stdout.log" in result["error"]

    def test_the_output_on_disk_is_named_and_left_alone(self, tmp_path):
        bam = (tmp_path / "results" / "success" / "SRR1" / "cellranger_output"
               / "outs" / "possorted_genome_bam.bam")
        result = self._no_record(tmp_path)

        assert result["cellranger_unverified"] == 1
        assert result["cellranger_unverified_samples"] == ["SRR1"]
        assert "SRR1" in result["error"]
        assert bam.is_file() and bam.read_bytes() == b"BAM"

    def test_output_belonging_to_another_run_is_still_reported_as_theirs(self, tmp_path):
        result = self._no_record(tmp_path, sample="SRR999999999")

        assert result["other_samples_with_output"] == ["SRR999999999"]
        assert result["cellranger_unverified"] == 0
        assert result["success"] is False

    def test_an_exit_3_without_a_record_is_refused_too(self, tmp_path):
        # Exit 3 credits nothing, so believing it costs nothing — except that
        # the operator of an image that can never verify a run would be told
        # "valid run, nothing qualified" every time, and never why.
        result = self._no_record(tmp_path, returncode=3)

        assert result["success"] is False
        assert result["nothing_processed"] is False
        assert "predates run records" in result["error"]

    def test_a_container_failure_still_reports_the_container_failure(self, tmp_path):
        # A run that exited 1 has an explanation already, and it is the better
        # one: the missing record is a consequence of it, not a second fault.
        result = self._no_record(tmp_path, returncode=1)

        assert result["success"] is False
        assert "code 1" in result["error"]
        assert result["cellranger_completed"] == 0

    def test_a_configuration_exit_is_still_a_configuration_error(self, tmp_path):
        # Exit 2 happens before a run directory exists, so there is never a
        # record. The fault the user has to fix is the configuration.
        result = self._no_record(tmp_path, returncode=2)

        assert result["status"] == "config_error"
        assert result["configuration_error"] is True

    def test_a_record_that_does_not_parse_is_not_a_record(self, tmp_path):
        from cycle_test.utils import stage3_runner as s3

        runner = _stage3_runner(tmp_path)
        _cellranger_output(tmp_path / "results", sample="SRR1")
        runner.run_record_dir.mkdir(parents=True)
        (runner.run_record_dir / "outcome.json").write_text('{"run_id": "ru')
        with mock.patch.object(runner, "check_prerequisites",
                               return_value={"errors": [], "sra_files_count": 1}), \
             mock.patch.object(s3.subprocess, "Popen", return_value=_FakeProc(0)):
            result = runner.run_pipeline(timeout_hours=1)

        assert result["success"] is False
        assert result["cellranger_completed"] == 0
        assert "interrupted while writing it" in result["error"]

    def test_another_runs_record_cannot_stand_in_for_this_ones(self, tmp_path):
        from cycle_test.utils import stage3_runner as s3

        runner = _stage3_runner(tmp_path)
        _cellranger_output(tmp_path / "results", sample="SRR1")
        runner.run_record_dir.mkdir(parents=True)
        (runner.run_record_dir / "outcome.json").write_text(json.dumps({
            "run_id": "somebody-else", "expected_samples": ["SRR1"],
            "counts": {"expected": 1, "completed": 1, "fresh": 1},
            "samples": {"SRR1": {"status": "fresh", "sample": "SRR1"}}}))
        with mock.patch.object(runner, "check_prerequisites",
                               return_value={"errors": [], "sra_files_count": 1}), \
             mock.patch.object(s3.subprocess, "Popen", return_value=_FakeProc(0)):
            result = runner.run_pipeline(timeout_hours=1)

        assert result["success"] is False
        assert result["cellranger_completed"] == 0
        assert "records run 'somebody-else'" in result["error"]

    def test_a_record_that_contradicts_itself_credits_nothing(self, tmp_path):
        # `completed` is a scalar sitting beside the per-sample statuses it
        # summarises. Reading one without the other is how an adoption was
        # counted as a completion in the first place.
        from cycle_test.utils import stage3_runner as s3

        runner = _stage3_runner(tmp_path)
        _cellranger_output(tmp_path / "results", sample="SRR1")
        runner.run_record_dir.mkdir(parents=True)
        (runner.run_record_dir / "outcome.json").write_text(json.dumps({
            "run_id": runner.run_id, "expected_samples": ["SRR1"],
            "counts": {"expected": 1, "completed": 1, "fresh": 0,
                       "cache_hit": 0, "adopted": 1},
            "samples": {"SRR1": {"status": "adopted", "sample": "SRR1"}}}))
        with mock.patch.object(runner, "check_prerequisites",
                               return_value={"errors": [], "sra_files_count": 1}), \
             mock.patch.object(s3.subprocess, "Popen", return_value=_FakeProc(0)):
            result = runner.run_pipeline(timeout_hours=1)

        assert result["success"] is False
        assert result["cellranger_completed"] == 0
        assert "contradicts itself" in result["error"]


class TestPrerequisitesAreCheckedAsInstalls:
    """`cellranger/` and `ref/` existing is not the same as being usable."""

    def test_a_directory_named_cellranger_is_not_an_executable(self, tmp_path):
        from cycle_test.utils.stage3_runner import validate_cellranger

        (tmp_path / "cellranger").mkdir()
        problems = validate_cellranger(tmp_path / "cellranger")
        assert problems and "not found" in problems[0]

    def test_a_non_executable_cellranger_is_refused(self, tmp_path):
        from cycle_test.utils.stage3_runner import validate_cellranger

        install = tmp_path / "cellranger"
        install.mkdir()
        (install / "cellranger").write_text("")
        (install / "cellranger").chmod(0o644)
        problems = validate_cellranger(install)
        assert problems and "not executable" in problems[0]

    def test_a_complete_install_passes(self, tmp_path):
        from cycle_test.utils.stage3_runner import (
            validate_cellranger, validate_reference)

        assert validate_cellranger(_install_cellranger(tmp_path)) == []
        assert validate_reference(_install_reference(tmp_path)) == []

    def test_every_missing_reference_part_is_named(self, tmp_path):
        from cycle_test.utils.stage3_runner import validate_reference

        ref = _install_reference(tmp_path, omit=("reference.json", "genes", "star"))
        problems = validate_reference(ref)
        assert any("reference.json" in p for p in problems)
        assert any("genes/" in p for p in problems)
        assert any("star/" in p for p in problems)
        assert not any("fasta/" in p for p in problems)

    def test_an_empty_reference_json_is_refused(self, tmp_path):
        from cycle_test.utils.stage3_runner import validate_reference

        ref = _install_reference(tmp_path)
        (ref / "reference.json").write_text("")
        assert any("reference.json" in p for p in validate_reference(ref))

    def test_a_broken_install_is_a_configuration_error_not_an_empty_run(self, tmp_path):
        from cycle_test.utils.stage3_runner import Stage3PipelineRunner

        sra = tmp_path / "sra" / "SRR1"
        sra.mkdir(parents=True)
        (sra / "SRR1.sra").write_bytes(b"NCBI.sra")
        runner = Stage3PipelineRunner(
            sra_dir=tmp_path / "sra", results_dir=tmp_path / "results",
            logs_dir=tmp_path / "logs",
            cellranger_path=tmp_path / "nowhere",
            ref_genome_path=_install_reference(tmp_path, omit=("star",)),
        )
        with mock.patch.object(runner, "check_prerequisites",
                               wraps=runner.check_prerequisites):
            result = runner.run_pipeline(timeout_hours=1)

        assert result["configuration_error"] is True
        assert result["status"] == "config_error"
        assert result["success"] is False
        assert result["nothing_processed"] is False

    def test_the_pipelines_own_configuration_exit_is_not_read_as_no_data(self, tmp_path):
        result = _run_stage3(tmp_path, returncode=2)
        assert result["configuration_error"] is True
        assert result["success"] is False
        assert result["nothing_processed"] is False
        assert "misconfigured" in result["error"]

    def test_a_configuration_error_fails_the_cycle(self, tmp_path):
        import logging
        from cycle_test import run_cycle_test as rct

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        (dirs["stage3"] / "srr_list.txt").write_text("SRR1\n")
        with mock.patch.object(rct, "extract_srr_from_stage2_tables",
                               return_value={"success": True, "unique_srr_ids": 1, "gse_count": 1}), \
             mock.patch.object(rct, "download_sra_files",
                               return_value={"success": True, "total": 1, "downloaded": 1, "failed": 0}), \
             mock.patch.object(rct, "run_stage3_pipeline", return_value={
                 "success": False, "nothing_processed": False,
                 "configuration_error": True, "error": "no cellranger executable",
                 "returncode": 2, "cellranger_completed": 0,
                 "successful_samples": 0, "failed_samples": 0}):
            result = rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs, umls_dir=tmp_path,
                logger=logging.getLogger("test"), stage3_only=True,
            )
        assert result["status"] == "failed_stage3"


class TestTheExpectedSetComesFromTheInput:
    def test_loose_and_nested_sra_files_both_count(self, tmp_path):
        from cycle_test.utils.stage3_runner import expected_samples_in

        sra = tmp_path / "sra"
        (sra / "SRR2").mkdir(parents=True)
        (sra / "SRR2" / "SRR2.sra").write_bytes(b"")
        (sra / "SRR1.sra").write_bytes(b"")
        assert expected_samples_in(sra) == ["SRR1", "SRR2"]

    def test_non_srr_entries_are_ignored(self, tmp_path):
        from cycle_test.utils.stage3_runner import expected_samples_in

        sra = tmp_path / "sra"
        sra.mkdir()
        (sra / "SRR1.sra").write_bytes(b"")
        (sra / "notes.txt").write_text("")
        (sra / "ERR9.sra").write_bytes(b"")
        assert expected_samples_in(sra) == ["SRR1"]

    def test_a_missing_directory_expects_nothing(self, tmp_path):
        from cycle_test.utils.stage3_runner import expected_samples_in

        assert expected_samples_in(tmp_path / "absent") == []


def _provenance_source():
    """The verifier's own text, exactly as the shell feeds it to python3.

    It lives as a heredoc inside run_docker_pipeline.sh (the image's Dockerfile
    copies pipeline files one by one, so it cannot be split out from here), and
    it is what decides whether a run may claim a Cell Ranger output as its own.
    """
    sh = (Path(__file__).resolve().parents[2] / "srr_pipeline_package"
          / "pipeline_next" / "run_docker_pipeline.sh")
    lines = sh.read_text().split("\n")
    start = 'python3 - "$@" << \'PY\''
    i = next(n for n, l in enumerate(lines) if l.strip() == start)
    j = next(n for n in range(i + 1, len(lines)) if lines[n] == "PY")
    return str(sh), "\n".join(lines[i + 1:j]) + "\n"


class _Provenance:
    """The run-provenance code the Stage 3 orchestrator runs.

    Loading the same text the shell feeds to python3 keeps these tests on the
    shipped code rather than on a copy of it. The command dispatcher at the
    bottom is dropped so the module can be called function by function.
    """

    def __new__(cls):
        import types

        path, body = _provenance_source()
        body = body[:body.index("\nsub = sys.argv[1]")]
        mod = types.ModuleType("genoar_provenance")
        exec(compile(body, path, "exec"), mod.__dict__)
        return mod


BAM_REL = "cellranger_output/outs/possorted_genome_bam.bam"
RECEIPT = ".genoar_cellranger.json"
STARTED = 1700000000
SAMPLE = "SRR000001"


BAM_DIGEST_BYTES = 64 * 1024


def _edge_digests(path, window=BAM_DIGEST_BYTES):
    """The digests the Cell Ranger rule records, computed the way it computes.

    Mirrors bam_edge_digests in Snakefile_hs.smk: sha256 of the first and last
    `window` bytes, and the window actually used. A file smaller than two
    windows has them overlap, which is exactly what the rule does too.
    """
    import hashlib

    size = path.stat().st_size
    span = min(size, window)
    data = path.read_bytes()
    return (hashlib.sha256(data[:span]).hexdigest(),
            hashlib.sha256(data[len(data) - span:] if span else b"").hexdigest(),
            span)


def _rewrite_in_place(bam, where, window=BAM_DIGEST_BYTES):
    """Change one byte of the BAM, keeping its size and its timestamp.

    "head" and "tail" land inside the windows the rule hashed; "middle" lands
    between them, which only exists for a file larger than two windows. This is
    the shape of the case the digests are for: a file swapped for one the
    filesystem cannot tell apart by size or age.
    """
    stat = bam.stat()
    data = bytearray(bam.read_bytes())
    if where == "head":
        index = 0
    elif where == "tail":
        index = len(data) - 1
    elif where == "middle":
        assert len(data) > 2 * window, "a middle needs a BAM bigger than 2 windows"
        index = len(data) // 2
    else:
        raise AssertionError("unknown rewrite: %r" % where)
    data[index] ^= 0xFF
    bam.write_bytes(bytes(data))
    os.utime(bam, (stat.st_atime, stat.st_mtime))
    assert bam.stat().st_size == stat.st_size


def _completion_record(run_dir, sample, bam, *, run_id="run-B", size_delta=0,
                       inode_delta=0, mtime_delta=0, complete=True,
                       version=1, bam_path=None, digests=True,
                       digest_bytes=None):
    """What the Cell Ranger rule writes when it has run a sample.

    Mirrors record_cellranger_completed in Snakefile_hs.smk: the run and sample
    it is about, and the file Cell Ranger produced, identified by device, inode
    and size. `mtime_delta` moves the recorded timestamp forward, which is how a
    file that has gone backwards in time is expressed. `version` and `bam_path`
    express a record from a layout this reader does not know, and a record about
    a file other than the one being credited.

    TestTheRuleAndTheVerifierAgree drives the real rule instead of writing this;
    what this shape asserts is only how the reader treats a record, never that
    the rule still writes one.
    """
    stat = bam.stat()
    directory = run_dir / "cellranger"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "record": "genoar.cellranger.completion",
        "record_version": version,
        "complete": complete,
        "run_id": run_id,
        "sample": sample,
        "bam": str(bam_path or bam),
        "bam_size": stat.st_size + size_delta,
        "bam_device": stat.st_dev,
        "bam_inode": stat.st_ino + inode_delta,
        "bam_mtime": int(stat.st_mtime) + mtime_delta,
        "completed_at": "2023-11-14T22:13:20Z",
    }
    if digests:
        head, tail, span = _edge_digests(bam)
        payload.update({"bam_head": head, "bam_tail": tail,
                        "digest_bytes": span if digest_bytes is None
                        else digest_bytes})
    (directory / (sample + ".json")).write_text(json.dumps(payload))


def _stage3_run(tmp_path, *, bam_mtime=None, receipt=None, dispatched="0",
                eligibility_run_id=None, adopt="0", inputs=None,
                receipt_size_delta=0, record=None, started_marker=False,
                bam_bytes=b"BAM", rewrite=None, digests=True,
                digest_bytes=None, unreadable=False,
                receipt_provenance="fresh"):
    """Run the provenance check over one sample and return its record.

    `bam_mtime` is relative to the run's start second, so the cases read as
    "the BAM appeared this many seconds after the run started".

    `record` is what this run's Cell Ranger rule left behind for the sample:
    None for nothing at all, "match" for a record of exactly the BAM on disk,
    "changed" for a record of a file that has since been replaced, "foreign"
    for another run's record dropped into this run's directory, "truncated"
    for one cut off mid-write, "incomplete" for one missing its terminator.
    `started_marker` adds the marker the rule writes before Cell Ranger starts.
    """
    prov = _Provenance()
    root = tmp_path / "r"
    sra = root / "sra" / SAMPLE
    sra.mkdir(parents=True)
    for name, data in (inputs or {SAMPLE + ".sra": b"INPUT"}).items():
        (sra / name).write_bytes(data)
    success = root / "success"
    success.mkdir()
    run_dir = root / "runs" / "run-B"
    (run_dir / "samples").mkdir(parents=True)

    manifest = run_dir / "expected_samples.tsv"
    prov.cmd_manifest(str(root / "sra"), str(manifest), "sha256")
    fingerprint = manifest.read_text().split("\n")[1].split("\t")[3]

    if started_marker:
        (run_dir / "cellranger").mkdir(parents=True, exist_ok=True)
        (run_dir / "cellranger" / (SAMPLE + ".started")).write_text(
            json.dumps({"run_id": "run-B", "sample": SAMPLE,
                        "started_at": "2023-11-14T22:13:20Z"}))

    if bam_mtime is not None:
        bam = success / SAMPLE / BAM_REL
        bam.parent.mkdir(parents=True)
        bam.write_bytes(bam_bytes)
        os.utime(bam, (STARTED + bam_mtime, STARTED + bam_mtime))
        if receipt is not None:
            stat = bam.stat()
            payload = {
                "sample": SAMPLE, "run_id": "run-A",
                "input_fingerprint": (fingerprint if receipt == "match"
                                      else "sha256:" + "de" * 32),
                "bam_size": stat.st_size + receipt_size_delta,
                "bam_mtime": int(stat.st_mtime)}
            if receipt_provenance is not None:
                payload["provenance"] = receipt_provenance
            (success / SAMPLE / RECEIPT).write_text(json.dumps(payload))
        if record == "match":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes)
        elif record == "touched_by_snakemake":
            # Snakemake bumps every output's timestamp after the job. The
            # record's timestamp is therefore a floor, not an equality.
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes, mtime_delta=-3)
        elif record == "changed":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes, size_delta=7)
        elif record == "replaced":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes, inode_delta=1)
        elif record == "rolled_back":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes, mtime_delta=60)
        elif record == "foreign":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes, run_id="run-OTHER")
        elif record == "future_version":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes, version=99)
        elif record == "no_version":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes, version=None)
        elif record == "another_file":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes,
                               bam_path=bam.parent / "somewhere_else.bam")
        elif record == "incomplete":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes, complete=False)
        elif record == "truncated":
            _completion_record(run_dir, SAMPLE, bam, digests=digests,
                               digest_bytes=digest_bytes)
            path = run_dir / "cellranger" / (SAMPLE + ".json")
            path.write_text(path.read_text()[:40])
        elif record is not None:
            raise AssertionError("unknown record kind: %r" % record)
        if rewrite is not None:
            # After the record: the file the verifier finds is not the file the
            # rule described, and nothing about its size or age says so.
            _rewrite_in_place(bam, rewrite)
        if unreadable:
            bam.chmod(0o000)
    if eligibility_run_id is not None:
        (success / "cellranger_eligibility.tsv").write_text(
            "# run_id\t%s\n# sample\tstatus\treason\tdetail\n%s\teligible\t\t\n"
            % (eligibility_run_id, SAMPLE))

    prov.cmd_verify(str(manifest), str(success), "run-B", str(STARTED),
                    str(run_dir), adopt, str(root / "sra"), dispatched)
    outcome = json.loads((run_dir / "outcome.json").read_text())
    receipt_path = success / SAMPLE / RECEIPT
    return (outcome, outcome["samples"][SAMPLE],
            json.loads(receipt_path.read_text()) if receipt_path.exists() else None)


class TestATimestampIsNotProvenance:
    """A modification time at or after the run's start is not provenance.

    A BAM belonging to different input may not be credited to the run on that
    alone, and the receipt that contradicts it may not be rewritten to agree.
    """

    def test_a_bam_dated_at_the_run_start_is_not_this_runs_work(self, tmp_path):
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=0)
        assert rec["status"] == "unverified"
        assert outcome["counts"]["completed"] == 0
        assert receipt is None

    def test_a_future_dated_bam_is_not_this_runs_work(self, tmp_path):
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=3600)
        assert rec["status"] == "unverified"
        assert outcome["counts"]["completed"] == 0
        assert receipt is None

    def test_a_touched_foreign_bam_stays_stale_and_keeps_its_receipt(self, tmp_path):
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                            receipt="foreign")
        assert rec["status"] == "stale"
        assert outcome["counts"]["completed"] == 0
        # The record of where the output came from must survive the run that
        # found it: rewriting it destroys the only contradicting evidence.
        assert receipt["run_id"] == "run-A"
        assert receipt["input_fingerprint"].endswith("de" * 32)

    def test_an_eligibility_report_from_another_run_proves_nothing(self, tmp_path):
        outcome, rec, _ = _stage3_run(tmp_path, bam_mtime=5, dispatched="1",
                                      eligibility_run_id="run-OTHER")
        assert rec["status"] == "unverified"
        assert outcome["counts"]["completed"] == 0

    def test_a_receipt_that_no_longer_describes_the_bam_is_unverified(self, tmp_path):
        # The receipt is about this run's input, but not about the file now on
        # disk: the BAM has been replaced or touched since it was recorded.
        outcome, rec, _ = _stage3_run(tmp_path, bam_mtime=5, receipt="match",
                                      receipt_size_delta=99)
        assert rec["status"] == "unverified"
        assert outcome["counts"]["completed"] == 0


class TestOnlyRecordedWorkIsFresh:
    """Freshness is the Cell Ranger rule's own record of having run the sample.

    Being eligible, plus snakemake exiting 0, plus a BAM no older than the run
    does not add up to "this run produced it". None of the three is about doing
    the work: eligibility is decided before any job runs, snakemake exits 0
    exactly when it finds nothing to do, and a modification time cannot say who
    wrote a file. A restored backup, a shared volume whose clock runs ahead, or
    a second container writing to the same volume satisfies all three.
    """

    def test_a_sample_the_rule_recorded_running_is_fresh(self, tmp_path):
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                            record="match", dispatched="1",
                                            eligibility_run_id="run-B")
        assert rec["status"] == "fresh"
        assert outcome["counts"]["completed"] == 1
        assert receipt["run_id"] == "run-B"

    def test_dispatch_and_a_timestamp_without_a_record_prove_nothing(self, tmp_path):
        # Eligible, dispatched by this run, BAM dated inside this run's
        # window -- and Cell Ranger never ran.
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                            dispatched="1",
                                            eligibility_run_id="run-B")
        assert rec["status"] == "unverified"
        assert outcome["counts"]["completed"] == 0
        assert receipt is None

    def test_the_run_start_second_itself_proves_nothing_either(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=0, dispatched="1",
                                eligibility_run_id="run-B")
        assert rec["status"] == "unverified"

    def test_a_record_does_not_need_the_dispatch_list(self, tmp_path):
        # The record is the whole claim. Nothing about the eligibility report
        # adds to it or takes away from it.
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="match")
        assert rec["status"] == "fresh"

    def test_a_record_of_a_file_that_has_since_changed_is_not_fresh(self, tmp_path):
        _, rec, receipt = _stage3_run(tmp_path, bam_mtime=5, record="changed",
                                      dispatched="1", eligibility_run_id="run-B")
        assert rec["status"] == "unverified"
        assert "no longer the one it wrote" in rec["reason"]
        assert receipt is None

    def test_a_record_of_a_file_that_has_been_replaced_is_not_fresh(self, tmp_path):
        # Same size, same timestamp, different file. The inode moved AND the
        # bytes did: what is on disk is not what the rule wrote, by both
        # measures, and neither of them will credit it.
        #
        # (An inode that moves while the bytes stay identical is a different
        # case, not this one: some filesystems renumber an unchanged file, and
        # the digests below tell the two apart. See
        # TestTheBytesAtBothEndsDecideWhatTheInodeCannot.)
        _, rec, receipt = _stage3_run(tmp_path, bam_mtime=5, record="replaced",
                                      rewrite="head", dispatched="1",
                                      eligibility_run_id="run-B")
        assert rec["status"] == "unverified"
        assert receipt is None

    def test_output_that_has_gone_backwards_in_time_is_not_fresh(self, tmp_path):
        # The record's timestamp is a floor. A file older than the moment Cell
        # Ranger finished with it is not the state it was left in.
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="rolled_back",
                                dispatched="1", eligibility_run_id="run-B")
        assert rec["status"] == "unverified"

    def test_snakemakes_own_touch_does_not_unmake_the_record(self, tmp_path):
        # Snakemake touches every output file after the job that produced it,
        # deliberately, to defeat clock skew. Requiring the timestamp to match
        # exactly made a genuine fresh run report `unverified` whenever that
        # touch crossed a second boundary.
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                            record="touched_by_snakemake",
                                            dispatched="1",
                                            eligibility_run_id="run-B")
        assert rec["status"] == "fresh"
        assert outcome["counts"]["completed"] == 1
        assert receipt["run_id"] == "run-B"

    def test_a_started_marker_alone_is_not_a_completion(self, tmp_path):
        # Killed between starting Cell Ranger and recording that it finished.
        _, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                      started_marker=True, dispatched="1",
                                      eligibility_run_id="run-B")
        assert rec["status"] == "unverified"
        assert "never recorded finishing" in rec["reason"]
        assert receipt is None

    def test_a_started_marker_with_no_output_is_reported_as_such(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, started_marker=True, dispatched="1",
                                eligibility_run_id="run-B")
        assert rec["status"] == "missing"
        assert "did not finish" in rec["reason"]

    def test_a_truncated_record_is_not_a_completion(self, tmp_path):
        _, rec, receipt = _stage3_run(tmp_path, bam_mtime=5, record="truncated",
                                      dispatched="1", eligibility_run_id="run-B")
        assert rec["status"] == "unverified"
        assert receipt is None

    def test_a_record_without_its_terminator_is_not_a_completion(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="incomplete",
                                dispatched="1", eligibility_run_id="run-B")
        assert rec["status"] == "unverified"

    def test_another_runs_record_is_not_this_runs_evidence(self, tmp_path):
        _, rec, receipt = _stage3_run(tmp_path, bam_mtime=5, record="foreign",
                                      dispatched="1", eligibility_run_id="run-B")
        assert rec["status"] == "unverified"
        assert receipt is None

    def test_a_selected_sample_that_produced_nothing_says_so(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, dispatched="1",
                                eligibility_run_id="run-B")
        assert rec["status"] == "missing"
        assert "selected this sample" in rec["reason"]

    def test_a_resume_a_second_later_is_a_cache_hit_not_this_runs_work(self, tmp_path):
        # Two runs can share a start second, so a dispatched sample whose BAM an
        # earlier run produced would otherwise be claimed as this run's own
        # work. A receipt that already vouches for exactly this file wins.
        outcome, rec, _ = _stage3_run(tmp_path, bam_mtime=0, receipt="match",
                                      dispatched="1", eligibility_run_id="run-B")
        assert rec["status"] == "cache_hit"
        assert rec["source_run_id"] == "run-A"
        assert outcome["counts"]["fresh"] == 0
        assert outcome["counts"]["completed"] == 1

    def test_a_genuine_resume_is_still_a_cache_hit(self, tmp_path):
        outcome, rec, _ = _stage3_run(tmp_path, bam_mtime=-500, receipt="match",
                                      dispatched="1", eligibility_run_id="run-B")
        assert rec["status"] == "cache_hit"
        assert outcome["counts"]["completed"] == 1

    def test_prior_output_is_still_adoptable_on_request(self, tmp_path):
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=-500, adopt="1")
        assert rec["status"] == "adopted"
        assert receipt["provenance"] == "adopted"

    def test_a_record_in_an_unknown_layout_is_not_a_completion(self, tmp_path):
        # The record names the layout it was written in. A reader that does not
        # know that layout cannot know what its fields mean, so it may not
        # credit the sample on them.
        _, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                      record="future_version", dispatched="1",
                                      eligibility_run_id="run-B")
        assert rec["status"] == "unverified"
        assert receipt is None

    def test_a_record_that_does_not_say_its_layout_is_not_a_completion(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="no_version",
                                dispatched="1", eligibility_run_id="run-B")
        assert rec["status"] == "unverified"

    def test_a_record_about_another_file_credits_nothing(self, tmp_path):
        # Right run, right sample, right size and inode -- about a different
        # path. A success directory restored from elsewhere, or a run directory
        # pointed at a second results volume, produces exactly this.
        _, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                      record="another_file", dispatched="1",
                                      eligibility_run_id="run-B")
        assert rec["status"] == "unverified"
        assert receipt is None


class TestTheBytesAtBothEndsDecideWhatTheInodeCannot:
    """One undecidable case, decided with evidence about the bytes.

    Identity is the device and inode a file occupies and its size. On a macOS
    Docker Desktop bind mount (virtiofs) the inode number is not an identity:
    the same untouched file comes back with a different number after the job,
    same size, same timestamp, same contents. An honest run there could not
    vouch for its own output, and there was no way to tell that apart from a
    file genuinely swapped for one of the same size and age.

    So the rule now records sha256 of the first and last 64 KiB of the BAM, and
    that one case - everything agrees but the number - is settled by asking the
    file what it contains. This is not the timestamp defect in another costume:
    a timestamp says when something was written and never by whom or from what,
    while these digests are evidence about the bytes themselves.

    Layered, not replaced. An inode match still decides it. A size mismatch, a
    timestamp that went backwards, a wrong device, a wrong path, a record
    version this reader does not know: all keep their own verdicts, and the
    digests are never consulted for them.
    """

    BIG = b"".join(bytes([i % 251]) * 1024 for i in range(200))   # ~200 KiB

    def test_a_renumbered_file_is_this_runs_work_again(self, tmp_path):
        # The whole point: the rule wrote it, the filesystem renamed the number
        # underneath it, and the bytes at both ends say it is the same file.
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                            record="replaced")

        assert rec["status"] == "fresh"
        assert outcome["counts"]["completed"] == 1
        assert receipt["run_id"] == "run-B"
        assert "bytes at both of its ends" in rec["reason"]

    def test_a_rewritten_head_is_caught(self, tmp_path):
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                            record="replaced", bam_bytes=self.BIG,
                                            rewrite="head")

        assert rec["status"] == "unverified"
        assert outcome["counts"]["completed"] == 0
        assert "bytes at its start and end are not the ones" in rec["reason"]
        assert receipt is None

    def test_a_rewritten_tail_is_caught(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="replaced",
                                bam_bytes=self.BIG, rewrite="tail")

        assert rec["status"] == "unverified"
        assert "bytes at its start and end are not the ones" in rec["reason"]

    def test_a_rewritten_middle_is_the_residual_risk_and_is_NOT_caught(self, tmp_path):
        """The accepted risk, written down as a test rather than a comment.

        A writer who rewrites the middle of a multi-gigabyte BAM while keeping
        its size, its timestamp and both 64 KiB ends byte-for-byte is credited.
        Catching that means hashing the whole file on every run and every
        resume; the trade is recorded in record_describes. If this test ever
        starts failing, someone has closed the hole - which is good news, and
        this test should be deleted rather than repaired.
        """
        outcome, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="replaced",
                                      bam_bytes=self.BIG, rewrite="middle")

        assert rec["status"] == "fresh"
        assert outcome["counts"]["completed"] == 1

    def test_a_record_written_before_digests_existed_keeps_todays_answer(self, tmp_path):
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                            record="replaced", digests=False)

        assert rec["status"] == "unverified"
        assert outcome["counts"]["completed"] == 0
        assert "predates the content digests" in rec["reason"]
        assert "cannot tell which" in rec["reason"]
        assert receipt is None

    def test_the_undecidable_case_names_both_readings_and_an_action_for_each(self, tmp_path):
        """Where the digests cannot reach, the message still may not guess.

        A record from before this change leaves the run where it was: it saw a
        number move and it does not know why. It says both readings - a
        filesystem that renumbers, or a file swapped for one of the same size
        and age - with what to do about each, because the sentence this
        replaced asserted the second and sent anyone on the first filesystem
        round a loop that ends in the same place every time.
        """
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="replaced",
                                digests=False)

        assert "recomputing will NOT help" in rec["reason"].replace(
            "Recomputing", "recomputing")
        assert "Docker volume or a Linux filesystem" in rec["reason"]
        assert "swapped for a file of the same size" in rec["reason"]
        assert "remove the cellranger_output directory" in rec["reason"]

    @pytest.mark.parametrize("window", [-1, "sixty-four", 10 ** 9])
    def test_a_digest_field_that_is_not_one_is_not_a_match(self, tmp_path, window):
        # Nonsense where a window size should be is not an answer, and "no
        # answer" may never read as "the bytes agree".
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="replaced",
                                digest_bytes=window)

        assert rec["status"] == "unverified"
        assert "cannot tell which" in rec["reason"]

    @pytest.mark.skipif(os.geteuid() == 0,
                        reason="root can read a file with mode 000")
    def test_a_file_whose_ends_cannot_be_read_is_not_a_match(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="replaced",
                                unreadable=True)

        assert rec["status"] == "unverified"
        assert "cannot tell which" in rec["reason"]

    def test_a_size_change_is_refused_even_when_both_ends_agree(self, tmp_path):
        # The record says a different size, so this is not the case the digests
        # are for, and they are not asked. The gate is not widened.
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="changed")

        assert rec["status"] == "unverified"
        assert "no longer the one it wrote" in rec["reason"]

    def test_output_that_went_backwards_in_time_is_still_refused(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="rolled_back")

        assert rec["status"] == "unverified"
        assert "no longer the one it wrote" in rec["reason"]

    def test_another_runs_record_is_still_not_this_runs_evidence(self, tmp_path):
        # Digests cannot lend a record to a run it does not belong to: the
        # record is refused before anything is computed from it.
        _, rec, receipt = _stage3_run(tmp_path, bam_mtime=5, record="foreign")

        assert rec["status"] == "unverified"
        assert receipt is None

    def test_a_record_naming_another_file_is_still_refused(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="another_file")

        assert rec["status"] == "unverified"

    def test_an_ordinary_fresh_run_is_untouched(self, tmp_path):
        # The inode matches, so nothing is read from the file at all and the
        # record carries no explanation, exactly as before.
        outcome, rec, receipt = _stage3_run(tmp_path, bam_mtime=5,
                                            record="match")

        assert rec["status"] == "fresh"
        assert "reason" not in rec
        assert outcome["counts"]["completed"] == 1


class TestAdoptionIsNotCompletion:
    """`adopted` is the operator's word for output, not evidence about it.

    GENOAR_ADOPT_PRIOR_RESULTS=1 exists so that pre-existing output can be
    reused deliberately and on the record. Counting an adopted sample into
    `completed`, which decides the exit status, would finish the run on a BAM
    with no provenance whatsoever: "PIPELINE COMPLETED - all 1 expected
    sample(s) have Cell Ranger output", exit 0. Adoption is reported and writes
    its receipt; it completes nothing.
    """

    def test_an_adopted_sample_is_not_counted_as_completed(self, tmp_path):
        outcome, rec, _ = _stage3_run(tmp_path, bam_mtime=-500, adopt="1")

        assert rec["status"] == "adopted"
        assert outcome["counts"]["adopted"] == 1
        assert outcome["counts"]["completed"] == 0

    def test_adoption_does_not_complete_the_expected_set(self, tmp_path):
        outcome, _, _ = _stage3_run(tmp_path, bam_mtime=-500, adopt="1")

        # What the banner's exit-0 test reads. Equal counts here is the whole
        # false success.
        assert outcome["counts"]["completed"] < outcome["counts"]["expected"]

    def test_verified_work_is_still_counted(self, tmp_path):
        # Adoption is on, and this sample does not need it: the run's own rule
        # recorded producing the output. Turning adoption on must not turn a
        # verified completion into an adoption.
        outcome, rec, _ = _stage3_run(tmp_path, bam_mtime=5, record="match",
                                      adopt="1")
        assert rec["status"] == "fresh"
        assert outcome["counts"]["completed"] == 1
        assert outcome["counts"]["adopted"] == 0

    def test_a_cache_hit_is_still_a_completion(self, tmp_path):
        # A genuine resume: an earlier run's receipt vouches for this exact file
        # against this exact input. That is evidence, and it still completes.
        outcome, rec, _ = _stage3_run(tmp_path, bam_mtime=-500, receipt="match",
                                      adopt="1")
        assert rec["status"] == "cache_hit"
        assert outcome["counts"]["completed"] == 1

    def test_the_runner_does_not_report_an_adopted_run_as_a_success(self):
        from cycle_test.utils.stage3_runner import Stage3PipelineRunner

        result = {"error": None, "returncode": EXIT_PARTIAL,
                  "cellranger_completed": 0, "expected_count": 1,
                  "cellranger_adopted": 1, "step_status": {"step8": "warn"},
                  "success": True, "status": "success", "partial": False,
                  "nothing_processed": False}
        Stage3PipelineRunner._decide_outcome(result)

        assert result["success"] is False
        assert result["status"] != "success"
        assert "adopted" in (result["nothing_processed_reason"] or "")

    def test_the_runner_names_adoption_in_a_partial_run(self):
        from cycle_test.utils.stage3_runner import Stage3PipelineRunner

        result = {"error": None, "returncode": EXIT_PARTIAL,
                  "cellranger_completed": 1, "expected_count": 3,
                  "cellranger_adopted": 1, "step_status": {"step8": "warn"},
                  "success": True, "status": "success", "partial": False,
                  "nothing_processed": False}
        Stage3PipelineRunner._decide_outcome(result)

        assert result["status"] == "partial_success"
        assert "adopted" in result["partial_reason"]
        assert "1 of 3" in result["partial_reason"]


def _stage3_two_runs(tmp_path, *, adopt_first="0", first="none", count=2):
    """Two runs, one after the other, over one persistent results volume.

    Run A is the run with the flags. Run B is an ordinary run — no adoption,
    nothing dispatched, no completion record of its own — that can see only
    what run A left beside the output. That is the whole shape of it: the
    receipt is the one statement that crosses from A to B, so what B may claim
    is decided entirely by what A wrote in it.

    `first` is what run A's own Cell Ranger did: "none" (nothing; the BAM
    predates both runs) or "work" (the rule recorded producing it).
    """
    prov = _Provenance()
    root = tmp_path / "r"
    sra = root / "sra" / SAMPLE
    sra.mkdir(parents=True)
    (sra / (SAMPLE + ".sra")).write_bytes(b"INPUT")
    success = root / "success"
    success.mkdir()
    bam = success / SAMPLE / BAM_REL
    bam.parent.mkdir(parents=True)
    bam.write_bytes(b"BAM")
    os.utime(bam, (STARTED - 500, STARTED - 500))

    runs = []
    later = [("run-%s" % chr(ord("B") + n), "0") for n in range(count - 1)]
    for run_id, adopt in [("run-A", adopt_first)] + later:
        run_dir = root / "runs" / run_id
        (run_dir / "samples").mkdir(parents=True)
        manifest = run_dir / "expected_samples.tsv"
        prov.cmd_manifest(str(root / "sra"), str(manifest), "sha256")
        if run_id == "run-A" and first == "work":
            _completion_record(run_dir, SAMPLE, bam, run_id="run-A")
        prov.cmd_verify(str(manifest), str(success), run_id, str(STARTED),
                        str(run_dir), adopt, str(root / "sra"), "0")
        outcome = json.loads((run_dir / "outcome.json").read_text())
        receipt_path = success / SAMPLE / RECEIPT
        runs.append((outcome, outcome["samples"][SAMPLE],
                     json.loads(receipt_path.read_text())
                     if receipt_path.exists() else None))
    return runs


class TestAnAdoptionDoesNotMatureIntoACompletion:
    """The receipt carries WHAT KIND of provenance it is, or it carries nothing.

    Adoption was excluded from this run's completions, and then written to a
    receipt that the next run read for its fingerprint alone:

        run A: adopted,   completed = 0, exit 4
        run B: cache_hit, completed = 1, exit 0

    One GENOAR_ADOPT_PRIOR_RESULTS=1, and every ordinary run after it reported
    the same unverifiable BAM as verified work. Reuse is not evidence, however
    many runs pass it along, so an adoption stays an adoption until a Cell
    Ranger run over the input replaces it with work.
    """

    def test_the_adopting_run_still_completes_nothing(self, tmp_path):
        (a, _, _), _ = _stage3_two_runs(tmp_path, adopt_first="1")

        assert a["samples"][SAMPLE]["status"] == "adopted"
        assert a["counts"]["completed"] == 0

    def test_the_next_plain_run_does_not_call_it_a_cache_hit(self, tmp_path):
        _, (b, rec, _) = _stage3_two_runs(tmp_path, adopt_first="1")

        assert rec["status"] == "adopted"
        assert b["counts"]["cache_hit"] == 0
        assert b["counts"]["completed"] == 0
        assert b["counts"]["adopted"] == 1

    def test_the_next_plain_run_says_who_adopted_it_and_what_would_settle_it(self, tmp_path):
        _, (_, rec, _) = _stage3_two_runs(tmp_path, adopt_first="1")

        assert "run-A" in rec["reason"]
        assert "still not verified" in rec["reason"]
        assert "recompute" in rec["reason"]

    def test_the_reused_receipt_is_not_rewritten(self, tmp_path):
        # Run B did not adopt anything; the run that did stays named on it.
        (_, _, first), (_, _, second) = _stage3_two_runs(tmp_path, adopt_first="1")

        assert first["run_id"] == "run-A" and first["provenance"] == "adopted"
        assert second == first

    def test_a_third_run_says_exactly_what_the_second_did(self, tmp_path):
        # Whatever else changes, the answer must not drift towards credit.
        _, (b, brec, _), (c, crec, _) = _stage3_two_runs(
            tmp_path, adopt_first="1", count=3)

        assert b["counts"] == c["counts"]
        assert brec["reason"] == crec["reason"]

    def test_a_genuinely_fresh_run_is_still_a_cache_hit_next_time(self, tmp_path):
        # The case that must not be broken while fixing the adopted one: run A
        # really ran Cell Ranger, and run B reuses it as verified work.
        (a, _, _), (b, rec, receipt) = _stage3_two_runs(tmp_path, first="work")

        assert a["counts"]["fresh"] == 1 and a["counts"]["completed"] == 1
        assert receipt["provenance"] == "fresh" and receipt["run_id"] == "run-A"
        assert rec["status"] == "cache_hit"
        assert b["counts"]["cache_hit"] == 1 and b["counts"]["completed"] == 1
        assert "run-A" in rec["reason"]

    def test_a_receipt_that_does_not_say_what_produced_the_output(self, tmp_path):
        # No provenance field at all: a receipt this reader cannot act on, in
        # either direction. It credits nothing and says so.
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=-500, receipt="match",
                                receipt_provenance=None)

        assert rec["status"] == "unverified"
        assert "does not say what produced it" in rec["reason"]

    def test_a_receipt_from_a_layout_this_reader_does_not_know(self, tmp_path):
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=-500, receipt="match",
                                receipt_provenance="something-later")

        assert rec["status"] == "unverified"

    def test_an_adopted_receipt_for_different_input_is_still_stale(self, tmp_path):
        # Carrying the kind forward must not weaken the fingerprint test.
        _, rec, _ = _stage3_run(tmp_path, bam_mtime=-500, receipt="other",
                                receipt_provenance="adopted")

        assert rec["status"] == "stale"

    def test_this_runs_own_work_still_wins_over_an_adopted_receipt(self, tmp_path):
        # Cell Ranger ran here and recorded it. An old adoption receipt beside
        # the output does not hold the run back.
        _, rec, receipt = _stage3_run(tmp_path, bam_mtime=5, receipt="match",
                                      receipt_provenance="adopted",
                                      record="match")

        assert rec["status"] == "fresh"
        assert receipt["provenance"] == "fresh"


PIPELINE_SH = (Path(__file__).resolve().parents[2] / "srr_pipeline_package"
               / "pipeline_next" / "run_docker_pipeline.sh")


def _stage3_identity(tmp_path, run_id=None, config_run_id=None):
    """Start the real Stage 3 orchestrator and see what it makes of a run id.

    Run identity is settled before step 0, so a refused id costs nothing else
    and an accepted one only has to get as far as creating its run directory.
    Everything after that needs the image, and is not what these ask about.
    """
    import subprocess

    root = tmp_path / "w"
    for name in ("sra", "results", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    config = root / "config.yaml"
    lines = ["input_dir: %s/sra" % root, "output_dir: %s/results" % root,
             "log_dir: %s/logs" % root]
    if config_run_id is not None:
        lines.append("run_id: %s" % config_run_id)
    config.write_text("\n".join(lines) + "\n")

    env = dict(os.environ, CONFIG=str(config))
    env.pop("GENOAR_RUN_ID", None)
    if run_id is not None:
        env["GENOAR_RUN_ID"] = run_id
    proc = subprocess.run(["bash", str(PIPELINE_SH)], env=env,
                          capture_output=True, text=True, timeout=120)
    return proc, root / "results"


class TestARunIdIsADirectoryName:
    """Taken on trust, `../escape` wrote this run's records outside the run
    tree, and an id that already named a run replaced that run's record -- the
    one thing the run-record design exists to keep. The crawler already refuses
    both for the same reason and from the same environment variable, so Stage 3
    is held to the crawler's rule: IDENTITY_PATTERN in genoar_crawler.py.
    """

    ESCAPES = ["../escape", "a/b", "/absolute", "..", ".", "-rf",
               "evil; touch pwned", "$(id)", "`id`", "x with space",
               "y" * 65, "ümlaut"]

    @pytest.mark.parametrize("bad", ESCAPES)
    def test_an_id_that_is_not_a_directory_name_is_refused(self, tmp_path, bad):
        proc, results = _stage3_identity(tmp_path, run_id=bad)

        assert proc.returncode == EXIT_CONFIG_ERROR
        assert "CONFIGURATION ERROR" in proc.stdout
        assert "not usable as a directory name" in proc.stdout
        # Nothing was attempted, and nothing was written outside the run tree.
        assert not (results / "expected_samples.tsv").exists()
        assert not (results / "outcome.json").exists()
        assert list((results / "runs").glob("*")) == [] or \
            not (results / "runs").exists()

    @pytest.mark.parametrize("bad", ESCAPES)
    def test_the_crawler_refuses_the_same_ids(self, tmp_path, bad):
        # Both halves of the pipeline read GENOAR_RUN_ID. They have to agree
        # about what one is, or the same value names a run in one and a
        # traversal in the other.
        with pytest.raises(ValueError):
            gc._validate_identity("GENOAR_RUN_ID", bad)

    def test_the_pointer_file_name_is_reserved(self, tmp_path):
        # <results>/runs/LATEST is the newest-run pointer, so a run of that name
        # would need a directory where that file is.
        proc, _ = _stage3_identity(tmp_path, run_id="LATEST")

        assert proc.returncode == EXIT_CONFIG_ERROR
        assert "reserved" in proc.stdout

    def test_an_id_from_the_config_file_is_validated_too(self, tmp_path):
        proc, results = _stage3_identity(tmp_path, config_run_id="../escape")

        assert proc.returncode == EXIT_CONFIG_ERROR
        assert "config.yaml is not usable" in proc.stdout
        assert not (results / "expected_samples.tsv").exists()

    def test_a_usable_id_is_accepted(self, tmp_path):
        proc, results = _stage3_identity(tmp_path, run_id="run-2026.08_13-1")

        assert proc.returncode != EXIT_CONFIG_ERROR
        assert "[run] RUN_ID=run-2026.08_13-1 (GENOAR_RUN_ID)" in proc.stdout
        assert (results / "runs" / "run-2026.08_13-1").is_dir()
        assert (results / "runs" / "LATEST").read_text().strip() == \
            "run-2026.08_13-1"

    def test_an_id_that_already_has_a_run_record_is_refused(self, tmp_path):
        first, results = _stage3_identity(tmp_path, run_id="keeper")
        assert first.returncode != EXIT_CONFIG_ERROR
        record = results / "runs" / "keeper" / "expected_samples.tsv"
        before = record.read_text()

        second, _ = _stage3_identity(tmp_path, run_id="keeper")

        assert second.returncode == EXIT_CONFIG_ERROR
        assert "already exists" in second.stdout
        # The record of the run that did the work is what this refusal protects.
        assert record.read_text() == before

    @pytest.mark.parametrize("bad", ESCAPES + ["LATEST", ""])
    def test_the_runner_refuses_them_before_starting_a_container(self, tmp_path,
                                                                 bad):
        from cycle_test.utils.stage3_runner import validate_run_id

        assert validate_run_id(bad)

    def test_the_runner_accepts_the_ids_it_generates(self, tmp_path):
        from cycle_test.utils.stage3_runner import (Stage3PipelineRunner,
                                                    validate_run_id)

        runner = Stage3PipelineRunner(tmp_path / "sra", tmp_path / "results",
                                      tmp_path / "logs")
        assert validate_run_id(runner.run_id) == []
        assert gc._validate_identity("generated", runner.run_id)

    def test_the_runner_refuses_an_id_that_already_has_a_record(self, tmp_path):
        from cycle_test.utils.stage3_runner import Stage3PipelineRunner

        runner = Stage3PipelineRunner(tmp_path / "sra", tmp_path / "results",
                                      tmp_path / "logs", run_id="keeper")
        runner.run_record_dir.mkdir(parents=True)

        checks = runner.check_prerequisites()

        assert not checks["run_id_usable"]
        assert any("already has a run record" in problem
                   for problem in checks["configuration_errors"])

    def test_generated_ids_do_not_collide(self, tmp_path):
        # $$ is 1 for a container entrypoint, so a timestamp and a pid were not
        # enough to keep two runs of the same second apart -- and a collision is
        # now a refusal, which would turn a fine run into a failure.
        first, results = _stage3_identity(tmp_path)
        second, _ = _stage3_identity(tmp_path)

        ids = sorted(p.name for p in (results / "runs").iterdir()
                     if p.is_dir())
        assert len(ids) == 2
        for name in ids:
            assert gc._validate_identity("generated", name) == name
        assert first.returncode != EXIT_CONFIG_ERROR
        assert second.returncode != EXIT_CONFIG_ERROR


class TestTheFingerprintCoversTheWholeInput:
    """Hashing only the largest file is not enough: a sample whose other read
    files changed would fingerprint identically to the one before it.
    """

    def _fingerprint(self, tmp_path, files):
        prov = _Provenance()
        sra = tmp_path / "sra" / SAMPLE
        sra.mkdir(parents=True)
        for name, data in files.items():
            (sra / name).write_bytes(data)
        out = tmp_path / "m.tsv"
        prov.cmd_manifest(str(tmp_path / "sra"), str(out), "sha256")
        return out.read_text().split("\n")[1].split("\t")[3]

    def test_changing_a_smaller_read_file_changes_the_fingerprint(self, tmp_path):
        before = self._fingerprint(tmp_path / "a", {
            "SRR000001_S1_R1_001.fastq.gz": b"r1" * 10,
            "SRR000001_S1_R2_001.fastq.gz": b"r2" * 200})
        after = self._fingerprint(tmp_path / "b", {
            "SRR000001_S1_R1_001.fastq.gz": b"XX" * 10,
            "SRR000001_S1_R2_001.fastq.gz": b"r2" * 200})
        assert before != after

    def test_renaming_a_read_file_changes_the_fingerprint(self, tmp_path):
        before = self._fingerprint(tmp_path / "a", {
            "SRR000001_S1_L001_R1_001.fastq.gz": b"r1" * 10,
            "SRR000001_S1_L001_R2_001.fastq.gz": b"r2" * 200})
        after = self._fingerprint(tmp_path / "b", {
            "SRR000001_S1_L002_R1_001.fastq.gz": b"r1" * 10,
            "SRR000001_S1_L002_R2_001.fastq.gz": b"r2" * 200})
        assert before != after

    def test_an_unchanged_input_fingerprints_the_same(self, tmp_path):
        files = {"SRR000001_S1_R1_001.fastq.gz": b"r1" * 10,
                 "SRR000001_S1_R2_001.fastq.gz": b"r2" * 200}
        assert (self._fingerprint(tmp_path / "a", files)
                == self._fingerprint(tmp_path / "b", files))

    def test_a_single_sra_file_still_fingerprints_by_its_own_bytes(self, tmp_path):
        # Unchanged from before: an SRA sample is one file, and its fingerprint
        # is that file's hash, so receipts written for it stay comparable.
        import hashlib

        fp = self._fingerprint(tmp_path, {"SRR000001.sra": b"SRA-BYTES"})
        assert fp == "sha256:" + hashlib.sha256(b"SRA-BYTES").hexdigest()


class TestAPaddedIdIsTheSameIdStripped:
    """Strip first, judge what is underneath, then use exactly that.

    `validate_run_id` matched with `re.match`, whose `$` also matches before a
    trailing newline: "good\\n" was approved and then used - as "good\\n" - to
    name a directory and to set GENOAR_RUN_ID inside the container. "LATEST\\n"
    was approved the same way and was not equal to "LATEST", so it walked past
    the reserved-name check too. Checking a value and checking a prefix of it
    are different acts, and only one of them is a check.

    The fix is `fullmatch` plus one normalization, not a refusal of padding:
    genoar_crawler.py strips GENOAR_RUN_ID, and both halves read that same
    variable, so ` pilot ` has to name one run or a full-pipeline run splits
    down the middle after the crawl. Stripping first keeps the property that
    mattered - the string that is validated is the string that is used - and
    nothing rides through on the whitespace: what is left is judged on its own,
    and no created directory can contain any.

    (genoar_crawler.py's `_validate_identity` still uses `.match`. Reading
    GENOAR_RUN_ID it strips first, so the environment path is safe, but an id
    handed to it directly - `run_dir_for(output, "good\\n")` - is not. That file
    belongs to another owner this round and is reported, not touched.)
    """

    # (value, the id it means, or None when it may not be used at all)
    CASES = [
        ("good-id-1", "good-id-1"),
        ("good-id-1\n", "good-id-1"),        # a trailing newline is whitespace
        ("  good-id-1  ", "good-id-1"),
        ("good-id-1\r", "good-id-1"),
        ("\tgood-id-1", "good-id-1"),
        ("LATEST\n", None),                  # strips to the reserved name
        (" LATEST ", None),
        ("../escape\n", None),               # strips to a path
        (".\n", None),
        ("a\nb", None),                      # inner whitespace is not stripped
        ("has space", None),
        ("y" * 65 + "\n", None),             # too long once stripped
    ]

    @pytest.mark.parametrize("value,means", CASES)
    def test_the_runner_reads_it_the_way_the_shell_gate_does(self, tmp_path,
                                                             value, means):
        from cycle_test.utils.stage3_runner import (normalize_run_id,
                                                    validate_run_id)

        problems = validate_run_id(normalize_run_id(value))
        proc, results = _stage3_identity(tmp_path, run_id=value)

        if means is None:
            assert problems
            assert proc.returncode == EXIT_CONFIG_ERROR
            assert "cannot be used" in proc.stdout
            assert list((results / "runs").glob("*")) == [] or \
                not (results / "runs").exists()
        else:
            assert problems == []
            assert proc.returncode != EXIT_CONFIG_ERROR
            assert f"[run] RUN_ID={means} (GENOAR_RUN_ID)" in proc.stdout
            # The directory is named by the stripped value, and by nothing else.
            assert [p.name for p in (results / "runs").iterdir() if p.is_dir()] \
                == [means]

    def test_a_padded_id_and_a_plain_one_are_one_run(self, tmp_path):
        # The consequence that matters: the second of these is not a new run
        # with a whitespace-flavoured name, it is the same id, already taken.
        first, results = _stage3_identity(tmp_path, run_id="pilot")
        assert first.returncode != EXIT_CONFIG_ERROR

        second, _ = _stage3_identity(tmp_path, run_id="  pilot\n")

        assert second.returncode == EXIT_CONFIG_ERROR
        assert "already exists" in second.stdout
        assert [p.name for p in (results / "runs").iterdir() if p.is_dir()] \
            == ["pilot"]

    def test_no_run_directory_ever_carries_whitespace(self, tmp_path):
        for value, means in self.CASES:
            proc, results = _stage3_identity(tmp_path / str(abs(hash(value))),
                                             run_id=value)
            for path in (results / "runs").glob("*"):
                assert not any(c.isspace() for c in path.name), (value, proc)

    def test_an_id_that_is_only_whitespace_means_no_id_was_set(self, tmp_path):
        # The crawler reads GENOAR_RUN_ID this way too: blank is unset, and an
        # unset id is generated rather than refused.
        proc, results = _stage3_identity(tmp_path, run_id="   ")

        assert proc.returncode != EXIT_CONFIG_ERROR
        assert "(generated)" in proc.stdout

    def test_the_id_that_is_validated_is_the_id_that_is_used(self, tmp_path):
        from cycle_test.utils.stage3_runner import (Stage3PipelineRunner,
                                                    validate_run_id)

        runner = Stage3PipelineRunner(tmp_path / "sra", tmp_path / "results",
                                      tmp_path / "logs", run_id="good\n")

        # Normalized once, at the boundary. Everything downstream - the record
        # directory, the GENOAR_RUN_ID the container gets, the generated config
        # - is this one string, and it is the string validate_run_id passed.
        assert runner.run_id == "good"
        assert validate_run_id(runner.run_id) == []
        assert runner.run_record_dir.name == "good"

    def test_the_unstripped_form_is_still_refused_on_its_own(self):
        # normalize_run_id is where whitespace is allowed to matter. Handed a
        # raw value, the validator judges the whole of it - which is what makes
        # a direct caller that forgets to normalize fail loudly rather than
        # create `runs/good\n`.
        from cycle_test.utils.stage3_runner import validate_run_id

        for raw in ("good\n", "LATEST\n", " pad ", "y" * 65):
            assert validate_run_id(raw)


def _race_fixture(tmp_path, run_id="racer"):
    """Two runs of one id, over the same results directory, from two inputs.

    Different inputs make a lost race visible rather than merely duplicated: the
    expected-sample manifest is the run's record of what it was asked to do, and
    whoever writes it second replaces what the first one promised.
    """
    root = tmp_path / "race"
    (root / "results").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)
    configs = {}
    for name, sample in (("a", "SRR000001"), ("b", "SRR000002")):
        sra = root / f"sra_{name}" / sample
        sra.mkdir(parents=True)
        (sra / f"{sample}.sra").write_bytes(b"INPUT-" + name.encode())
        config = root / f"config_{name}.yaml"
        config.write_text(
            "input_dir: %s\noutput_dir: %s\nlog_dir: %s\n"
            % (root / f"sra_{name}", root / "results", root / "logs"))
        configs[name] = (config, sample)
    return root, configs


class TestTwoRunsCannotShareOneId:
    """Reserving an id was a check followed, later, by a create.

    `[[ -e $OUTPUT_DIR/runs/$RUN_ID ]]` and `mkdir -p "$RUN_DIR/samples"` are two
    acts, and between them another run does the same two. That only ever caught
    a run that had already finished - the case that was never dangerous, because
    a finished run's record is on disk and nothing was going to touch it. Two
    runs started together both passed: 26 of 30 concurrent trials of the
    previous version reserved one id twice, and then wrote one manifest, one
    outcome and one set of per-sample records between them. In 24 of those, one
    of the two also died on the shared pointer file and exited 1 - a run that
    was fine, reported as a failure.

    `mkdir` without -p is the reservation now: it creates the directory or fails
    because it exists, in one step nothing can interleave, so exactly one of any
    number of racers gets the id. This is the primitive the crawler's launchers
    use for the same job, so both halves of the pipeline hold an id the same way.
    """

    # A race that passes by luck is worse than none. The barrier below makes one
    # pair enough to reproduce (both racers are already inside bash, past every
    # fork, when the starter file appears), but several run by default and the
    # count is raisable for a soak: GENOAR_RACE_TRIALS=30 python3 -m pytest ...
    TRIALS = int(os.environ.get("GENOAR_RACE_TRIALS", "5"))

    def _race(self, tmp_path):
        root, configs = _race_fixture(tmp_path)
        barrier = root / "go"
        procs = {}
        for name, (config, _) in configs.items():
            env = dict(os.environ, CONFIG=str(config), GENOAR_RUN_ID="racer")
            env.pop("GENOAR_ADOPT_PRIOR_RESULTS", None)
            procs[name] = subprocess.Popen(
                ["bash", "-c",
                 'while [ ! -e "$1" ]; do :; done; exec bash "$2"',
                 "racer", str(barrier), str(PIPELINE_SH)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True)
        # Both are spinning on the barrier by now; releasing it starts them
        # within scheduler noise of each other.
        barrier.write_text("go\n")
        out = {name: proc.communicate()[0] for name, proc in procs.items()}
        return root, configs, {name: proc.returncode
                               for name, proc in procs.items()}, out

    def test_exactly_one_of_two_simultaneous_runs_reserves_the_id(self, tmp_path):
        for trial in range(self.TRIALS):
            root, _, codes, out = self._race(tmp_path / f"t{trial}")

            refused = [n for n, text in out.items()
                       if "run id 'racer' cannot be used" in text]
            assert len(refused) == 1, (
                "trial %d: %d of 2 runs were refused the id; both reserving it "
                "is the defect" % (trial, len(refused)))
            assert codes[refused[0]] == EXIT_CONFIG_ERROR
            winner = next(n for n in codes if n != refused[0])
            assert codes[winner] != EXIT_CONFIG_ERROR

    def test_the_loser_writes_nothing_and_removes_nothing(self, tmp_path):
        for trial in range(self.TRIALS):
            root, configs, codes, out = self._race(tmp_path / f"t{trial}")

            refused = next(n for n, text in out.items()
                           if "run id 'racer' cannot be used" in text)
            winner = next(n for n in codes if n != refused)
            runs = root / "results" / "runs"
            manifest = (runs / "racer" / "expected_samples.tsv").read_text()

            # The record describes the run that reserved the id, and only it.
            assert configs[winner][1] in manifest
            assert configs[refused][1] not in manifest
            assert (runs / "LATEST").read_text().strip() == "racer"
            # Not even a temporary file: mkdir leaves the loser's hands empty.
            assert list(runs.glob(".LATEST.*")) == []

    def test_an_unwritable_results_tree_is_not_reported_as_a_taken_id(self, tmp_path):
        # mkdir fails for reasons that have nothing to do with the id, and
        # telling a user their run id is taken when the volume is read-only
        # sends them to change the one thing that was fine.
        #
        # Exit 1, not 2: the id was usable and the machine could not honour it.
        # Exit 2 is for what the user gave the run - an unusable id, a missing
        # Cell Ranger - and a full disk is not that. The crawler's launchers
        # separate the two the same way.
        root, configs = _race_fixture(tmp_path)
        runs = root / "results" / "runs"
        runs.mkdir()
        runs.chmod(0o500)
        try:
            env = dict(os.environ, CONFIG=str(configs["a"][0]),
                       GENOAR_RUN_ID="racer")
            proc = subprocess.run(["bash", str(PIPELINE_SH)], env=env,
                                  capture_output=True, text=True, timeout=120)
        finally:
            runs.chmod(0o700)

        assert proc.returncode == 1
        assert proc.returncode != EXIT_CONFIG_ERROR
        assert "could not be created" in proc.stdout
        assert "does NOT say the run id is taken" in proc.stdout
        assert "already exists" not in proc.stdout

    def test_two_generated_ids_started_together_both_proceed(self, tmp_path):
        # The generated path mints its own id, so concurrency must cost it
        # nothing: two runs starting at once must both get one.
        root, configs = _race_fixture(tmp_path)
        barrier = root / "go"
        procs = []
        for name, (config, _) in configs.items():
            env = dict(os.environ, CONFIG=str(config))
            env.pop("GENOAR_RUN_ID", None)
            procs.append(subprocess.Popen(
                ["bash", "-c",
                 'while [ ! -e "$1" ]; do :; done; exec bash "$2"',
                 "racer", str(barrier), str(PIPELINE_SH)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True))
        barrier.write_text("go\n")
        outs = [p.communicate()[0] for p in procs]

        assert all(p.returncode != EXIT_CONFIG_ERROR for p in procs), outs
        ids = sorted(p.name for p in (root / "results" / "runs").iterdir()
                     if p.is_dir())
        assert len(ids) == 2


# ---------------------------------------------------------------------------
# The rule and the verifier, driven together
#
# Everything above writes the completion record itself and asks what the
# verifier makes of it. That tests the record's *format* and leaves its
# *production* untested: a rule that stopped writing one, or wrote a different
# one, would pass every one of them. The two halves are the mechanism, and only
# running both proves they agree.
#
# So these run the real Snakemake rule from Snakefile_hs.smk, with a fake
# `cellranger` that produces a BAM, and then the real verifier out of
# run_docker_pipeline.sh, end to end. Snakemake is not a test dependency of this
# repository, so they run inside the Stage 3 image, which is where the rule runs
# in production.
#
# The fixture is copied into the container's own filesystem first, deliberately.
# The rule identifies the BAM it produced by the inode it occupies, and a macOS
# Docker Desktop bind mount (virtiofs) hands out a different inode number for
# the same unchanged file from one stat to the next -- which turns an honest
# fresh run into `unverified`. That is a real limitation of running Stage 3 over
# a mounted host directory on macOS, and it is not what these tests are about.
# ---------------------------------------------------------------------------

STAGE3_IMAGE = os.environ.get("GENOAR_STAGE3_IMAGE", "genoar-srr:step9")
SNAKEFILE = (Path(__file__).resolve().parents[2] / "srr_pipeline_package"
             / "pipeline_next" / "Snakefile_hs.smk")

# What the fake Cell Ranger does: create the one file that means "this sample
# was analysed", where the rule's command line says to put it.
#
# It answers `--version` first, in the shape a real 8.x answers it. The rule
# reads the installed version to decide whether `--create-bam` exists, and a
# launcher that answers nothing now stops the run instead of having a form
# guessed for it — so a stub that only pretends to count is not a stand-in for
# Cell Ranger, it is a Cell Ranger that cannot be identified.
FAKE_CELLRANGER = """#!/bin/sh
case "$1" in
  --version) echo "cellranger cellranger-8.0.1"; exit 0 ;;
esac
id=cellranger_output
while [ $# -gt 0 ]; do
  case "$1" in --id) id="$2"; shift ;; esac
  shift
done
mkdir -p "$id/outs"
printf 'FAKE BAM\\n' > "$id/outs/possorted_genome_bam.bam"
"""

# One snakemake invocation with the arguments the orchestrator passes, then the
# shipped verifier over what it left behind, then one line of JSON for the test.
RULE_AND_VERIFY = """#!/bin/bash
set -e
if [ "${GENOAR_RULE_IN_PLACE:-0}" = 1 ]; then
  # The mounted host directory itself, filesystem and all.
  ln -s /fixture /tmp/work
else
  cp -a /fixture /tmp/work
fi
cd /tmp/work
chmod +x bin/cellranger
mkdir -p runs/run-B ref
python3 prov.py manifest /tmp/work/sra /tmp/work/runs/run-B/expected_samples.tsv sha256 >&2
snakemake -s /tmp/work/Snakefile_hs.smk --cores 1 --printshellcmds \\
  --config success_dir=/tmp/work/success ref_path=/tmp/work/ref \\
  cellranger_bin=/tmp/work/bin/cellranger run_id=run-B \\
  run_dir=/tmp/work/runs/run-B >&2
python3 prov.py verify /tmp/work/runs/run-B/expected_samples.tsv \\
  /tmp/work/success run-B 1700000000 /tmp/work/runs/run-B 0 /tmp/work/sra 1
python3 - <<'REPORT'
import json, os
run = "/tmp/work/runs/run-B"
outcome = json.load(open(os.path.join(run, "outcome.json")))
sample = outcome["samples"]["SRR000001"]
path = os.path.join(run, "cellranger", "SRR000001.json")
record = json.load(open(path)) if os.path.isfile(path) else {}
print("GENOAR_RESULT " + json.dumps({
    "counts": outcome["counts"],
    "status": sample["status"],
    "reason": sample.get("reason", ""),
    "completion_record": os.path.isfile(
        os.path.join(run, "cellranger", "SRR000001.json")),
    "record_digests": bool(record.get("bam_head") and record.get("bam_tail")
                           and record.get("digest_bytes")),
    "bam": os.path.isfile("/tmp/work/success/SRR000001/cellranger_output/"
                          "outs/possorted_genome_bam.bam"),
}))
REPORT
"""


def _stage3_image_or_skip():
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed, so the real Cell Ranger rule "
                    "cannot be run; snakemake is not installed here either")
    probe = subprocess.run(["docker", "image", "inspect", STAGE3_IMAGE],
                           capture_output=True)
    if probe.returncode != 0:
        pytest.skip(f"the Stage 3 image {STAGE3_IMAGE} is not built, so the "
                    "real Cell Ranger rule cannot be run; build it with "
                    "`make build-srr` (or set GENOAR_STAGE3_IMAGE)")


def _cellranger_rule_fixture(directory, snakefile_text):
    """One eligible sample, a fake cellranger, and the shipped verifier."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Snakefile_hs.smk").write_text(snakefile_text)
    (directory / "prov.py").write_text(_provenance_source()[1])
    (directory / "run.sh").write_text(RULE_AND_VERIFY)
    binary = directory / "bin" / "cellranger"
    binary.parent.mkdir()
    binary.write_text(FAKE_CELLRANGER)
    binary.chmod(0o755)
    sample = directory / "success" / "SRR000001"
    sample.mkdir(parents=True)
    (sample / "SRR000001_S1_R1_001.fastq.gz").write_bytes(b"R1")
    (sample / "SRR000001_S1_R2_001.fastq.gz").write_bytes(b"R2")
    sra = directory / "sra" / "SRR000001"
    sra.mkdir(parents=True)
    (sra / "SRR000001.sra").write_bytes(b"INPUT")
    return directory


def _run_rule_and_verifier(directory, snakefile_text, in_place=False):
    """Run the rule and the verifier over one sample, in the Stage 3 image.

    `in_place` runs them directly on the mounted host directory instead of on a
    copy inside the container -- which on macOS means virtiofs, the filesystem
    that renumbers an unchanged file across the job boundary. That is the path
    a user on a Mac actually takes, and the one the content digests exist for.
    It runs as the invoking user so the run leaves nothing root-owned behind in
    a pytest temporary directory.
    """
    _stage3_image_or_skip()
    fixture = _cellranger_rule_fixture(directory, snakefile_text)
    if in_place:
        mounts = ["-v", f"{fixture}:/fixture",
                  "--user", f"{os.getuid()}:{os.getgid()}",
                  "-e", "GENOAR_RULE_IN_PLACE=1"]
    else:
        mounts = ["-v", f"{fixture}:/fixture:ro"]
    proc = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "bash",
         "-e", "HOME=/tmp"] + mounts + [STAGE3_IMAGE, "/fixture/run.sh"],
        capture_output=True, text=True, timeout=900)
    line = next((l for l in proc.stdout.split("\n")
                 if l.startswith("GENOAR_RESULT ")), None)
    assert line, ("the rule and the verifier did not run to a verdict:\n"
                  + proc.stdout[-4000:] + "\n" + proc.stderr[-4000:])
    return json.loads(line[len("GENOAR_RESULT "):])


class TestTheRuleAndTheVerifierAgree:
    """The Cell Ranger rule and the run verifier, run against each other.

    The rule is the only thing that can say a sample is this run's own work, and
    the verifier is the only thing that reads what it says. Each was tested
    against a hand-written copy of the other's file, which is exactly the shape
    of test that survives the two halves drifting apart.
    """

    def test_a_sample_the_real_rule_ran_is_credited_to_the_run(self, tmp_path):
        result = _run_rule_and_verifier(tmp_path / "real",
                                        SNAKEFILE.read_text())

        assert result["bam"], "the fake cellranger produced no BAM"
        assert result["completion_record"], (
            "the rule ran Cell Ranger and recorded nothing")
        assert result["status"] == "fresh", result["reason"]
        assert result["counts"]["fresh"] == 1
        assert result["counts"]["completed"] == 1
        assert result["counts"]["unverified"] == 0

    def test_the_run_survives_a_filesystem_that_renumbers_its_output(self, tmp_path):
        """The rule and the verifier, on the mounted host directory itself.

        On macOS that is virtiofs, which hands back a different inode number
        for the same untouched file across the job boundary -- measured at
        64592 when the rule recorded it and 64597 afterwards, same size, same
        timestamp, same bytes. Before the content digests this reported
        `unverified` and the run exited 4; the output was perfect and the run
        could not say so.

        On a filesystem that does keep the number still this passes for the
        ordinary reason, which is the point: one code path, and it is right on
        both.
        """
        result = _run_rule_and_verifier(tmp_path / "in_place",
                                        SNAKEFILE.read_text(), in_place=True)

        assert result["bam"]
        assert result["completion_record"]
        assert result["status"] == "fresh", result["reason"]
        assert result["counts"]["completed"] == 1

    def test_the_rule_records_what_the_verifier_reads(self, tmp_path):
        # The two halves compute the same two windows over the same file, in
        # two different files of source. Nothing but running them together says
        # so.
        result = _run_rule_and_verifier(tmp_path / "digests",
                                        SNAKEFILE.read_text())

        assert result["record_digests"], (
            "the rule recorded no content digests: %r" % (result,))
        assert result["status"] == "fresh"

    def test_a_rule_that_stops_recording_completes_nothing(self, tmp_path):
        """The proof that the test above is about the rule.

        The same run, the same fake Cell Ranger, the same BAM on disk - with the
        one call that records the work removed. Nothing else changes, and the
        run can no longer claim the sample.
        """
        text = SNAKEFILE.read_text()
        call = ("        record_cellranger_completed(wildcards.sample, "
                "output.bam,\n"
                "                                    list(input.fastqs), "
                "started_at)")
        assert call in text, (
            "the rule no longer makes the call this test removes; update the "
            "mutation to match Snakefile_hs.smk")
        forgetful = text.replace(
            call, "        pass  # the rule stops recording what it produced")

        result = _run_rule_and_verifier(tmp_path / "forgetful", forgetful)

        assert result["bam"], "the fake cellranger produced no BAM"
        assert not result["completion_record"]
        assert result["status"] == "unverified"
        assert result["counts"]["completed"] == 0

    def _orchestrator_over_a_stray_bam(self, tmp_path, name, runs):
        """The whole orchestrator, `runs` times, over one persistent volume.

        `runs` is a list of (run id, environment prefix). One BAM nobody can
        account for is in place before the first run, and the results volume
        carries everything each run leaves for the next. Returns one
        (exit code, banner text) per run.
        """
        _stage3_image_or_skip()
        fixture = tmp_path / name
        (fixture / "sra" / "SRR000001").mkdir(parents=True)
        (fixture / "sra" / "SRR000001" / "SRR000001.sra").write_bytes(b"INPUT")
        bam = (fixture / "results" / "success" / "SRR000001"
               / "cellranger_output" / "outs" / "possorted_genome_bam.bam")
        bam.parent.mkdir(parents=True)
        bam.write_bytes(b"OUTPUT OF NOBODY KNOWS WHAT")
        (fixture / "logs").mkdir()
        (fixture / "ref").mkdir()
        for part in ("fasta", "genes", "star"):
            (fixture / "ref" / part).mkdir()
        (fixture / "ref" / "reference.json").write_text("{}\n")
        binary = fixture / "bin" / "cellranger"
        binary.parent.mkdir()
        # Names a version, as the stub at :395 does. A launcher that reports
        # none is a configuration fault step 0 refuses, which would stop the
        # run before it reaches the adoption rule these tests are about.
        binary.write_text('#!/bin/sh\necho "cellranger cellranger-8.0.1"\nexit 0\n')
        binary.chmod(0o755)
        (fixture / "config.yaml").write_text(
            "input_dir: /tmp/work/sra\noutput_dir: /tmp/work/results\n"
            "log_dir: /tmp/work/logs\nref_path: /tmp/work/ref\n"
            "cellranger_bin: /tmp/work/bin/cellranger\n")
        shutil.copy(PIPELINE_SH, fixture / "run_docker_pipeline.sh")
        script = [
            "#!/bin/bash",
            "cp -a /fixture /tmp/work",
            "cd /tmp/work",
            "chmod +x bin/cellranger",
            # This is about the final banner, not about fastq-dump, which has
            # no real SRA archive to read here. Stubbed in the tree the script
            # under test actually calls -- it names its own steps by absolute
            # path, and stubbing the other tree's leaves the real one running.
            "printf '#!/bin/sh\\nexit 0\\n' > /pipeline_next/fastq_dump_parallel.sh",
        ]
        for run_id, env in runs:
            script += [
                "echo \"GENOAR_RUN %s\"" % run_id,
                "CONFIG=/tmp/work/config.yaml GENOAR_RUN_ID=%s %s "
                "bash /tmp/work/run_docker_pipeline.sh" % (run_id, env),
                "echo \"GENOAR_EXIT $?\"",
            ]
        (fixture / "run.sh").write_text("\n".join(script) + "\n")

        proc = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "bash",
             "-v", f"{fixture}:/fixture:ro", STAGE3_IMAGE, "/fixture/run.sh"],
            capture_output=True, text=True, timeout=900)
        out, results, banner = proc.stdout.split("\n"), [], []
        for line in out:
            if line.startswith("[result]"):
                banner.append(line)
            elif line.startswith("GENOAR_EXIT "):
                results.append((int(line.split()[1]), "\n".join(banner)))
                banner = []
        assert len(results) == len(runs), proc.stdout[-4000:] + proc.stderr[-2000:]
        return results

    def test_adoption_does_not_carry_the_run_to_exit_0(self, tmp_path):
        """The whole orchestrator, over one adopted sample.

        A BAM nobody can account for, the expected sample it happens to be
        named after, and GENOAR_ADOPT_PRIOR_RESULTS=1. None of that may print
        "PIPELINE COMPLETED - all 1 expected sample(s) have Cell Ranger output"
        or exit 0.
        """
        (code, text), = self._orchestrator_over_a_stray_bam(
            tmp_path, "adopt", [("adoptrun", "GENOAR_ADOPT_PRIOR_RESULTS=1")])

        assert code == EXIT_PARTIAL, text
        assert "verified complete: 0" in text
        assert "adopted, NOT verified and NOT counted as complete: 1" in text
        # The two words are never spent on each other.
        assert "PIPELINE COMPLETED" not in text
        assert "0 of 1 expected sample(s) have VERIFIED Cell Ranger output." in text
        assert "Do not report this run as a success." in text

    def test_the_run_after_an_adoption_does_not_launder_it(self, tmp_path):
        """The whole orchestrator, twice.

        Run A adopts. Run B is an ordinary run with no flags at all, and must
        not read run A's adoption receipt as a cache hit:

            run A: adopted,   completed = 0, exit 4
            run B: cache_hit, completed = 1, exit 0

        Without that, refusing to complete on adoption holds for exactly one
        run. What run B may say is decided by what the receipt says it is, so
        run B says what run A said.
        """
        (first, a), (second, b) = self._orchestrator_over_a_stray_bam(
            tmp_path, "launder",
            [("adoptrun", "GENOAR_ADOPT_PRIOR_RESULTS=1"), ("plainrun", "")])

        assert first == EXIT_PARTIAL, a
        assert second == EXIT_PARTIAL, b
        assert "verified complete: 0" in b
        assert "cache hit 0" in b
        assert "adopted, NOT verified and NOT counted as complete: 1" in b
        assert "PIPELINE COMPLETED" not in b
        assert "Do not report this run as a success." in b
        # And it says whose adoption it is reusing.
        assert "adopted by run adoptrun" in b

    def test_the_run_that_records_is_the_run_that_claims(self, tmp_path):
        """A record the rule files under another run's id credits nobody.

        Same rule, same output, one word different: the run id it stamps. The
        verifier is asked about run-B and finds a record about run-OTHER.
        """
        text = SNAKEFILE.read_text().replace(
            'run_id = str(config.get("run_id", ""))',
            'run_id = "run-OTHER"  # the rule stamps someone else\'s run')

        result = _run_rule_and_verifier(tmp_path / "foreign", text)

        assert result["bam"]
        assert result["status"] == "unverified"
        assert result["counts"]["completed"] == 0
