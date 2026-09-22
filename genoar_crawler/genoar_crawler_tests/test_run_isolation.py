"""Tests for run/worker isolation and for the run aggregator.

The defect these pin: a parallel run gave every worker the same output
directory and the same working directory, so one checkpoint.json, one
crawl_manifest.json and one download area served all of them. Each worker
deleted the manifest at startup and wrote its own slice at the end, so the
run's only completion evidence described one worker's pages and was read as the
whole requested range.

The shape of the fix is what is asserted here:
  * a worker's identity comes from the orchestrator, never from its pid
  * every private file lives under that worker's own directory
  * only the aggregator writes a manifest claiming a whole run, and it refuses
    to whenever the workers do not add up
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import genoar_crawler as gc  # noqa: E402


def worker_dir(out, run_id, worker_id):
    return Path(out) / "runs" / run_id / "workers" / f"worker-{worker_id}"


class TestWorkerScope:
    def test_an_assigned_worker_keeps_everything_under_its_own_directory(self, tmp_path):
        scope = gc.resolve_worker_scope(tmp_path, run_id="r1", worker_id="2")
        expected = worker_dir(tmp_path, "r1", "2")

        assert scope.assigned is True
        assert scope.worker_dir == expected
        assert scope.state_dir == expected
        assert scope.download_dir == expected / "downloads"
        assert scope.profile_dir == expected / "chrome-profile"
        assert scope.log_dir == expected
        assert scope.checkpoint_path() == expected / gc.CHECKPOINT_FILE
        assert scope.manifest_path() == expected / gc.COMPLETION_MANIFEST_FILE

    def test_two_workers_of_a_run_share_no_path(self, tmp_path):
        first = gc.resolve_worker_scope(tmp_path, run_id="r1", worker_id="1")
        second = gc.resolve_worker_scope(tmp_path, run_id="r1", worker_id="2")

        for field in ("state_dir", "download_dir", "profile_dir", "log_dir"):
            assert getattr(first, field) != getattr(second, field)

    def test_two_runs_do_not_share_a_worker_directory(self, tmp_path):
        """A new run is isolated by its run id, not by deleting the old one."""
        first = gc.resolve_worker_scope(tmp_path, run_id="r1", worker_id="1")
        second = gc.resolve_worker_scope(tmp_path, run_id="r2", worker_id="1")

        assert first.state_dir != second.state_dir

    def test_identity_comes_from_the_environment(self, tmp_path):
        scope = gc.resolve_worker_scope(
            tmp_path, env={"GENOAR_RUN_ID": "r9", "GENOAR_WORKER_ID": "3"}
        )
        assert scope.run_id == "r9"
        assert scope.worker_id == "3"
        assert scope.state_dir == worker_dir(tmp_path, "r9", "3")

    def test_an_unassigned_crawler_keeps_the_historic_paths(self, tmp_path):
        """`--resume` and every artifact written before this layout must keep
        working for a plain single crawl."""
        scope = gc.resolve_worker_scope(tmp_path, env={})

        assert scope.assigned is False
        assert scope.state_dir == tmp_path
        assert scope.checkpoint_path() == tmp_path / "checkpoint.json"
        assert scope.manifest_path() == tmp_path / "crawl_manifest.json"

    def test_an_unassigned_crawler_still_gets_private_scratch(self, tmp_path):
        scope = gc.resolve_worker_scope(tmp_path, env={})
        assert scope.download_dir.parent == tmp_path / gc.DOWNLOAD_SCRATCH_DIR
        assert scope.download_dir != tmp_path

    def test_the_unassigned_identity_survives_a_shared_pid(self, tmp_path, monkeypatch):
        """Every container's crawler is PID 1, so the pid cannot be the whole
        identity - that is precisely how four containers collided."""
        monkeypatch.setattr(gc.os, "getpid", lambda: 1)

        first = gc.resolve_worker_scope(tmp_path, env={})
        second = gc.resolve_worker_scope(tmp_path, env={})

        assert first.identity != second.identity
        assert first.download_dir != second.download_dir
        assert first.profile_dir != second.profile_dir

    def test_a_worker_without_a_run_is_refused(self, tmp_path):
        """It could never be aggregated, and inventing a run id would hide the
        miswiring until the merged manifest came out short."""
        with pytest.raises(ValueError, match="GENOAR_RUN_ID"):
            gc.resolve_worker_scope(tmp_path, env={"GENOAR_WORKER_ID": "1"})

    def test_a_run_without_a_worker_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="GENOAR_WORKER_ID"):
            gc.resolve_worker_scope(tmp_path, env={"GENOAR_RUN_ID": "r1"})

    @pytest.mark.parametrize("bad", ["../escape", "a/b", "", " ", "with space"])
    def test_an_identity_that_is_a_path_is_refused(self, tmp_path, bad):
        with pytest.raises(ValueError):
            gc.resolve_worker_scope(
                tmp_path, env={"GENOAR_RUN_ID": bad, "GENOAR_WORKER_ID": "1"}
            )


class TestAtomicWrite:
    def test_a_reader_never_sees_a_partial_file(self, tmp_path):
        target = tmp_path / "m.json"
        gc.atomic_write_json(target, {"a": 1})
        gc.atomic_write_json(target, {"a": 2})

        assert json.loads(target.read_text()) == {"a": 2}
        assert sorted(p.name for p in tmp_path.iterdir()) == ["m.json"]


def write_worker(out, run_id, worker_id, start, end, exit_code,
                 completed=None, processed=1, failed=0, status="completed",
                 corpus_pages=None, collected=None):
    """One finished worker on disk, the way the orchestrator leaves it.

    `corpus_pages` is what that worker read off GEO before it started - the
    page count it capped itself against. `collected` is what it wrote into
    SMTX/SRR/META. Both default to absent, which is what a manifest written by
    anything other than this crawler looks like.
    """
    gc.write_worker_assignment(out, run_id, worker_id, start, end)
    directory = worker_dir(out, run_id, worker_id)
    (directory / gc.WORKER_EXIT_CODE_FILE).write_text(f"{exit_code}\n")
    if completed is not None:
        manifest = {
            "status": status,
            "requested_pages": {"start": start, "end": end},
            "completed_pages": {"start": completed[0], "end": completed[1]},
            "resolved_end_page": completed[1],
            "items_per_page": 500,
            "processed": processed,
            "failed": failed,
            "source": "worker",
            "run_id": run_id,
            "worker_id": str(worker_id),
        }
        if corpus_pages is not None:
            manifest["corpus_pages"] = corpus_pages
        if collected is not None:
            manifest["collected_files"] = dict(collected)
        gc.atomic_write_json(directory / gc.COMPLETION_MANIFEST_FILE, manifest)
    return directory


class TestAggregateRun:
    def test_the_merged_manifest_covers_the_whole_requested_range(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 4)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2), processed=7)
        write_worker(tmp_path, "r1", 2, 3, 4, 0, completed=(3, 4), processed=5,
                     failed=2)

        manifest = gc.aggregate_run(tmp_path, "r1")

        assert manifest["status"] == "completed"
        assert manifest["requested_pages"] == {"start": 1, "end": 4}
        assert manifest["completed_pages"] == {"start": 1, "end": 4}
        assert manifest["resolved_end_page"] == 4
        assert manifest["processed"] == 12
        assert manifest["failed"] == 2
        assert manifest["source"] == "aggregated"
        assert [w["worker_id"] for w in manifest["workers"]] == ["1", "2"]

    def test_it_writes_both_the_run_copy_and_the_top_level_pointer(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 2)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))

        gc.aggregate_run(tmp_path, "r1")

        run_copy = tmp_path / "runs" / "r1" / gc.COMPLETION_MANIFEST_FILE
        pointer = tmp_path / gc.COMPLETION_MANIFEST_FILE
        assert json.loads(run_copy.read_text()) == json.loads(pointer.read_text())

    def test_an_earlier_run_keeps_its_own_manifest(self, tmp_path):
        """Isolation by run id, not by deleting what came before."""
        gc.write_run_plan(tmp_path, "old", 1, 1)
        write_worker(tmp_path, "old", 1, 1, 1, 0, completed=(1, 1))
        gc.aggregate_run(tmp_path, "old")

        gc.write_run_plan(tmp_path, "new", 1, 2)
        write_worker(tmp_path, "new", 1, 1, 2, 0, completed=(1, 2))
        gc.aggregate_run(tmp_path, "new")

        old = json.loads(
            (tmp_path / "runs" / "old" / gc.COMPLETION_MANIFEST_FILE).read_text()
        )
        assert old["completed_pages"] == {"start": 1, "end": 1}
        assert old["run_id"] == "old"

    def test_a_short_corpus_reads_as_a_cap_not_a_truncated_run(self, tmp_path):
        """Asked for 6 pages, GEO held 3: worker 2 capped, worker 3 had nothing.

        resolved_end_page is what lets the reader call this "capped" rather
        than "stopped early".
        """
        gc.write_run_plan(tmp_path, "r1", 1, 6, distributed_end=6,
                          available_pages=3)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))
        write_worker(tmp_path, "r1", 2, 3, 4, 0, completed=(3, 3))
        write_worker(tmp_path, "r1", 3, 5, 6, 3)

        manifest = gc.aggregate_run(tmp_path, "r1")

        assert manifest["requested_pages"] == {"start": 1, "end": 6}
        assert manifest["completed_pages"] == {"start": 1, "end": 3}
        assert manifest["resolved_end_page"] == 3

    def test_a_failed_worker_leaves_no_whole_range_manifest(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 4)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))
        write_worker(tmp_path, "r1", 2, 3, 4, 1)

        with pytest.raises(gc.RunAggregationError, match="exited 1"):
            gc.aggregate_run(tmp_path, "r1")

        assert not (tmp_path / gc.COMPLETION_MANIFEST_FILE).exists()
        assert not (tmp_path / "runs" / "r1" / gc.COMPLETION_MANIFEST_FILE).exists()

    def test_a_worker_that_never_finished_stops_the_run(self, tmp_path):
        """No exit code on disk means the orchestrator never harvested it."""
        gc.write_run_plan(tmp_path, "r1", 1, 4)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))
        gc.write_worker_assignment(tmp_path, "r1", "2", 3, 4)

        with pytest.raises(gc.RunAggregationError, match="never finished"):
            gc.aggregate_run(tmp_path, "r1")

    def test_a_worker_that_exited_0_without_a_manifest_stops_the_run(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 4)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))
        write_worker(tmp_path, "r1", 2, 3, 4, 0)

        with pytest.raises(gc.RunAggregationError, match="no completed manifest"):
            gc.aggregate_run(tmp_path, "r1")

    def test_a_hole_in_the_middle_stops_the_run(self, tmp_path):
        """A skipped slice is only ever legitimate past the end of the corpus."""
        gc.write_run_plan(tmp_path, "r1", 1, 6)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))
        write_worker(tmp_path, "r1", 2, 3, 4, 3)
        write_worker(tmp_path, "r1", 3, 5, 6, 0, completed=(5, 6))

        with pytest.raises(gc.RunAggregationError, match="crawled by nobody"):
            gc.aggregate_run(tmp_path, "r1")

    def test_a_worker_crawling_outside_its_slice_stops_the_run(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 4)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 4))
        write_worker(tmp_path, "r1", 2, 3, 4, 0, completed=(3, 4))

        with pytest.raises(gc.RunAggregationError, match="was assigned pages"):
            gc.aggregate_run(tmp_path, "r1")

    def test_a_worker_manifest_that_is_not_completed_stops_the_run(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 2)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2),
                     status="interrupted")

        with pytest.raises(gc.RunAggregationError):
            gc.aggregate_run(tmp_path, "r1")

    def test_every_worker_finding_nothing_is_not_a_failure(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 4)
        write_worker(tmp_path, "r1", 1, 1, 2, 3)
        write_worker(tmp_path, "r1", 2, 3, 4, 3)

        with pytest.raises(gc.RunHadNothingToCrawlError):
            gc.aggregate_run(tmp_path, "r1")

        assert not (tmp_path / gc.COMPLETION_MANIFEST_FILE).exists()

    def test_a_run_with_no_plan_cannot_be_aggregated(self, tmp_path):
        with pytest.raises(gc.RunAggregationError, match="run.json"):
            gc.aggregate_run(tmp_path, "never-started")


class TestAShortfallIsACapOnlyWhenSomethingProvesIt:
    """Coverage short of the requested end is either a cap or a truncated run.

    Assuming the first - taking the shortfall to be "because GEO holds fewer
    pages" and checking nothing - writes a run that stopped early, one worker
    covering pages 1-2 of the 1-4 it was given, out as a completed run of the
    whole request, with resolved_end_page 2 excusing the gap. A reader cannot
    tell that from a genuine cap, so a partial crawl produces whole-range
    completion evidence.

    The premise is checkable: the orchestrator probes GEO for the page count
    before it distributes (run.json's available_pages) and every worker records
    the count it capped itself against (corpus_pages). A shortfall is a cap
    only when the coverage reaches that count.
    """

    def test_a_run_that_stopped_short_of_what_geo_holds_is_refused(self, tmp_path):
        """The reproduction: pages 1-4 requested, GEO holds 4, covered 1-2."""
        gc.write_run_plan(tmp_path, "r1", 1, 4, distributed_end=4,
                          available_pages=4)
        write_worker(tmp_path, "r1", 1, 1, 4, 0, completed=(1, 2),
                     corpus_pages=4)

        with pytest.raises(gc.RunAggregationError, match="4 page"):
            gc.aggregate_run(tmp_path, "r1")

        assert not (tmp_path / gc.COMPLETION_MANIFEST_FILE).exists()
        assert not (tmp_path / "runs" / "r1" / gc.COMPLETION_MANIFEST_FILE).exists()

    def test_a_tail_worker_that_wrongly_found_nothing_is_refused(self, tmp_path):
        """The same hole one worker further on: pages 3-4 exist on GEO, yet the
        worker holding them came back with "nothing in range"."""
        gc.write_run_plan(tmp_path, "r1", 1, 4, distributed_end=4,
                          available_pages=4)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2),
                     corpus_pages=4)
        write_worker(tmp_path, "r1", 2, 3, 4, 3)

        with pytest.raises(gc.RunAggregationError):
            gc.aggregate_run(tmp_path, "r1")

        assert not (tmp_path / gc.COMPLETION_MANIFEST_FILE).exists()

    def test_a_cap_the_probe_proves_still_completes(self, tmp_path):
        """Asked for 6, GEO held 3: real work, whole corpus, capped range."""
        gc.write_run_plan(tmp_path, "r1", 1, 6, distributed_end=3,
                          available_pages=3)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))
        write_worker(tmp_path, "r1", 2, 3, 3, 0, completed=(3, 3))

        manifest = gc.aggregate_run(tmp_path, "r1")

        assert manifest["status"] == "completed"
        assert manifest["completed_pages"] == {"start": 1, "end": 3}
        assert manifest["resolved_end_page"] == 3
        assert manifest["coverage"] == "capped"
        assert manifest["corpus_pages"] == 3

    def test_a_cap_only_the_workers_saw_still_completes(self, tmp_path):
        """The probe is allowed to fail - the workers cap themselves anyway,
        and the count they capped against is evidence in its own right."""
        gc.write_run_plan(tmp_path, "r1", 1, 6, distributed_end=6,
                          available_pages=None)
        write_worker(tmp_path, "r1", 1, 1, 3, 0, completed=(1, 3),
                     corpus_pages=3)
        write_worker(tmp_path, "r1", 2, 4, 6, 3)

        manifest = gc.aggregate_run(tmp_path, "r1")

        assert manifest["completed_pages"] == {"start": 1, "end": 3}
        assert manifest["coverage"] == "capped"
        assert manifest["corpus_pages"] == 3

    def test_a_shortfall_nothing_can_vouch_for_is_refused(self, tmp_path):
        """Probe failed, manifests say nothing about the corpus. The run may
        have been capped, but nothing here proves it, and an unverifiable cap
        must not be recorded as a verified one."""
        gc.write_run_plan(tmp_path, "r1", 1, 6, distributed_end=6,
                          available_pages=None)
        write_worker(tmp_path, "r1", 1, 1, 3, 0, completed=(1, 3))
        write_worker(tmp_path, "r1", 2, 4, 6, 3)

        with pytest.raises(gc.RunAggregationError, match="cannot be told apart"):
            gc.aggregate_run(tmp_path, "r1")

        assert not (tmp_path / gc.COMPLETION_MANIFEST_FILE).exists()

    def test_a_run_that_covered_its_whole_request_needs_no_corpus_evidence(
        self, tmp_path
    ):
        """Nothing is short, so there is nothing to excuse."""
        gc.write_run_plan(tmp_path, "r1", 1, 4)
        write_worker(tmp_path, "r1", 1, 1, 4, 0, completed=(1, 4))

        manifest = gc.aggregate_run(tmp_path, "r1")

        assert manifest["coverage"] == "complete"
        assert manifest["completed_pages"] == {"start": 1, "end": 4}

    def test_the_corpus_may_shrink_between_the_probe_and_the_workers(self, tmp_path):
        """GEO's corpus changes daily. The workers ran after the probe, so a
        count they read that is smaller than the probe's is the newer truth."""
        gc.write_run_plan(tmp_path, "r1", 1, 6, distributed_end=5,
                          available_pages=5)
        write_worker(tmp_path, "r1", 1, 1, 3, 0, completed=(1, 3),
                     corpus_pages=3)
        write_worker(tmp_path, "r1", 2, 4, 5, 3)

        manifest = gc.aggregate_run(tmp_path, "r1")

        assert manifest["coverage"] == "capped"
        assert manifest["corpus_pages"] == 3

    def test_an_all_pages_run_must_still_reach_the_corpus_end(self, tmp_path):
        """`end 0` means "everything GEO has", so the count the workers read is
        the whole of the request."""
        gc.write_run_plan(tmp_path, "r1", 1, 0, distributed_end=0,
                          available_pages=4)
        write_worker(tmp_path, "r1", 1, 1, 4, 0, completed=(1, 2),
                     corpus_pages=4)

        with pytest.raises(gc.RunAggregationError):
            gc.aggregate_run(tmp_path, "r1")


class TestTheMergedManifestSaysWhatThisRunCollected:
    """A run's own output, kept apart from whatever else is in the directory.

    SMTX/SRR/META are shared across every run ever made into an output
    directory - that is deliberate, earlier results must survive. So the number
    of files in them says nothing about this run, and the only place that does
    is what each worker recorded writing.
    """

    def test_the_files_of_every_worker_are_summed(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 4)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2),
                     collected={"SMTX": 2, "SRR": 1, "META": 1})
        write_worker(tmp_path, "r1", 2, 3, 4, 0, completed=(3, 4),
                     collected={"SMTX": 1, "SRR": 0, "META": 0})

        manifest = gc.aggregate_run(tmp_path, "r1")

        assert manifest["collected_files"] == {
            "SMTX": 3, "SRR": 1, "META": 1, "total": 5
        }

    def test_a_run_that_wrote_nothing_says_so(self, tmp_path):
        """Even with an output directory full of earlier runs' results."""
        for name in ("SMTX", "SRR", "META"):
            (tmp_path / name).mkdir(parents=True)
        (tmp_path / "SMTX" / "GSE1_series_matrix.txt.gz").write_text("earlier run")

        gc.write_run_plan(tmp_path, "r1", 1, 2)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2),
                     collected={"SMTX": 0, "SRR": 0, "META": 0})

        manifest = gc.aggregate_run(tmp_path, "r1")

        assert manifest["collected_files"]["total"] == 0
        assert (tmp_path / "SMTX" / "GSE1_series_matrix.txt.gz").exists()

    def test_a_worker_that_recorded_nothing_counts_as_nothing(self, tmp_path):
        """A manifest without the record cannot prove files were collected, and
        "cannot prove" must not read as "did"."""
        gc.write_run_plan(tmp_path, "r1", 1, 2)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))

        manifest = gc.aggregate_run(tmp_path, "r1")

        assert manifest["collected_files"]["total"] == 0


