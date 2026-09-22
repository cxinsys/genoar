"""Rotation: what a completed sample leaves on the disk, and on the record.

A run that cannot delete anything can only process as many samples as the
filesystem holds at once. On a shared facility that is far fewer than a corpus,
so a long run has to rotate: analyse, keep what the analysis was for, release
the rest, take the next batch.

Releasing collides with how this pipeline decides anything. Completion is
proved by the BAM being where a run recorded writing it, and deleting the BAM
is therefore indistinguishable from losing it — which the verifier reports as a
failure, correctly. These pin the one thing that separates the two: the run
that removes the file writes down that it is doing so, into the receipt that
already vouches for the work.
"""

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_SH = REPO / "srr_pipeline_package" / "pipeline_next" / "run_docker_pipeline.sh"

BAM_REL = "cellranger_output/outs/possorted_genome_bam.bam"
RECEIPT = ".genoar_cellranger.json"
STARTED = 1700000000
FINGERPRINT = "sha256:aaaa"


def _provenance():
    """The shipped verifier, as the shell feeds it to python3, minus dispatch."""
    lines = RUN_SH.read_text().split("\n")
    start = 'python3 - "$@" << \'PY\''
    i = next(n for n, l in enumerate(lines) if l.strip() == start)
    j = next(n for n in range(i + 1, len(lines)) if lines[n] == "PY")
    body = "\n".join(lines[i + 1:j]) + "\n"
    body = body[:body.index("\nsub = sys.argv[1]")]
    mod = types.ModuleType("genoar_provenance")
    exec(compile(body, str(RUN_SH), "exec"), mod.__dict__)
    return mod


prov = _provenance()


class Site:
    """One run's worth of disk: an input dir, a success dir, a run dir."""

    def __init__(self, root: Path):
        self.root = root
        self.input = root / "sra"
        self.success = root / "success"
        self.run = root / "runs" / "run-A"
        for d in (self.input, self.success, self.run):
            d.mkdir(parents=True, exist_ok=True)
        self.manifest = self.run / "expected_samples.tsv"
        self._rows = []

    def add_sample(self, sample="SRR000001", status="fresh",
                   provenance="fresh", with_input=True, outs=None):
        """A sample as it looks after Cell Ranger and the verifier are done."""
        outs_dir = self.success / sample / "cellranger_output" / "outs"
        outs_dir.mkdir(parents=True, exist_ok=True)
        contents = outs if outs is not None else {
            "possorted_genome_bam.bam": b"BAM" * 400,
            "possorted_genome_bam.bam.bai": b"BAI",
            "filtered_feature_bc_matrix.h5": b"H5",
            "filtered_feature_bc_matrix": None,
            "raw_feature_bc_matrix": None,
            "molecule_info.h5": b"MOL" * 200,
            "metrics_summary.csv": b"metrics",
            "web_summary.html": b"<html>",
            "analysis": None,
        }
        for name, blob in contents.items():
            target = outs_dir / name
            if blob is None:
                target.mkdir(exist_ok=True)
                (target / "matrix.mtx.gz").write_bytes(b"MTX")
            else:
                target.write_bytes(blob)

        source = self.input / sample
        if with_input:
            source.mkdir(exist_ok=True)
            (source / (sample + ".sra")).write_bytes(b"INPUT" * 100)

        bam = self.success / sample / BAM_REL
        if bam.exists():
            st = bam.stat()
            receipt = {"sample": sample, "run_id": "run-A",
                       "provenance": provenance,
                       "input_fingerprint": FINGERPRINT,
                       "bam_size": st.st_size, "bam_mtime": int(st.st_mtime),
                       "completed_at": "2026-01-01T00:00:00Z"}
            (self.success / sample / RECEIPT).write_text(json.dumps(receipt))

        self._rows.append((sample, str(source), "500", FINGERPRINT))
        self._write_manifest()
        self._write_outcome(sample, status)
        return sample

    def _write_manifest(self):
        lines = ["# sample\tsource\tsize_bytes\tfingerprint"]
        lines += ["\t".join(row) for row in self._rows]
        self.manifest.write_text("\n".join(lines) + "\n")

    def _write_outcome(self, sample, status):
        path = self.run / "outcome.json"
        outcome = json.loads(path.read_text()) if path.exists() else {"samples": {}}
        outcome["samples"][sample] = {"sample": sample, "status": status}
        path.write_text(json.dumps(outcome))

    def release(self, policy="analysis", release_input="0", run_id="run-B"):
        """Release as a run of its own, which is how the ledger is found.

        cmd_release writes release.json under the run directory of the run that
        is doing the releasing, and the verifier looks it up by the id on the
        note. A fixture where the two disagree is a fixture that cannot reach
        the code being tested.
        """
        run_dir = self.root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "outcome.json").write_text(
            (self.run / "outcome.json").read_text())
        prov.cmd_release(str(self.manifest), str(self.success), run_id,
                         str(run_dir), policy, release_input, str(self.input))
        summary = run_dir / "release.json"
        # Absent when the run had nothing to release, which is a state worth
        # telling apart from an empty release.
        return json.loads(summary.read_text()) if summary.exists() else None

    def verify(self, run_id="run-C", adopt="0"):
        run_dir = self.root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        prov.cmd_verify(str(self.manifest), str(self.success), run_id,
                        str(STARTED), str(run_dir), adopt, str(self.input), "0")
        return json.loads((run_dir / "outcome.json").read_text())

    def outs(self, sample="SRR000001"):
        d = self.success / sample / "cellranger_output" / "outs"
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def receipt(self, sample="SRR000001"):
        return json.loads((self.success / sample / RECEIPT).read_text())


