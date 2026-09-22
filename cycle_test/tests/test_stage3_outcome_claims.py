"""What a Stage 3 run is allowed to claim, at both ends of the handoff.

Two places are in a position to report more than they know.

run_cycle_test.py ends its Stage 3 handling with a catch-all. Turning every
failure into cycle status partial_success grades a run that left no record at
all as partial work, and a run that proved nothing is not a partial success.

run_hpc_stage3.py sees PBS accept the job. Calling that success, without reading
the run record the pipeline writes, tells a caller a cluster analysis succeeded
when all that has happened is a submission.

No cluster is available here, so the HPC tests drive the wrapper against the fake
cluster in conftest.py: stand-in `ssh`, `qsub` and `qstat` on PATH, the
outcome.json a retrieval brings back, and the PBS Exit_status qstat reports.

This file owns the PBS side of the reporting contract.
`test_hpc_wrapper_contract.py` owns the same contract under Slurm, along with
everything only Slurm has. The overlap is deliberate: the contract has to hold
whichever scheduler the site runs, and the code paths differ.
"""

import json
import logging
import sys
from pathlib import Path
from unittest import mock

import pytest

from cycle_test.run_cycle_test import _stage3_cycle_status, _exit_code_for
from cycle_test.tests.conftest import qstat_detail

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "srr_pipeline_package" / "hpc"))
import prepare_hpc_handoff as handoff  # noqa: E402
import run_hpc_stage3 as hpc  # noqa: E402


# --------------------------------------------------------------------------
# Stage 3 outcome -> cycle status
# --------------------------------------------------------------------------

def _outcome(**over):
    """A Stage 3 pipeline result in the shape stage3_runner returns."""
    result = {
        "success": False,
        "status": "failed",
        "error": None,
        "nothing_processed": False,
        "nothing_processed_reason": None,
        "partial": False,
        "partial_reason": None,
        "configuration_error": False,
        "expected_count": 2,
        "cellranger_completed": 0,
        "successful_samples": 0,
        "failed_samples": 0,
        "returncode": 1,
    }
    result.update(over)
    return result


# One row per outcome stage3_runner._decide_outcome can produce, and the cycle
# status that is true of it.
STAGE3_OUTCOMES = {
    "success": (
        _outcome(success=True, status="success", returncode=0,
                 cellranger_completed=2, successful_samples=2),
        "success",
    ),
    "partial_success": (
        _outcome(status="partial_success", partial=True, returncode=4,
                 partial_reason="1 of 2 expected sample(s) have verified output",
                 cellranger_completed=1, successful_samples=2),
        "partial_success",
    ),
    "nothing_processed": (
        _outcome(status="nothing_processed", nothing_processed=True, returncode=3,
                 nothing_processed_reason="no sample qualified for Cell Ranger",
                 successful_samples=2),
        "no_data",
    ),
    "config_error": (
        _outcome(status="config_error", configuration_error=True, returncode=2,
                 error="no usable cellranger executable"),
        "failed_stage3",
    ),
    "container_failure": (
        _outcome(status="failed", error="Docker exited with code 137"),
        "failed_stage3",
    ),
    "no_run_record": (
        _outcome(status="failed", returncode=0,
                 error="this Stage 3 run left no outcome.json, so it made no "
                       "statement about what it processed"),
        "failed_stage3",
    ),
    "counts_disagree": (
        _outcome(status="failed", returncode=3, cellranger_completed=2,
                 error="the run and its results disagree"),
        "failed_stage3",
    ),
    "step8_ok_without_output": (
        _outcome(status="failed", returncode=0,
                 error="Pipeline marked step8 ok but no sample has Cell Ranger output"),
        "failed_stage3",
    ),
}


class TestEveryStage3OutcomeGetsTheStatusItEarned:
    @pytest.mark.parametrize("name", sorted(STAGE3_OUTCOMES))
    def test_the_mapping(self, name):
        pipeline_result, expected = STAGE3_OUTCOMES[name]
        status, _ = _stage3_cycle_status(pipeline_result)
        assert status == expected

    def test_a_run_with_no_record_is_not_a_partial_success(self):
        # Landing every Stage 3 failure on partial_success credits a run that
        # left no record with work nobody can point at.
        pipeline_result, _ = STAGE3_OUTCOMES["no_run_record"]
        status, reason = _stage3_cycle_status(pipeline_result)
        assert status == "failed_stage3"
        assert "outcome.json" in reason

    def test_only_a_measured_shortfall_is_partial(self):
        partial = [name for name, (_, status) in STAGE3_OUTCOMES.items()
                   if status == "partial_success"]
        assert partial == ["partial_success"]

    def test_a_failure_carries_its_reason(self):
        _, reason = _stage3_cycle_status(STAGE3_OUTCOMES["container_failure"][0])
        assert "137" in reason

    def test_an_unknown_grade_is_a_failure(self):
        # A status this table does not know is not evidence of work.
        status, _ = _stage3_cycle_status(_outcome(status="something_new"))
        assert status == "failed_stage3"

    def test_a_grade_that_says_success_and_denies_it_is_a_failure(self):
        status, _ = _stage3_cycle_status(_outcome(status="success", success=False))
        assert status == "failed_stage3"

    def test_fastq_failures_under_a_reported_success_are_still_partial(self):
        status, reason = _stage3_cycle_status(
            _outcome(success=True, status="success", failed_samples=1,
                     successful_samples=1))
        assert status == "partial_success"
        assert "1 sample(s) failed" in reason

    def test_every_fastq_sample_failing_is_a_stage3_failure(self):
        status, _ = _stage3_cycle_status(
            _outcome(success=True, status="success", failed_samples=2,
                     successful_samples=0))
        assert status == "failed_stage3"


