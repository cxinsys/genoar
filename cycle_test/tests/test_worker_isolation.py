"""Two real worker processes in one output directory must not touch each other.

Give every worker of a run the same output directory and the same working
directory, as `run_parallel_crawl.sh` is in a position to do, and they share

  * checkpoint.json - rewritten from every worker, so `--resume` state is
    whichever worker wrote last
  * crawl_manifest.json - deleted by each worker at startup and rewritten with
    only its own slice, so the run's completion evidence describes one worker
  * the browser download area and the cleanup globs that sweep it, so a worker
    can delete a file another worker has just downloaded

None of that is visible to a test that only calls functions: it needs two
processes running at the same time against one directory. That is what this
does. The browser is the only thing standing in - the path resolution, the
checkpoint writer, the manifest writer, the download-cleanup glob and the
aggregator are all the shipped code.

The container-level version of this check (two real Docker containers, where
every crawler is PID 1) is run by hand; see the crawler's worker-scope tests
for the identity rules it depends on.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

GENOAR_ROOT = Path(__file__).resolve().parents[2]
CRAWLER_DIR = GENOAR_ROOT / "genoar_crawler"

sys.path.insert(0, str(CRAWLER_DIR))
import genoar_crawler as gc  # noqa: E402

from cycle_test.utils.stage1_runner import classify_stage1_outcome  # noqa: E402


# One worker, as a standalone program, so the two really do run at once.
WORKER_PROGRAM = textwrap.dedent('''
    import json, os, sys, time
    from pathlib import Path

    sys.path.insert(0, sys.argv[1])
    import genoar_crawler as gc

    output_dir = Path(sys.argv[2])
    start_page, end_page = int(sys.argv[3]), int(sys.argv[4])
    report_path = Path(sys.argv[5])
    barrier_dir = Path(sys.argv[6])

    scope = gc.resolve_worker_scope(output_dir)
    scope.state_dir.mkdir(parents=True, exist_ok=True)
    scope.download_dir.mkdir(parents=True, exist_ok=True)

    # What main() does at startup: drop this process's own stale manifest.
    stale = scope.manifest_path()
    if stale.exists():
        stale.unlink()

    checkpoint = gc.CheckpointManager(scope.state_dir)
    report = {
        "worker_id": scope.worker_id,
        "state_dir": str(scope.state_dir),
        "download_dir": str(scope.download_dir),
        "profile_dir": str(scope.profile_dir),
        "deleted_files_that_were_not_mine": [],
    }

    # Start together, so the two workers really overlap.
    (barrier_dir / f"ready-{scope.worker_id}").write_text("ready")
    while len(list(barrier_dir.glob("ready-*"))) < 2:
        time.sleep(0.02)

    for page in range(start_page, end_page + 1):
        mine = scope.download_dir / f"SRR_Acc_List_w{scope.worker_id}_p{page}.txt"
        mine.write_text(f"worker {scope.worker_id} page {page}")
        time.sleep(0.1)

        # download_sra_data() sweeps the download dir before every SRA download.
        for pattern in ["SRR_Acc_List*.txt", "SraRunTable*.txt", "SraRunTable*.csv"]:
            for found in sorted(scope.download_dir.glob(pattern)):
                if f"_w{scope.worker_id}_" not in found.name:
                    report["deleted_files_that_were_not_mine"].append(found.name)
                found.unlink()

        checkpoint.save(page=page, gse_index=0,
                        downloaded=[f"GSE_w{scope.worker_id}_p{page}"],
                        failed=[], total_pages=end_page)
        time.sleep(0.1)

    gc.write_completion_manifest(
        scope.state_dir,
        requested_start=start_page, requested_end=end_page,
        completed_start=start_page, completed_end=end_page,
        items_per_page=500, processed=end_page - start_page + 1, failed=0,
        run_id=scope.run_id, worker_id=scope.worker_id,
    )
    report_path.write_text(json.dumps(report))
''')


@pytest.fixture
def concurrent_run(tmp_path):
    """Two workers of one run, started together, both finished.

    Returns the output directory, the run id and each worker's own report.
    """
    output_dir = tmp_path / "crawl_output"
    (output_dir / "SMTX").mkdir(parents=True)
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    program = tmp_path / "worker.py"
    program.write_text(WORKER_PROGRAM)

    run_id = "run-under-test"
    gc.write_run_plan(output_dir, run_id, 1, 4)

    slices = {"1": (1, 2), "2": (3, 4)}
    processes = []
    for worker_id, (start, end) in slices.items():
        gc.write_worker_assignment(output_dir, run_id, worker_id, start, end)
        processes.append((worker_id, subprocess.Popen(
            [sys.executable, str(program), str(CRAWLER_DIR), str(output_dir),
             str(start), str(end), str(tmp_path / f"report-{worker_id}.json"),
             str(barrier)],
            env={"PATH": "/usr/bin:/bin", "GENOAR_RUN_ID": run_id,
                 "GENOAR_WORKER_ID": worker_id,
                 "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )))

    for worker_id, process in processes:
        out, _ = process.communicate(timeout=120)
        assert process.returncode == 0, f"worker {worker_id} failed:\n{out}"
        directory = output_dir / "runs" / run_id / "workers" / f"worker-{worker_id}"
        (directory / gc.WORKER_EXIT_CODE_FILE).write_text("0")

    reports = {
        worker_id: json.loads((tmp_path / f"report-{worker_id}.json").read_text())
        for worker_id in slices
    }
    return {"output_dir": output_dir, "run_id": run_id, "reports": reports}


class TestTwoWorkersInOneDirectory:
    def test_each_worker_wrote_its_own_checkpoint(self, concurrent_run):
        output_dir = concurrent_run["output_dir"]
        checkpoints = sorted(output_dir.rglob(gc.CHECKPOINT_FILE))

        assert len(checkpoints) == 2
        progress = {
            json.loads(path.read_text())["last_page"] for path in checkpoints
        }
        assert progress == {2, 4}

    def test_no_checkpoint_landed_in_the_shared_directory(self, concurrent_run):
        """One shared checkpoint.json is a run whose `--resume` replays or
        skips the wrong pages."""
        assert not (concurrent_run["output_dir"] / gc.CHECKPOINT_FILE).exists()

    def test_neither_worker_touched_the_others_downloads(self, concurrent_run):
        for report in concurrent_run["reports"].values():
            assert report["deleted_files_that_were_not_mine"] == []

    def test_the_workers_share_no_directory_at_all(self, concurrent_run):
        first, second = concurrent_run["reports"].values()
        for key in ("state_dir", "download_dir", "profile_dir"):
            assert first[key] != second[key]

    def test_neither_worker_wrote_a_manifest_to_the_shared_directory(
        self, concurrent_run
    ):
        """A worker's manifest proves one slice. Written where the run's
        manifest belongs, it was read as the whole range."""
        shared = concurrent_run["output_dir"] / gc.COMPLETION_MANIFEST_FILE
        assert not shared.exists()

    def test_each_worker_manifest_covers_only_its_own_slice(self, concurrent_run):
        output_dir = concurrent_run["output_dir"]
        manifests = sorted(output_dir.rglob(gc.COMPLETION_MANIFEST_FILE))

        covered = sorted(
            (json.loads(path.read_text())["completed_pages"]["start"],
             json.loads(path.read_text())["completed_pages"]["end"])
            for path in manifests
        )
        assert covered == [(1, 2), (3, 4)]
        assert all(
            json.loads(path.read_text())["source"] == "worker"
            for path in manifests
        )


class TestOnlyTheAggregatorClaimsTheWholeRange:
    def test_the_merged_manifest_asserts_the_requested_range(self, concurrent_run):
        manifest = gc.aggregate_run(
            concurrent_run["output_dir"], concurrent_run["run_id"]
        )

        assert manifest["completed_pages"] == {"start": 1, "end": 4}
        assert manifest["requested_pages"] == {"start": 1, "end": 4}
        assert manifest["source"] == "aggregated"
        assert manifest["processed"] == 4

    def test_the_merged_manifest_names_every_worker(self, concurrent_run):
        manifest = gc.aggregate_run(
            concurrent_run["output_dir"], concurrent_run["run_id"]
        )

        assert [(w["worker_id"], w["completed_pages"]) for w in manifest["workers"]] == [
            ("1", {"start": 1, "end": 2}),
            ("2", {"start": 3, "end": 4}),
        ]

    def test_a_worker_that_died_leaves_the_run_without_a_manifest(
        self, concurrent_run
    ):
        """Half a run must never produce whole-range completion evidence."""
        output_dir = concurrent_run["output_dir"]
        worker_two = (output_dir / "runs" / concurrent_run["run_id"] /
                      "workers" / "worker-2")
        (worker_two / gc.WORKER_EXIT_CODE_FILE).write_text("137")

        with pytest.raises(gc.RunAggregationError):
            gc.aggregate_run(output_dir, concurrent_run["run_id"])

        assert not (output_dir / gc.COMPLETION_MANIFEST_FILE).exists()


class TestStage1RunnerReadsTheMergedManifest:
    """The manifest schema gained fields; it lost none, so the reader in
    stage1_runner judges an aggregated manifest exactly as it judges a single
    crawler's."""

    def test_a_complete_run_classifies_as_success(self, concurrent_run):
        gc.aggregate_run(concurrent_run["output_dir"], concurrent_run["run_id"])

        status, error = classify_stage1_outcome(
            0, concurrent_run["output_dir"], start_page=1, end_page=4
        )
        assert (status, error) == ("success", None)

    def test_a_capped_run_classifies_as_capped(self, tmp_path):
        """Requested 6 pages, GEO held 3. Real work happened; the range moved."""
        output_dir = tmp_path / "out"
        gc.write_run_plan(output_dir, "r", 1, 6, distributed_end=6,
                          available_pages=3)
        for worker_id, (start, end, completed) in {
            "1": (1, 3, (1, 3)), "2": (4, 6, None)
        }.items():
            gc.write_worker_assignment(output_dir, "r", worker_id, start, end)
            directory = output_dir / "runs" / "r" / "workers" / f"worker-{worker_id}"
            if completed is None:
                (directory / gc.WORKER_EXIT_CODE_FILE).write_text("3")
                continue
            (directory / gc.WORKER_EXIT_CODE_FILE).write_text("0")
            gc.write_completion_manifest(
                directory, requested_start=start, requested_end=end,
                completed_start=completed[0], completed_end=completed[1],
                items_per_page=500, processed=2, failed=0,
                run_id="r", worker_id=worker_id,
            )

        gc.aggregate_run(output_dir, "r")

        status, error = classify_stage1_outcome(0, output_dir, start_page=1,
                                                end_page=6)
        assert status == "capped", error

    def test_a_manifest_for_another_range_is_not_this_requests_proof(
        self, concurrent_run
    ):
        gc.aggregate_run(concurrent_run["output_dir"], concurrent_run["run_id"])

        status, error = classify_stage1_outcome(
            0, concurrent_run["output_dir"], start_page=1, end_page=9
        )
        assert status == "failed"
        assert "1-4" in error
