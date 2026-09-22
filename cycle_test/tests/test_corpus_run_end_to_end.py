"""The seams between the bundle, the transfer, the task and the pipeline.

Every piece of the corpus path had tests and every one passed while the path
itself could not run a single sample. Two separate breaks, both at a join:
`transfer_and_submit.sh` never uploaded the batch lists the job script reads,
and the staging linked each sample's *directory*, which step 2's
`find -type d` does not match. Neither is visible from inside a piece. A test
that builds the remote tree by hand proves the staging block works on a tree
that never exists.

So these run the generated scripts. `ssh`, `scp` and `rsync` are replaced by
shims that act on a local directory standing in for the cluster, and the checks
are made with the pipeline's own commands rather than with restatements of
them.
"""

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HPC = REPO / "srr_pipeline_package" / "hpc"
PIPELINE = REPO / "srr_pipeline_package" / "pipeline_next"
sys.path.insert(0, str(HPC))

import schedulers as sch  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "prepare_hpc_handoff", HPC / "prepare_hpc_handoff.py")
handoff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handoff)


# --- the stand-in cluster ---------------------------------------------------

SSH_SHIM = """#!/usr/bin/env bash
# Runs the command locally. The remote base is a real local path in the test,
# so nothing needs rewriting; a submission is acknowledged and not run.
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    -p|-i) shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
host="${args[0]}"; unset 'args[0]'
cmd="${args[*]}"
case "$cmd" in
  *sbatch*|*qsub*) echo "SUBMITTED: $cmd"; exit 0 ;;
esac
bash -c "$cmd"
"""

COPY_SHIM = """#!/usr/bin/env bash
# scp/rsync, locally. Strips their flags, drops the `host:` prefix from the
# destination, and copies. Directory-trailing-slash semantics follow rsync's.
set -e
paths=()
delete=0
while [ $# -gt 0 ]; do
  case "$1" in
    -P|-p|-i|-e) shift 2 ;;
    --delete) delete=1; shift ;;
    -*) shift ;;
    *) paths+=("$1"); shift ;;
  esac
done
count=${#paths[@]}
dst="${paths[$((count - 1))]}"
dst="${dst#*:}"
[ "$delete" -eq 0 ] || rm -rf "$dst"
mkdir -p "$dst"
i=0
while [ $i -lt $((count - 1)) ]; do
  src="${paths[$i]}"
  src="${src#*:}"
  if [ -d "$src" ]; then
    cp -R "$src"/. "$dst"/
  else
    cp "$src" "$dst"/
  fi
  i=$((i + 1))
done
"""


def _shims(tmp_path: Path) -> Path:
    binary_dir = tmp_path / "fakebin"
    binary_dir.mkdir(exist_ok=True)
    for name, body in (("ssh", SSH_SHIM), ("scp", COPY_SHIM),
                       ("rsync", COPY_SHIM)):
        path = binary_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return binary_dir


def _cfg(remote_base, **over):
    base = dict(host="cluster.example", user="op", port=22,
                remote_base=str(remote_base),
                remote_sif=str(remote_base / "image.sif"),
                remote_ref=str(remote_base / "ref"),
                remote_cellranger=str(remote_base / "cellranger"),
                transfer_tool="scp", queue="normal", ncpus=16, mem_gb=120,
                walltime="24:00:00", cellranger_threads=16, cellranger_mem=100)
    base.update(over)
    return base