@pytest.fixture
def site(tmp_path):
    return Site(tmp_path)


class TestTheDefaultReleasesNothing:
    """A site opts into rotation. It is never opted in for them."""

    def test_retain_all_removes_nothing(self, site, capsys):
        site.add_sample()
        before = site.outs()
        site.release(policy="all")
        assert site.outs() == before
        assert not (site.run / "release.json").exists()
        assert "nothing to release" in capsys.readouterr().out

    def test_the_shipped_default_is_all(self):
        """The config key's default, read straight out of the orchestrator."""
        text = RUN_SH.read_text()
        assert 'RETAIN="$(yaml_get retain "all")"' in text
        assert 'RELEASE_INPUT_RAW="$(yaml_get release_input "false")"' in text

    def test_an_unknown_policy_is_refused(self, site):
        site.add_sample()
        with pytest.raises(SystemExit) as raised:
            site.release(policy="whatever")
        assert raised.value.code == 2
        assert site.outs(), "nothing may be removed under a policy we cannot read"


class TestWhatEachPolicyKeeps:

    def test_analysis_drops_the_bam_and_keeps_the_rest(self, site):
        site.add_sample()
        site.release(policy="analysis")
        kept = site.outs()
        assert "possorted_genome_bam.bam" not in kept
        assert "possorted_genome_bam.bam.bai" not in kept
        for name in ("filtered_feature_bc_matrix", "raw_feature_bc_matrix",
                     "molecule_info.h5", "metrics_summary.csv",
                     "web_summary.html", "analysis"):
            assert name in kept, f"{name} is not the BAM and must survive"

    def test_summary_keeps_only_what_leaves_the_cluster(self, site):
        site.add_sample()
        site.release(policy="summary")
        assert site.outs() == ["filtered_feature_bc_matrix",
                               "filtered_feature_bc_matrix.h5",
                               "metrics_summary.csv", "web_summary.html"]

    def test_summary_keeps_a_whitelist_not_a_blacklist(self, site):
        """A Cell Ranger release adding an output must not widen `summary`."""
        site.add_sample()
        extra = (site.success / "SRR000001" / "cellranger_output" / "outs"
                 / "something_new_in_9.0.h5")
        extra.write_bytes(b"NEW")
        site.release(policy="summary")
        assert "something_new_in_9.0.h5" not in site.outs()

    def test_the_bytes_freed_are_reported(self, site):
        site.add_sample()
        summary = site.release(policy="analysis")
        assert summary["bytes_freed"] == len(b"BAM" * 400) + len(b"BAI")


