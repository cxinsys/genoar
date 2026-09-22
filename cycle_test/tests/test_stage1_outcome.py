"""Tests for interrupt/completion detection in Stage 1.

These pin the fix for the validation finding that an interrupted crawl was
recorded as success: the crawler swallowed the interrupt and exited 0, the runner
saw exit 0 and reported success, and later stages ran on partial data.

The crawler now writes a completion manifest only on a natural finish and exits
130 on interrupt, and classify_stage1_outcome() treats exit 0 without a manifest
as a failure rather than a success.
"""

import json


from cycle_test.utils.stage1_runner import (
    classify_stage1_outcome,
    COMPLETION_MANIFEST_FILE,
    CRAWLER_INTERRUPT_CODE,
    CRAWLER_NO_PAGES_IN_RANGE_CODE,
)


def _write_manifest(d, status="completed", start=1, end=5):
    """A manifest for pages start-end. The range matters: the classifier checks
    that the manifest covers what was asked for (see test_status_propagation)."""
    (d / COMPLETION_MANIFEST_FILE).write_text(json.dumps({
        "status": status,
        "requested_pages": {"start": start, "end": end},
        "completed_pages": {"start": start, "end": end},
        "items_per_page": 500,
    }))


class TestClassifyOutcome:
    def test_exit_0_with_completion_manifest_is_success(self, tmp_path):
        _write_manifest(tmp_path)
        status, error = classify_stage1_outcome(0, tmp_path, 1, 5)
        assert status == "success"
        assert error is None

    def test_exit_0_without_manifest_is_failure(self, tmp_path):
        # The crux: a crawl that stops early still exits 0. No manifest, so it is
        # not success.
        status, error = classify_stage1_outcome(0, tmp_path, 1, 5)
        assert status == "failed"
        assert "manifest" in error

    def test_interrupt_code_is_interrupted(self, tmp_path):
        # Even if a stale manifest is present, exit 130 means interrupted.
        _write_manifest(tmp_path)
        status, error = classify_stage1_outcome(CRAWLER_INTERRUPT_CODE, tmp_path, 1, 5)
        assert status == "interrupted"

    def test_other_nonzero_is_failure(self, tmp_path):
        status, error = classify_stage1_outcome(1, tmp_path, 1, 5)
        assert status == "failed"
        assert "code 1" in error

    def test_manifest_not_marked_completed_is_failure(self, tmp_path):
        _write_manifest(tmp_path, status="partial")
        status, error = classify_stage1_outcome(0, tmp_path, 1, 5)
        assert status == "failed"

    def test_corrupt_manifest_is_failure(self, tmp_path):
        (tmp_path / COMPLETION_MANIFEST_FILE).write_text("{ not json")
        status, error = classify_stage1_outcome(0, tmp_path, 1, 5)
        assert status == "failed"
        assert "unreadable" in error


class TestCappedRangeIsNotAFailure:
    """The crawler caps a request that runs past the pages GEO holds.

    Asking for 100 pages when GEO has 47 now crawls 1-47 and exits 0 with an
    honest manifest. Reading "completed end < requested end" as a failure turned
    that into a false failure, but the same shape also describes a crawl that
    stopped early, which must still be caught. The manifest tells them apart:
    resolved_end_page is the end the crawler settled on, and a legitimate cap
    reached exactly it.
    """

    def _capped(self, d, requested_end=100, resolved=47, completed_end=47):
        (d / COMPLETION_MANIFEST_FILE).write_text(json.dumps({
            "status": "completed",
            "requested_pages": {"start": 1, "end": requested_end},
            "completed_pages": {"start": 1, "end": completed_end},
            "resolved_end_page": resolved,
            "items_per_page": 500,
        }))

    def test_a_capped_run_is_reported_as_capped(self, tmp_path):
        self._capped(tmp_path)
        status, error = classify_stage1_outcome(0, tmp_path, 1, 100)
        assert status == "capped"
        assert error is None

    def test_a_crawl_that_stopped_before_the_resolved_end_is_a_failure(self, tmp_path):
        # The crawler resolved 47 pages and was going to do all of them, but the
        # manifest only claims 30. That is a genuinely short run.
        self._capped(tmp_path, resolved=47, completed_end=30)
        status, error = classify_stage1_outcome(0, tmp_path, 1, 100)
        assert status == "failed"
        assert "stopped before" in error

    def test_a_short_run_without_a_resolved_end_is_a_failure(self, tmp_path):
        # Nothing in the manifest says a cap happened, so the missing pages are
        # not excused.
        (tmp_path / COMPLETION_MANIFEST_FILE).write_text(json.dumps({
            "status": "completed",
            "requested_pages": {"start": 1, "end": 100},
            "completed_pages": {"start": 1, "end": 47},
            "items_per_page": 500,
        }))
        status, error = classify_stage1_outcome(0, tmp_path, 1, 100)
        assert status == "failed"
        assert "resolved_end_page" in error

    def test_a_resolved_end_that_is_not_a_cap_is_a_failure(self, tmp_path):
        # resolved_end_page 100 means nothing was capped, so completing 47 of
        # the 100 requested pages is a short crawl however it is labelled.
        self._capped(tmp_path, requested_end=100, resolved=100, completed_end=47)
        status, error = classify_stage1_outcome(0, tmp_path, 1, 100)
        assert status == "failed"

    def test_a_resolved_end_below_the_completed_start_is_a_failure(self, tmp_path):
        (tmp_path / COMPLETION_MANIFEST_FILE).write_text(json.dumps({
            "status": "completed",
            "requested_pages": {"start": 10, "end": 100},
            "completed_pages": {"start": 10, "end": 47},
            "resolved_end_page": 5,
            "items_per_page": 500,
        }))
        status, _ = classify_stage1_outcome(0, tmp_path, 10, 100)
        assert status == "failed"

    def test_a_non_integer_resolved_end_is_a_failure_not_a_crash(self, tmp_path):
        self._capped(tmp_path, resolved="47")
        status, _ = classify_stage1_outcome(0, tmp_path, 1, 100)
        assert status == "failed"

    def test_an_uncapped_full_run_is_still_plain_success(self, tmp_path):
        _write_manifest(tmp_path, start=1, end=5)
        status, _ = classify_stage1_outcome(0, tmp_path, 1, 5)
        assert status == "success"


