"""What the Stage 3 handoff generates for each scheduler.

The cluster this handoff targets runs Slurm. The code that ran there is the
one-sample K-BDS pilot bundle, and the Slurm output of the generator reproduces
it. The parity test below pins that correspondence: it holds a copy of the
pilot's #SBATCH block and asserts the generator produces it. A change to either
side fails here instead of on a cluster months later.

The PBS block is pinned line by line as well. It is the block that shipped
before Slurm support, and nothing about adding a second scheduler is allowed to
move it.

No cluster is involved. Every test reads generated text.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HPC_DIR = Path(__file__).resolve().parents[2] / "srr_pipeline_package" / "hpc"
if str(HPC_DIR) not in sys.path:
    sys.path.insert(0, str(HPC_DIR))

import schedulers  # noqa: E402
import prepare_hpc_handoff as handoff  # noqa: E402


# The pilot's settings, as make_job.sh reads them out of preflight.env, written
# as an hpc_config. GENOAR_WORK_ROOT/GENOAR_RUN_ID is the pilot's run directory
# and remote_base is this generator's, so the two name the same place.
PILOT_RUN_DIR = "/scratch/myid/genoar/work/pilot-20260813T000000Z"

PILOT_CONFIG = {
    "host": "kbds.example.kr",
    "user": "myid",
    "remote_base": PILOT_RUN_DIR,
    "remote_sif": "/scratch/myid/genoar-srr_step9.sif",
    "remote_ref": "/scratch/myid/refdata-gex-GRCh38-2020-A",
    "remote_cellranger": "/scratch/myid/cellranger",
    "transfer_tool": "rsync",
    "scheduler": "slurm",
    "queue": "cpu",
    "ncpus": 16,
    "mem_mb": 120000,
    "mem_gb": 120,
    "walltime": "24:00:00",
    "module_load": "singularity",
    "cellranger_threads": 16,
    "cellranger_mem": 100,
}

# Copied from the pilot bundle's make_job.sh, with its variables resolved to the
# settings above. This is the block that was submitted to K-BDS.
PILOT_SBATCH_BLOCK = f"""#!/usr/bin/env bash
#SBATCH --job-name=genoar_stage3
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=120000M
#SBATCH --time=24:00:00
#SBATCH --output={PILOT_RUN_DIR}/logs/slurm-%j.out
#SBATCH --error={PILOT_RUN_DIR}/logs/slurm-%j.err"""

# The pilot appends these two, in this order, when the site requires them.
PILOT_SBATCH_ACCOUNT_QOS = """#SBATCH --account=my_project
#SBATCH --qos=normal"""


def pbs_config(dialect="pbspro", **over):
    cfg = dict(PILOT_CONFIG, scheduler=dialect, queue="normal")
    cfg.update(over)
    return cfg


def slurm_config(**over):
    cfg = dict(PILOT_CONFIG)
    cfg.update(over)
    return cfg


class TestTheGeneratorReproducesThePilotBlock:
    """The product's Slurm output is the bundle that ran on K-BDS."""

    def test_the_sbatch_block_is_the_pilots(self):
        assert schedulers.render_directives(PILOT_CONFIG) == PILOT_SBATCH_BLOCK

    def test_account_and_qos_keep_the_pilots_order(self):
        cfg = slurm_config(account="my_project", qos="normal")
        expected = PILOT_SBATCH_BLOCK + "\n" + PILOT_SBATCH_ACCOUNT_QOS
        assert schedulers.render_directives(cfg) == expected

    def test_mail_is_a_product_addition_and_comes_last(self):
        # The pilot was hand-delivered and watched, so it has no mail directive.
        # Adding one must not move the lines the pilot does have.
        cfg = slurm_config(email="me@lab.ac.kr")
        block = schedulers.render_directives(cfg)
        assert block.startswith(PILOT_SBATCH_BLOCK)
        assert block[len(PILOT_SBATCH_BLOCK):] == (
            "\n#SBATCH --mail-type=BEGIN,END,FAIL\n#SBATCH --mail-user=me@lab.ac.kr")

    def test_the_job_body_carries_the_pilots_decisions(self):
        script = schedulers.render_job_script(PILOT_CONFIG, "pilot-1")
        # The exit trap is armed before anything can fail.
        assert script.index("trap finish EXIT") < script.index("run_singularity_pipeline.sh")
        # `set -e` would kill the body before it could report.
        assert "set -uo pipefail" in script
        assert "set -euo pipefail" not in script
        # The run id reaches the container under both engines' prefixes.
        assert 'export SINGULARITYENV_GENOAR_RUN_ID="$RUN_ID"' in script
        assert 'export APPTAINERENV_GENOAR_RUN_ID="$RUN_ID"' in script
        # The job ends on the pipeline's own code, which is what the scheduler
        # records and the wrapper grades.
        assert script.rstrip().endswith('exit "$PIPELINE_RC"')

    def test_the_bodys_own_failures_stay_off_the_pipelines_exit_codes(self):
        # 4 means partial to the wrapper. A bind path the compute node cannot
        # see is not a run that analysed some of its samples.
        script = schedulers.render_job_script(PILOT_CONFIG, "pilot-1")
        assert "exit 41" in script
        assert "exit 42" in script
        for reserved in ("exit 2\n", "exit 3\n", "exit 4\n"):
            assert reserved not in script