class TestReleasedIsStillCompleted:
    """The question `completed` answers is whether the sample was analysed."""

    def test_a_released_sample_verifies_as_released(self, site):
        site.add_sample()
        site.release(policy="analysis")
        outcome = site.verify()
        rec = outcome["samples"]["SRR000001"]
        assert rec["status"] == "released"
        assert outcome["counts"]["completed"] == 1
        assert outcome["counts"]["released"] == 1

    def test_the_reason_names_who_analysed_and_who_released(self, site):
        site.add_sample()
        site.release(policy="analysis")
        reason = site.verify()["samples"]["SRR000001"]["reason"]
        assert "run-A" in reason and "run-B" in reason and "analysis" in reason

    def test_a_bam_that_is_merely_gone_is_still_missing(self, site):
        """Absence is not evidence. Deleting everything must not pass."""
        site.add_sample()
        (site.success / "SRR000001" / BAM_REL).unlink()
        outcome = site.verify()
        assert outcome["samples"]["SRR000001"]["status"] == "missing"
        assert outcome["counts"]["completed"] == 0

    def test_a_release_note_for_another_input_does_not_count(self, site):
        site.add_sample()
        site.release(policy="analysis")
        receipt = site.receipt()
        receipt["input_fingerprint"] = "sha256:something-else"
        (site.success / "SRR000001" / RECEIPT).write_text(json.dumps(receipt))
        assert site.verify()["samples"]["SRR000001"]["status"] != "released"

    def test_an_interrupted_release_leaves_a_verifiable_sample(self, site):
        """The receipt is written first, so this is the only half-way state.

        A note saying "released" beside a BAM that is still there reads as the
        ordinary completion it is. The other order would leave a deleted file
        with nothing to explain it.
        """
        site.add_sample()
        receipt = site.receipt()
        receipt["released"] = {"policy": "analysis", "by_run_id": "run-B",
                               "at": "2026-01-01T00:00:00Z", "removed": [],
                               "bytes_freed": 0, "input_released": False}
        (site.success / "SRR000001" / RECEIPT).write_text(json.dumps(receipt))
        assert site.verify()["samples"]["SRR000001"]["status"] == "cache_hit"


class TestOnlyVerifiedWorkIsReleased:

    def test_adopted_output_is_never_released(self, site):
        """Adoption was never verification, and deletion cannot promote it."""
        site.add_sample(status="adopted", provenance="adopted")
        summary = site.release(policy="analysis")
        assert summary["samples"] == []
        assert "possorted_genome_bam.bam" in site.outs()

    def test_a_sample_that_did_not_complete_keeps_everything(self, site):
        """What a re-run needs is exactly what an incomplete sample still has."""
        site.add_sample(status="unverified")
        site.release(policy="summary")
        assert "possorted_genome_bam.bam" in site.outs()

    def test_output_with_no_receipt_is_left_alone(self, site):
        site.add_sample()
        (site.success / "SRR000001" / RECEIPT).unlink()
        summary = site.release(policy="analysis")
        assert summary["samples"] == []
        assert any(entry["why"] == "no verified receipt to amend"
                   for entry in summary["not_released"])


class TestReleasingTheInput:

    def test_the_samples_input_goes(self, site):
        sample = site.add_sample()
        site.release(policy="all", release_input="1")
        assert not (site.input / sample).exists()

    def test_keeping_every_output_writes_no_release_note(self, site):
        """The note is a claim about the BAM, and no BAM went.

        `retain: all` with `release_input: true` frees the input and leaves
        every output where it is, so the verifier reads the sample exactly as
        it did before.
        """
        site.add_sample()
        site.release(policy="all", release_input="1")
        assert "released" not in site.receipt()
        assert site.verify()["samples"]["SRR000001"]["status"] == "cache_hit"

    def test_the_output_is_untouched_when_only_the_input_is_released(self, site):
        site.add_sample()
        site.release(policy="all", release_input="1")
        assert "possorted_genome_bam.bam" in site.outs()

    def test_an_input_outside_the_input_directory_is_refused(self, site, tmp_path):
        """The manifest is a file on disk; a path out of the tree is not obeyed."""
        outsider = tmp_path / "not_input" / "SRR000001"
        outsider.mkdir(parents=True)
        (outsider / "SRR000001.sra").write_bytes(b"ELSEWHERE")
        site.add_sample(with_input=False)
        site._rows = [("SRR000001", str(outsider), "500", FINGERPRINT)]
        site._write_manifest()
        site.release(policy="all", release_input="1")
        assert outsider.exists(), "a release must not follow a path out of the tree"

    def test_the_input_stays_when_release_input_is_off(self, site):
        sample = site.add_sample()
        site.release(policy="analysis", release_input="0")
        assert (site.input / sample).exists()