class TestTheCycleCarriesTheStatusThrough:
    """The same table, driven through run_single_cycle rather than the helper."""

    def _run(self, tmp_path, pipeline_result):
        from cycle_test import run_cycle_test as rct

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        (dirs["stage3"] / "srr_list.txt").write_text("SRR000001\n")
        with mock.patch.object(rct, "extract_srr_from_stage2_tables",
                               return_value={"success": True, "unique_srr_ids": 1,
                                             "gse_count": 1}), \
             mock.patch.object(rct, "download_sra_files",
                               return_value={"success": True, "total": 1,
                                             "downloaded": 1, "failed": 0}), \
             mock.patch.object(rct, "run_stage3_pipeline", return_value=pipeline_result):
            return rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs, umls_dir=tmp_path,
                logger=logging.getLogger("test"), stage3_only=True,
            )

    @pytest.mark.parametrize("name", sorted(STAGE3_OUTCOMES))
    def test_the_cycle_status(self, tmp_path, name):
        pipeline_result, expected = STAGE3_OUTCOMES[name]
        assert self._run(tmp_path, pipeline_result)["status"] == expected

    def test_a_run_with_no_record_fails_the_cycle(self, tmp_path):
        result = self._run(tmp_path, STAGE3_OUTCOMES["no_run_record"][0])
        assert result["status"] == "failed_stage3"
        assert "outcome.json" in result["failed_reason"]
        assert _exit_code_for([result]) == 1

    def test_only_the_success_row_exits_zero(self, tmp_path):
        for name, (pipeline_result, expected) in STAGE3_OUTCOMES.items():
            result = self._run(tmp_path, pipeline_result)
            assert _exit_code_for([result]) == (0 if expected == "success" else 1), name

    def test_an_earlier_problem_is_not_softened(self, tmp_path):
        from cycle_test import run_cycle_test as rct

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        (dirs["stage3"] / "srr_list.txt").write_text("SRR000001\n")
        with mock.patch.object(rct, "extract_srr_from_stage2_tables",
                               return_value={"success": True, "unique_srr_ids": 1,
                                             "gse_count": 1}), \
             mock.patch.object(rct, "download_sra_files",
                               return_value={"success": False, "total": 3,
                                             "downloaded": 1, "failed": 2}), \
             mock.patch.object(rct, "run_stage3_pipeline",
                               return_value=STAGE3_OUTCOMES["success"][0]):
            result = rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs, umls_dir=tmp_path,
                logger=logging.getLogger("test"), stage3_only=True,
            )
        assert result["status"] == "partial_success"


# --------------------------------------------------------------------------
# The HPC wrapper reports the analysis, not the submission
# --------------------------------------------------------------------------

def _record(expected=2, completed=2, adopted=0, **counts):
    """An outcome.json as run_docker_pipeline.sh writes it."""
    base = {"expected": expected, "completed": completed, "adopted": adopted,
            "fresh": completed, "cache_hit": 0, "ineligible": 0, "missing": 0,
            "unverified": 0, "stale": 0}
    base.update(counts)
    return {
        "run_id": "run-fixture",
        "expected_samples": [f"SRR{i}" for i in range(expected)],
        "counts": base,
        "samples": {},
        "other_samples_with_output": [],
    }


