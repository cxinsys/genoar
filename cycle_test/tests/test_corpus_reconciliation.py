"""Seventy clean reports and a corpus that lost samples anyway.

Each array task reports against its own manifest, so nothing that reads one
outcome can see a batch nobody submitted, a task killed before it wrote
anything, or a stale list that ran the same samples twice. The plan and every
outcome have to be read together, and the answer that matters is not a sum but
a reconciliation: which planned samples nothing has anything to say about.
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HPC = REPO / "srr_pipeline_package" / "hpc"
sys.path.insert(0, str(HPC))

import corpus_report as cr  # noqa: E402


class Corpus:
    def __init__(self, tmp_path, batches):
        self.plan = tmp_path / "plan"
        self.plan.mkdir(parents=True)
        for index, samples in enumerate(batches, start=1):
            (self.plan / ("batch_%03d.txt" % index)).write_text(
                "\n".join(samples) + "\n")
        self.results = tmp_path / "results"
        self.results.mkdir()

    def outcome(self, batch, run_id, statuses, reasons=None, finished_at=None):
        run = self.results / ("batch_%d" % batch) / "runs" / run_id
        run.mkdir(parents=True)
        reasons = reasons or {}
        (run / "outcome.json").write_text(json.dumps({
            "run_id": run_id,
            "finished_at": finished_at or "",
            "samples": {s: {"sample": s, "status": st,
                            "reason": reasons.get(s, "")}
                        for s, st in statuses.items()}}))

    def run(self, run_id="run"):
        planned, count, _, unlisted = cr.planned_samples(self.plan)
        result = cr.reconcile(planned, cr.outcomes(self.results, run_id))
        result["unlisted_by_plan"] = unlisted
        out = io.StringIO()
        cr.report(planned, count, result, out)
        return result, out.getvalue()

    def cli(self, extra=()):
        return subprocess.run(
            [sys.executable, str(HPC / "corpus_report.py"),
             "--plan", str(self.plan), "--results", str(self.results),
             "--run-id", "run"]
            + list(extra), capture_output=True, text=True)


@pytest.fixture
def corpus(tmp_path):
    return Corpus(tmp_path, [["SRR1", "SRR2"], ["SRR3", "SRR4"]])


class TestWhenEverythingWorked:

    def test_all_complete(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "cache_hit"})
        result, text = corpus.run()
        assert len(result["complete"]) == 4
        assert result["unaccounted"] == []
        assert "Every planned sample is accounted for" in text

    def test_released_counts_as_complete(self, corpus):
        """The work was done and verified; the artefact was let go after."""
        corpus.outcome(1, "run-b1", {"SRR1": "released", "SRR2": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"})
        result, _ = corpus.run()
        assert len(result["complete"]) == 4

    def test_the_exit_code_says_so(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"})
        assert corpus.cli().returncode == 0


class TestTheBatchNobodySubmitted:
    """The failure no single outcome can show: nothing failed, nothing ran."""

    def test_a_missing_batch_is_unaccounted_not_complete(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        result, text = corpus.run()
        assert sorted(result["unaccounted"]) == ["SRR3", "SRR4"]
        assert len(result["complete"]) == 2
        assert "nothing failed, because nothing ran" in text

    def test_it_names_the_batch_to_re_run(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        _, text = corpus.run()
        assert "batch 002" in text

    def test_a_partial_corpus_exits_four(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        assert corpus.cli().returncode == 4

    def test_every_batch_missing_is_not_a_success(self, corpus):
        assert corpus.cli().returncode == 3


class TestWhatTheRunsRefused:

    def test_an_uncredited_sample_carries_the_reason_the_run_gave(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "missing"},
                       reasons={"SRR2": "no Cell Ranger output"})
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"})
        result, text = corpus.run()
        assert result["incomplete"]["SRR2"][0] == "missing"
        assert "no Cell Ranger output" in text

    def test_adopted_is_not_complete(self, corpus):
        """Adoption is the operator's word, not evidence."""
        corpus.outcome(1, "run-b1", {"SRR1": "adopted", "SRR2": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"})
        result, _ = corpus.run()
        assert "SRR1" in result["incomplete"]
        assert len(result["complete"]) == 3

    def test_a_later_run_that_completes_it_wins(self, corpus):
        """A re-run of a failed batch is how this is meant to be fixed."""
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "missing"})
        corpus.outcome(1, "run-b1-retry", {"SRR1": "cache_hit", "SRR2": "fresh"})
        result, _ = corpus.run()
        assert len(result["complete"]) == 2
        assert result["incomplete"] == {}


