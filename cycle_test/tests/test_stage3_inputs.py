"""Negative tests for Stage 3 inputs and download bookkeeping.

From the second re-validation: a stale SRR list from an earlier cycle could be
downloaded again as if current, an empty list counted as a successful download,
the progress file could be left half-written, and the strong file checks applied
only on the unknown-size path.
"""

import json
from pathlib import Path
from unittest import mock

from cycle_test.utils.stage3_downloader import (
    S3SRADownloader,
    MIN_SRA_SIZE,
    verify_and_promote,
)


class TestStaleSrrList:
    """A prep that finds nothing must not leave the previous run's list behind."""

    def _prep(self, tmp_path, stage2_rows=None):
        from cycle_test.utils.stage3_preparer import extract_srr_from_stage2_tables

        stage2 = tmp_path / "stage2"
        stage2.mkdir(parents=True, exist_ok=True)
        # The preparer reads the Stage 2 tables; an empty table means no ids.
        for name in ("HS_cell_type", "HS_tissue", "HS_disease"):
            path = stage2 / f"{name}_1st_pass_meta_table.csv"
            path.write_text(stage2_rows if stage2_rows else "Run,Series\n")
        return extract_srr_from_stage2_tables(
            stage2_dir=stage2, output_dir=tmp_path / "stage3"
        )

    def test_previous_list_is_removed_when_no_ids_are_found(self, tmp_path):
        stage3 = tmp_path / "stage3"
        stage3.mkdir(parents=True)
        stale = stage3 / "srr_list.txt"
        stale.write_text("SRR999999\n")  # left by an earlier cycle

        self._prep(tmp_path)

        # Stage 3 branches on this file existing, so a stale one would be
        # downloaded again as though it belonged to this cycle.
        assert not stale.exists()

    def test_a_real_list_is_written_when_ids_are_found(self, tmp_path):
        result = self._prep(tmp_path, "Run,Series\nSRR000001,GSE1\n")
        assert result["success"]
        assert (tmp_path / "stage3" / "srr_list.txt").exists()


class TestEmptyDownloadIsNotSuccess:
    def test_downloading_an_empty_list_is_not_a_success(self, tmp_path):
        srr_list = tmp_path / "srr_list.txt"
        srr_list.write_text("")  # no ids at all
        downloader = S3SRADownloader(srr_list, tmp_path / "out")

        result = downloader.download_all()

        # Nothing was fetched, so this is not a completed download step.
        assert result["success"] is False
        assert result["total"] == 0


class TestProgressIsWrittenAtomically:
    def test_a_failed_write_leaves_the_previous_progress_intact(self, tmp_path):
        srr_list = tmp_path / "srr_list.txt"
        srr_list.write_text("SRR000001\n")
        out = tmp_path / "out"
        out.mkdir()
        downloader = S3SRADownloader(srr_list, out)

        downloader.progress.total = 1
        downloader._save_progress()
        good = (out / "download_progress.json").read_text()

        # Interrupt a later write partway through.
        downloader.progress.total = 2
        with mock.patch("json.dump", side_effect=RuntimeError("disk full")):
            try:
                downloader._save_progress()
            except RuntimeError:
                pass

        # The file must still be the last complete version, not a truncated one.
        text = (out / "download_progress.json").read_text()
        assert text == good
        json.loads(text)


class TestFileChecksApplyEverywhere:
    def test_a_known_size_match_still_has_to_look_like_an_sra(self, tmp_path):
        # The size came from the server, but a proxy error page of exactly that
        # length is not the file we asked for.
        part = tmp_path / "SRR1.sra.part"
        final = tmp_path / "SRR1.sra"
        payload = b"<html>error</html>" * 1000
        part.write_bytes(payload)
        assert verify_and_promote(part, final, remote_size=len(payload)) is False
        assert not final.exists()

    def test_an_existing_final_that_is_not_an_sra_is_not_reused(self, tmp_path):
        srr_list = tmp_path / "srr_list.txt"
        srr_list.write_text("SRR1\n")
        d = S3SRADownloader(srr_list, tmp_path)
        final = d._get_output_path("SRR1")
        final.parent.mkdir(parents=True)
        final.write_bytes(b"NOT-AN-SRA" * 100_001)  # big enough, wrong content

        # Skipping this as "already downloaded" would carry a corrupt file
        # forward for the rest of the pipeline.
        assert d._file_exists_and_valid(final) is False

    def test_an_existing_real_sra_is_reused(self, tmp_path):
        srr_list = tmp_path / "srr_list.txt"
        srr_list.write_text("SRR1\n")
        d = S3SRADownloader(srr_list, tmp_path)
        final = d._get_output_path("SRR1")
        final.parent.mkdir(parents=True)
        final.write_bytes(b"NCBI.sra" + b"\0" * MIN_SRA_SIZE)
        assert d._file_exists_and_valid(final) is True


