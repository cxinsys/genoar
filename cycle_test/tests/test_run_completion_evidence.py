"""What a parallel run's evidence is allowed to say, end to end.

Stage 1's reader (`classify_stage1_outcome`) tells a capped crawl from a
truncated one by comparing the manifest's `completed_pages` with its
`resolved_end_page`. That comparison is sound for a single crawler, which
writes both from the same finished page loop. For a parallel run the aggregator
wrote both from whatever the workers happened to cover, so every shortfall
satisfied it: a run that covered pages 1-2 of the 1-4 it planned produced a
"completed" manifest that the reader classified as "capped", which is a
success. Half a run therefore became whole-range completion evidence.

These drive the real aggregator and the real reader together, because the
defect lived in neither alone - it lived in the aggregator promising something
the reader was entitled to believe.
"""

import json
import sys
from pathlib import Path

import pytest

GENOAR_ROOT = Path(__file__).resolve().parents[2]
CRAWLER_DIR = GENOAR_ROOT / "genoar_crawler"

sys.path.insert(0, str(CRAWLER_DIR))
import genoar_crawler as gc  # noqa: E402

from cycle_test.utils.stage1_runner import classify_stage1_outcome  # noqa: E402


def finished_worker(output_dir, run_id, worker_id, start, end, exit_code,
                    completed=None, corpus_pages=None, collected=None):
    """One worker of a finished run, as the orchestrator leaves it on disk."""
    gc.write_worker_assignment(output_dir, run_id, worker_id, start, end)
    directory = (Path(output_dir) / "runs" / run_id / "workers"
                 / f"worker-{worker_id}")
    (directory / gc.WORKER_EXIT_CODE_FILE).write_text(f"{exit_code}\n")
    if completed is not None:
        gc.write_completion_manifest(
            directory, requested_start=start, requested_end=end,
            completed_start=completed[0], completed_end=completed[1],
            items_per_page=500, processed=1, failed=0,
            run_id=run_id, worker_id=str(worker_id),
            corpus_pages=corpus_pages,
            collected_files=collected or {"SMTX": 1, "SRR": 0, "META": 0},
        )
    return directory


class TestATruncatedRunLeavesNoEvidenceToMisread:
    def test_the_aggregator_refuses_the_run(self, tmp_path):
        """Pages 1-4 planned, GEO holds 4, one worker covered 1-2."""
        gc.write_run_plan(tmp_path, "r", 1, 4, distributed_end=4,
                          available_pages=4)
        finished_worker(tmp_path, "r", "1", 1, 4, 0, completed=(1, 2),
                        corpus_pages=4)

        with pytest.raises(gc.RunAggregationError):
            gc.aggregate_run(tmp_path, "r")

    def test_stage_one_cannot_call_it_capped(self, tmp_path):
        """With no manifest there is nothing left to misread: the run reads as
        failed, which is what it was."""
        gc.write_run_plan(tmp_path, "r", 1, 4, distributed_end=4,
                          available_pages=4)
        finished_worker(tmp_path, "r", "1", 1, 4, 0, completed=(1, 2),
                        corpus_pages=4)
        with pytest.raises(gc.RunAggregationError):
            gc.aggregate_run(tmp_path, "r")

        status, error = classify_stage1_outcome(0, tmp_path, start_page=1,
                                                end_page=4)

        assert status == "failed"
        assert "no completion manifest" in error

    def test_an_earlier_runs_manifest_is_not_borrowed(self, tmp_path):
        """The earlier run's own evidence survives under its run id, but the
        truncated run may not inherit the pointer that says "this completed"."""
        gc.write_run_plan(tmp_path, "earlier", 1, 2, distributed_end=2,
                          available_pages=2)
        finished_worker(tmp_path, "earlier", "1", 1, 2, 0, completed=(1, 2),
                        corpus_pages=2)
        gc.aggregate_run(tmp_path, "earlier")
        # What a launcher does before a new run: the pointer is cleared, each
        # run's own manifest stays.
        (tmp_path / gc.COMPLETION_MANIFEST_FILE).unlink()

        gc.write_run_plan(tmp_path, "later", 1, 4, distributed_end=4,
                          available_pages=4)
        finished_worker(tmp_path, "later", "1", 1, 4, 0, completed=(1, 2),
                        corpus_pages=4)
        with pytest.raises(gc.RunAggregationError):
            gc.aggregate_run(tmp_path, "later")

        assert not (tmp_path / gc.COMPLETION_MANIFEST_FILE).exists()
        earlier = json.loads(
            (tmp_path / "runs" / "earlier" / gc.COMPLETION_MANIFEST_FILE).read_text()
        )
        assert earlier["completed_pages"] == {"start": 1, "end": 2}


class TestAGenuineCapIsStillASuccess:
    """The case that must not be broken by refusing the truncated one: GEO
    really does hold fewer pages than were asked for, the run crawled every one
    of them, and that is all the work that exists."""

    def aggregate_capped_run(self, output_dir):
        gc.write_run_plan(output_dir, "r", 1, 6, distributed_end=3,
                          available_pages=3)
        finished_worker(output_dir, "r", "1", 1, 2, 0, completed=(1, 2),
                        corpus_pages=3)
        finished_worker(output_dir, "r", "2", 3, 3, 0, completed=(3, 3),
                        corpus_pages=3)
        return gc.aggregate_run(output_dir, "r")

    def test_the_run_completes_and_says_it_was_capped(self, tmp_path):
        manifest = self.aggregate_capped_run(tmp_path)

        assert manifest["status"] == "completed"
        assert manifest["completed_pages"] == {"start": 1, "end": 3}
        assert manifest["resolved_end_page"] == 3
        assert manifest["coverage"] == "capped"

    def test_stage_one_still_classifies_it_as_capped(self, tmp_path):
        self.aggregate_capped_run(tmp_path)

        status, error = classify_stage1_outcome(0, tmp_path, start_page=1,
                                                end_page=6)

        assert (status, error) == ("capped", None)

    def test_the_files_it_collected_are_its_own(self, tmp_path):
        manifest = self.aggregate_capped_run(tmp_path)

        assert manifest["collected_files"] == {
            "SMTX": 2, "SRR": 0, "META": 0, "total": 2
        }
