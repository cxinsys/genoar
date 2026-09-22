"""Tests for atomic SRA download promotion.

Pins the fix for the validation finding that a partial .sra larger than 1MB was
taken for a completed file and skipped on the next run. Downloads now land in a
.part and are promoted to the final name only when their size checks out.
"""


from cycle_test.utils.stage3_downloader import (
    verify_and_promote,
    MIN_SRA_SIZE,
    S3SRADownloader,
)


class TestVerifyAndPromote:
    def test_exact_remote_size_promotes(self, tmp_path):
        part = tmp_path / "SRR1.sra.part"
        final = tmp_path / "SRR1.sra"
        payload = b"NCBI.sra" + b"\0" * MIN_SRA_SIZE
        part.write_bytes(payload)
        assert verify_and_promote(part, final, remote_size=len(payload)) is True
        assert final.exists()
        assert not part.exists()

    def test_short_part_against_known_size_is_kept(self, tmp_path):
        # The exact case from the finding: a partial transfer. It must not be
        # promoted, and the .part must survive for the next run to resume.
        part = tmp_path / "SRR1.sra.part"
        final = tmp_path / "SRR1.sra"
        part.write_bytes(b"x" * (2 * 1024 * 1024))  # 2MB, over the old 1MB gate
        assert verify_and_promote(part, final, remote_size=50 * 1024 * 1024) is False
        assert part.exists()
        assert not final.exists()

    def test_unknown_size_promotes_a_large_enough_sra(self, tmp_path):
        # Without a remote size the file must both clear the floor and look like
        # an SRA; see TestFormatCheck for the payload that only does the former.
        part = tmp_path / "SRR1.sra.part"
        final = tmp_path / "SRR1.sra"
        part.write_bytes(b"NCBI.sra" + b"\0" * MIN_SRA_SIZE)
        assert verify_and_promote(part, final, remote_size=None) is True
        assert final.exists()

    def test_unknown_size_rejects_tiny_file(self, tmp_path):
        part = tmp_path / "SRR1.sra.part"
        final = tmp_path / "SRR1.sra"
        part.write_bytes(b"stub")
        assert verify_and_promote(part, final, remote_size=None) is False
        assert part.exists()
        assert not final.exists()

    def test_missing_part_is_not_promoted(self, tmp_path):
        assert verify_and_promote(tmp_path / "absent.part", tmp_path / "f.sra", 10) is False


class TestSkipOnlyCompleted:
    def _downloader(self, tmp_path):
        srr_list = tmp_path / "srr_list.txt"
        srr_list.write_text("SRR1\n")
        return S3SRADownloader(srr_list, tmp_path)

    def test_a_completed_sra_is_treated_as_present(self, tmp_path):
        d = self._downloader(tmp_path)
        final = d._get_output_path("SRR1")
        final.parent.mkdir(parents=True)
        final.write_bytes(b"NCBI.sra" + b"\0" * MIN_SRA_SIZE)
        assert d._file_exists_and_valid(final) is True

    def test_a_part_file_is_not_treated_as_present(self, tmp_path):
        # A big leftover .part must not count as a finished download.
        d = self._downloader(tmp_path)
        part = d._get_part_path("SRR1")
        part.parent.mkdir(parents=True)
        part.write_bytes(b"x" * (5 * 1024 * 1024))
        assert d._file_exists_and_valid(d._get_output_path("SRR1")) is False

    def test_part_path_sits_beside_the_final_path(self, tmp_path):
        d = self._downloader(tmp_path)
        assert d._get_part_path("SRR1") == d._get_output_path("SRR1").with_name("SRR1.sra.part")


class TestFormatCheck:
    """Size alone cannot tell an SRA from any other file of the same length.

    When the remote size is unknown the size gate is all that stands between a
    truncated or wrong payload and the final name, so the header is checked too.
    """

    def test_unknown_size_rejects_a_non_sra_payload(self, tmp_path):
        part = tmp_path / "SRR1.sra.part"
        final = tmp_path / "SRR1.sra"
        part.write_bytes(b"NOT-AN-SRA" * 100_001)  # over the size floor, wrong format
        assert verify_and_promote(part, final, remote_size=None) is False
        assert not final.exists()
        assert part.exists()

    def test_unknown_size_accepts_a_real_sra_header(self, tmp_path):
        part = tmp_path / "SRR1.sra.part"
        final = tmp_path / "SRR1.sra"
        part.write_bytes(b"NCBI.sra" + b"\0" * MIN_SRA_SIZE)
        assert verify_and_promote(part, final, remote_size=None) is True
        assert final.exists()

    def test_a_known_size_match_is_still_subject_to_the_size_floor(self, tmp_path):
        # Promotion and the next run's reuse check share one definition, so a file
        # promoted here must still be accepted there. A 108-byte file would not be,
        # which is why an exact size match alone is not enough.
        part = tmp_path / "SRR1.sra.part"
        final = tmp_path / "SRR1.sra"
        part.write_bytes(b"NCBI.sra" + b"\0" * 100)
        assert verify_and_promote(part, final, remote_size=108) is False
        assert not final.exists()