class TestCorruptFinalIsQuarantined:
    """Declining to reuse a corrupt file is not enough if it stays where the
    pipeline looks. Stage 3 collects inputs with a */*.sra glob."""

    def _downloader(self, tmp_path):
        srr_list = tmp_path / "srr_list.txt"
        srr_list.write_text("SRR1\n")
        return S3SRADownloader(srr_list, tmp_path)

    def test_corrupt_final_is_moved_aside(self, tmp_path):
        d = self._downloader(tmp_path)
        final = d._get_output_path("SRR1")
        final.parent.mkdir(parents=True)
        final.write_bytes(b"NOT-AN-SRA" * 100_001)

        assert d._file_exists_and_valid(final) is False
        # Gone from the glob's reach, kept for inspection under a new name.
        assert not final.exists()
        assert final.with_name(final.name + ".corrupt").exists()

    def test_a_valid_final_is_left_alone(self, tmp_path):
        d = self._downloader(tmp_path)
        final = d._get_output_path("SRR1")
        final.parent.mkdir(parents=True)
        final.write_bytes(b"NCBI.sra" + b"\0" * MIN_SRA_SIZE)

        assert d._file_exists_and_valid(final) is True
        assert final.exists()
        assert not final.with_name(final.name + ".corrupt").exists()


class TestPromotionAndReuseAgree:
    """Whatever promotion accepts, the next run's reuse check must accept too."""

    def _downloader(self, tmp_path):
        srr_list = tmp_path / "srr_list.txt"
        srr_list.write_text("SRR1\n")
        return S3SRADownloader(srr_list, tmp_path)

    def test_a_promoted_file_is_reusable_next_run(self, tmp_path):
        d = self._downloader(tmp_path)
        final = d._get_output_path("SRR1")
        final.parent.mkdir(parents=True)
        part = d._get_part_path("SRR1")
        payload = b"NCBI.sra" + b"\0" * MIN_SRA_SIZE
        part.write_bytes(payload)

        assert verify_and_promote(part, final, remote_size=len(payload)) is True
        assert d._file_exists_and_valid(final) is True

    def test_a_file_too_small_to_reuse_is_not_promoted(self, tmp_path):
        # A known-size match does not exempt a file from the size floor:
        # promotion would otherwise accept a file the next run rejects.
        part = tmp_path / "SRR1.sra.part"
        final = tmp_path / "SRR1.sra"
        payload = b"NCBI.sra" + b"\0" * 100
        part.write_bytes(payload)
        assert verify_and_promote(part, final, remote_size=len(payload)) is False


class TestAlternatePrepPathAlsoClearsStaleLists:
    def test_missing_srr_directory_clears_the_previous_list(self, tmp_path):
        from cycle_test.utils.stage3_preparer import prepare_stage3

        out = tmp_path / "stage3"
        out.mkdir()
        stale = out / "srr_list.txt"
        stale.write_text("SRR999999\n")

        # The public/CLI entry point, not the one the cycle runner uses.
        prepare_stage3(srr_dir=tmp_path / "absent", output_dir=out)
        assert not stale.exists()

    def test_empty_srr_directory_clears_the_previous_list(self, tmp_path):
        from cycle_test.utils.stage3_preparer import prepare_stage3

        srr_dir = tmp_path / "SRR"
        srr_dir.mkdir()
        out = tmp_path / "stage3"
        out.mkdir()
        stale = out / "srr_list.txt"
        stale.write_text("SRR999999\n")

        prepare_stage3(srr_dir=srr_dir, output_dir=out)
        assert not stale.exists()