class TestTheSlurmDirectivesAreConcrete:
    """Slurm reads #SBATCH before any shell runs."""

    def test_no_directive_holds_a_shell_variable(self):
        block = schedulers.render_directives(PILOT_CONFIG)
        for line in block.splitlines():
            if line.startswith("#SBATCH"):
                assert "$" not in line, line

    def test_a_working_directory_with_a_space_is_refused(self):
        cfg = slurm_config(remote_base="/scratch/my id/genoar")
        with pytest.raises(ValueError, match="whitespace"):
            schedulers.validate(cfg)

    def test_the_same_path_is_fine_under_pbs(self):
        # PBS writes its output relative to the working directory, so the
        # directive parser never sees the path.
        schedulers.validate(pbs_config(remote_base="/scratch/my id/genoar"))

    def test_memory_in_mb_is_taken_as_quoted(self):
        assert "#SBATCH --mem=120000M" in schedulers.render_directives(PILOT_CONFIG)

    def test_memory_falls_back_to_gb(self):
        cfg = slurm_config()
        del cfg["mem_mb"]
        assert "#SBATCH --mem=122880M" in schedulers.render_directives(cfg)


class TestThePbsBlockIsUnchanged:
    PBSPRO_BLOCK = """#!/usr/bin/env bash
#PBS -N genoar_cellranger
#PBS -q normal
#PBS -l select=1:ncpus=16:mem=120gb
#PBS -l walltime=24:00:00
# (no email notification configured)
#PBS -o pbs_stage3.out
#PBS -e pbs_stage3.err"""

    TORQUE_BLOCK = """#!/usr/bin/env bash
#PBS -N genoar_cellranger
#PBS -q normal
#PBS -l nodes=1:ppn=16
#PBS -l mem=120gb
#PBS -l walltime=24:00:00
# (no email notification configured)
#PBS -o pbs_stage3.out
#PBS -e pbs_stage3.err"""

    def test_pbspro_line_by_line(self):
        block = schedulers.render_directives(pbs_config("pbspro"))
        assert block.splitlines() == self.PBSPRO_BLOCK.splitlines()

    def test_torque_line_by_line(self):
        block = schedulers.render_directives(pbs_config("torque"))
        assert block.splitlines() == self.TORQUE_BLOCK.splitlines()

    def test_email_keeps_its_place(self):
        block = schedulers.render_directives(pbs_config("pbspro", email="me@lab.ac.kr"))
        assert block.splitlines()[5:7] == ["#PBS -m abe", "#PBS -M me@lab.ac.kr"]

    def test_each_pbs_dialect_emits_its_own_array_directive(self):
        pbspro = schedulers.render_directives(
            pbs_config("pbspro", array_count=4))
        torque = schedulers.render_directives(
            pbs_config("torque", array_count=4))

        assert "#PBS -J 1-4" in pbspro
        assert "#PBS -t 1-4" in torque
        assert "submit each batch separately" not in torque

    @pytest.mark.parametrize(
        "dialect,index_name,job_id",
        [
            ("pbspro", "PBS_ARRAY_INDEX", "1234[7].server"),
            ("torque", "PBS_ARRAYID", "1234-7.server"),
        ],
    )
    def test_pbs_array_ids_expand_to_pipeline_safe_run_ids(
            self, dialect, index_name, job_id):
        script = schedulers.render_job_script(
            pbs_config(dialect, array_count=9), "run-a")
        start = script.index('GENOAR_BATCH="')
        end = script.index("\n\n# Named for the run", start)
        assignments = script[start:end]
        env = {index_name: "7", "PBS_JOBID": job_id}

        done = subprocess.run(
            ["bash", "-c", assignments + '\nprintf "%s\\n" "$RUN_ID"\n'],
            env=env,
            capture_output=True,
            text=True,
        )

        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == "run-a-b7-1234"