class TestAReleaseNoteHasToBeOneThisPipelineWrote:
    """`released` is the one completion with no artefact left to check.

    Every other rung is anchored to a file the verifier can stat. This one has
    nothing on disk, so what stands in for the file is the description of it
    the verification wrote, plus a note shaped like one cmd_release produces.
    None of that makes a receipt unforgeable — it is a file, and a site that can
    write files can write receipts. What it does is stop a sample that was
    never analysed from being credited by one trivial hand-written object.
    """

    def _released(self, site, note, receipt_changes=None):
        site.add_sample()
        receipt = site.receipt()
        receipt.update(receipt_changes or {})
        if note is not None:
            receipt["released"] = note
        (site.success / "SRR000001" / RECEIPT).write_text(json.dumps(receipt))
        (site.success / "SRR000001" / BAM_REL).unlink()
        return site.verify()["samples"]["SRR000001"]["status"]

    def test_an_empty_note_credits_nothing(self, site):
        assert self._released(site, {}) == "missing"

    def test_a_note_naming_no_removal_credits_nothing(self, site):
        note = {"kind": "genoar.retention.release", "policy": "analysis",
                "by_run_id": "run-B", "at": "2026-01-01T00:00:00Z",
                "removed": [], "input_released": False}
        assert self._released(site, note) == "missing"

    def test_a_note_with_the_wrong_kind_credits_nothing(self, site):
        note = {"kind": "something.else", "policy": "analysis",
                "by_run_id": "run-B", "at": "2026-01-01T00:00:00Z",
                "removed": ["possorted_genome_bam.bam"]}
        assert self._released(site, note) == "missing"

    def test_a_receipt_that_never_described_a_bam_credits_nothing(self, site):
        """bam_size and bam_mtime are what a real verification always wrote."""
        note = {"kind": "genoar.retention.release", "policy": "analysis",
                "by_run_id": "run-B", "at": "2026-01-01T00:00:00Z",
                "removed": ["possorted_genome_bam.bam"]}
        status = self._released(site, note,
                                receipt_changes={"bam_size": None,
                                                 "bam_mtime": None})
        assert status == "missing"

    def test_a_complete_note_from_a_real_release_does_credit(self, site):
        """The control: the same path, with a note cmd_release actually wrote."""
        site.add_sample()
        site.release(policy="analysis")
        assert site.verify()["samples"]["SRR000001"]["status"] == "released"


class TestNothingIsDeletedOutsideTheTree:

    def _outside(self, tmp_path):
        outside = tmp_path / "elsewhere"
        outside.mkdir(exist_ok=True)
        (outside / "irreplaceable.txt").write_bytes(b"KEEP")
        return outside

    def test_a_symlinked_cellranger_output_is_refused(self, site, tmp_path):
        """Results symlinked onto a bigger filesystem is routine on a cluster."""
        outside = self._outside(tmp_path)
        site.add_sample()
        real = site.success / "SRR000001" / "cellranger_output"
        import shutil as _shutil
        _shutil.rmtree(real)
        (outside / "outs").mkdir(exist_ok=True)
        (outside / "outs" / "metrics_summary.csv").write_bytes(b"m")
        real.symlink_to(outside)
        summary = site.release(policy="summary")
        assert (outside / "irreplaceable.txt").exists()
        assert summary["samples"] == []
        assert any("does not resolve inside" in entry["why"]
                   for entry in summary["not_released"])

    def test_a_symlinked_outs_is_refused(self, site, tmp_path):
        outside = self._outside(tmp_path)
        site.add_sample()
        outs = site.success / "SRR000001" / "cellranger_output" / "outs"
        import shutil as _shutil
        _shutil.rmtree(outs)
        outs.symlink_to(outside)
        summary = site.release(policy="summary")
        assert (outside / "irreplaceable.txt").exists()
        assert any("does not resolve inside" in entry["why"]
                   for entry in summary["not_released"])

    def test_a_link_inside_outs_takes_only_the_link(self, site, tmp_path):
        outside = self._outside(tmp_path)
        site.add_sample()
        outs = site.success / "SRR000001" / "cellranger_output" / "outs"
        link = outs / "linked_away"
        link.symlink_to(outside)
        site.release(policy="summary")
        assert not link.exists() and not link.is_symlink()
        assert (outside / "irreplaceable.txt").exists()

    def test_a_symlinked_input_is_not_followed(self, site, tmp_path):
        """The input is derived from the sample name, and then resolved.

        cmd_release never reads the manifest's source column -- a path that
        arrives as data is not a path to delete -- so what has to be tested is
        the derived path resolving somewhere it should not.
        """
        outside = self._outside(tmp_path)
        sample = site.add_sample(with_input=False)
        (site.input / sample).symlink_to(outside)
        site.release(policy="all", release_input="1")
        assert (outside / "irreplaceable.txt").exists()
        assert outside.is_dir()


