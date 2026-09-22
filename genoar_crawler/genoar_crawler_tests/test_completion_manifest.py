"""Tests for the crawler's completion manifest.

The manifest is the signal that a crawl finished its whole requested range. The
runner reads it to tell a completed crawl from one that stopped early yet still
exited a container with code 0. These check the writer produces what that reader
(cycle_test.utils.stage1_runner.classify_stage1_outcome) expects.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from genoar_crawler import write_completion_manifest, COMPLETION_MANIFEST_FILE  # noqa: E402


def test_writes_manifest_marked_completed(tmp_path):
    write_completion_manifest(
        tmp_path,
        requested_start=1, requested_end=5,
        completed_start=1, completed_end=5,
        items_per_page=20, processed=42, failed=3,
    )
    data = json.loads((tmp_path / COMPLETION_MANIFEST_FILE).read_text())
    assert data["status"] == "completed"
    assert data["requested_pages"] == {"start": 1, "end": 5}
    assert data["completed_pages"] == {"start": 1, "end": 5}
    assert data["items_per_page"] == 20
    assert data["processed"] == 42
    assert data["failed"] == 3


def test_returns_the_manifest_path(tmp_path):
    path = write_completion_manifest(
        tmp_path, requested_start=1, requested_end=1, completed_start=1,
        completed_end=1, items_per_page=500, processed=0, failed=0,
    )
    assert path == tmp_path / COMPLETION_MANIFEST_FILE
    assert path.exists()


def test_all_pages_request_records_resolved_end(tmp_path):
    # requested_end 0 means "all pages"; the resolved end is what completed.
    write_completion_manifest(
        tmp_path, requested_start=1, requested_end=0, completed_start=1,
        completed_end=37, items_per_page=500, processed=100, failed=0,
    )
    data = json.loads((tmp_path / COMPLETION_MANIFEST_FILE).read_text())
    assert data["requested_pages"]["end"] == 0
    assert data["completed_pages"]["end"] == 37


class TestMainExitCodes:
    """main() must signal outcome through the exit code, so a container running
    the crawler exits non-zero on interrupt or failure rather than a bare 0.

    The contract the parallel runner reads:
      0 completed, 1 failure, 2 invalid range, 3 nothing in range, 130 interrupt.
    """

    def _run_main(self, tmp_path, crawl_side_effect):
        from unittest import mock
        import genoar_crawler as gc

        argv = ["genoar_crawler.py", "--start", "1", "--end", "1", "-o", str(tmp_path)]
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(gc.SimpleCrawler, "setup_driver", return_value=None), \
             mock.patch.object(gc.SimpleCrawler, "crawl", crawl_side_effect), \
             mock.patch.object(gc.SimpleCrawler, "cleanup", return_value=None):
            try:
                gc.main()
                return 0
            except SystemExit as e:
                return e.code

    def test_interrupt_exits_130(self, tmp_path):
        def crawl(self, **kw):
            raise KeyboardInterrupt
        assert self._run_main(tmp_path, crawl) == 130

    def test_failure_exits_1(self, tmp_path):
        def crawl(self, **kw):
            raise RuntimeError("boom")
        assert self._run_main(tmp_path, crawl) == 1

    def test_no_pages_in_range_exits_3_without_a_manifest(self, tmp_path):
        """A worker whose slice fell past the end of GEO's pages did nothing.
        Exit 0 would be a false success; exit 1 would blame a request that was
        fine."""
        import genoar_crawler as gc

        def crawl(self, **kw):
            raise gc.EmptyPageRangeError(
                "start page 51 is past the 47 pages GEO holds for this query"
            )
        assert self._run_main(tmp_path, crawl) == 3
        assert not (tmp_path / COMPLETION_MANIFEST_FILE).exists()

    def test_an_invalid_range_exits_2_before_launching_chrome(self, tmp_path):
        """2 stays distinct from 3: a range that cannot be crawled at all is
        not the same as one that has nothing left to crawl."""
        from unittest import mock
        import genoar_crawler as gc

        argv = ["genoar_crawler.py", "--start", "5", "--end", "2",
                "-o", str(tmp_path)]
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(
                 gc.SimpleCrawler, "__init__",
                 mock.Mock(side_effect=AssertionError("Chrome must not start"))):
            with pytest.raises(SystemExit) as excinfo:
                gc.main()
        assert excinfo.value.code == 2

    def test_natural_completion_exits_0_with_manifest(self, tmp_path):
        import genoar_crawler as gc

        def crawl(self, **kw):
            gc.write_completion_manifest(tmp_path, 1, 1, 1, 1, 500, 5, 0)
        assert self._run_main(tmp_path, crawl) in (0, None)
        assert (tmp_path / COMPLETION_MANIFEST_FILE).exists()

    def test_stale_manifest_is_removed_before_a_crawl_that_interrupts(self, tmp_path):
        # A leftover manifest must not survive an interrupted run, or the runner
        # would read the stale one as completion.
        (tmp_path / COMPLETION_MANIFEST_FILE).write_text('{"status": "completed"}')

        def crawl(self, **kw):
            raise KeyboardInterrupt
        self._run_main(tmp_path, crawl)
        assert not (tmp_path / COMPLETION_MANIFEST_FILE).exists()
