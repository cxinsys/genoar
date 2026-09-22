"""What a resumed worker's manifest claims, and what earns the claim.

The defect this pins. A worker assigned pages 1-2 died part way through page 2,
was resumed from its own checkpoint, finished page 2 and exited 0 - a resume
working exactly as designed. It then wrote a manifest saying `completed_pages`
1... no: 2-2, the pages *that invocation* walked. Its own aggregator refused the
run:

    WORKER_COMPLETED_PAGES {'start': 2, 'end': 2}
    RunAggregationError: assigned pages 1-2 but manifest covers 2-2

Two pieces of code with a shared assumption that was never tested together. The
writer meant `completed_pages` as "the pages this process crawled"; both readers
- `aggregate_run`, which fits slices together starting at the page each worker
was assigned, and `stage1_runner._check_manifest_covers`, which asks whether the
request it made is covered - mean "how much of the request is finished". The
readers are right: a manifest is completion evidence for a request, and a run
that resumed is not a run that covered less.

So the writer changed. `completed_pages` starts where the request starts, and
what one invocation walked is recorded separately as `crawled_pages`, which
nothing judges completion on.

That claim has to be earned, or the fix would be a new false success: saying
pages 1-2 are done because a checkpoint sits in the state directory is only
true while that checkpoint is progress through *this* request. So a checkpoint
records the request it belongs to, and a resume that cannot show the two match
is refused rather than believed - which is also what stops
    genoar_crawler.py 5 9        # crashes
    genoar_crawler.py 1 9 --resume
from producing a manifest that claims pages 1-4 nobody ever crawled.

Everything below the browser is the real crawler and the real aggregator.
"""

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

import genoar_crawler as gc  # noqa: E402
from test_pagination_completion import (  # noqa: E402
    FakeDriver,
    stub_chrome_paths,
    visit_detail_then_succeed,
)

RUN_ID = "crash-run"

# Two GSEs per page, so a crawl can die after the first of a page and leave a
# checkpoint that resumes in the middle of that page - the reported case.
PAGES = {1: ["GSE100", "GSE101"], 2: ["GSE200", "GSE201"]}


def worker_crawler(tmp_path, monkeypatch, worker_id="1"):
    """A crawler that is worker `worker_id` of RUN_ID, as a launcher starts it.

    Its checkpoint and manifest therefore land under the worker directory, which
    is where the aggregator looks for them.
    """
    output_dir = tmp_path / "out"
    scope = gc.resolve_worker_scope(output_dir, run_id=RUN_ID,
                                    worker_id=worker_id)
    scope.state_dir.mkdir(parents=True, exist_ok=True)
    stub_chrome_paths(monkeypatch)
    monkeypatch.setattr(gc, "CHROME_PROFILE_ROOT", tmp_path / "chrome-profiles")
    monkeypatch.setattr(gc.webdriver, "Chrome", lambda **kw: FakeDriver(PAGES))
    monkeypatch.setattr(gc.time, "sleep", lambda seconds: None)
    crawler = gc.SimpleCrawler(output_dir=str(output_dir), scope=scope)
    crawler.NAVIGATION_TIMEOUT = 0.2
    return crawler


def dies_on(crawler, gse_id):
    """A GSE processor that works until it meets one particular accession."""
    working = visit_detail_then_succeed(crawler)

    def process(accession):
        if accession == gse_id:
            raise gc.SearchContextLostError(f"Chrome died on {accession}")
        return working(accession)
    return process


def crash_then_resume(tmp_path, monkeypatch, assigned=(1, 2)):
    """Run the reported sequence and return the resumed worker.

    Invocation one crawls page 1, starts page 2, saves its checkpoint after the
    first GSE of page 2 and dies. Invocation two resumes it and finishes.
    """
    start, end = assigned
    crawler = worker_crawler(tmp_path, monkeypatch)
    monkeypatch.setattr(crawler, "process_gse_direct", dies_on(crawler, "GSE201"))
    with pytest.raises(gc.SearchContextLostError):
        crawler.crawl(start_page=start, end_page=end, items_per_page=2)

    checkpoint = json.loads(crawler.checkpoint.checkpoint_file.read_text())
    assert checkpoint["last_page"] == 2, "the reported case resumes mid-range"
    assert not crawler.scope.manifest_path().exists(), (
        "a worker that died must leave no completion evidence"
    )

    resumed = worker_crawler(tmp_path, monkeypatch)
    monkeypatch.setattr(resumed, "process_gse_direct",
                        visit_detail_then_succeed(resumed))
    resumed.crawl(start_page=start, end_page=end, items_per_page=2, resume=True)
    return resumed


def worker_manifest(crawler):
    return json.loads(crawler.scope.manifest_path().read_text())