class Cluster:
    """A directory that stands in for the far side, and the bundle for it."""

    def __init__(self, tmp_path, samples, per_batch, layout="dir",
                 release_input=False, retain="analysis"):
        self.tmp = tmp_path
        self.remote_parent = tmp_path / "cluster" / "run"
        self.remote_parent.mkdir(parents=True)
        self.remote = self.remote_parent / "runs" / "run-A"
        self.results = self.remote_parent / "results"
        self.local_sra = tmp_path / "sra"
        self.local_sra.mkdir()
        for sample in samples:
            if layout == "dir":
                (self.local_sra / sample).mkdir()
                (self.local_sra / sample / (sample + ".sra")).write_bytes(b"SRA")
            else:
                (self.local_sra / (sample + ".sra")).write_bytes(b"SRA")
        self.plan = tmp_path / "plan"
        self.plan.mkdir()
        self.batches = [samples[i:i + per_batch]
                        for i in range(0, len(samples), per_batch)]
        for index, chunk in enumerate(self.batches, start=1):
            (self.plan / ("batch_%03d.txt" % index)).write_text(
                "\n".join(chunk) + "\n")
        # The bundle takes retain and release_input from here, and refuses a
        # plan that does not fit, so a plan directory without one is not a
        # plan directory.
        (self.plan / "plan.json").write_text(json.dumps({
            "kind": "genoar.batch.plan", "plan_id": "plan-testfixture",
            "samples": len(samples), "sample_names": list(samples),
            "settings": {"retain": retain,
                         "pipeline_mode": "corrected",
                         "release_input": release_input,
                         "max_concurrent_jobs": len(self.batches),
                         "disk_gb": 1000, "walltime": 1440},
            "plan": {"fits": True, "peak_gb": 1.0}}))
        self.cfg = _cfg(self.remote_parent, release_input=release_input)
        self.bundle = handoff.generate_bundle(
            self.cfg, self.local_sra, tmp_path / "bundle", run_id="run-A",
            batches_dir=self.plan)

    def transfer(self):
        """Run the bundle's own transfer script against the stand-in cluster."""
        env = dict(os.environ,
                   PATH="%s:%s" % (_shims(self.tmp), os.environ["PATH"]))
        return subprocess.run(["bash", str(self.bundle / "transfer_and_submit.sh")],
                              capture_output=True, text=True, env=env,
                              cwd=self.bundle)

    def stage(self, batch_index, attempt="1"):
        """Run the job script's staging block on the cluster, as the task does."""
        script = (self.bundle / sch.job_script_name(
            dict(self.cfg, array_count=len(self.batches)))).read_text()
        start = script.index("# --- this task's batch")
        end = script.index("# A compute node does not always mount")
        harness = "\n".join([
            "set -uo pipefail",
            'RUN_DIR="%s"' % self.remote,
            'SHARED_RESULTS="%s"' % self.results,
            'cd "$RUN_DIR"',
            "SLURM_ARRAY_TASK_ID=%d" % batch_index,
            "SLURM_ARRAY_JOB_ID=%s" % attempt,
            script[start:end],
            'echo "SRA_DIR=$SRA_DIR"',
            'echo "LOG_DIR=$LOG_DIR"',
            'echo "RUN_ID=$RUN_ID"',
        ])
        return subprocess.run(["bash", "-c", harness], capture_output=True,
                              text=True)

    def run_job(self, batch_index, attempt, pipeline_rc):
        """Run the complete generated job with only the container call stubbed."""
        remote_sif = Path(self.cfg["remote_sif"])
        remote_sif.parent.mkdir(parents=True, exist_ok=True)
        remote_sif.write_text("")
        Path(self.cfg["remote_ref"]).mkdir(parents=True, exist_ok=True)
        Path(self.cfg["remote_cellranger"]).mkdir(parents=True, exist_ok=True)
        runner = self.remote / "run_singularity_pipeline.sh"
        runner.write_text(
            '#!/usr/bin/env bash\nexit "${FAKE_PIPELINE_RC:-0}"\n')
        runner.chmod(runner.stat().st_mode | stat.S_IEXEC)
        job = self.remote / "run_stage3.slurm"
        env = dict(os.environ, SLURM_ARRAY_TASK_ID=str(batch_index),
                   SLURM_ARRAY_JOB_ID=str(attempt),
                   SLURM_JOB_ID=str(attempt),
                   FAKE_PIPELINE_RC=str(pipeline_rc))
        return subprocess.run(
            ["bash", str(job)], cwd=self.remote, env=env,
            capture_output=True, text=True)

    @staticmethod
    def discovered(sra_dir):
        """What step 2 would find, using step 2's own command."""
        source = (PIPELINE / "fastq_dump_parallel.sh").read_text()
        found = re.search(r"find \"\$BASE_DIR\"[^\n|]*", source)
        assert found, "step 2's sample discovery has moved"
        command = found.group(0).replace('"$BASE_DIR"', '"%s"' % sra_dir)
        done = subprocess.run(["bash", "-c", command + " -printf '%f\\n' 2>/dev/null"
                               " || " + command.replace("-print0", "-print")],
                              capture_output=True, text=True)
        return sorted(Path(line).name for line in done.stdout.split() if line)


