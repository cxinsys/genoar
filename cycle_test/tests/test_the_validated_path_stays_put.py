"""The single-sample bundle is the only thing that has run on a real cluster.

One sample went through K-BDS on 2026-08-14 and came back complete. Everything
since — the version checks, retention, the array, the planner — is local work
against stubs. That is fine for the parts that are new and switched off, and it
is not fine for the parts the verified run went through.

So the shape of that run is pinned here. Not because the current code is
better or worse, but because a change to it should be a decision somebody made
rather than a side effect of building something else. Two of these caught
exactly that: the pipeline's output paths quietly became absolute while an
array feature was being added, and the retention step wrote over another
step's sentinel.

What is deliberately NOT pinned is anything reachable only by opting in. A
bundle built with `--batches`, a config that sets `retain`, a plan — those are
new surface and are tested where they are built.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
HPC = REPO / "srr_pipeline_package" / "hpc"
sys.path.insert(0, str(HPC))

import schedulers as sch  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "prepare_hpc_handoff", HPC / "prepare_hpc_handoff.py")
handoff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handoff)


# The pilot's own settings, with the site's host, account, paths and queue
# written as the placeholders the sibling HPC tests use. Only the rendered
# text is asserted on, so the values themselves carry no meaning.
PILOT = {
    "host": "kbds.example.kr", "user": "myid", "port": 22,
    "remote_base": "/scratch/myid/genoar_stage3",
    "remote_sif": "/scratch/myid/genoar-srr_step9.sif",
    "remote_ref": "/scratch/myid/ref",
    "remote_cellranger": "/scratch/myid/cellranger",
    "transfer_tool": "rsync", "scheduler": "slurm", "queue": "cpu",
    "ncpus": 32, "mem_gb": 120, "walltime": "24:00:00",
    "module_load": "singularity", "cellranger_threads": 16,
    "cellranger_mem": 100,
}


@pytest.fixture
def script():
    return sch.render_job_script(dict(PILOT), "pilot-1")


class TestWhatThePipelineIsHanded:
    """The four paths the job passes in, and where its output lands."""

    def _invocation(self, script):
        block = re.search(r"bash run_singularity_pipeline\.sh.*?\n\n", script,
                          re.S)
        assert block, "the pipeline invocation has moved"
        found = dict(re.findall(r"--(\w+)\s+\"?([^\s\"\\]+)", block.group(0)))
        return found

    def test_run_local_paths_and_the_shared_result_cache_are_unambiguous(
            self, script):
        """Inputs/logs stay with the submission; verified results are shared.

        Each generated bundle now owns a run-scoped submitting directory so a
        later upload cannot merge stale inputs into it. Results are the one
        deliberate exception: their receipts are the cross-run cache used by a
        retry, and the job names that shared path explicitly rather than
        depending on whichever directory the scheduler starts in.
        """
        found = self._invocation(script)
        assert found["sra"] in ("./data/sra", "$SRA_DIR")
        assert found["logs"] in ("./logs", "$PIPELINE_LOGS")
        assert found["results"] in ("./results", "$PIPELINE_RESULTS")
        for name, value in (("SRA_DIR", "./data/sra"),
                            ("PIPELINE_LOGS", "./logs")):
            assert '%s="%s"' % (name, value) in script
        assert 'SHARED_RESULTS="%s/results"' % PILOT["remote_base"] in script
        assert 'PIPELINE_RESULTS="$SHARED_RESULTS"' in script

    def test_the_three_staged_paths_come_from_the_config(self, script):
        found = self._invocation(script)
        assert found["sif"] == PILOT["remote_sif"]
        assert found["ref"] == PILOT["remote_ref"]
        assert found["cellranger"] == PILOT["remote_cellranger"]

    def test_the_job_ends_on_the_pipelines_own_exit_code(self, script):
        assert 'exit "$PIPELINE_RC"' in script


class TestTheDirectiveBlockIsThePilotsOwn:

    def test_it_is_what_the_cluster_accepted(self, script):
        assert script.split("\n\n")[0] == "\n".join([
            "#!/usr/bin/env bash",
            "#SBATCH --job-name=genoar_stage3",
            "#SBATCH --partition=cpu",
            "#SBATCH --nodes=1",
            "#SBATCH --ntasks=1",
            "#SBATCH --cpus-per-task=32",
            "#SBATCH --mem=122880M",
            "#SBATCH --time=24:00:00",
            "#SBATCH --output=/scratch/myid/genoar_stage3/logs/slurm-%j.out",
            "#SBATCH --error=/scratch/myid/genoar_stage3/logs/slurm-%j.err",
        ])

    def test_a_plain_bundle_carries_no_array_anything(self, script):
        for stranger in ("--array", "GENOAR_BATCH", "batches/", "stage_batch",
                         "GENOAR_ATTEMPT", "data/staged"):
            assert stranger not in script, stranger


class TestTheDefaultsDoWhatTheyDidBefore:

    def _config(self, **over):
        cfg = dict(PILOT)
        cfg.update(over)
        return yaml.safe_load(handoff.render_config_yaml(cfg, "pilot-1"))

    def test_the_paths_inside_the_container_are_unchanged(self):
        config = self._config()
        assert config["input_dir"] == "/work/data/sra"
        assert config["output_dir"] == "/work/results"
        assert config["ref_path"] == "/ref"
        assert config["cellranger_bin"] == "/opt/cellranger/cellranger"
        assert config["log_dir"] == "/work/logs"

    def test_it_does_not_render_keys_the_pipeline_never_reads(self):
        # A rendered key that decides nothing reads as a setting a site can
        # change. These three are hardcoded in the pipeline, so shipping them
        # in the handoff invites an edit that does nothing.
        config = self._config()
        for key in ("success_dir", "retry_move_dir", "keep_intermediate"):
            assert key not in config, key

    def test_retention_is_off_unless_a_site_asks_for_it(self):
        config = self._config()
        assert config["retain"] == "all"
        assert config["release_input"] is False

    def test_the_resource_numbers_come_straight_from_the_config(self):
        config = self._config()
        assert config["cellranger_threads"] == 16
        assert config["cellranger_mem"] == 100
        assert config["cores"] == 16

    def test_retrieval_still_brings_everything_by_default(self):
        assert 'MODE="all"' in handoff.render_retrieve(dict(PILOT), "res")


class TestNothingNewRunsWithoutBeingAskedFor:
    """The new surface is opt-in, and this is what "opt-in" has to mean."""

    def _pipeline(self):
        return (REPO / "srr_pipeline_package" / "pipeline_next"
                / "run_docker_pipeline.sh").read_text()

    def test_retention_is_skipped_entirely_at_the_defaults(self):
        source = self._pipeline()
        assert ('if [[ "$RETAIN" != "all" || "$RELEASE_INPUT" -eq 1 ]]; then'
                in source)

    def test_retention_writes_no_step_sentinel(self):
        """It shared step 9 with retry handling, and cleared its result."""
        source = self._pipeline()
        block = source[source.index("# Retention (not a numbered step"):]
        block = block[:block.index("\nload_provenance_outcome")]
        assert "mark_ok" not in block and "mark_warn" not in block

    def test_released_needs_a_release_note_that_no_ordinary_run_writes(self):
        """A run at the defaults deletes nothing, so nothing can be released."""
        source = self._pipeline()
        assert 'note = receipt.get(RELEASE_KEY)' in source
        assert 'if not isinstance(note, dict):\n        return None' in source