class TestAResumedWorkerReportsTheAssignmentItFinished:
    def test_completed_pages_covers_the_whole_assignment(self, tmp_path,
                                                          monkeypatch):
        resumed = crash_then_resume(tmp_path, monkeypatch)

        assert worker_manifest(resumed)["completed_pages"] == {"start": 1, "end": 2}

    def test_the_pages_this_invocation_walked_are_still_recorded(
        self, tmp_path, monkeypatch
    ):
        """The narrower statement is not wrong, it is just a different one.
        Losing it would cost a reader the ability to see that a resume happened
        at all."""
        resumed = crash_then_resume(tmp_path, monkeypatch)

        assert worker_manifest(resumed)["crawled_pages"] == {"start": 2, "end": 2}

    def test_a_crawl_that_never_resumed_says_the_same_thing_twice(
        self, tmp_path, monkeypatch
    ):
        crawler = worker_crawler(tmp_path, monkeypatch)
        monkeypatch.setattr(crawler, "process_gse_direct",
                            visit_detail_then_succeed(crawler))

        crawler.crawl(start_page=1, end_page=2, items_per_page=2)

        manifest = worker_manifest(crawler)
        assert manifest["completed_pages"] == {"start": 1, "end": 2}
        assert manifest["crawled_pages"] == manifest["completed_pages"]

    def test_the_run_aggregates_and_says_it_covered_its_range(
        self, tmp_path, monkeypatch
    ):
        """The headline: a resume that works as designed can be aggregated."""
        output_dir = tmp_path / "out"
        gc.write_run_plan(output_dir, RUN_ID, 1, 2, items_per_page=2,
                          distributed_end=2, available_pages=2, workers=1)
        gc.write_worker_assignment(output_dir, RUN_ID, "1", 1, 2)
        resumed = crash_then_resume(tmp_path, monkeypatch)
        (resumed.scope.worker_dir / gc.WORKER_EXIT_CODE_FILE).write_text("0\n")

        merged = gc.aggregate_run(output_dir, RUN_ID)

        assert merged["status"] == "completed"
        assert merged["requested_pages"] == {"start": 1, "end": 2}
        assert merged["completed_pages"] == {"start": 1, "end": 2}
        assert merged["coverage"] == "complete"
        # Every GSE on both pages, across both invocations. The four are what
        # makes the claim of pages 1-2 true rather than merely accepted.
        assert merged["processed"] == 4

    def test_the_aggregator_still_refuses_a_worker_that_overran_its_slice(
        self, tmp_path, monkeypatch
    ):
        """The check that was refusing the resume is not relaxed. A worker
        assigned page 1 alone, which crawls into page 2, is still refused - the
        end is compared against the assignment exactly as before."""
        output_dir = tmp_path / "out"
        gc.write_run_plan(output_dir, RUN_ID, 1, 1, items_per_page=2,
                          distributed_end=1, available_pages=2, workers=1)
        gc.write_worker_assignment(output_dir, RUN_ID, "1", 1, 1)
        crawler = worker_crawler(tmp_path, monkeypatch, worker_id="1")
        monkeypatch.setattr(crawler, "process_gse_direct",
                            visit_detail_then_succeed(crawler))
        crawler.crawl(start_page=1, end_page=2, items_per_page=2)  # not its slice
        (crawler.scope.worker_dir / gc.WORKER_EXIT_CODE_FILE).write_text("0\n")

        with pytest.raises(gc.RunAggregationError, match="but its manifest covers"):
            gc.aggregate_run(output_dir, RUN_ID)