class TestQuarantineFailureIsFatal:
    """If a corrupt final cannot even be moved aside, the pipeline must not run:
    its */*.sra glob would still find the corrupt file."""

    def _downloader(self, tmp_path):
        srr_list = tmp_path / "srr_list.txt"
        srr_list.write_text("SRR1\n")
        return S3SRADownloader(srr_list, tmp_path)

    def test_a_failed_quarantine_raises(self, tmp_path):
        import pytest

        from cycle_test.utils.stage3_downloader import UnsafeSRAInputError

        d = self._downloader(tmp_path)
        final = d._get_output_path("SRR1")
        final.parent.mkdir(parents=True)
        final.write_bytes(b"NOT-AN-SRA" * 100_001)

        with mock.patch.object(Path, "replace", side_effect=OSError("read-only fs")):
            # Cannot be silently treated as "just re-download": the corrupt file
            # is still in the glob's path, so this raises the dedicated fatal error.
            with pytest.raises(UnsafeSRAInputError):
                d._file_exists_and_valid(final)


class TestPrepStartInvalidatesStaleList:
    """Every prep exit, including an exception, must not leave a stale list."""

    def test_an_exception_during_prep_does_not_leave_the_old_list(self, tmp_path):
        from cycle_test.utils.stage3_preparer import prepare_stage3

        out = tmp_path / "stage3"
        out.mkdir()
        (out / "srr_list.txt").write_text("SRR999999\n")

        # A directory where a file is expected makes reading it raise.
        bad = tmp_path / "not_a_file"
        bad.mkdir()
        result = prepare_stage3(srr_dir=tmp_path / "srr", output_dir=out,
                                complete_datasets_file=bad)

        assert result["success"] is False
        assert not (out / "srr_list.txt").exists()


class TestUnsafeInputStopsThePipeline:
    """A corrupt final that could not be quarantined is a safety-invariant
    failure: the run must stop before Stage 3b, not just re-download. The
    system-level condition is "pipeline not called", not "helper raised"."""

    def test_download_all_reports_a_fatal_input_error(self, tmp_path):
        from cycle_test.utils.stage3_downloader import S3SRADownloader

        srr_list = tmp_path / "srr_list.txt"
        srr_list.write_text("SRR1\n")
        out = tmp_path / "out"
        d = S3SRADownloader(srr_list, out, max_concurrent=1)
        final = d._get_output_path("SRR1")
        final.parent.mkdir(parents=True)
        final.write_bytes(b"NOT-AN-SRA" * 100_001)

        with mock.patch.object(Path, "replace", side_effect=OSError("read-only fs")):
            result = d.download_all()

        # Not an ordinary failure the rest of the pipeline can run around.
        assert result["fatal_input_error"] is True
        assert result["success"] is False

    def test_the_cycle_does_not_run_the_pipeline_on_a_fatal_input(self, tmp_path):
        from cycle_test import run_cycle_test as rct
        import logging

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        (dirs["stage3"] / "srr_list.txt").write_text("SRR1\n")
        corrupt = dirs["stage3_sra"] / "SRR1" / "SRR1.sra"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_bytes(b"NOT-AN-SRA" * 100_001)

        pipeline_called = {"v": False}

        def fake_pipeline(**kw):
            pipeline_called["v"] = True
            return {"success": True}

        with mock.patch.object(rct, "extract_srr_from_stage2_tables",
                               return_value={"success": True, "unique_srr_ids": 1, "gse_count": 1}), \
             mock.patch.object(Path, "replace", side_effect=OSError("read-only fs")), \
             mock.patch.object(rct, "run_stage3_pipeline", side_effect=fake_pipeline):
            result = rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs, umls_dir=tmp_path,
                logger=logging.getLogger("test"), stage3_only=True,
            )

        assert pipeline_called["v"] is False
        assert result["status"] == "failed_stage3"

    def test_a_normal_download_failure_still_lets_the_pipeline_run(self, tmp_path):
        # Only an unsafe input stops the pipeline; an ordinary failed download
        # (network, timeout) still lets Cell Ranger run on the samples that did
        # arrive.
        from cycle_test import run_cycle_test as rct
        import logging

        dirs = rct.setup_cycle_directory(tmp_path, 1, 1, 5, include_stage3=True)
        (dirs["stage3"] / "srr_list.txt").write_text("SRR1\n")

        pipeline_called = {"v": False}

        def fake_pipeline(**kw):
            pipeline_called["v"] = True
            return {"success": True, "successful_samples": 1, "failed_samples": 0}

        with mock.patch.object(rct, "extract_srr_from_stage2_tables",
                               return_value={"success": True, "unique_srr_ids": 2, "gse_count": 1}), \
             mock.patch.object(rct, "download_sra_files",
                               return_value={"success": False, "total": 2, "downloaded": 1,
                                             "failed": 1, "fatal_input_error": False}), \
             mock.patch.object(rct, "run_stage3_pipeline", side_effect=fake_pipeline):
            result = rct.run_single_cycle(
                cycle_num=1, start_page=1, end_page=5, dirs=dirs, umls_dir=tmp_path,
                logger=logging.getLogger("test"), stage3_only=True,
            )

        assert pipeline_called["v"] is True
        assert result["status"] == "partial_success"