@pytest.fixture
def cluster(tmp_path):
    return Cluster(tmp_path, ["SRR0000001", "SRR0000002", "SRR0000003"], 2)


class TestTheBundleArrivesComplete:

    def test_the_transfer_script_runs(self, cluster):
        done = cluster.transfer()
        assert done.returncode == 0, done.stderr

    def test_the_batch_lists_reach_the_cluster(self, cluster):
        """They did not, and every array task exited 41 looking for them."""
        cluster.transfer()
        arrived = sorted(p.name for p in (cluster.remote / "batches").iterdir())
        assert arrived == ["batch_001.txt", "batch_002.txt"]

    def test_the_run_files_reach_the_cluster(self, cluster):
        cluster.transfer()
        present = {p.name for p in cluster.remote.iterdir()}
        assert {"config.yaml", "run_stage3.slurm",
                "run_singularity_pipeline.sh"} <= present

    def test_the_inputs_reach_the_cluster(self, cluster):
        cluster.transfer()
        assert (cluster.remote / "data" / "sra" / "SRR0000001").exists()

    @pytest.mark.parametrize("transfer_tool", ["scp", "rsync"])
    def test_a_submitted_bundle_refuses_a_second_transfer_before_mutation(
            self, tmp_path, transfer_tool):
        cluster = Cluster(tmp_path, ["SRR0000001", "SRR0000002"], 2)
        cluster.cfg["transfer_tool"] = transfer_tool
        cluster.bundle = handoff.generate_bundle(
            cluster.cfg, cluster.local_sra, tmp_path / "bundle-again",
            run_id="run-A", batches_dir=cluster.plan)

        first = cluster.transfer()
        assert first.returncode == 0, first.stderr
        import shutil
        shutil.rmtree(cluster.local_sra / "SRR0000002")

        second = cluster.transfer()
        assert second.returncode == 2
        assert "no remote input was changed" in second.stderr
        assert "confirm that no job is queued or running" in second.stderr
        arrived = sorted(
            p.name for p in (cluster.remote / "data" / "sra").iterdir())
        assert arrived == ["SRR0000001", "SRR0000002"]

    def test_a_bundle_with_no_batches_uploads_none(self, tmp_path):
        sra = tmp_path / "sra" / "SRR1"
        sra.mkdir(parents=True)
        (sra / "SRR1.sra").write_bytes(b"x")
        cfg = _cfg(tmp_path / "remote")
        script = handoff.render_transfer(cfg, tmp_path / "sra", "x.sif")
        assert "batches/" not in script