class TestACheckpointOnlySpeaksForItsOwnRequest:
    """Claiming the pages before the resume point rests entirely on the
    checkpoint being progress through this request. Where that cannot be shown
    the resume is refused, because the alternative is a manifest that claims
    pages nobody crawled - the same false success one level below the
    aggregator."""

    def _plant(self, crawler, data):
        gc.atomic_write_json(crawler.checkpoint.checkpoint_file, data)

    def _checkpoint(self, **overrides):
        data = {
            "last_page": 2, "last_gse_index": 0, "downloaded_gse": ["GSE200"],
            "failed_gse": [], "total_pages": 2, "items_per_page": 2,
            "requested_pages": {"start": 1, "end": 2},
            "collected_files": None, "timestamp": "x",
        }
        data.update(overrides)
        return data

    def test_a_checkpoint_from_a_different_request_is_refused(
        self, tmp_path, monkeypatch
    ):
        crawler = worker_crawler(tmp_path, monkeypatch)
        self._plant(crawler, self._checkpoint(
            requested_pages={"start": 2, "end": 2}
        ))

        with pytest.raises(ValueError, match="started at page 2"):
            crawler.crawl(start_page=1, end_page=2, items_per_page=2, resume=True)
        assert not crawler.scope.manifest_path().exists()

    def test_only_the_start_page_has_to_match(self, tmp_path, monkeypatch):
        """The start decides which pages are taken on trust; the end does not.
        Requiring the end to match too would refuse the documented
            genoar_crawler.py 1 5   # crashes
            genoar_crawler.py --resume
        even though page 1 was crawled either way. run_parallel_crawl.sh still
        holds a parallel resume to the plan's whole page request, because there
        the end decides the split as well."""
        crawler = worker_crawler(tmp_path, monkeypatch)
        monkeypatch.setattr(crawler, "process_gse_direct",
                            visit_detail_then_succeed(crawler))
        self._plant(crawler, self._checkpoint(
            requested_pages={"start": 1, "end": 4}
        ))

        crawler.crawl(start_page=1, end_page=2, items_per_page=2, resume=True)

        assert worker_manifest(crawler)["completed_pages"] == {"start": 1, "end": 2}

    def test_a_checkpoint_that_names_no_request_is_refused(
        self, tmp_path, monkeypatch
    ):
        """What every checkpoint written before this rule looks like. Silence
        is not evidence: it cannot be told from a checkpoint of another
        request."""
        crawler = worker_crawler(tmp_path, monkeypatch)
        planted = self._checkpoint()
        del planted["requested_pages"]
        self._plant(crawler, planted)

        with pytest.raises(ValueError, match="does not record which crawl request"):
            crawler.crawl(start_page=1, end_page=2, items_per_page=2, resume=True)
        assert not crawler.scope.manifest_path().exists()

    def test_the_refusal_names_a_way_out(self, tmp_path, monkeypatch):
        crawler = worker_crawler(tmp_path, monkeypatch)
        planted = self._checkpoint()
        del planted["requested_pages"]
        self._plant(crawler, planted)

        with pytest.raises(ValueError, match="--clear-checkpoint"):
            crawler.crawl(start_page=1, end_page=2, items_per_page=2, resume=True)

    def test_a_matching_checkpoint_resumes(self, tmp_path, monkeypatch):
        crawler = worker_crawler(tmp_path, monkeypatch)
        monkeypatch.setattr(crawler, "process_gse_direct",
                            visit_detail_then_succeed(crawler))
        self._plant(crawler, self._checkpoint())

        crawler.crawl(start_page=1, end_page=2, items_per_page=2, resume=True)

        assert worker_manifest(crawler)["completed_pages"] == {"start": 1, "end": 2}

    def test_a_wider_request_cannot_adopt_a_narrower_checkpoint(
        self, tmp_path, monkeypatch
    ):
        """The single-crawler version of the same danger: `5 9` crashed, and
        `1 9 --resume` would otherwise write a manifest covering pages 1-4 that
        this configuration never crawled."""
        crawler = worker_crawler(tmp_path, monkeypatch)
        monkeypatch.setattr(crawler, "process_gse_direct",
                            visit_detail_then_succeed(crawler))
        self._plant(crawler, self._checkpoint(
            requested_pages={"start": 2, "end": 2}, last_page=2
        ))

        with pytest.raises(ValueError):
            crawler.crawl(start_page=1, end_page=2, items_per_page=2, resume=True)


class TestTheNumbersBesideTheClaimSpanTheSameWork:
    """`completed_pages` now spans every invocation that contributed to it, so
    the counts reported beside it must too. `processed` already did - it comes
    from the checkpoint's downloaded list - and the file tally did not, so a
    resumed run whose first invocation collected everything reported collecting
    nothing, and the launcher's exit 4 ("finished cleanly, collected nothing")
    would have fired on a run that collected plenty."""

    def test_the_file_tally_survives_the_resume(self, tmp_path, monkeypatch):
        crawler = worker_crawler(tmp_path, monkeypatch)
        monkeypatch.setattr(crawler, "process_gse_direct",
                            visit_detail_then_succeed(crawler))
        # Two files published by the invocation that then dies.
        gc.atomic_write_json(crawler.checkpoint.checkpoint_file, {
            "last_page": 2, "last_gse_index": 0, "downloaded_gse": ["GSE200"],
            "failed_gse": [], "total_pages": 2, "items_per_page": 2,
            "requested_pages": {"start": 1, "end": 2},
            "collected_files": {"SMTX": 1, "SRR": 1, "META": 0},
            "timestamp": "x",
        })

        crawler.crawl(start_page=1, end_page=2, items_per_page=2, resume=True)

        collected = worker_manifest(crawler)["collected_files"]
        assert collected == {"SMTX": 1, "SRR": 1, "META": 0, "total": 2}

    def test_a_saved_checkpoint_carries_the_tally_forward(self, tmp_path,
                                                          monkeypatch):
        """The other half: nothing can be restored that was never written."""
        crawler = worker_crawler(tmp_path, monkeypatch)
        monkeypatch.setattr(crawler, "process_gse_direct",
                            visit_detail_then_succeed(crawler))
        crawler.collected_files["META"] = 3

        crawler.crawl(start_page=1, end_page=2, items_per_page=2)

        saved = json.loads(crawler.checkpoint.checkpoint_file.read_text())
        assert saved["collected_files"]["META"] == 3
        assert saved["requested_pages"] == {"start": 1, "end": 2}