def _run_wrapper(cluster, record, exit_status, prior_runs=(), execute=True):
    """One --execute run of the wrapper, over a fake PBS cluster.

    `record` is the outcome.json a retrieval brings back, or None for a run whose
    record never arrives. `exit_status` is the PBS Exit_status of the finished
    job, or None when qstat cannot supply one.

    Both arrive as stand-in binaries on PATH (conftest.py) rather than as
    patches on the wrapper's own functions, so the wrapper builds its real
    commands, runs them, and parses what a scheduler prints -- and the
    assertions below do not move when the wrapper's internals do.
    """
    if record is not None:
        cluster.plant_record(record)
    if exit_status is None:
        # A job PBS has already purged: qstat -x -f errors and says nothing.
        cluster.setenv("GENOAR_FAKE_QSTAT_DETAIL", "")
        cluster.setenv("GENOAR_FAKE_QSTAT_RC", 153)
    else:
        cluster.setenv("GENOAR_FAKE_QSTAT_DETAIL", qstat_detail(exit_status))
    cluster.setenv("GENOAR_FAKE_JOB_ID", "12345")
    if prior_runs:
        cluster.plant_earlier_runs(*prior_runs)
    return hpc.run_hpc_stage3(
        cluster.config("pbspro"), cluster.sra, cluster.bundle,
        str(cluster.results), execute=execute, poll_interval=0,
    )


class TestTheHpcWrapperReportsTheAnalysis:
    def test_a_complete_run_is_a_success(self, fake_cluster):
        result = _run_wrapper(fake_cluster, _record(2, 2), exit_status=0)
        assert result["success"] is True
        assert result["analysis_status"] == "success"
        assert hpc.exit_code_for(result, execute=True) == 0

    def test_a_partial_run_is_not_a_success(self, fake_cluster):
        result = _run_wrapper(fake_cluster, _record(2, 1), exit_status=4)
        assert result["success"] is False
        assert result["analysis_status"] == "partial"
        assert "1 of 2" in result["analysis_message"]
        assert hpc.exit_code_for(result, execute=True) == 4

    def test_a_failed_run_is_reported_as_failed(self, fake_cluster):
        result = _run_wrapper(fake_cluster, _record(2, 0), exit_status=1)
        assert result["success"] is False
        assert result["analysis_status"] == "failed"
        assert hpc.exit_code_for(result, execute=True) == 1

    def test_a_run_that_analysed_nothing_says_so(self, fake_cluster):
        result = _run_wrapper(fake_cluster, _record(2, 0), exit_status=3)
        assert result["analysis_status"] == "nothing_processed"
        assert hpc.exit_code_for(result, execute=True) == 3

    def test_an_unreadable_record_is_unknown(self, fake_cluster):
        # The likely cluster case: the results volume is remote and the record
        # does not come back. Absence of evidence is not success.
        result = _run_wrapper(fake_cluster, None, exit_status=None)
        assert result["success"] is False
        assert result["analysis_status"] == "unknown"
        assert "cannot say what the analysis achieved" in result["analysis_message"]
        assert hpc.exit_code_for(result, execute=True) == 1

    def test_the_unknown_message_says_it_is_not_a_failure_either(self, fake_cluster):
        result = _run_wrapper(fake_cluster, None, exit_status=None)
        assert "not a success" in result["analysis_message"]
        assert "may still have completed" in result["analysis_message"]

    def test_a_clean_exit_without_a_record_is_still_unknown(self, fake_cluster):
        # A steps 1-7 image also exits 0 after analysing nothing, so a zero exit
        # on its own is no evidence that Cell Ranger ran.
        result = _run_wrapper(fake_cluster, None, exit_status=0)
        assert result["analysis_status"] == "unknown"
        assert result["success"] is False

    def test_a_failure_exit_without_a_record_is_still_a_failure(self, fake_cluster):
        result = _run_wrapper(fake_cluster, None, exit_status=1)
        assert result["analysis_status"] == "failed"

    def test_the_record_and_the_exit_status_disagreeing_takes_the_weaker(self, fake_cluster):
        # The record claims every sample; the job says it exited 4. The run is
        # one run, so the weaker claim is the one reported.
        result = _run_wrapper(fake_cluster, _record(2, 2), exit_status=4)
        assert result["analysis_status"] == "partial"
        assert result["success"] is False
        assert "weaker" in result["analysis_message"]

    def test_a_record_from_a_steps_1_to_7_image_beats_the_zero_exit(self, fake_cluster):
        result = _run_wrapper(fake_cluster, _record(2, 0), exit_status=0)
        assert result["analysis_status"] == "nothing_processed"

    def test_adopted_output_is_not_a_completion(self, fake_cluster):
        result = _run_wrapper(fake_cluster, _record(2, 0, adopted=2), exit_status=4)
        assert result["success"] is False
        assert "unverified" in result["analysis_message"]

    def test_counts_that_disagree_with_themselves_are_a_failure(self, fake_cluster):
        result = _run_wrapper(fake_cluster, _record(1, 3), exit_status=0)
        assert result["analysis_status"] == "failed"

    def test_an_earlier_runs_record_is_not_read_as_this_ones(self, fake_cluster):
        # The results volume is persistent. A record that was there before the
        # job ran describes a different run. The one planted here says every
        # sample completed, so reading it would report a success.
        result = _run_wrapper(fake_cluster, None, exit_status=None,
                              prior_runs=("run-from-last-week",))
        assert result["analysis_status"] == "unknown"
        assert "no run record for this run" in result["run_record_problem"]

    def test_the_record_is_found_by_name_and_not_by_what_is_new(self, fake_cluster):
        # The bundle names the run and the job script carries the name, so the
        # wrapper asks for one file rather than working out which appeared.
        result = _run_wrapper(fake_cluster, _record(2, 2), exit_status=0,
                              prior_runs=("run-from-last-week",))
        assert result["run_id"] == handoff.bundle_run_id(result["bundle_dir"])
        assert result["analysis_status"] == "success"

    def test_a_bundle_that_names_no_run_falls_back_to_elimination(self, tmp_path):
        # The fallback for a bundle generated before run ids existed: the run
        # directory that was not there before the job ran is this run's.
        results = tmp_path / "results"
        (results / "runs" / "run-from-last-week").mkdir(parents=True)
        record, run_id, problem = hpc.read_run_record(
            results, {"run-from-last-week"}, run_id=None)
        assert record is None
        assert "no run record for this run" in problem

        fresh = results / "runs" / "run-this-one"
        fresh.mkdir()
        (fresh / "outcome.json").write_text(json.dumps(_record(2, 2)))
        record, run_id, problem = hpc.read_run_record(
            results, {"run-from-last-week"}, run_id=None)
        assert run_id == "run-this-one"
        assert problem is None
        assert hpc.grade_record(record)[0] == "success"