class _Step8:
    """Step 8's own eligibility code, loaded from the Snakefile it ships in.

    `has_r1r2`, `classify_sample` and `get_sample_fastqs` are plain Python and
    are what decides which samples reach Cell Ranger, so the tests ask them
    directly rather than restating the rule. Only the snakemake-only blocks
    (`rule`, `onsuccess`) are dropped; everything else runs exactly as snakemake
    evaluates it at parse time.
    """

    SMK = (Path(__file__).resolve().parents[2] / "srr_pipeline_package"
           / "pipeline_next" / "Snakefile_hs.smk")
    BLOCKS = ("rule ", "onsuccess", "onerror", "onstart", "checkpoint ")

    def __init__(self, success_dir, run_id=""):
        import types

        kept, skipping = [], False
        for line in self.SMK.read_text().split("\n"):
            if line[:1] not in (" ", "\t", ""):
                skipping = line.startswith(self.BLOCKS)
            kept.append("" if skipping else line)
        mod = types.ModuleType("snakefile_hs")
        mod.__dict__["config"] = {"success_dir": str(success_dir),
                                  "run_id": run_id}
        exec(compile("\n".join(kept), str(self.SMK), "exec"), mod.__dict__)
        self.mod = mod
        self.eligible = list(mod.ELIGIBLE)
        self.ineligible = {name: reason for name, reason, _ in mod.INELIGIBLE}

    def fastqs(self, sample):
        import types as t

        return sorted(Path(p).name for p in
                      self.mod.get_sample_fastqs(t.SimpleNamespace(sample=sample)))


def _sample(tmp_path, *names):
    """A success directory holding one sample with the given FASTQ names."""
    d = tmp_path / "success" / "SRR000001"
    d.mkdir(parents=True)
    for i, name in enumerate(names):
        (d / name).write_bytes(b"x" * (100 + i))
    return tmp_path / "success"


class TestCellRangerEligibilityNeedsCompleteLanes:
    """Step 7 records a cross-lane sample as ineligible, and step 8 must not
    hand it to Cell Ranger anyway, which fails on it. The two steps have to
    agree.
    """

    def test_a_cross_lane_r1_and_r2_are_not_a_pair(self, tmp_path):
        success = _sample(tmp_path,
                          "SRR000001_S1_L001_R1_001.fastq.gz",
                          "SRR000001_S1_L002_R2_001.fastq.gz")
        step8 = _Step8(success)
        assert step8.eligible == []
        assert "same lane" in step8.ineligible["SRR000001"]

    def test_a_lane_missing_its_r2_makes_the_sample_ineligible(self, tmp_path):
        success = _sample(tmp_path,
                          "SRR000001_S1_L001_R1_001.fastq.gz",
                          "SRR000001_S1_L001_R2_001.fastq.gz",
                          "SRR000001_S1_L002_R1_001.fastq.gz")
        step8 = _Step8(success)
        # Cell Ranger is pointed at the sample directory and re-globs it, so the
        # unpaired lane cannot be hidden from it by choosing files here.
        assert step8.eligible == []
        assert "same lane" in step8.ineligible["SRR000001"]

    def test_r1_files_alone_are_ineligible(self, tmp_path):
        success = _sample(tmp_path,
                          "SRR000001_S1_L001_R1_001.fastq.gz",
                          "SRR000001_S1_L002_R1_001.fastq.gz")
        assert _Step8(success).eligible == []

    def test_r2_files_alone_are_ineligible(self, tmp_path):
        success = _sample(tmp_path,
                          "SRR000001_S1_L001_R2_001.fastq.gz",
                          "SRR000001_S1_L002_R2_001.fastq.gz")
        assert _Step8(success).eligible == []

    def test_step8_records_its_own_exclusions_in_the_census(self, tmp_path):
        # More than four FASTQ files: step 6 never lists the sample and step 7
        # never processes it, so only step 8 can explain the exclusion.
        success = _sample(tmp_path,
                          "SRR000001_S1_L001_R1_001.fastq.gz",
                          "SRR000001_S1_L001_R2_001.fastq.gz",
                          "SRR000001_S1_L002_R1_001.fastq.gz",
                          "SRR000001_S1_L002_R2_001.fastq.gz",
                          "SRR000001_S1_L003_R1_001.fastq.gz")
        step8 = _Step8(success)
        assert step8.eligible == []
        census = (success / "cellranger_ineligible.tsv").read_text()
        assert "SRR000001" in census and "same lane" in census

    def test_the_census_keeps_one_row_per_sample(self, tmp_path):
        success = _sample(tmp_path,
                          "SRR000001_S1_L001_R1_001.fastq.gz",
                          "SRR000001_S1_L002_R2_001.fastq.gz")
        _Step8(success)
        _Step8(success)  # step 8 re-run, e.g. a resume
        rows = [l for l in (success / "cellranger_ineligible.tsv")
                .read_text().splitlines() if l.startswith("SRR")]
        assert len(rows) == 1