class TestOnlyALostBamIsAReleasedBam:
    """`released` says the BAM was released, so it has to have been."""

    def test_an_input_only_release_does_not_launder_a_lost_bam(self, site):
        """`retain: all` with `release_input: true` may not write a real note.

        It removes nothing from outs/, so a note claiming it did is something
        any later, ordinary loss of the BAM could hide behind.
        """
        site.add_sample()
        site.release(policy="all", release_input="1")
        (site.success / "SRR000001" / BAM_REL).unlink()
        assert site.verify()["samples"]["SRR000001"]["status"] == "missing"

    def test_a_summary_release_does_credit(self, site):
        """The control: summary removes the BAM, so the claim holds."""
        site.add_sample()
        site.release(policy="summary")
        assert site.verify()["samples"]["SRR000001"]["status"] == "released"


class TestTheReadsGoToo:
    """Step 5 copies the whole sample in, so the outputs are not the big pile."""

    def _with_reads(self, site, sample="SRR000001"):
        site.add_sample(sample)
        sample_dir = site.success / sample
        (sample_dir / (sample + ".sra")).write_bytes(b"SRA" * 500)
        (sample_dir / (sample + "_S1_L001_R1_001.fastq.gz")).write_bytes(b"R1" * 500)
        (sample_dir / (sample + "_S1_L001_R2_001.fastq.gz")).write_bytes(b"R2" * 500)
        return sample_dir

    def test_analysis_releases_the_copied_reads(self, site):
        sample_dir = self._with_reads(site)
        site.release(policy="analysis")
        left = sorted(p.name for p in sample_dir.iterdir())
        assert not any(n.endswith((".sra", ".fastq.gz")) for n in left), left

    def test_retain_all_leaves_them(self, site):
        sample_dir = self._with_reads(site)
        site.release(policy="all", release_input="1")
        assert (sample_dir / "SRR000001.sra").exists()

    def test_the_note_says_which_reads_went(self, site):
        self._with_reads(site)
        site.release(policy="analysis")
        assert sorted(site.receipt()["released"]["reads_released"]) == [
            "SRR000001.sra", "SRR000001_S1_L001_R1_001.fastq.gz",
            "SRR000001_S1_L001_R2_001.fastq.gz"]

    def test_nothing_under_cellranger_output_is_matched_by_the_read_rule(self, site):
        sample_dir = self._with_reads(site)
        deep = sample_dir / "cellranger_output" / "outs" / "keep_me.fastq.gz"
        deep.write_bytes(b"x")
        site.release(policy="analysis")
        assert deep.exists(), "the read rule is top level only"


class TestNothingIsDeletedOnAPromiseThatFailed:

    def test_a_receipt_that_cannot_be_written_stops_the_deletion(self, site):
        """A failing filesystem is exactly when a run is releasing space."""
        site.add_sample()
        sample_dir = site.success / "SRR000001"
        mode = sample_dir.stat().st_mode
        sample_dir.chmod(0o555)
        try:
            summary = site.release(policy="analysis")
        finally:
            sample_dir.chmod(mode)
        assert summary["samples"] == []
        assert (site.success / "SRR000001" / BAM_REL).exists()
        assert any("receipt could not be written" in entry["why"]
                   for entry in summary["not_released"])

    def test_a_sibling_symlink_cannot_reach_another_samples_output(self, site):
        """Both are inside the results tree, so the tree is not the boundary."""
        site.add_sample("SRR000001")
        # Not eligible on its own, so anything that happens to it came through
        # the link rather than through its own release.
        site.add_sample("SRR000002", status="unverified")
        import shutil as _shutil
        victim = site.success / "SRR000002" / "cellranger_output"
        attacker = site.success / "SRR000001" / "cellranger_output"
        _shutil.rmtree(attacker)
        attacker.symlink_to(victim)
        site.release(policy="summary")
        assert (site.success / "SRR000002" / BAM_REL).exists()