class TestOneConfigProducesEitherScheduler:
    """The same settings, rendered for both, both valid."""

    @pytest.mark.parametrize("scheduler,script_name,directive", [
        ("slurm", "run_stage3.slurm", "#SBATCH"),
        ("pbspro", "run_stage3.pbs", "#PBS"),
        ("torque", "run_stage3.pbs", "#PBS"),
    ])
    def test_the_script_is_valid_shell(self, tmp_path, scheduler, script_name, directive):
        cfg = slurm_config(scheduler=scheduler, queue="normal")
        script = schedulers.render_job_script(cfg, "run-1")
        assert schedulers.job_script_name(cfg) == script_name
        assert directive in script
        path = tmp_path / script_name
        path.write_text(script)
        proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    def test_neither_carries_the_others_directives(self):
        slurm = schedulers.render_job_script(slurm_config(), "run-1")
        pbs = schedulers.render_job_script(pbs_config(), "run-1")
        assert "#PBS" not in slurm
        assert "#SBATCH" not in pbs

    def test_an_unknown_scheduler_is_refused(self):
        with pytest.raises(ValueError, match="scheduler must be one of"):
            schedulers.validate(slurm_config(scheduler="lsf"))


class TestTheDefaultScheduler:
    def test_a_config_without_a_scheduler_gets_slurm(self):
        cfg = slurm_config()
        del cfg["scheduler"]
        assert schedulers.scheduler_of(cfg) == "slurm"
        assert "#SBATCH" in schedulers.render_directives(cfg)

    def test_the_example_config_names_slurm(self):
        example = yaml.safe_load((HPC_DIR / "hpc_config.example.yaml").read_text())
        assert example["scheduler"] == "slurm"

    def test_naming_pbs_still_gets_pbs(self):
        assert schedulers.scheduler_of({"scheduler": "pbspro"}) == "pbspro"


class TestTheCommandsMatchTheScheduler:
    def test_slurm_tests_the_request_before_queueing_it(self):
        cmd = schedulers.test_only_command(slurm_config())
        assert cmd.endswith("sbatch --test-only run_stage3.slurm")

    def test_pbs_has_no_dry_submission(self):
        assert schedulers.test_only_command(pbs_config()) is None

    @pytest.mark.parametrize("cfg,expected", [
        (slurm_config(), "sbatch run_stage3.slurm"),
        (pbs_config(), "qsub run_stage3.pbs"),
    ])
    def test_the_submit_command(self, cfg, expected):
        assert schedulers.submit_command(cfg).endswith(expected)

    def test_slurm_polls_the_queue_and_reports_from_accounting(self):
        cfg = slurm_config()
        assert schedulers.poll_command(cfg, "123") == "squeue -h -j 123 -o %T"
        assert schedulers.report_command(cfg, "123") == (
            "sacct -j 123 --format=JobID,State,ExitCode,Elapsed,MaxRSS,ReqMem -n -P")

    def test_the_sacct_fields_are_the_pilots(self):
        # The pilot prints this command for the operator. The fields a person
        # reads by hand and the fields this code parses are the same fields.
        assert schedulers.SACCT_FORMAT == "JobID,State,ExitCode,Elapsed,MaxRSS,ReqMem"
        assert schedulers.human_report_command(slurm_config(), "123") == (
            "sacct -j 123 --format=JobID,State,ExitCode,Elapsed,MaxRSS,ReqMem")

    def test_pbs_polls_and_reports_as_it_always_did(self):
        cfg = pbs_config()
        assert schedulers.poll_command(cfg, "123.head") == "qstat 123.head"
        assert schedulers.report_command(cfg, "123.head") == "qstat -x -f 123.head"

    @pytest.mark.parametrize("cfg,out,expected", [
        (slurm_config(), "Submitted batch job 4711", "4711"),
        (slurm_config(), "sbatch: something\nSubmitted batch job 4711\n", "4711"),
        (slurm_config(), "sbatch: error", None),
        (pbs_config(), "4711.headnode", "4711.headnode"),
        (pbs_config(), "", None),
    ])
    def test_the_job_id_is_read_from_what_was_printed(self, cfg, out, expected):
        assert schedulers.parse_job_id(cfg, out) == expected


