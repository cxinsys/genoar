"""Tests for the cycle-test final report aggregation.

These pin the behaviour that the integration-test validation flagged: a report
that counts skipped stages as successes and never surfaces Stage 3, so a
stage3-only run that downloaded data is reported as Stage 1/2 100% with every
total at zero and no Stage 3 line at all.

The report reads a list of cycle_result dicts shaped like run_cycle_test.py
writes them under result["stages"]: stage1, stage2, stage3_prep,
stage3_download, stage3_pipeline, stage3_handoff. A skipped stage is
{"success": True, "skipped": True}.
"""

import json


from cycle_test.utils.report_generator import generate_cycle_summary


def _full_cycle(**overrides):
    """A cycle where Stage 1 and 2 really ran and produced data."""
    cycle = {
        "cycle_num": 1,
        "start_page": 1,
        "end_page": 5,
        "status": "success",
        "start_time": "2026-07-22T10:00:00",
        "end_time": "2026-07-22T10:20:00",
        "stages": {
            "stage1": {"success": True, "statistics": {"meta_files": 21, "smtx_files": 30, "srr_files": 12, "total_srr_ids": 189}},
            "stage2": {"success": True, "tables": {"cell_type": {"rows": 74}, "tissue": {"rows": 121}, "disease": {"rows": 66}}},
            "stage3_prep": {"success": True, "unique_srr_ids": 189, "gse_count": 12},
        },
    }
    cycle.update(overrides)
    return cycle


def _stage3_only_cycle():
    """A --stage3-only run: Stage 1/2 skipped, a download actually happened."""
    return {
        "cycle_num": 1,
        "start_page": 1,
        "end_page": 5,
        "status": "success",
        "start_time": "2026-07-22T10:00:00",
        "end_time": "2026-07-22T10:15:00",
        "stages": {
            "stage1": {"success": True, "skipped": True},
            "stage2": {"success": True, "skipped": True},
            "stage3_prep": {"success": True, "unique_srr_ids": 1, "gse_count": 1},
            "stage3_download": {"success": True, "total": 1, "downloaded": 1, "failed": 0},
            "stage3_pipeline": {"success": True, "skipped": True},
        },
    }


def _read_report(tmp_path, cycles):
    path = generate_cycle_summary(cycles, tmp_path)
    return json.loads(path.read_text())


class TestNormalRun:
    def test_full_cycle_reports_100_percent(self, tmp_path):
        r = _read_report(tmp_path, [_full_cycle()])
        assert r["success_rate"]["stage1"] == 100.0
        assert r["success_rate"]["stage2"] == 100.0
        assert r["success_rate"]["overall"] == 100.0

    def test_totals_are_summed(self, tmp_path):
        r = _read_report(tmp_path, [_full_cycle()])
        assert r["totals"]["meta_files"] == 21
        assert r["totals"]["tissue_samples"] == 121

    def test_a_real_stage1_failure_lowers_the_rate(self, tmp_path):
        bad = _full_cycle()
        bad["stages"]["stage1"] = {"success": False, "error": "timed out"}
        r = _read_report(tmp_path, [bad])
        assert r["success_rate"]["stage1"] == 0.0


class TestSkippedStagesAreNotSuccesses:
    """A skipped stage was not attempted, so it must not count as a success."""

    def test_stage3_only_does_not_report_stage1_2_as_100(self, tmp_path):
        r = _read_report(tmp_path, [_stage3_only_cycle()])
        # Skipped -> excluded from the rate, reported as None (N/A), never 100%.
        assert r["success_rate"]["stage1"] is None
        assert r["success_rate"]["stage2"] is None

    def test_overall_excludes_skipped_stages(self, tmp_path):
        r = _read_report(tmp_path, [_stage3_only_cycle()])
        # Only Stage 3 was attempted and it succeeded, so overall is 100 of what ran.
        assert r["success_rate"]["overall"] == 100.0