class TestOnlyADeletionThatHappenedIsARelease:
    """The ledger records what went, not what was meant to go."""

    def _bam_undeletable(self, site):
        outs = site.success / "SRR000001" / "cellranger_output" / "outs"
        mode = outs.stat().st_mode
        outs.chmod(0o555)
        return outs, mode

    def test_a_failed_deletion_is_not_in_the_ledger(self, site):
        site.add_sample()
        outs, mode = self._bam_undeletable(site)
        try:
            summary = site.release(policy="analysis")
        finally:
            outs.chmod(mode)
        assert summary["samples"] == []
        assert (site.success / "SRR000001" / BAM_REL).exists()

    def test_a_failed_deletion_takes_the_note_back_off_the_receipt(self, site):
        site.add_sample()
        outs, mode = self._bam_undeletable(site)
        try:
            site.release(policy="analysis")
        finally:
            outs.chmod(mode)
        assert "released" not in site.receipt()

    def test_a_bam_that_survived_and_is_later_lost_is_not_laundered(self, site):
        """The hole the `removed` check closed, arriving by the other door.

        The release intended to remove the BAM and failed. If that intent had
        stayed on the record, an ordinary loss of the file afterwards would
        have been read as this release.
        """
        site.add_sample()
        outs, mode = self._bam_undeletable(site)
        try:
            site.release(policy="analysis")
        finally:
            outs.chmod(mode)
        (site.success / "SRR000001" / BAM_REL).unlink()
        assert site.verify()["samples"]["SRR000001"]["status"] == "missing"


class TestTheInputDeletedIsTheSamplesOwn:
    """A path that arrives as data is not a path to delete."""

    def test_the_manifest_cannot_name_what_gets_deleted(self, site):
        """SRR1's row pointing at SRR2's directory must not delete SRR2's.

        The input is built from the sample's own name, so whatever the manifest
        says is beside the point -- which is the property to hold.
        """
        site.add_sample("SRR000001")
        other = site.input / "SRR000002"
        other.mkdir()
        (other / "SRR000002.sra").write_bytes(b"NEIGHBOUR")
        site._rows = [("SRR000001", str(other), "500", FINGERPRINT)]
        site._write_manifest()
        site.release(policy="all", release_input="1")
        assert (other / "SRR000002.sra").exists()
        assert not (site.input / "SRR000001").exists()

    def test_a_bare_sra_input_is_found_too(self, site):
        sample = site.add_sample(with_input=False)
        (site.input / (sample + ".sra")).write_bytes(b"INPUT")
        site.release(policy="all", release_input="1")
        assert not (site.input / (sample + ".sra")).exists()

    def test_the_derivation_itself_refuses_to_leave_the_tree(self, site):
        """sample_inputs is the whole of it, so it is tested directly."""
        assert prov.sample_inputs(str(site.input), "../escape") == []
        assert prov.sample_inputs(str(site.input), "/etc") == []


class TestAPolicyThatKeepsThingsHasToFindThem:

    def test_summary_will_not_strip_a_sample_that_has_no_summary(self, site):
        """An interrupted Cell Ranger leaves a BAM and little else.

        Nothing in the keep list matched, so everything counted as droppable:
        the BAM went, the sample was left with no outputs at all, and it was
        still reported complete.
        """
        site.add_sample(outs={"possorted_genome_bam.bam": b"BAM" * 100,
                              "possorted_genome_bam.bam.bai": b"BAI"})
        summary = site.release(policy="summary")
        assert summary["samples"] == []
        assert (site.success / "SRR000001" / BAM_REL).exists()
        assert any("are not there" in entry["why"]
                   for entry in summary["not_released"])

    def test_the_input_is_not_released_either_in_that_case(self, site):
        sample = site.add_sample(
            outs={"possorted_genome_bam.bam": b"BAM" * 100})
        site.release(policy="summary", release_input="1")
        assert (site.input / sample).exists()

    def test_a_complete_output_directory_is_released_normally(self, site):
        site.add_sample()
        summary = site.release(policy="summary")
        assert summary["samples"] == ["SRR000001"]