class TestReadingTheQueue:
    @pytest.mark.parametrize("rc,out,expected", [
        (0, "RUNNING\n", (False, "RUNNING")),
        (0, "PENDING\n", (False, "PENDING")),
        (0, "COMPLETED\n", (True, "COMPLETED")),
        (0, "", (True, "gone")),
        (1, "slurm_load_jobs error: Invalid job id specified", (True, "gone")),
    ])
    def test_squeue(self, rc, out, expected):
        assert schedulers.parse_poll(slurm_config(), rc, out, "123") == expected

    def test_qstat_is_read_as_before(self):
        listing = ("Job id  Name   User  Time Use S Queue\n"
                   "123.hn  genoar me    00:01     R normal\n")
        assert schedulers.parse_poll(pbs_config(), 0, listing, "123.hn") == (False, "R")
        assert schedulers.parse_poll(pbs_config(), 1, "", "123.hn") == (True, "gone")


class TestReadingWhatTheJobUsed:
    """Peak memory and elapsed time, which is what sizes the next request."""

    COMPLETED = (
        "4711|COMPLETED|0:0|02:14:31||120000M\n"
        "4711.batch|COMPLETED|0:0|02:14:31|98765432K|120000M\n"
    )

    def test_a_finished_job_reports_its_peak_memory_and_elapsed_time(self):
        report = schedulers.parse_report(slurm_config(), 0, self.COMPLETED, "4711")
        assert report["state"] == "COMPLETED"
        assert report["exit_status"] == 0
        assert report["elapsed"] == "02:14:31"
        assert report["max_rss"] == "98765432K"
        assert report["max_rss_kib"] == 98765432
        assert report["req_mem"] == "120000M"

    def test_the_peak_is_taken_across_the_steps(self):
        out = ("4711|COMPLETED|0:0|00:10:00||120000M\n"
               "4711.batch|COMPLETED|0:0|00:10:00|100M|120000M\n"
               "4711.0|COMPLETED|0:0|00:10:00|4G|120000M\n")
        report = schedulers.parse_report(slurm_config(), 0, out, "4711")
        assert report["max_rss"] == "4G"
        assert report["max_rss_kib"] == 4 * 1024 * 1024

    def test_a_failed_job_carries_its_exit_code(self):
        out = ("4711|FAILED|1:0|00:03:12||120000M\n"
               "4711.batch|FAILED|1:0|00:03:12|512M|120000M\n")
        report = schedulers.parse_report(slurm_config(), 0, out, "4711")
        assert report["state"] == "FAILED"
        assert report["exit_status"] == 1
        assert report["elapsed"] == "00:03:12"

    def test_the_out_of_memory_killer_leaves_a_clean_looking_exit_code(self):
        out = ("4711|OUT_OF_MEMORY|0:125|01:02:03||120000M\n"
               "4711.batch|OUT_OF_MEMORY|0:125|01:02:03|119998M|120000M\n")
        report = schedulers.parse_report(slurm_config(), 0, out, "4711")
        assert report["state"] == "OUT_OF_MEMORY"
        assert report["exit_status"] == 0
        assert report["signal"] == 125
        assert schedulers.state_is_failure(slurm_config(), "OUT_OF_MEMORY")

    def test_a_cancelled_job_loses_the_name_of_who_cancelled_it(self):
        out = "4711|CANCELLED by 1002|0:15|00:00:41||120000M\n"
        report = schedulers.parse_report(slurm_config(), 0, out, "4711")
        assert report["state"] == "CANCELLED"

    def test_sacct_that_is_not_installed_reports_nothing(self):
        report = schedulers.parse_report(slurm_config(), 127, "", "4711")
        assert report == schedulers.empty_report()

    def test_sacct_that_answers_with_silence_reports_nothing(self):
        report = schedulers.parse_report(slurm_config(), 0, "", "4711")
        assert report["state"] is None
        assert report["max_rss"] is None
        assert report["source"] is None

    def test_another_jobs_row_is_not_read_as_this_ones(self):
        out = "4712|COMPLETED|0:0|09:09:09|1G|120000M\n"
        report = schedulers.parse_report(slurm_config(), 0, out, "4711")
        assert report["elapsed"] is None

    def test_pbs_reports_the_same_three_under_its_own_names(self):
        out = ("Job Id: 4711.hn\n"
               "    job_state = F\n"
               "    Exit_status = 0\n"
               "    resources_used.mem = 94371840kb\n"
               "    resources_used.walltime = 02:14:31\n")
        report = schedulers.parse_report(pbs_config(), 0, out, "4711.hn")
        assert report["exit_status"] == 0
        assert report["max_rss"] == "94371840kb"
        assert report["max_rss_kib"] == 94371840
        assert report["elapsed"] == "02:14:31"

    def test_pbs_that_forgot_the_job_reports_nothing(self):
        assert schedulers.parse_report(pbs_config(), 153, "", "4711.hn")["exit_status"] is None

    def test_a_pbs_state_never_overrides_its_exit_code(self):
        # PBS states are single letters and say nothing about how a job ended,
        # so the exit status stays the only verdict on the PBS path.
        assert schedulers.state_is_failure(pbs_config(), "F") is False