class TestTheTaskStagesWhatThePipelineCanFind:

    def test_staging_succeeds_after_a_real_transfer(self, cluster):
        cluster.transfer()
        done = cluster.stage(1)
        assert done.returncode == 0, done.stderr + done.stdout

    def test_step_two_finds_every_staged_sample(self, cluster):
        """A linked directory staged a batch that then processed nothing.

        `find -type d` does not match a symlink, so the task exited 0 having
        analysed no samples, and no log said why.
        """
        cluster.transfer()
        done = cluster.stage(1)
        sra_dir = re.search(r"SRA_DIR=(\S+)", done.stdout).group(1)
        assert Cluster.discovered(sra_dir) == ["SRR0000001", "SRR0000002"]

    def test_the_run_id_is_unique_to_this_attempt(self, cluster):
        """A re-submitted task is a new run; the pipeline refuses a repeat id."""
        cluster.transfer()
        first = cluster.stage(1, attempt="4001")
        second = cluster.stage(1, attempt="4002")
        ids = [re.search(r"RUN_ID=(\S+)", d.stdout).group(1)
               for d in (first, second)]
        assert ids[0] != ids[1]
        assert all(i.startswith("run-A-b1-") for i in ids)

    def test_a_new_attempt_does_not_inherit_the_last_ones_staging(self, cluster):
        """A fixed staged path that nothing cleared mixed two plans together."""
        cluster.transfer()
        first = cluster.stage(1, attempt="4001")
        stale = Path(re.search(r"SRA_DIR=(\S+)", first.stdout).group(1))
        (stale / "SRR_FROM_LAST_TIME").mkdir()
        again = cluster.stage(1, attempt="4001")
        sra_dir = re.search(r"SRA_DIR=(\S+)", again.stdout).group(1)
        assert "SRR_FROM_LAST_TIME" not in Cluster.discovered(sra_dir)

    def test_the_staged_files_are_readable_through_the_link(self, cluster):
        cluster.transfer()
        done = cluster.stage(1)
        sra_dir = Path(re.search(r"SRA_DIR=(\S+)", done.stdout).group(1))
        assert (sra_dir / "SRR0000001" / "SRR0000001.sra").read_bytes() == b"SRA"

    def test_staging_costs_no_extra_bytes(self, cluster):
        """A batch is hundreds of GB; arranging it must not copy it."""
        cluster.transfer()
        done = cluster.stage(1)
        sra_dir = Path(re.search(r"SRA_DIR=(\S+)", done.stdout).group(1))
        staged = sra_dir / "SRR0000001" / "SRR0000001.sra"
        original = cluster.remote / "data" / "sra" / "SRR0000001" / "SRR0000001.sra"
        assert staged.is_symlink() or staged.stat().st_ino == original.stat().st_ino

    def test_a_bare_sra_layout_is_staged_too(self, tmp_path):
        """The other input shape the pipeline documents."""
        flat = Cluster(tmp_path, ["SRR0000001", "SRR0000002"], 2, layout="flat")
        flat.transfer()
        done = flat.stage(1)
        assert done.returncode == 0, done.stderr
        sra_dir = re.search(r"SRA_DIR=(\S+)", done.stdout).group(1)
        assert Cluster.discovered(sra_dir) == ["SRR0000001", "SRR0000002"]


class TestAShortBatchIsRefused:
    """A batch that runs short still reports N/N against its own manifest."""

    def test_a_missing_input_stops_the_task(self, cluster):
        cluster.transfer()
        import shutil
        shutil.rmtree(cluster.remote / "data" / "sra" / "SRR0000002")
        done = cluster.stage(1)
        assert done.returncode == 41
        assert "SRR0000002" in done.stderr

    def test_a_missing_batch_list_stops_the_task(self, cluster):
        cluster.transfer()
        (cluster.remote / "batches" / "batch_002.txt").unlink()
        done = cluster.stage(2)
        assert done.returncode == 41

    def test_a_failing_task_writes_its_own_log_directory(self, cluster):
        """Every task wrote the base one, so the last failure erased the rest."""
        cluster.transfer()
        (cluster.remote / "batches" / "batch_002.txt").unlink()
        done = cluster.stage(2)
        assert any(p.name.startswith("batch_2-")
                   for p in (cluster.remote / "logs").iterdir())