class TestWhatAHalfFinishedDeletionLeaves:
    """The order is chosen for the state a failure part way through leaves."""

    def _blocked(self, site, name):
        """Make one entry under outs/ undeletable, by its own directory."""
        outs = site.success / "SRR000001" / "cellranger_output" / "outs"
        guard = outs / "guarded"
        guard.mkdir()
        (guard / name).write_bytes(b"x")
        return outs, guard

    def test_the_bam_is_the_last_output_to_go(self, site):
        """It is the BAM's absence the `released` rung reads.

        Removing it first meant an error on any later entry left the sample
        with its BAM gone and its release cancelled: missing, permanently.
        """
        site.add_sample()
        outs = site.success / "SRR000001" / "cellranger_output" / "outs"
        stubborn = outs / "unremovable"
        stubborn.mkdir()
        (stubborn / "inside").write_bytes(b"x")
        mode = stubborn.stat().st_mode
        stubborn.chmod(0o555)
        try:
            summary = site.release(policy="summary")
        finally:
            stubborn.chmod(mode)
        assert (site.success / "SRR000001" / BAM_REL).exists(), (
            "the BAM must survive a failure on anything else")
        assert summary["samples"] == []

    def test_the_input_is_never_released_before_the_output(self, site):
        sample = site.add_sample()
        outs = site.success / sample / "cellranger_output" / "outs"
        stubborn = outs / "unremovable"
        stubborn.mkdir()
        (stubborn / "inside").write_bytes(b"x")
        mode = stubborn.stat().st_mode
        stubborn.chmod(0o555)
        try:
            site.release(policy="summary", release_input="1")
        finally:
            stubborn.chmod(mode)
        assert (site.input / sample).exists(), (
            "the input is the only thing a re-run could rebuild from")

    def test_an_incomplete_rotation_is_marked_for_the_job(self, site):
        """The next task in the array meets the disk this did not free."""
        site.add_sample()
        outs = site.success / "SRR000001" / "cellranger_output" / "outs"
        stubborn = outs / "unremovable"
        stubborn.mkdir()
        (stubborn / "inside").write_bytes(b"x")
        mode = stubborn.stat().st_mode
        stubborn.chmod(0o555)
        try:
            site.release(policy="summary")
        finally:
            stubborn.chmod(mode)
        assert (site.root / "runs" / "run-B" / "retention_incomplete").is_file()

    def test_a_clean_rotation_leaves_no_mark(self, site):
        site.add_sample()
        site.release(policy="summary")
        assert not (site.root / "runs" / "run-B" / "retention_incomplete").exists()


class TestAnOutputThatIsThereAndEmptyIsNotAnOutput:

    def test_a_zero_byte_summary_does_not_license_the_deletion(self, site):
        site.add_sample(outs={"possorted_genome_bam.bam": b"BAM" * 100,
                              "filtered_feature_bc_matrix.h5": b"",
                              "filtered_feature_bc_matrix": None,
                              "metrics_summary.csv": b"",
                              "web_summary.html": b""})
        summary = site.release(policy="summary")
        assert summary["samples"] == []
        assert (site.success / "SRR000001" / BAM_REL).exists()

    def test_an_empty_matrix_directory_does_not_either(self, site):
        site.add_sample()
        matrix = (site.success / "SRR000001" / "cellranger_output" / "outs"
                  / "filtered_feature_bc_matrix")
        for child in matrix.iterdir():
            child.unlink()
        summary = site.release(policy="summary")
        assert summary["samples"] == []