class TestTheSameWorkDoneTwice:

    def test_two_runs_crediting_one_sample_are_reported(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR1": "fresh", "SRR3": "fresh",
                                     "SRR4": "fresh"})
        result, text = corpus.run()
        assert result["duplicated"] == ["SRR1"]
        assert "queue time spent twice" in text

    def test_a_duplicate_is_not_a_finished_corpus(self, corpus):
        """Exact-once is the claim, so work done twice is a failed claim."""
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR1": "fresh", "SRR3": "fresh",
                                     "SRR4": "fresh"})
        assert corpus.cli().returncode == 4

    def test_work_credited_that_was_never_planned_is_reported(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh",
                                     "SRR_STRANGER": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"})
        result, text = corpus.run()
        assert result["unplanned"] == ["SRR_STRANGER"]
        assert "not in this plan" in text


class TestReadingAnAwkwardTree:

    def test_outcomes_are_found_wherever_the_layout_put_them(self, tmp_path):
        """A single-job run writes results/runs/; an array writes batch_N/runs/."""
        corpus = Corpus(tmp_path, [["SRR1"]])
        run = corpus.results / "runs" / "run-A"
        run.mkdir(parents=True)
        (run / "outcome.json").write_text(json.dumps(
            {"run_id": "run-A", "samples": {"SRR1": {"status": "fresh"}}}))
        result, _ = corpus.run(run_id="run-A")
        assert len(result["complete"]) == 1

    def test_an_unreadable_outcome_is_skipped_not_fatal(self, corpus, capsys):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        broken = corpus.results / "batch_2" / "runs" / "run-b2"
        broken.mkdir(parents=True)
        (broken / "outcome.json").write_text("{not json")
        result, _ = corpus.run()
        assert sorted(result["unaccounted"]) == ["SRR3", "SRR4"]

    def test_a_directory_that_is_not_a_plan_is_refused(self, tmp_path):
        (tmp_path / "empty").mkdir()
        (tmp_path / "results").mkdir()
        done = subprocess.run(
            [sys.executable, str(HPC / "corpus_report.py"),
             "--plan", str(tmp_path / "empty"),
             "--results", str(tmp_path / "results"), "--run-id", "run"],
            capture_output=True, text=True)
        assert done.returncode != 0
        assert "not a plan directory" in done.stderr

    def test_a_missing_results_tree_is_a_configuration_error(self, corpus):
        done = subprocess.run(
            [sys.executable, str(HPC / "corpus_report.py"),
             "--plan", str(corpus.plan), "--results", "/nowhere/at/all",
             "--run-id", "run"],
            capture_output=True, text=True)
        assert done.returncode == 2

    def test_the_reconciliation_can_be_written_out(self, corpus, tmp_path):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        target = tmp_path / "corpus.json"
        corpus.cli(["--json", str(target)])
        payload = json.loads(target.read_text())
        assert payload["planned"] == 4
        assert payload["unaccounted"] == ["SRR3", "SRR4"]


class TestARetryIsNotADuplicate:
    """Reusing an earlier run's output is what makes re-running a batch safe."""

    def test_the_later_attempt_is_the_one_that_stands(self, corpus):
        """Ordered by the attempt in the run id, not by any node's clock.

        Array tasks land on different nodes, and with enough skew a failed
        first attempt's verdict overwrote the successful retry's.
        """
        corpus.outcome(1, "run-b1-100r0", {"SRR1": "fresh", "SRR2": "missing"},
                       finished_at="2026-01-09T00:00:00Z")
        corpus.outcome(1, "run-b1-100r1", {"SRR1": "cache_hit", "SRR2": "fresh"},
                       finished_at="2026-01-01T00:00:00Z")
        corpus.outcome(2, "run-b2-101r0", {"SRR3": "fresh", "SRR4": "fresh"})
        result, _ = corpus.run()
        assert len(result["complete"]) == 4, "a skewed clock must not win"

    def test_the_later_verdict_is_the_one_that_stands(self, corpus):
        """An earlier `fresh` must not hide a later `missing`.

        That is what a lost output looks like, and taking the first favourable
        verdict reported the corpus finished.
        """
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"},
                       finished_at="2026-01-01T00:00:00Z")
        corpus.outcome(1, "run-b1-later", {"SRR1": "missing", "SRR2": "fresh"},
                       finished_at="2026-01-02T00:00:00Z")
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"},
                       finished_at="2026-01-01T00:00:00Z")
        result, _ = corpus.run()
        assert "SRR1" in result["incomplete"]
        assert corpus.cli().returncode == 4

    def test_a_later_stale_is_not_hidden_either(self, corpus):
        """An environment that changed under a corpus looks exactly like this."""
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"},
                       finished_at="2026-01-01T00:00:00Z")
        corpus.outcome(1, "run-b1-later", {"SRR1": "stale", "SRR2": "fresh"},
                       finished_at="2026-01-02T00:00:00Z")
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"},
                       finished_at="2026-01-01T00:00:00Z")
        assert "SRR1" in corpus.run()[0]["incomplete"]

    def test_a_cache_hit_alongside_the_original_is_not_duplicate_work(self, corpus):
        """The normal shape of a retry: A was fine, B failed, run it again."""
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "missing"})
        corpus.outcome(1, "run-b1-retry", {"SRR1": "cache_hit", "SRR2": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"})
        result, text = corpus.run()
        assert result["duplicated"] == []
        assert len(result["complete"]) == 4
        assert "Every planned sample is accounted for" in text

    def test_a_successful_retry_exits_zero(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "missing"})
        corpus.outcome(1, "run-b1-retry", {"SRR1": "cache_hit", "SRR2": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"})
        assert corpus.cli().returncode == 0

    def test_a_released_sample_met_again_is_not_duplicate_work_either(self, corpus):
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        corpus.outcome(1, "run-b1-retry", {"SRR1": "released", "SRR2": "released"})
        corpus.outcome(2, "run-b2", {"SRR3": "fresh", "SRR4": "fresh"})
        assert corpus.run()[0]["duplicated"] == []

    def test_two_runs_that_both_analysed_it_still_count(self, corpus):
        """Two `fresh` claims is Cell Ranger run twice on the same sample."""
        corpus.outcome(1, "run-b1", {"SRR1": "fresh", "SRR2": "fresh"})
        corpus.outcome(2, "run-b2", {"SRR1": "fresh", "SRR3": "fresh",
                                     "SRR4": "fresh"})
        result, text = corpus.run()
        assert result["duplicated"] == ["SRR1"]
        assert "cannot be reported finished" in text
        assert "Every planned sample is accounted for" not in text