class TestTheBundleNamesTheRightCommands:
    def _bundle(self, tmp_path, cfg, name):
        sra = tmp_path / "sra"
        sra.mkdir(exist_ok=True)
        (sra / "SRR1.sra").write_text("")
        return handoff.generate_bundle(cfg, sra, tmp_path / name, run_id="run-1")

    def test_the_slurm_bundle_submits_with_sbatch_and_watches_with_squeue(self, tmp_path):
        out = self._bundle(tmp_path, slurm_config(), "slurm")
        assert (out / "run_stage3.slurm").exists()
        assert not (out / "run_stage3.pbs").exists()
        transfer = (out / "transfer_and_submit.sh").read_text()
        assert "sbatch --test-only run_stage3.slurm" in transfer
        assert "sbatch run_stage3.slurm" in transfer
        assert "squeue -u myid" in transfer
        assert "qsub" not in transfer
        readme = (out / "HANDOFF.md").read_text()
        assert "sacct -j <job id> --format=JobID,State,ExitCode,Elapsed,MaxRSS,ReqMem" in readme
        assert "MaxRSS" in readme

    def test_the_pbs_bundle_submits_with_qsub_and_watches_with_qstat(self, tmp_path):
        out = self._bundle(tmp_path, pbs_config(), "pbs")
        assert (out / "run_stage3.pbs").exists()
        assert not (out / "run_stage3.slurm").exists()
        transfer = (out / "transfer_and_submit.sh").read_text()
        assert "qsub run_stage3.pbs" in transfer
        assert "qstat -u myid" in transfer
        assert "sbatch" not in transfer
        assert "qstat -x -f <job id>" in (out / "HANDOFF.md").read_text()

    def test_the_generated_shell_is_valid(self, tmp_path):
        out = self._bundle(tmp_path, slurm_config(), "slurm")
        for name in ("transfer_and_submit.sh", "retrieve_results.sh", "run_stage3.slurm"):
            proc = subprocess.run(["bash", "-n", str(out / name)],
                                  capture_output=True, text=True)
            assert proc.returncode == 0, f"{name}: {proc.stderr}"

    def test_a_config_that_omits_the_scheduler_gets_a_slurm_bundle(self, tmp_path):
        cfg = slurm_config()
        del cfg["scheduler"]
        out = self._bundle(tmp_path, cfg, "default")
        assert (out / "run_stage3.slurm").exists()