class TestSkippedStagesAreLabelled:
    def test_skipped_stage1_2_are_marked_in_the_cycle(self, tmp_path):
        cyc = _read_report(tmp_path, [_stage3_only_cycle()])["cycles"][0]
        assert cyc["stage1"]["skipped"] is True
        assert cyc["stage2"]["skipped"] is True

    def test_a_run_stage1_is_not_marked_skipped(self, tmp_path):
        cyc = _read_report(tmp_path, [_full_cycle()])["cycles"][0]
        assert cyc["stage1"]["skipped"] is False


class TestStage3IsReported:
    def test_stage3_download_appears_in_the_report(self, tmp_path):
        r = _read_report(tmp_path, [_stage3_only_cycle()])
        cyc = r["cycles"][0]
        assert "stage3_download" in cyc
        assert cyc["stage3_download"]["downloaded"] == 1

    def test_stage3_pipeline_skip_is_marked(self, tmp_path):
        r = _read_report(tmp_path, [_stage3_only_cycle()])
        cyc = r["cycles"][0]
        assert cyc["stage3_pipeline"]["skipped"] is True


class TestFailuresPropagate:
    def test_download_failure_is_not_hidden(self, tmp_path):
        cyc = _stage3_only_cycle()
        cyc["stages"]["stage3_download"] = {"success": False, "total": 3, "downloaded": 1, "failed": 2}
        r = _read_report(tmp_path, [cyc])
        # The Stage 3 rate must reflect the failure; overall cannot be 100%.
        assert r["success_rate"]["stage3"] != 100.0
        assert r["success_rate"]["overall"] != 100.0

    def test_missing_srr_list_is_a_stage3_failure(self, tmp_path):
        cyc = _stage3_only_cycle()
        del cyc["stages"]["stage3_pipeline"]
        cyc["stages"]["stage3_download"] = {"success": False, "error": "No SRR list file"}
        r = _read_report(tmp_path, [cyc])
        assert r["success_rate"]["stage3"] != 100.0


class TestEmpty:
    def test_no_cycles_does_not_crash(self, tmp_path):
        r = _read_report(tmp_path, [])
        assert r["total_cycles"] == 0


class TestIntegrationOutcomeIsVisible:
    """Integration failing does not fail Stage 1, but it must not be invisible.

    Stage 2 reads the META files directly, so a failed integration pass leaves
    the crawl usable. It does lose the reports and the complete_datasets list
    that Stage 3 prep may filter on, so the report says so.
    """

    def test_integration_failure_is_recorded(self, tmp_path):
        cyc = _full_cycle()
        cyc["stages"]["stage1"]["integration"] = {"success": False, "returncode": 1}
        r = _read_report(tmp_path, [cyc])
        assert r["cycles"][0]["stage1"]["integration_ok"] is False

    def test_integration_success_is_recorded(self, tmp_path):
        cyc = _full_cycle()
        cyc["stages"]["stage1"]["integration"] = {"success": True, "returncode": 0}
        r = _read_report(tmp_path, [cyc])
        assert r["cycles"][0]["stage1"]["integration_ok"] is True

    def test_integration_failure_does_not_fail_stage1(self, tmp_path):
        cyc = _full_cycle()
        cyc["stages"]["stage1"]["integration"] = {"success": False, "returncode": 1}
        r = _read_report(tmp_path, [cyc])
        assert r["success_rate"]["stage1"] == 100.0