class TestTheLedgerIsWrittenOnceAtTheEnd:
    """Intent and evidence are different files, because they mean different things."""

    def test_the_intent_is_not_read_as_evidence(self, site):
        site.add_sample()
        site.release(policy="analysis")
        run = site.root / "runs" / "run-B"
        assert (run / "release_intent.json").is_file()
        run.joinpath("release.json").unlink()
        # The intent still names the sample; only the committed ledger counts.
        assert site.verify()["samples"]["SRR000001"]["status"] == "missing"

    def test_a_run_killed_before_committing_credits_nothing(self, site):
        """Receipt amended, file not yet deleted, process gone.

        The BAM is still there, so this run reads normally. What must not
        happen is the next ordinary loss of that file being read as a release.
        """
        site.add_sample()
        site.release(policy="analysis")
        (site.root / "runs" / "run-B" / "release.json").unlink()
        (site.success / "SRR000001" / BAM_REL).write_bytes(b"BAM" * 400)
        assert site.verify()["samples"]["SRR000001"]["status"] in (
            "cache_hit", "unverified")
        (site.success / "SRR000001" / BAM_REL).unlink()
        assert site.verify()["samples"]["SRR000001"]["status"] == "missing"

    def test_the_committed_ledger_names_only_what_went(self, site):
        site.add_sample("SRR000001")
        site.add_sample("SRR000002", status="unverified")
        summary = site.release(policy="analysis")
        assert summary["samples"] == ["SRR000001"]


class TestReuseHasToBeReuseOfTheSameMeasurement:
    """Same input, different Cell Ranger or reference, is a different answer."""

    def _environment(self, site, run_id, cellranger, reference):
        run = site.root / "runs" / run_id
        run.mkdir(parents=True, exist_ok=True)
        (run / "environment.json").write_text(json.dumps(
            {"kind": "genoar.environment", "cellranger_version": cellranger,
             "reference": reference}))

    def _receipt_environment(self, site, cellranger, reference, sample="SRR000001"):
        path = site.success / sample / RECEIPT
        receipt = json.loads(path.read_text())
        receipt["environment"] = {"cellranger_version": cellranger,
                                  "reference": reference}
        path.write_text(json.dumps(receipt))

    def test_a_cache_hit_from_another_cell_ranger_is_stale(self, site):
        site.add_sample()
        self._receipt_environment(site, "7.0.1", "GRCh38 2020-A")
        self._environment(site, "run-C", "8.0.1", "GRCh38 2024-A")
        rec = site.verify()["samples"]["SRR000001"]
        assert rec["status"] == "stale"
        assert "7.0.1" in rec["reason"] and "8.0.1" in rec["reason"]

    def test_a_cache_hit_from_another_reference_is_stale(self, site):
        site.add_sample()
        self._receipt_environment(site, "8.0.1", "GRCh38 2020-A")
        self._environment(site, "run-C", "8.0.1", "GRCh38 2024-A")
        assert site.verify()["samples"]["SRR000001"]["status"] == "stale"

    def test_the_same_environment_is_still_a_cache_hit(self, site):
        site.add_sample()
        self._receipt_environment(site, "8.0.1", "GRCh38 2024-A")
        self._environment(site, "run-C", "8.0.1", "GRCh38 2024-A")
        assert site.verify()["samples"]["SRR000001"]["status"] == "cache_hit"

    def test_a_receipt_with_nothing_recorded_says_so(self, site):
        """Output from before this existed is reused, and the log admits why."""
        site.add_sample()
        self._environment(site, "run-C", "8.0.1", "GRCh38 2024-A")
        rec = site.verify()["samples"]["SRR000001"]
        assert rec["status"] == "cache_hit"
        assert "could not be compared" in rec["reason"]

    def test_a_released_sample_from_another_environment_is_stale(self, site):
        """Its output is gone, so there is nothing here for this environment."""
        site.add_sample()
        site.release(policy="analysis")
        self._receipt_environment(site, "7.0.1", "GRCh38 2020-A")
        self._environment(site, "run-C", "8.0.1", "GRCh38 2024-A")
        rec = site.verify()["samples"]["SRR000001"]
        assert rec["status"] == "stale"
        assert "released" in rec["reason"]

    def test_a_released_sample_in_the_same_environment_still_counts(self, site):
        site.add_sample()
        site.release(policy="analysis")
        self._receipt_environment(site, "8.0.1", "GRCh38 2024-A")
        self._environment(site, "run-C", "8.0.1", "GRCh38 2024-A")
        assert site.verify()["samples"]["SRR000001"]["status"] == "released"