class TestNoPagesInRange:
    """Crawler exit 3: the request was valid, the pages are not there."""

    def test_exit_3_is_its_own_status(self, tmp_path):
        status, error = classify_stage1_outcome(
            CRAWLER_NO_PAGES_IN_RANGE_CODE, tmp_path, 1, 5
        )
        assert status == "no_pages_in_range"
        assert "no page fell in range" in error

    def test_exit_3_does_not_need_a_manifest(self, tmp_path):
        # Nothing was crawled, so the crawler writes no manifest. Demanding one
        # would turn an honest empty result into a reported defect.
        assert not (tmp_path / COMPLETION_MANIFEST_FILE).exists()
        status, _ = classify_stage1_outcome(
            CRAWLER_NO_PAGES_IN_RANGE_CODE, tmp_path, 1, 5
        )
        assert status == "no_pages_in_range"

    def test_exit_3_is_not_masked_by_a_stale_manifest(self, tmp_path):
        _write_manifest(tmp_path)
        status, _ = classify_stage1_outcome(
            CRAWLER_NO_PAGES_IN_RANGE_CODE, tmp_path, 1, 5
        )
        assert status == "no_pages_in_range"

    def test_exit_2_is_still_a_failure(self, tmp_path):
        # Invalid arguments are a defect in the request, not an empty result.
        status, error = classify_stage1_outcome(2, tmp_path, 1, 5)
        assert status == "failed"


class TestCliExitCode:
    def _cyc(self, status):
        return {"status": status}

    def test_all_success_is_zero(self):
        from cycle_test.run_cycle_test import _exit_code_for
        assert _exit_code_for([self._cyc("success"), self._cyc("success")]) == 0

    def test_any_interrupted_is_130(self):
        from cycle_test.run_cycle_test import _exit_code_for
        assert _exit_code_for([self._cyc("success"), self._cyc("interrupted")]) == 130

    def test_failure_is_one(self):
        from cycle_test.run_cycle_test import _exit_code_for
        assert _exit_code_for([self._cyc("failed_stage1")]) == 1

    def test_partial_success_is_one(self):
        from cycle_test.run_cycle_test import _exit_code_for
        assert _exit_code_for([self._cyc("partial_success")]) == 1

    def test_no_cycles_is_one(self):
        from cycle_test.run_cycle_test import _exit_code_for
        assert _exit_code_for([]) == 1

    def test_interrupt_wins_over_failure(self):
        from cycle_test.run_cycle_test import _exit_code_for
        # A user interrupt is reported as 130 even alongside a failure, so it is
        # not misread as a plain error.
        assert _exit_code_for([self._cyc("failed_stage1"), self._cyc("interrupted")]) == 130


class TestRunStage1CarriesTheOutcome:
    """The classification has to survive the trip through run_stage1(), which is
    where the regression actually bit: a capped crawl classified as failed made
    run_stage1 return early with success=False."""

    def _run(self, tmp_path, manifest, start=1, end=100):
        from types import SimpleNamespace
        from unittest import mock

        from cycle_test.utils import stage1_runner as s1

        out = tmp_path / "stage1"
        out.mkdir()
        (out / COMPLETION_MANIFEST_FILE).write_text(json.dumps(manifest))

        # Both the crawler and the integration pass are replaced; the manifest
        # above stands in for what a real crawl would have left behind.
        with mock.patch.object(s1.subprocess, "run",
                               return_value=SimpleNamespace(returncode=0, stderr="")):
            return s1.run_stage1(
                start_page=start, end_page=end, output_dir=out, use_docker=False
            )

    def test_a_capped_run_succeeds_and_says_it_was_capped(self, tmp_path):
        result = self._run(tmp_path, {
            "status": "completed",
            "requested_pages": {"start": 1, "end": 100},
            "completed_pages": {"start": 1, "end": 47},
            "resolved_end_page": 47,
            "items_per_page": 500,
        })
        assert result["success"] is True
        assert result["status"] == "capped"
        assert result["capped"] is True
        assert result["capped_pages"]["requested_end"] == 100
        assert result["capped_pages"]["completed_end"] == 47

    def test_a_full_run_is_plain_success(self, tmp_path):
        result = self._run(tmp_path, {
            "status": "completed",
            "requested_pages": {"start": 1, "end": 5},
            "completed_pages": {"start": 1, "end": 5},
            "items_per_page": 500,
        }, end=5)
        assert result["success"] is True
        assert result["status"] == "success"
        assert result.get("capped", False) is False

    def test_a_genuinely_short_run_still_fails(self, tmp_path):
        result = self._run(tmp_path, {
            "status": "completed",
            "requested_pages": {"start": 1, "end": 100},
            "completed_pages": {"start": 1, "end": 30},
            "resolved_end_page": 47,
            "items_per_page": 500,
        })
        assert result["success"] is False
        assert result["status"] == "failed"