class TestSampleFailuresReachTheReport:
    """The cycle status already reflects failed samples; the report must too.

    run_cycle_test marks a cycle partial_success when Cell Ranger fails on some
    samples, but the report aggregated only each sub-stage's success boolean. The
    pipeline sets that to True when it ran without a container error, so the
    failed samples vanished from the rate.
    """

    def _cycle_with_pipeline(self, **pipeline):
        cyc = _stage3_only_cycle()
        cyc["stages"]["stage3_pipeline"] = {"success": True, **pipeline}
        return cyc

    def test_some_samples_failing_is_not_100_percent(self, tmp_path):
        r = _read_report(
            tmp_path, [self._cycle_with_pipeline(successful_samples=1, failed_samples=2)]
        )
        assert r["success_rate"]["stage3"] != 100.0
        assert r["success_rate"]["overall"] != 100.0

    def test_every_sample_failing_is_zero(self, tmp_path):
        r = _read_report(
            tmp_path, [self._cycle_with_pipeline(successful_samples=0, failed_samples=3)]
        )
        assert r["success_rate"]["stage3"] == 0.0

    def test_all_samples_succeeding_is_100_percent(self, tmp_path):
        r = _read_report(
            tmp_path, [self._cycle_with_pipeline(successful_samples=3, failed_samples=0)]
        )
        assert r["success_rate"]["stage3"] == 100.0

    def test_a_download_with_failures_is_not_100_percent(self, tmp_path):
        cyc = _stage3_only_cycle()
        cyc["stages"]["stage3_download"] = {
            "success": True, "total": 3, "downloaded": 1, "failed": 2
        }
        r = _read_report(tmp_path, [cyc])
        assert r["success_rate"]["stage3"] != 100.0


class TestPrepFailureReachesTheReport:
    """A prep that failed is a failed Stage 3, not an absent one.

    When prep errors, download and pipeline never run, so Stage 3 was N/A and the
    report showed the successful Stage 1/2 as 100% overall. Prep is the first
    Stage 3 sub-stage, so its failure has to count.
    """

    def _cycle(self, prep):
        return {
            "cycle_num": 1, "start_page": 1, "end_page": 5, "status": "failed_stage3",
            "start_time": "2026-07-24T10:00:00", "end_time": "2026-07-24T10:05:00",
            "stage3_requested": True,
            "stages": {
                "stage1": {"success": True, "statistics": {"meta_files": 10}},
                "stage2": {"success": True, "tables": {"tissue": {"rows": 5}}},
                "stage3_prep": prep,
            },
        }

    def test_prep_failure_is_a_stage3_failure(self, tmp_path):
        r = _read_report(tmp_path, [self._cycle({"success": False, "error": "disk full"})])
        assert r["success_rate"]["stage3"] == 0.0
        assert r["success_rate"]["overall"] != 100.0

    def test_prep_success_with_data_is_a_stage3_success(self, tmp_path):
        cyc = self._cycle({"success": True, "unique_srr_ids": 5, "gse_count": 1})
        cyc["stages"]["stage3_download"] = {"success": True, "total": 5, "downloaded": 5, "failed": 0}
        cyc["status"] = "success"
        r = _read_report(tmp_path, [cyc])
        assert r["success_rate"]["stage3"] == 100.0

    def test_prep_finding_no_ids_without_stage3_is_not_a_failure(self, tmp_path):
        # A clean prep over data with no SRR ids, on a run that never asked for
        # Stage 3, is not a Stage 3 failure; nothing was attempted beyond it. (A
        # run that *did* request Stage 3 ends as no_data and does count — see
        # TestNoDataIsAConsistentFailure.)
        cyc = self._cycle({"success": True, "unique_srr_ids": 0})
        cyc["status"] = "success"
        cyc["stage3_requested"] = False
        r = _read_report(tmp_path, [cyc])
        assert r["success_rate"]["stage3"] is None