class TestLayoutsThatQualifyStillQualify:
    """The two layouts Cell Ranger accepts must be unaffected by the lane rule."""

    def test_a_single_lane_pair_is_eligible(self, tmp_path):
        success = _sample(tmp_path,
                          "SRR000001_S1_R1_001.fastq.gz",
                          "SRR000001_S1_R2_001.fastq.gz")
        step8 = _Step8(success)
        assert step8.eligible == ["SRR000001"]
        assert step8.fastqs("SRR000001") == ["SRR000001_S1_R1_001.fastq.gz",
                                             "SRR000001_S1_R2_001.fastq.gz"]

    def test_two_complete_lanes_are_eligible_with_every_file(self, tmp_path):
        names = ["SRR000001_S1_L001_R1_001.fastq.gz",
                 "SRR000001_S1_L001_R2_001.fastq.gz",
                 "SRR000001_S1_L002_R1_001.fastq.gz",
                 "SRR000001_S1_L002_R2_001.fastq.gz"]
        success = _sample(tmp_path, *names)
        step8 = _Step8(success)
        assert step8.eligible == ["SRR000001"]
        assert step8.fastqs("SRR000001") == sorted(names)

    def test_three_complete_lanes_are_eligible(self, tmp_path):
        success = _sample(tmp_path, *[
            f"SRR000001_S1_L00{lane}_R{read}_001.fastq.gz"
            for lane in (1, 2, 3) for read in (1, 2)])
        assert _Step8(success).eligible == ["SRR000001"]

    def test_a_single_fastq_still_says_so(self, tmp_path):
        success = _sample(tmp_path, "SRR000001_S1_R1_001.fastq.gz")
        assert "only 1 FASTQ file" in _Step8(success).ineligible["SRR000001"]

    def test_an_empty_sample_directory_still_says_so(self, tmp_path):
        (tmp_path / "success" / "SRR000001").mkdir(parents=True)
        step8 = _Step8(tmp_path / "success")
        assert "no fastq.gz files found" in step8.ineligible["SRR000001"]

    def test_unrecognised_names_still_report_no_pair(self, tmp_path):
        # Raw fastq-dump output, before step 7 renames it.
        success = _sample(tmp_path, "SRR000001_1.fastq.gz", "SRR000001_2.fastq.gz")
        assert "no R1/R2 pair found" in _Step8(success).ineligible["SRR000001"]


class TestTheEligibilityReportNamesItsRun:
    def test_the_report_carries_the_run_that_wrote_it(self, tmp_path):
        success = _sample(tmp_path,
                          "SRR000001_S1_R1_001.fastq.gz",
                          "SRR000001_S1_R2_001.fastq.gz")
        _Step8(success, run_id="run-42")
        report = (success / "cellranger_eligibility.tsv").read_text()
        # The provenance check reads this to tell a dispatch by THIS run from a
        # report an earlier run left on the persistent volume.
        assert report.startswith("# run_id\trun-42\n")
        assert "SRR000001\teligible" in report
