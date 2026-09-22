"""Slurm support in the Stage 3 handoff, and the reporting contract under it.

The fake cluster lives in conftest.py. It puts stand-ins for `ssh`, `scp`,
`rsync`, `sbatch`, `squeue` and `sacct` on PATH and runs the wrapper end to end
against them, so the commands under test are the commands the wrapper builds.

This file owns the Slurm side. `test_stage3_outcome_claims.py` owns the same
reporting contract under PBS, and the two overlap on purpose: the contract has
to hold whichever scheduler the site runs, and the paths through the code differ
(`sbatch`/`squeue`/`sacct` against `qsub`/`qstat`).

What is only here: the dry submission, `squeue` polling, `sacct` and the figures
it reports, a job the out-of-memory killer takes, the run id reaching the
container, and the command-line exit codes.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cycle_test.tests.conftest import (
    HPC_DIR, SACCT_COMPLETED, SACCT_FAILED, SACCT_OOM, SACCT_PARTIAL_EXIT,
)

if str(HPC_DIR) not in sys.path:
    sys.path.insert(0, str(HPC_DIR))

import run_hpc_stage3 as hpc  # noqa: E402
import prepare_hpc_handoff as handoff  # noqa: E402


def record(expected=2, completed=2, adopted=0, **extra):
    """An outcome.json in the shape run_docker_pipeline.sh writes."""
    counts = {"expected": expected, "completed": completed, "adopted": adopted,
              "fresh": completed, "cache_hit": 0, "ineligible": 0, "missing": 0,
              "unverified": 0, "stale": 0}
    counts.update(extra)
    return {"run_id": "written-by-the-pipeline",
            "expected_samples": [f"SRR{i}" for i in range(expected)],
            "counts": counts, "samples": {}}


def run(cluster, cfg=None, execute=True, **kwargs):
    cfg = cfg or cluster.config()
    return hpc.run_hpc_stage3(
        cfg, cluster.sra, cluster.bundle, str(cluster.results),
        execute=execute, poll_interval=0, **kwargs)


# ---------------------------------------------------------------------------
# The Slurm submission sequence
# ---------------------------------------------------------------------------

class TestSlurmSubmission:
    def test_a_request_the_scheduler_refuses_never_reaches_the_queue(self, fake_cluster, capsys):
        fake_cluster.setenv("GENOAR_FAKE_TESTONLY_RC", 1)
        fake_cluster.plant_record(record())
        result = run(fake_cluster)
        assert result["submitted"] is False
        assert result["job_id"] is None
        assert result["success"] is False
        assert result["analysis_status"] == "not_attempted"
        assert "sbatch --test-only rejected the request" in result["error"]
        assert "Invalid partition name" in result["error"]
        assert hpc.exit_code_for(result, execute=True) == 1
        # The bundle is still there for a manual run.
        assert Path(result["bundle_dir"], "run_stage3.slurm").exists()
        assert "manual (semi-auto) fallback" in capsys.readouterr().out

    def test_a_dry_submission_runs_before_the_real_one(self, fake_cluster, capsys):
        fake_cluster.plant_record(record())
        run(fake_cluster)
        assert "sbatch --test-only" in capsys.readouterr().out

    def test_a_successful_submission_reports_its_job_id(self, fake_cluster):
        fake_cluster.plant_record(record())
        result = run(fake_cluster)
        assert result["submitted"] is True
        assert result["job_id"] == "4711"
        assert result["mode"] == "auto"

    def test_a_submission_that_prints_no_job_id_queued_nothing(self, fake_cluster):
        fake_cluster.setenv("GENOAR_FAKE_SUBMIT_OUT", "sbatch: nothing to say")
        result = run(fake_cluster)
        assert result["submitted"] is False
        assert "printed no job id" in result["error"]
        assert hpc.exit_code_for(result, execute=True) == 1

    def test_a_job_still_in_the_queue_is_waited_for(self, fake_cluster, capsys):
        fake_cluster.setenv("GENOAR_FAKE_QUEUE_RUNNING", 2)
        fake_cluster.plant_record(record())
        result = run(fake_cluster)
        out = capsys.readouterr().out
        assert "state=RUNNING" in out
        assert result["analysis_status"] == "success"

    def test_a_job_that_never_finishes_is_unknown(self, fake_cluster):
        fake_cluster.setenv("GENOAR_FAKE_QUEUE_RUNNING", 999)
        fake_cluster.plant_record(record())
        result = run(fake_cluster, max_hours=0)
        assert result["submitted"] is True
        assert result["success"] is False
        assert result["analysis_status"] == "unknown"
        assert hpc.exit_code_for(result, execute=True) == 1


class TestWhatTheSchedulerMeasured:
    def test_a_finished_job_reports_elapsed_time_and_peak_memory(self, fake_cluster, capsys):
        fake_cluster.plant_record(record())
        result = run(fake_cluster)
        assert result["elapsed"] == "02:14:31"
        assert result["max_rss"] == "98765432K"
        assert result["max_rss_kib"] == 98765432
        assert result["req_mem"] == "120000M"
        assert result["job_state"] == "COMPLETED"
        out = capsys.readouterr().out
        assert "elapsed=02:14:31" in out
        assert "peak memory=98765432K" in out

    def test_sacct_that_is_unavailable_costs_the_numbers_and_nothing_else(self, fake_cluster, capsys):
        fake_cluster.setenv("GENOAR_FAKE_SACCT_RC", 1)
        fake_cluster.plant_record(record())
        result = run(fake_cluster)
        assert result["elapsed"] is None
        assert result["max_rss"] is None
        # The record still speaks, so the analysis is still reported.
        assert result["analysis_status"] == "success"
        assert result["success"] is True
        out = capsys.readouterr().out
        assert "did not report elapsed time and peak memory" in out
        assert "sacct -j 4711 --format=JobID,State,ExitCode,Elapsed,MaxRSS,ReqMem" in out

    def test_sacct_that_answers_with_silence_says_so(self, fake_cluster, capsys):
        fake_cluster.setenv("GENOAR_FAKE_SACCT_OUT", "")
        fake_cluster.plant_record(record())
        result = run(fake_cluster)
        assert result["job_state"] is None
        assert result["job_exit"] is None
        assert result["analysis_status"] == "success"
        assert "did not report elapsed time and peak memory" in capsys.readouterr().out

    def test_the_pbs_path_reports_the_same_three(self, fake_cluster):
        fake_cluster.plant_record(record())
        result = run(fake_cluster, fake_cluster.config("pbspro"))
        assert result["job_id"] == "4711.headnode"
        assert result["elapsed"] == "02:14:31"
        assert result["max_rss"] == "94371840kb"
        assert result["analysis_status"] == "success"


class TestAJobTheSchedulerKilled:
    def test_the_out_of_memory_killer_is_a_failure(self, fake_cluster):
        # Slurm records OUT_OF_MEMORY with ExitCode 0:125. A reader who trusts
        # the exit code alone calls this clean.
        fake_cluster.setenv("GENOAR_FAKE_SACCT_OUT", SACCT_OOM)
        result = run(fake_cluster)
        assert result["job_exit"] == 0
        assert result["job_state"] == "OUT_OF_MEMORY"
        assert result["success"] is False
        assert result["analysis_status"] == "failed"
        assert "out-of-memory killer" in result["analysis_message"]
        assert hpc.exit_code_for(result, execute=True) == 1

    def test_a_killed_job_beats_a_record_that_claims_success(self, fake_cluster):
        fake_cluster.setenv("GENOAR_FAKE_SACCT_OUT", SACCT_OOM)
        fake_cluster.plant_record(record(expected=2, completed=2))
        result = run(fake_cluster)
        assert result["analysis_status"] == "failed"
        assert result["success"] is False
        assert "The weaker of the two is reported" in result["analysis_message"]

    def test_the_peak_memory_of_a_killed_job_is_still_reported(self, fake_cluster):
        fake_cluster.setenv("GENOAR_FAKE_SACCT_OUT", SACCT_OOM)
        result = run(fake_cluster)
        assert result["max_rss"] == "122879000K"
        assert result["req_mem"] == "120000M"


# ---------------------------------------------------------------------------
# The reporting contract, over Slurm
# ---------------------------------------------------------------------------

class TestTheReportingContractUnderSlurm:
    """success, submitted, analysis_status and the exit code, per record."""

    def test_a_record_saying_success(self, fake_cluster, capsys):
        fake_cluster.plant_record(record(expected=2, completed=2))
        result = run(fake_cluster)
        assert result["success"] is True
        assert result["submitted"] is True
        assert result["analysis_status"] == "success"
        assert hpc.exit_code_for(result, execute=True) == 0
        out = capsys.readouterr().out
        assert "analysis: success." in out
        assert "submitted: yes (job 4711)" in out

    def test_a_record_saying_partial(self, fake_cluster, capsys):
        fake_cluster.plant_record(record(expected=2, completed=1))
        result = run(fake_cluster)
        assert result["success"] is False
        assert result["submitted"] is True
        assert result["analysis_status"] == "partial"
        assert hpc.exit_code_for(result, execute=True) == 4
        assert "1 of 2 expected sample(s)" in capsys.readouterr().out

    def test_a_record_saying_failure(self, fake_cluster, capsys):
        fake_cluster.setenv("GENOAR_FAKE_SACCT_OUT", SACCT_FAILED)
        fake_cluster.plant_record(record(expected=2, completed=0))
        result = run(fake_cluster)
        assert result["success"] is False
        assert result["submitted"] is True
        assert result["analysis_status"] == "failed"
        assert hpc.exit_code_for(result, execute=True) == 1
        assert "analysis: failed." in capsys.readouterr().out

    def test_a_record_that_cannot_be_read(self, fake_cluster, capsys):
        fake_cluster.plant_record("this is not json")
        result = run(fake_cluster)
        assert result["success"] is False
        assert result["submitted"] is True
        assert result["analysis_status"] == "unknown"
        assert hpc.exit_code_for(result, execute=True) == 1
        out = capsys.readouterr().out
        assert "analysis: unknown." in out
        assert "is not reporting a success and is not reporting a failure" in out

    def test_no_record_at_all_is_unknown(self, fake_cluster):
        result = run(fake_cluster)
        assert result["analysis_status"] == "unknown"
        assert "no run record" in result["run_record_problem"]
        assert hpc.exit_code_for(result, execute=True) == 1

    def test_a_clean_exit_without_a_record_is_still_unknown(self, fake_cluster):
        # An image built with only steps 1 to 7 exits 0 having analysed nothing.
        result = run(fake_cluster)
        assert result["job_exit"] == 0
        assert result["success"] is False
        assert result["analysis_status"] == "unknown"

    def test_a_record_that_analysed_nothing(self, fake_cluster):
        fake_cluster.plant_record(record(expected=2, completed=0))
        fake_cluster.setenv("GENOAR_FAKE_SACCT_OUT", SACCT_COMPLETED)
        result = run(fake_cluster)
        assert result["analysis_status"] == "nothing_processed"
        assert hpc.exit_code_for(result, execute=True) == 3

    def test_the_exit_code_of_the_job_can_only_weaken_the_record(self, fake_cluster):
        fake_cluster.setenv("GENOAR_FAKE_SACCT_OUT", SACCT_PARTIAL_EXIT)
        fake_cluster.plant_record(record(expected=2, completed=2))
        result = run(fake_cluster)
        assert result["analysis_status"] == "partial"
        assert result["success"] is False
        assert hpc.exit_code_for(result, execute=True) == 4

    def test_submission_alone_is_never_a_success(self, fake_cluster):
        result = run(fake_cluster)
        assert result["submitted"] is True
        assert result["success"] is False

    def test_an_ssh_failure_never_reaches_submission(self, fake_cluster):
        fake_cluster.setenv("GENOAR_FAKE_SSH_RC", 255)
        result = run(fake_cluster)
        assert result["submitted"] is False
        assert result["success"] is False
        assert result["analysis_status"] == "not_attempted"
        assert hpc.exit_code_for(result, execute=True) == 1

    def test_a_bundle_only_run_analysed_nothing_and_still_exits_zero(self, fake_cluster, capsys):
        result = run(fake_cluster, execute=False)
        assert result["submitted"] is False
        assert result["success"] is False
        assert result["analysis_status"] == "not_attempted"
        assert hpc.exit_code_for(result, execute=False) == 0
        out = capsys.readouterr().out
        assert "bash " in out and "transfer_and_submit.sh" in out
        # The instructions name the configured scheduler's commands.
        assert "sbatch" in out
        assert "squeue -u myid" in out

    def test_the_bundle_only_instructions_name_pbs_commands_under_pbs(self, fake_cluster, capsys):
        run(fake_cluster, fake_cluster.config("pbspro"), execute=False)
        out = capsys.readouterr().out
        assert "qsub" in out
        assert "qstat -u myid" in out
        assert "sbatch" not in out


class TestTheRecordIsReadByName:
    """The wrapper reads the record the bundle named, never one by elimination."""

    def test_the_record_is_the_one_this_bundle_named(self, fake_cluster):
        fake_cluster.plant_record(record())
        result = run(fake_cluster)
        bundle_id = handoff.bundle_run_id(result["bundle_dir"])
        assert result["run_id"] == bundle_id
        assert result["run_record"].endswith(f"runs/{bundle_id}/outcome.json")

    def test_another_runs_record_is_not_read_as_this_ones(self, fake_cluster):
        # The results volume is persistent and holds earlier runs' records. One
        # of them says success. This run wrote none.
        fake_cluster.plant_earlier_runs("an-earlier-run")
        result = run(fake_cluster)
        assert result["analysis_status"] == "unknown"
        assert result["success"] is False

    def test_the_pipeline_is_told_the_id_through_the_environment(self, fake_cluster):
        result = run(fake_cluster, execute=False)
        script = Path(result["bundle_dir"], "run_stage3.slurm").read_text()
        run_id = handoff.bundle_run_id(result["bundle_dir"])
        assert f'RUN_ID="{run_id}"' in script
        assert 'export SINGULARITYENV_GENOAR_RUN_ID="$RUN_ID"' in script


class TestTheCommandLineContract:
    """The exit code a calling program reads."""

    def _cli(self, cluster, cfg, execute):
        import yaml
        cfg_path = cluster.tmp / "hpc_config.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg))
        json_path = cluster.tmp / "result.json"
        cmd = [sys.executable, str(HPC_DIR / "run_hpc_stage3.py"),
               "--sra-dir", str(cluster.sra),
               "--hpc-config", str(cfg_path),
               "--out", str(cluster.bundle),
               "--results-dir", str(cluster.results),
               "--json", str(json_path),
               "--poll-interval", "0"]
        if execute:
            cmd.append("--execute")
        proc = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
        payload = json.loads(json_path.read_text()) if json_path.exists() else {}
        return proc, payload

    def test_a_successful_analysis_exits_zero(self, fake_cluster):
        fake_cluster.plant_record(record())
        proc, payload = self._cli(fake_cluster, fake_cluster.config(), execute=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert payload["success"] is True
        assert payload["analysis_status"] == "success"
        assert payload["scheduler"] == "slurm"

    def test_a_partial_analysis_exits_four(self, fake_cluster):
        fake_cluster.plant_record(record(expected=2, completed=1))
        proc, payload = self._cli(fake_cluster, fake_cluster.config(), execute=True)
        assert proc.returncode == 4
        assert payload["analysis_status"] == "partial"

    def test_an_unreadable_record_exits_one(self, fake_cluster):
        proc, payload = self._cli(fake_cluster, fake_cluster.config(), execute=True)
        assert proc.returncode == 1
        assert payload["analysis_status"] == "unknown"

    def test_a_bundle_request_exits_zero(self, fake_cluster):
        proc, payload = self._cli(fake_cluster, fake_cluster.config(), execute=False)
        assert proc.returncode == 0
        assert payload["submitted"] is False
        assert Path(payload["bundle_dir"], "run_stage3.slurm").exists()