class TestTheBundleCarriesItsRunId:
    """The id the wrapper reads back, so it never identifies a record by elimination."""

    def _bundle(self, tmp_path, cfg, run_id=None):
        sra = tmp_path / "sra"
        sra.mkdir(exist_ok=True)
        (sra / "SRR1.sra").write_text("")
        return handoff.generate_bundle(cfg, sra, tmp_path / "bundle", run_id=run_id)

    def test_the_run_id_is_written_where_the_wrapper_reads_it(self, tmp_path):
        out = self._bundle(tmp_path, slurm_config(), run_id="run-abc")
        assert handoff.bundle_run_id(out) == "run-abc"

    def test_the_job_script_hands_it_to_the_container(self, tmp_path):
        out = self._bundle(tmp_path, slurm_config(), run_id="run-abc")
        script = (out / "run_stage3.slurm").read_text()
        assert 'RUN_ID="run-abc"' in script
        assert "SINGULARITYENV_GENOAR_RUN_ID" in script
        assert "APPTAINERENV_GENOAR_RUN_ID" in script

    def test_the_config_carries_it_too(self, tmp_path):
        # A container engine that drops the environment still names the run.
        out = self._bundle(tmp_path, slurm_config(), run_id="run-abc")
        assert yaml.safe_load((out / "config.yaml").read_text())["run_id"] == "run-abc"

    def test_a_generated_id_is_one_the_pipeline_accepts(self, tmp_path):
        import re
        out = self._bundle(tmp_path, slurm_config())
        run_id = handoff.bundle_run_id(out)
        # run_docker_pipeline.sh: a letter or digit, then letters, digits, '.',
        # '_' or '-', at most 64 characters.
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", run_id)

    def test_two_bundles_never_share_a_run_id(self, tmp_path):
        first = self._bundle(tmp_path, slurm_config())
        (tmp_path / "bundle2").mkdir()
        sra = tmp_path / "sra"
        second = handoff.generate_bundle(slurm_config(), sra, tmp_path / "bundle2")
        assert handoff.bundle_run_id(first) != handoff.bundle_run_id(second)

    def test_each_run_owns_a_distinct_remote_input_tree(self, tmp_path):
        sra = tmp_path / "sra"
        sra.mkdir()
        (sra / "SRR1.sra").write_text("")
        first = handoff.generate_bundle(
            slurm_config(), sra, tmp_path / "bundle-a", run_id="run-a")
        second = handoff.generate_bundle(
            slurm_config(), sra, tmp_path / "bundle-b", run_id="run-b")

        first_transfer = (first / "transfer_and_submit.sh").read_text()
        second_transfer = (second / "transfer_and_submit.sh").read_text()
        assert f"{PILOT_RUN_DIR}/runs/run-a/data/sra" in first_transfer
        assert f"{PILOT_RUN_DIR}/runs/run-b/data/sra" in second_transfer
        assert f"{PILOT_RUN_DIR}/data/sra" not in first_transfer
        assert f"{PILOT_RUN_DIR}/data/sra" not in second_transfer

        # Verified outputs are the intentional cross-run cache. Work trees are
        # isolated; results remain shared so a retry can be a cache hit.
        first_job = (first / "run_stage3.slurm").read_text()
        second_job = (second / "run_stage3.slurm").read_text()
        assert f'SHARED_RESULTS="{PILOT_RUN_DIR}/results"' in first_job
        assert f'SHARED_RESULTS="{PILOT_RUN_DIR}/results"' in second_job

    @pytest.mark.parametrize("run_id", ["../escape", "bad id", "LATEST"])
    def test_an_unsafe_remote_run_id_is_refused_before_writing(
            self, tmp_path, run_id):
        sra = tmp_path / "sra"
        sra.mkdir()
        out = tmp_path / "bundle"
        with pytest.raises(SystemExit, match="run id"):
            handoff.generate_bundle(
                slurm_config(), sra, out, run_id=run_id)
        assert not out.exists()

    def test_the_pbs_bundle_carries_it_as_well(self, tmp_path):
        out = self._bundle(tmp_path, pbs_config(), run_id="run-abc")
        assert 'RUN_ID="run-abc"' in (out / "run_stage3.pbs").read_text()