class TestTheTaskReleasesItsInputsWhenToldTo:
    """The block that runs unattended across seventy tasks, executed.

    It had no test at all, and it did not work: written with f-string escaping
    in a plain string, the generated script carried `${{BATCH_FILE:-}}` and
    `exit {EXIT_RETENTION}` literally. `bash -n` passed, the whole suite
    passed, and the task died at runtime with `bad substitution` having
    released nothing.
    """

    def _run_tail(self, cluster, batch_index, pipeline_rc="0",
                  retention_incomplete=False, fail_release_for=None,
                  fail_staged_cleanup=False):
        """Run the job script's post-pipeline block, as the task reaches it."""
        script = (cluster.bundle / sch.job_script_name(
            dict(cluster.cfg, array_count=len(cluster.batches)))).read_text()
        start = script.index("# --- this task's batch")
        stage_end = script.index("# A compute node does not always mount")
        tail_start = script.index('echo "[job] the pipeline exited $PIPELINE_RC"')
        tail_end = script.index("# The scheduler records this.")
        rm_failure_cases = ""
        if fail_release_for:
            rm_failure_cases += '''
    *"/data/sra/%s"*)
      echo "rm: injected input-release failure for %s" >&2
      return 1
      ;;''' % (fail_release_for, fail_release_for)
        if fail_staged_cleanup:
            rm_failure_cases += '''
    *"/data/staged/"*)
      echo "rm: injected staged-cleanup failure" >&2
      return 1
      ;;'''
        rm_override = ('''rm() {
  case " $* " in%s
  esac
  command rm "$@"
}''' % rm_failure_cases) if rm_failure_cases else ""

        harness = "\n".join([
            "set -uo pipefail",
            'RUN_DIR="%s"' % cluster.remote,
            'SHARED_RESULTS="%s"' % cluster.results,
            'cd "$RUN_DIR"',
            "SLURM_ARRAY_TASK_ID=%d" % batch_index,
            "SLURM_ARRAY_JOB_ID=1",
            script[start:stage_end],
            'PIPELINE_RC=%s' % pipeline_rc,
            'echo "RUN_ID=$RUN_ID"',
            rm_override,
            script[tail_start:tail_end],
            'echo "REACHED_END"',
        ])
        if retention_incomplete:
            mark = (cluster.results / ("batch_%d" % batch_index)
                    / "runs")
            mark.mkdir(parents=True, exist_ok=True)
        return subprocess.run(["bash", "-c", harness], capture_output=True,
                              text=True), script

    def test_the_block_is_rendered_not_left_as_a_template(self, tmp_path):
        cluster = Cluster(tmp_path, ["SRR0000001", "SRR0000002"], 1,
                          release_input=True)
        cluster.transfer()
        done, script = self._run_tail(cluster, 1)
        assert "{{" not in script and "{EXIT" not in script
        assert "bad substitution" not in done.stderr
        assert "numeric argument required" not in done.stderr
        assert done.returncode == 0, done.stderr

    def test_the_inputs_are_actually_removed(self, tmp_path):
        cluster = Cluster(tmp_path, ["SRR0000001", "SRR0000002"], 1,
                          release_input=True)
        cluster.transfer()
        done, _ = self._run_tail(cluster, 1)
        assert "released the inputs of 1 sample(s)" in done.stdout
        assert not (cluster.remote / "data" / "sra" / "SRR0000001").exists()
        assert (cluster.remote / "data" / "sra" / "SRR0000002").exists(), (
            "only this task's batch")

    def test_the_staged_copies_go_too(self, tmp_path):
        """A hard link left behind frees no blocks at all."""
        cluster = Cluster(tmp_path, ["SRR0000001"], 1, release_input=True)
        cluster.transfer()
        done, _ = self._run_tail(cluster, 1)
        staged = cluster.remote / "data" / "staged"
        assert not any(staged.iterdir()) if staged.is_dir() else True

    def test_a_successful_task_cleans_staging_while_preserving_originals(
            self, tmp_path):
        cluster = Cluster(tmp_path, ["SRR0000001"], 1,
                          release_input=False)
        cluster.transfer()

        done, _ = self._run_tail(cluster, 1)

        assert done.returncode == 0, done.stdout + done.stderr
        assert (cluster.remote / "data" / "sra" / "SRR0000001").exists()
        staged = cluster.remote / "data" / "staged"
        assert not any(staged.iterdir()) if staged.is_dir() else True

    def test_the_complete_generated_job_keeps_originals_but_cleans_staging(
            self, tmp_path):
        cluster = Cluster(tmp_path, ["SRR0000001"], 1,
                          release_input=False, retain="all")
        transferred = cluster.transfer()
        assert transferred.returncode == 0, transferred.stderr

        done = cluster.run_job(1, 4001, 0)

        assert done.returncode == 0, done.stdout + done.stderr
        assert (cluster.remote / "data" / "sra" / "SRR0000001").exists()
        staged = cluster.remote / "data" / "staged"
        assert not any(staged.iterdir()) if staged.is_dir() else True

    def test_a_staged_cleanup_failure_is_not_reported_as_success(self, tmp_path):
        cluster = Cluster(tmp_path, ["SRR0000001"], 1,
                          release_input=False)
        cluster.transfer()

        done, _ = self._run_tail(
            cluster, 1, fail_staged_cleanup=True)

        assert done.returncode == sch.EXIT_RETENTION
        assert "failed to release the staged inputs" in done.stderr
        assert (cluster.remote / "data" / "sra" / "SRR0000001").exists()

    def test_a_failed_pipeline_releases_nothing(self, tmp_path):
        cluster = Cluster(tmp_path, ["SRR0000001"], 1, release_input=True)
        cluster.transfer()
        done, _ = self._run_tail(cluster, 1, pipeline_rc="1")
        assert (cluster.remote / "data" / "sra" / "SRR0000001").exists()

    def test_a_failed_input_release_fails_the_task(self, tmp_path):
        """A full disk plan must not advance after input rotation failed."""
        cluster = Cluster(tmp_path, ["SRR0000001"], 1, release_input=True)
        cluster.transfer()
        done, _ = self._run_tail(
            cluster, 1, fail_release_for="SRR0000001")
        assert done.returncode == sch.EXIT_RETENTION, done.stdout + done.stderr
        assert "failed to release input" in done.stderr
        assert "SRR0000001" in done.stderr
        assert (cluster.remote / "data" / "sra" / "SRR0000001").exists()

    def test_an_incomplete_rotation_stops_the_task_before_it_deletes(self, tmp_path):
        cluster = Cluster(tmp_path, ["SRR0000001"], 1, release_input=True)
        cluster.transfer()
        first, _ = self._run_tail(cluster, 1)
        run_id = re.search(r"RUN_ID=(\S+)", first.stdout).group(1)
        (cluster.remote / "data" / "sra" / "SRR0000001").mkdir(
            parents=True, exist_ok=True)
        marker = (cluster.results / "batch_1" / "runs" / run_id)
        marker.mkdir(parents=True, exist_ok=True)
        (marker / "retention_incomplete").write_text("{}")
        done, _ = self._run_tail(cluster, 1)
        assert done.returncode == 43, done.stdout + done.stderr
        assert (cluster.remote / "data" / "sra" / "SRR0000001").exists()

    def test_a_bundle_that_keeps_its_inputs_still_checks_the_rotation(self, tmp_path):
        """The marker is about the disk, not about this task's inputs."""
        cluster = Cluster(tmp_path, ["SRR0000001"], 1, release_input=False)
        cluster.transfer()
        first, _ = self._run_tail(cluster, 1)
        run_id = re.search(r"RUN_ID=(\S+)", first.stdout).group(1)
        marker = (cluster.results / "batch_1" / "runs" / run_id)
        marker.mkdir(parents=True, exist_ok=True)
        (marker / "retention_incomplete").write_text("{}")
        done, _ = self._run_tail(cluster, 1)
        assert done.returncode == 43, done.stdout + done.stderr