class TestSubmissionIsReportedOnItsOwn:
    def test_a_submitted_job_is_recorded_as_submitted(self, fake_cluster):
        result = _run_wrapper(fake_cluster, _record(2, 2), exit_status=0)
        assert result["submitted"] is True
        assert result["job_id"] == "12345.headnode"

    def test_submission_alone_is_not_success(self, fake_cluster):
        result = _run_wrapper(fake_cluster, None, exit_status=None)
        assert result["submitted"] is True
        assert result["success"] is False

    def test_a_job_that_never_finishes_is_unknown(self, fake_cluster):
        # qstat keeps reporting the job as queued and the wrapper stops waiting.
        fake_cluster.setenv("GENOAR_FAKE_QUEUE_RUNNING", 999)
        result = hpc.run_hpc_stage3(
            fake_cluster.config("pbspro"), fake_cluster.sra, fake_cluster.bundle,
            str(fake_cluster.results), execute=True, poll_interval=0, max_hours=0)
        assert result["submitted"] is True
        assert result["analysis_status"] == "unknown"
        assert result["success"] is False
        assert hpc.exit_code_for(result, execute=True) == 1

    def test_an_ssh_failure_never_reaches_submission(self, fake_cluster):
        fake_cluster.setenv("GENOAR_FAKE_SSH_RC", 255)
        result = hpc.run_hpc_stage3(
            fake_cluster.config("pbspro"), fake_cluster.sra, fake_cluster.bundle,
            str(fake_cluster.results), execute=True, poll_interval=0)
        assert result["submitted"] is False
        assert result["success"] is False
        assert result["analysis_status"] == "not_attempted"
        assert hpc.exit_code_for(result, execute=True) == 1

    def test_a_bundle_only_run_analysed_nothing_and_still_exits_zero(self, fake_cluster):
        result = hpc.run_hpc_stage3(
            fake_cluster.config("pbspro"), fake_cluster.sra, fake_cluster.bundle,
            str(fake_cluster.results), execute=False)
        assert result["success"] is False
        assert result["analysis_status"] == "not_attempted"
        assert result["submitted"] is False
        # Generating the bundle is the whole job when --execute was not asked for.
        assert hpc.exit_code_for(result, execute=False) == 0


class TestTheHandoffVerdictReachesTheCycle:
    def _cycle_status(self, exit_code, execute, message="fixture"):
        from cycle_test.run_cycle_test import _handoff_cycle_status

        return _handoff_cycle_status(
            {"exit_code": exit_code, "analysis_message": message,
             "analysis_status": "fixture", "error": None},
            execute)[0]

    @pytest.mark.parametrize("code,expected", [
        (0, "success"),
        (1, "failed_stage3"),
        (2, "failed_stage3"),
        (3, "no_data"),
        (4, "partial_success"),
    ])
    def test_an_executed_handoff_maps_its_exit_code(self, code, expected):
        assert self._cycle_status(code, execute=True) == expected

    def test_an_unknown_analysis_fails_the_cycle(self):
        # exit 1 covers both a failed run and a run the wrapper cannot describe.
        assert self._cycle_status(1, execute=True) == "failed_stage3"

    def test_a_bundle_only_handoff_did_what_was_asked(self):
        assert self._cycle_status(0, execute=False) == "success"

    def test_a_bundle_that_was_not_written_fails_the_cycle(self):
        assert self._cycle_status(1, execute=False) == "failed_stage3"