class TestPrepOnlyIsNotStage3Success:
    """prep runs on every cycle, so a successful prep alone is not a Stage 3.

    Including prep in the Stage 3 tally caught prep *failures*, but also made a
    Stage-1+2 run (which never asked for Stage 3) report Stage 3 at 100%. Stage 3
    is only attempted when the run requested it.
    """

    def test_stage1_2_run_without_stage3_is_na(self, tmp_path):
        cyc = {
            "cycle_num": 1, "start_page": 1, "end_page": 5, "status": "success",
            "stage3_requested": False,
            "stages": {
                "stage1": {"success": True, "statistics": {"meta_files": 5}},
                "stage2": {"success": True, "tables": {"tissue": {"rows": 3}}},
                "stage3_prep": {"success": True, "unique_srr_ids": 0},
            },
        }
        r = _read_report(tmp_path, [cyc])
        # Stage 3 was not attempted, so it is N/A, not 100%.
        assert r["success_rate"]["stage3"] is None
        assert r["success_rate"]["overall"] == 100.0

    def test_no_data_run_is_not_stage3_100(self, tmp_path):
        cyc = {
            "cycle_num": 1, "start_page": 1, "end_page": 5, "status": "no_data",
            "stage3_requested": True,
            "stages": {
                "stage1": {"success": True, "skipped": True},
                "stage2": {"success": True, "skipped": True},
                "stage3_prep": {"success": True, "unique_srr_ids": 0},
            },
        }
        r = _read_report(tmp_path, [cyc])
        # Stage 3 was requested but nothing ran; it must not read as 100% success.
        assert r["success_rate"]["stage3"] != 100.0

    def test_prep_failure_when_requested_is_a_stage3_failure(self, tmp_path):
        cyc = {
            "cycle_num": 1, "start_page": 1, "end_page": 5, "status": "failed_stage3",
            "stage3_requested": True,
            "stages": {
                "stage1": {"success": True, "statistics": {"meta_files": 5}},
                "stage2": {"success": True, "tables": {"tissue": {"rows": 3}}},
                "stage3_prep": {"success": False, "error": "disk full"},
            },
        }
        r = _read_report(tmp_path, [cyc])
        assert r["success_rate"]["stage3"] == 0.0

    def test_a_real_stage3_run_still_counts(self, tmp_path):
        cyc = _stage3_only_cycle()
        cyc["stage3_requested"] = True
        r = _read_report(tmp_path, [cyc])
        assert r["success_rate"]["stage3"] == 100.0


class TestNoDataIsAConsistentFailure:
    """A requested Stage 3 that found no data is one verdict, not two.

    The orchestrator already treats no_data as a non-success: console icon ✗ and
    process exit 1. The final report must not disagree. Leaving Stage 3 N/A and
    Overall at 100 -- what an uncounted clean zero-id prep produces -- shows
    automation a failure while the operator sees 100%. So no_data counts as a
    failed Stage 3 attempt and the whole status/rate/exit contract lines up.
    """

    def _no_data_cycle(self):
        return {
            "cycle_num": 1, "start_page": 1, "end_page": 5, "status": "no_data",
            "start_time": "2026-07-26T10:00:00", "end_time": "2026-07-26T10:05:00",
            "stage3_requested": True,
            "stages": {
                "stage1": {"success": True, "statistics": {"meta_files": 5}},
                "stage2": {"success": True, "tables": {"tissue": {"rows": 3}}},
                "stage3_prep": {"success": True, "unique_srr_ids": 0, "gse_count": 2},
            },
        }

    def test_the_full_contract_agrees(self, tmp_path, capsys):
        from cycle_test.run_cycle_test import _exit_code_for

        cyc = self._no_data_cycle()
        r = _read_report(tmp_path, [cyc])
        out = capsys.readouterr().out

        # Stage 1 and 2 really ran and passed; Stage 3 was requested but produced
        # nothing, so it is a failed attempt, not N/A.
        assert r["success_rate"]["stage1"] == 100.0
        assert r["success_rate"]["stage2"] == 100.0
        assert r["success_rate"]["stage3"] == 0.0
        # 2 of 3 attempts across the three stages succeeded.
        assert r["success_rate"]["overall"] == 66.7
        assert r["success_rate"]["overall"] != 100.0
        # The console marks the cycle as a failure, matching the exit code.
        assert "✗ no_data" in out
        assert _exit_code_for([cyc]) == 1

    def test_no_data_alone_is_zero_not_na(self, tmp_path):
        r = _read_report(tmp_path, [self._no_data_cycle()])
        assert r["success_rate"]["stage3"] == 0.0