class TestAFailedArrayStopsFollowingTasks:

    @pytest.mark.parametrize(
        "retain,release_input",
        [("analysis", True), ("all", False)],
    )
    def test_a_failed_task_stops_a_later_task_before_it_stages_inputs(
            self, tmp_path, retain, release_input):
        cluster = Cluster(
            tmp_path, ["SRR0000001", "SRR0000002"], 1,
            release_input=release_input, retain=retain)
        transferred = cluster.transfer()
        assert transferred.returncode == 0, transferred.stderr

        failed = cluster.run_job(1, 4001, 1)
        assert failed.returncode == 1, failed.stdout + failed.stderr
        marker = cluster.remote / ".genoar-array-stop" / "failure.txt"
        assert marker.is_file()
        first_failure = marker.read_text()
        assert "run-A-b1-4001r0" in first_failure

        stopped = cluster.run_job(2, 4002, 0)
        assert stopped.returncode == sch.EXIT_ARRAY_STOPPED
        assert "an earlier array task failed" in stopped.stderr
        assert not (cluster.remote / "data" / "staged"
                    / "run-A-b2-4002r0").exists()
        assert marker.read_text() == first_failure


class TestTheArrayHandoffNamesTheFilesThatActuallyExist:

    def test_the_handoff_explains_reconciliation_and_a_safe_retry(self, cluster):
        guide = (cluster.bundle / "HANDOFF.md").read_text()

        assert ("stage3_results/batch_<n>/runs/"
                "run-A-b<n>-<attempt>/outcome.json") in guide
        assert ("python3 corpus_report.py --plan ./batches "
                "--results 'stage3_results' \\") in guide
        assert '--run-id "$(cat run_id.txt)"' in guide
        assert "new scheduler job id" in guide
        assert ".genoar-array-stop" in guide
        assert ".genoar-transfer-claimed" in guide
        assert "confirm no job is queued or running" in guide


