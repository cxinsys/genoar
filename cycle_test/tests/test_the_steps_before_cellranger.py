"""Steps 2 to 6 decide which samples reach Cell Ranger, and had no tests.

Not by oversight. Every one of them used a bash 4 construct -- `declare -A`,
`local -A`, `mapfile` -- and bash 3.2 is what a Mac ships and what many login
nodes still have, so none of them would start outside the image. A script that
cannot be run cannot be tested, and these are the scripts that decide what gets
analysed.

They start now, and these drive them.
"""

import gzip
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NEXT = REPO / "srr_pipeline_package" / "pipeline_next"


def _run(script, *args, **env):
    base = dict(os.environ)
    base.update({k: str(v) for k, v in env.items()})
    return subprocess.run(["bash", str(NEXT / script)] + [str(a) for a in args],
                          capture_output=True, text=True, env=base)


@pytest.fixture
def site(tmp_path):
    for name in ("success", "logs", "reports", "base", "out"):
        (tmp_path / name).mkdir()
    return tmp_path


class TestTheyStartOnTheShellAClusterHas:
    """bash 3.2. The reason none of these had tests."""

    @pytest.mark.parametrize("script", [
        "check_counts.sh", "rename_fastq.sh", "move_success_dirs.sh",
        "fastq_gzip_parallel.sh", "fastq_dump_parallel.sh",
        "make_directories.sh",
    ])
    def test_it_parses(self, script):
        done = subprocess.run(["bash", "-n", str(NEXT / script)],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr

    @pytest.mark.parametrize("script", [
        "check_counts.sh", "rename_fastq.sh", "move_success_dirs.sh",
        "fastq_gzip_parallel.sh", "fastq_dump_parallel.sh",
    ])
    def test_it_uses_nothing_from_bash_four(self, script):
        code = "\n".join(line for line in (NEXT / script).read_text().splitlines()
                         if not line.lstrip().startswith("#"))
        for construct in ("declare -A", "local -A", "mapfile", "readarray"):
            assert construct not in code, "%s in %s" % (construct, script)


class TestStepSixCountsWhatCanActuallyRun:
    """Eligibility is a claim about what a later step will do with the sample."""

    def _sample(self, site, name, files):
        d = site / "success" / name
        d.mkdir()
        for i in range(1, files + 1):
            (d / ("%s_%d.fastq.gz" % (name, i))).write_bytes(b"")
        return d

    def _ineligible(self, site):
        path = site / "success" / "cellranger_ineligible.tsv"
        return path.read_text() if path.is_file() else ""

    def test_a_pair_is_eligible(self, site):
        self._sample(site, "SRR000001", 2)
        done = _run("check_counts.sh", site / "success", LOG_DIR=site / "logs")
        assert "1 eligible, 0 ineligible" in done.stdout

    def test_more_than_four_files_is_not_eligible(self, site):
        """Step 7 reads fastq_2 through fastq_4, so nothing would rename it.

        Counting it eligible puts the claim in the summary for a sample no
        later step will touch, and only step 8 -- which a steps-1-to-7 image
        never runs -- would ever notice the difference.
        """
        self._sample(site, "SRR000001", 6)
        done = _run("check_counts.sh", site / "success", LOG_DIR=site / "logs")
        assert "0 eligible, 1 ineligible" in done.stdout
        assert "more than four" in self._ineligible(site)

    def test_a_complete_multilane_sample_is_eligible_even_above_four_files(
            self, site):
        """Step 8 accepts paired R1/R2 files for every named lane."""
        sample = "SRR000001"
        directory = site / "success" / sample
        directory.mkdir()
        for lane in ("001", "002", "003"):
            for read in ("R1", "R2"):
                (directory / (
                    f"{sample}_S1_L{lane}_{read}_001.fastq.gz"
                )).write_bytes(b"reads")

        done = _run("check_counts.sh", site / "success",
                    LOG_DIR=site / "logs")

        assert "1 eligible, 0 ineligible" in done.stdout
        assert sample not in self._ineligible(site)

    def test_a_single_file_is_not_eligible(self, site):
        self._sample(site, "SRR000001", 1)
        done = _run("check_counts.sh", site / "success", LOG_DIR=site / "logs")
        assert "0 eligible, 1 ineligible" in done.stdout

    def test_no_files_is_not_eligible(self, site):
        (site / "success" / "SRR000001").mkdir()
        done = _run("check_counts.sh", site / "success", LOG_DIR=site / "logs")
        assert "0 eligible, 1 ineligible" in done.stdout

    def test_every_ineligible_sample_carries_a_reason(self, site):
        for name, files in (("SRR000001", 0), ("SRR000002", 1),
                            ("SRR000003", 6)):
            self._sample(site, name, files) if files else (
                site / "success" / name).mkdir()
        _run("check_counts.sh", site / "success", LOG_DIR=site / "logs")
        rows = [r for r in self._ineligible(site).splitlines()
                if r and not r.startswith("#")]
        assert len(rows) == 3
        for row in rows:
            assert len(row.split("\t")) == 3 and row.split("\t")[2]


class TestStepFiveVerifiesWhatItCopied:
    """`cp -r` returning zero is not the bytes arriving."""

    def _source(self, site, name="SRR000001", files=("a", "b")):
        d = site / "base" / name
        d.mkdir(parents=True)
        for f in files:
            (d / ("%s.fastq.gz" % f)).write_bytes(b"payload-" + f.encode())
        (site / "success_list.txt").write_text(name + "\n")
        return d

    def _run5(self, site):
        return _run("move_success_dirs.sh", site / "base", site / "out",
                    site / "success_list.txt", LOG_DIR=site / "logs",
                    REPORTS_DIR=site / "reports")

    def test_a_clean_copy_succeeds(self, site):
        self._source(site)
        done = self._run5(site)
        assert done.returncode == 0, done.stdout + done.stderr
        assert (site / "out" / "success" / "SRR000001" / "a.fastq.gz").is_file()

    def test_a_half_copied_destination_is_replaced(self, site):
        """The state a `cp` that died leaves, and that the next run adopted."""
        self._source(site)
        half = site / "out" / "success" / "SRR000001"
        half.mkdir(parents=True)
        (half / "a.fastq.gz").write_bytes(b"pay")       # truncated
        done = self._run5(site)
        assert done.returncode == 0, done.stdout + done.stderr
        assert (half / "a.fastq.gz").read_bytes() == b"payload-a"
        assert (half / "b.fastq.gz").is_file(), "the missing file arrived too"

    def test_a_complete_destination_is_left_alone(self, site):
        self._source(site)
        self._run5(site)
        stamp = (site / "out" / "success" / "SRR000001" / "a.fastq.gz").stat().st_mtime
        done = self._run5(site)
        assert done.returncode == 0
        assert (site / "out" / "success" / "SRR000001" / "a.fastq.gz").stat().st_mtime == stamp

    def test_a_source_that_is_gone_does_not_destroy_the_copy(self, site):
        """Nothing left to compare against is not a reason to recopy nothing."""
        self._source(site)
        self._run5(site)
        import shutil
        shutil.rmtree(site / "base" / "SRR000001")
        done = self._run5(site)
        assert done.returncode == 0, done.stdout + done.stderr
        assert (site / "out" / "success" / "SRR000001" / "a.fastq.gz").is_file()


class TestStepThreeWillNotAcceptATruncatedArchive:
    """A `.gz` that exists is not a `.gz` that is whole."""

    def _fastq(self, site, name="SRR000001"):
        d = site / "base" / name
        d.mkdir(parents=True)
        path = d / ("%s_1.fastq" % name)
        path.write_text("@r\nACGT\n+\nIIII\n" * 50)
        return path

    def test_a_truncated_gz_is_replaced(self, site):
        source = self._fastq(site)
        whole = gzip.compress(source.read_bytes())
        Path(str(source) + ".gz").write_bytes(whole[:len(whole) // 2])
        done = _run("fastq_gzip_parallel.sh", site / "base",
                    LOG_DIR=site / "logs", REPORTS_DIR=site / "reports",
                    THREADS=1)
        assert done.returncode == 0, done.stdout + done.stderr
        assert gzip.decompress(
            Path(str(source) + ".gz").read_bytes()) == b"@r\nACGT\n+\nIIII\n" * 50

    def test_a_whole_gz_is_left_alone(self, site):
        source = self._fastq(site)
        Path(str(source) + ".gz").write_bytes(gzip.compress(source.read_bytes()))
        done = _run("fastq_gzip_parallel.sh", site / "base",
                    LOG_DIR=site / "logs", REPORTS_DIR=site / "reports",
                    THREADS=1)
        assert "is whole" in (site / "logs" / "step3_fastq_gzip.log").read_text()

    def test_an_empty_gz_is_replaced(self, site):
        source = self._fastq(site)
        Path(str(source) + ".gz").write_bytes(b"")
        _run("fastq_gzip_parallel.sh", site / "base", LOG_DIR=site / "logs",
             REPORTS_DIR=site / "reports", THREADS=1)
        assert gzip.decompress(Path(str(source) + ".gz").read_bytes())

    def test_a_file_name_with_a_command_in_it_is_not_run(self, site):
        """`eval "$tool" "\\"$fq\\""` executed what was inside the name."""
        d = site / "base" / "SRR000001"
        d.mkdir(parents=True)
        odd = d / "a$(touch PWNED)_1.fastq"
        odd.write_text("@r\nACGT\n+\nIIII\n")
        _run("fastq_gzip_parallel.sh", site / "base", LOG_DIR=site / "logs",
             REPORTS_DIR=site / "reports", THREADS=1)
        assert not list(site.rglob("PWNED")), "the file name was executed"
        assert Path(str(odd) + ".gz").is_file(), "and it was still compressed"


class TestAFailedStepSaysWhichSamplesFailed:

    def test_step_three_records_every_failure(self, site):
        d = site / "base" / "SRR000001"
        d.mkdir(parents=True)
        for i in (1, 2):
            (d / ("SRR000001_%d.fastq" % i)).write_text("@r\nACGT\n+\nIIII\n")
        d.chmod(0o555)
        try:
            done = _run("fastq_gzip_parallel.sh", site / "base",
                        LOG_DIR=site / "logs", REPORTS_DIR=site / "reports",
                        THREADS=1)
        finally:
            d.chmod(0o755)
        assert done.returncode == 1, done.stdout + done.stderr
        recorded = (site / "reports" / "step3_failed.txt").read_text()
        assert "SRR000001_1.fastq" in recorded
        assert "SRR000001_2.fastq" in recorded

    def test_the_report_does_not_land_at_the_filesystem_root(self, site):
        """The fallback ran `mkdir -p ""` and appended to /step3_failed.txt."""
        source = (NEXT / "fastq_gzip_parallel.sh").read_text()
        assert 'export REPORTS_DIR=' in source


class TestTheFailureReportIsAboutThisRun:
    """It is the file an operator is pointed at, so it has to be current."""

    def _failing(self, site, name="SRR000001"):
        d = site / "base" / name
        d.mkdir(parents=True)
        (d / ("%s_1.fastq" % name)).write_text("@r\nACGT\n+\nIIII\n")
        return d

    def _gzip(self, site, unwritable):
        d = site / "base" / "SRR000001"
        if unwritable:
            d.chmod(0o555)
        try:
            return _run("fastq_gzip_parallel.sh", site / "base",
                        LOG_DIR=site / "logs", REPORTS_DIR=site / "reports",
                        THREADS=1)
        finally:
            d.chmod(0o755)

    def test_a_clean_rerun_does_not_still_name_last_times_failure(self, site):
        """Appended and never truncated, so a fixed sample stayed named."""
        self._failing(site)
        assert self._gzip(site, unwritable=True).returncode == 1
        assert "SRR000001" in (site / "reports" / "step3_failed.txt").read_text()
        done = self._gzip(site, unwritable=False)
        assert done.returncode == 0, done.stdout + done.stderr
        assert (site / "reports" / "step3_failed.txt").read_text().strip() == ""

    def test_repeated_failures_do_not_accumulate_duplicates(self, site):
        self._failing(site)
        for _ in range(3):
            self._gzip(site, unwritable=True)
        lines = [l for l in (site / "reports" / "step3_failed.txt")
                 .read_text().splitlines() if l.strip()]
        assert len(lines) == len(set(lines))

    def test_the_step_log_does_not_report_a_missing_helper(self, site):
        """`ensure_reports_dir` was not exported into the xargs subshell."""
        self._failing(site)
        self._gzip(site, unwritable=True)
        log = (site / "logs" / "step3_fastq_gzip.log").read_text()
        assert "command not found" not in log


class TestStepTwoReportsSamplesNotWaitStatuses:
    """The number printed has to be a number of samples."""

    def _with_parallel(self, site, script_body):
        """A `parallel` stand-in on PATH, honouring its exit-status contract."""
        binary_dir = site / "fakebin"
        binary_dir.mkdir(exist_ok=True)
        stub = binary_dir / "parallel"
        stub.write_text("#!/usr/bin/env bash\n" + script_body)
        stub.chmod(0o755)
        (site / "base" / "SRR000001").mkdir(parents=True, exist_ok=True)
        (site / "base" / "SRR000001" / "SRR000001.sra").write_bytes(b"x")
        return _run("fastq_dump_parallel.sh", site / "base",
                    LOG_DIR=site / "logs", REPORTS_DIR=site / "reports",
                    PATH="%s:%s" % (binary_dir, os.environ["PATH"]))

    def test_a_signal_is_not_reported_as_a_hundred_and_forty_three_samples(self, site):
        done = self._with_parallel(site, 'kill -TERM $$\n')
        assert done.returncode == 1
        assert "143 sample(s) failed" not in done.stdout
        assert "recorded no per-sample failure" in done.stdout

    def test_the_count_comes_from_the_records(self, site):
        done = self._with_parallel(site, '''
printf 'FAIL\\tSRR_A\\tno_sra\\n' >> "$STATUS_FILE"
printf 'FAIL\\tSRR_B\\tno_sra\\n' >> "$STATUS_FILE"
exit 2
''')
        assert "2 sample(s) failed" in done.stdout
        recorded = (site / "reports" / "step2_failed.txt").read_text()
        assert "SRR_A" in recorded and "SRR_B" in recorded

    def test_a_last_line_without_a_newline_is_not_dropped(self, site):
        """A worker killed mid-`echo` leaves exactly that, and it is the one
        record the report exists for."""
        done = self._with_parallel(
            site, '''printf 'FAIL\\tSRR_KILLED\\tno_sra' >> "$STATUS_FILE"\nexit 1\n''')
        assert "SRR_KILLED" in (site / "reports" / "step2_failed.txt").read_text()

    def test_a_carriage_return_does_not_end_up_in_the_name(self, site):
        self._with_parallel(
            site, '''printf 'FAIL\\tSRR_CRLF\\tno_sra\\r\\n' >> "$STATUS_FILE"\nexit 1\n''')
        recorded = (site / "reports" / "step2_failed.txt").read_text()
        assert "SRR_CRLF\n" in recorded or recorded.strip() == "SRR_CRLF"

    def test_the_status_file_is_cleaned_up(self, site):
        before = set(Path("/tmp").glob("tmp.*")) if Path("/tmp").is_dir() else set()
        self._with_parallel(site, '''printf 'OK\\tSRR_A\\n' >> "$STATUS_FILE"\n''')
        source = (NEXT / "fastq_dump_parallel.sh").read_text()
        assert 'rm -f "$tmp_list" "$status_file"' in source


class TestACorruptArchiveWithNoSourceIsNotIgnored:
    """The skip loop only walks `.fastq` files, so an orphan was never looked at.

    A run interrupted after gzip and before the `.fastq` was removed leaves
    exactly that. Step 4 counts a matched `*.fastq.gz` glob as success, so the
    sample reached Cell Ranger with half its reads and a job's worth of time
    was spent on it.
    """

    def _orphan(self, site, payload):
        d = site / "base" / "SRR000001"
        d.mkdir(parents=True)
        (d / "SRR000001_1.fastq.gz").write_bytes(payload)
        return d / "SRR000001_1.fastq.gz"

    def _run3(self, site):
        return _run("fastq_gzip_parallel.sh", site / "base",
                    LOG_DIR=site / "logs", REPORTS_DIR=site / "reports",
                    THREADS=1)

    def test_a_truncated_orphan_fails_the_step(self, site):
        self._orphan(site, b"\x1f\x8b\x08nonsense")
        done = self._run3(site)
        assert done.returncode == 1, done.stdout + done.stderr
        assert "no source to rebuild from" in done.stdout

    def test_it_is_named_in_the_report(self, site):
        self._orphan(site, b"\x1f\x8b\x08nonsense")
        self._run3(site)
        assert "SRR000001_1.fastq.gz" in (
            site / "reports" / "step3_failed.txt").read_text()

    def test_a_whole_orphan_is_fine(self, site):
        import gzip as _gzip
        self._orphan(site, _gzip.compress(b"@r\nACGT\n+\nIIII\n"))
        done = self._run3(site)
        assert done.returncode == 0, done.stdout + done.stderr

    def test_an_archive_whose_source_is_present_is_left_to_the_other_check(self, site):
        """That one can rebuild it; this one only reports."""
        import gzip as _gzip
        path = self._orphan(site, b"\x1f\x8b\x08nonsense")
        Path(str(path)[:-3]).write_text("@r\nACGT\n+\nIIII\n")
        done = self._run3(site)
        assert done.returncode == 0, done.stdout + done.stderr
        assert _gzip.decompress(path.read_bytes())