class TestAggregationExitCodes:
    """The orchestrator scripts branch on these, so they are part of the
    contract: 0 the run completed, 3 it had nothing to crawl, 1 it did not
    complete, 2 the call itself was wrong."""

    def _aggregate(self, tmp_path, run_id):
        return gc._run_aggregation(Path(tmp_path), run_id)

    def test_a_completed_run_exits_zero(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 2)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))
        assert self._aggregate(tmp_path, "r1") == 0

    def test_nothing_in_range_exits_three(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 2)
        write_worker(tmp_path, "r1", 1, 1, 2, 3)
        assert self._aggregate(tmp_path, "r1") == 3

    def test_an_incomplete_run_exits_one(self, tmp_path):
        gc.write_run_plan(tmp_path, "r1", 1, 4)
        write_worker(tmp_path, "r1", 1, 1, 2, 0, completed=(1, 2))
        write_worker(tmp_path, "r1", 2, 3, 4, 137)
        assert self._aggregate(tmp_path, "r1") == 1

    def test_a_missing_run_id_exits_two(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GENOAR_RUN_ID", raising=False)
        assert self._aggregate(tmp_path, None) == 2


class TestWorkerWritesOnlyItsOwnFiles:
    """The half of the defect that no path computation can catch: what main()
    deletes at startup."""

    NO_BROWSER = "reached the browser"

    def _run_main(self, monkeypatch, argv, env):
        """Run main() up to the point it would start Chrome.

        Returns main()'s exit code, or NO_BROWSER when it got as far as
        building the crawler - by which point everything this class is about
        has already happened to the filesystem.
        """
        monkeypatch.setattr(sys, "argv", argv)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        class _StopHere(Exception):
            pass

        def refuse(**kwargs):
            raise _StopHere()

        monkeypatch.setattr(gc, "SimpleCrawler", refuse)
        try:
            gc.main()
        except SystemExit as exit_call:
            return exit_call.code
        except _StopHere:
            return self.NO_BROWSER
        raise AssertionError("main() returned without exiting")

    def test_a_worker_does_not_delete_the_runs_shared_manifest(
        self, tmp_path, monkeypatch
    ):
        """Every worker deleting it at startup, then writing its own slice, is
        how the run's completion evidence came to describe one worker."""
        shared = tmp_path / gc.COMPLETION_MANIFEST_FILE
        gc.atomic_write_json(shared, {"status": "completed", "run_id": "previous"})

        outcome = self._run_main(
            monkeypatch,
            ["genoar_crawler.py", "1", "2", "true", "-o", str(tmp_path)],
            {"GENOAR_RUN_ID": "r1", "GENOAR_WORKER_ID": "1"},
        )

        assert outcome == self.NO_BROWSER
        assert json.loads(shared.read_text())["run_id"] == "previous"

    def test_a_worker_does_delete_its_own_stale_manifest(self, tmp_path, monkeypatch):
        own = worker_dir(tmp_path, "r1", "1") / gc.COMPLETION_MANIFEST_FILE
        gc.atomic_write_json(own, {"status": "completed"})

        self._run_main(
            monkeypatch,
            ["genoar_crawler.py", "1", "2", "true", "-o", str(tmp_path)],
            {"GENOAR_RUN_ID": "r1", "GENOAR_WORKER_ID": "1"},
        )

        assert not own.exists()

    def test_an_unassigned_crawler_still_clears_its_stale_manifest(
        self, tmp_path, monkeypatch
    ):
        """The historic single-crawler behaviour, unchanged."""
        monkeypatch.delenv("GENOAR_RUN_ID", raising=False)
        monkeypatch.delenv("GENOAR_WORKER_ID", raising=False)
        stale = tmp_path / gc.COMPLETION_MANIFEST_FILE
        gc.atomic_write_json(stale, {"status": "completed"})

        self._run_main(
            monkeypatch,
            ["genoar_crawler.py", "1", "2", "true", "-o", str(tmp_path)],
            {},
        )

        assert not stale.exists()

    def test_a_worker_id_without_a_run_id_exits_two(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GENOAR_RUN_ID", raising=False)
        code = self._run_main(
            monkeypatch,
            ["genoar_crawler.py", "1", "2", "true", "-o", str(tmp_path)],
            {"GENOAR_WORKER_ID": "1"},
        )
        assert code == 2


class TestWorkerManifestLandsInItsOwnDirectory:
    def test_the_crawl_writes_its_manifest_under_the_worker_directory(self, tmp_path):
        scope = gc.resolve_worker_scope(tmp_path, run_id="r1", worker_id="2")
        scope.state_dir.mkdir(parents=True)

        gc.write_completion_manifest(
            scope.state_dir,
            requested_start=3, requested_end=4, completed_start=3,
            completed_end=4, items_per_page=500, processed=1, failed=0,
            run_id="r1", worker_id="2",
        )

        assert (scope.state_dir / gc.COMPLETION_MANIFEST_FILE).exists()
        assert not (tmp_path / gc.COMPLETION_MANIFEST_FILE).exists()

    def test_a_worker_manifest_says_it_is_only_a_worker(self, tmp_path):
        gc.write_completion_manifest(
            tmp_path, requested_start=1, requested_end=1, completed_start=1,
            completed_end=1, items_per_page=500, processed=0, failed=0,
            run_id="r1", worker_id="2",
        )
        data = json.loads((tmp_path / gc.COMPLETION_MANIFEST_FILE).read_text())
        assert data["source"] == "worker"
        assert data["run_id"] == "r1"
        assert data["worker_id"] == "2"