class TestTheRunReachesThePipelineItWasToldTo:
    """Traced from the command an operator runs, not from the file we wrote.

    The dispatcher that chooses between the two pipelines worked perfectly in
    isolation and nothing went through it: the standard image did not carry it,
    and the cluster runner called the frozen tree by its own path. Both were
    verified by running the dispatcher directly, which proves the dispatcher
    works and says nothing about whether anything uses it.

    So this starts where the operator starts.
    """

    def test_the_job_script_calls_the_runner(self, cluster):
        script = (cluster.bundle / sch.job_script_name(cluster.cfg)).read_text()
        assert "bash run_singularity_pipeline.sh" in script

    def test_the_runner_hands_the_container_the_dispatcher(self, cluster):
        """The container entry point, not `/pipeline/run_docker_pipeline.sh`."""
        runner = cluster.bundle / "run_singularity_pipeline.sh"
        assert runner.is_file(), "the bundle carries it"
        done = subprocess.run(
            ["bash", str(runner), "--dry-run", "--sif", "x.sif",
             "--sra", str(cluster.remote / "data" / "sra"),
             "--config", str(cluster.bundle / "config.yaml"),
             "--logs", str(cluster.remote / "logs"),
             "--results", str(cluster.results)],
            capture_output=True, text=True)
        assert "/pipeline_entry.sh" in done.stdout, done.stdout + done.stderr

    def test_the_image_the_standard_build_makes_carries_both_trees(self):
        """A stage the build does not build is a stage nobody has."""
        makefile = (REPO / "Makefile").read_text()
        built = re.search(r"--target (\w+) -t \$\(SRR_DOCKER_IMAGE\)",
                          makefile).group(1)
        dockerfile = (REPO / "srr_pipeline_package" / "docker"
                      / "Dockerfile").read_text()
        start = dockerfile.index("AS %s\n" % built)
        nxt = dockerfile.find("\nFROM ", start)
        stage = dockerfile[start:nxt if nxt != -1 else len(dockerfile)]
        assert "/pipeline_next/" in stage
        assert '/pipeline_entry.sh"]' in stage

    def test_the_generated_config_tells_it_which_one(self, cluster):
        import yaml as _yaml
        config = _yaml.safe_load((cluster.bundle / "config.yaml").read_text())
        assert isinstance(config["legacy_pipeline"], bool)

    def test_an_array_bundle_is_told_the_current_one(self, cluster):
        """Retention and the array are not in the earlier tree."""
        import yaml as _yaml
        config = _yaml.safe_load((cluster.bundle / "config.yaml").read_text())
        assert config["legacy_pipeline"] is False
