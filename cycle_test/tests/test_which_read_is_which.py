"""Step 7 decides which FASTQ is the barcode read and which is the cDNA.

Get it backwards and Cell Ranger still runs, still writes a BAM, and still
reports success -- with almost no cells, because it was handed an index read
where it expected barcodes. Nothing downstream notices: completion is proved by
the BAM existing, and the BAM exists.

Compressed file size cannot make the call, and neither can position after a
sort. Size is a proxy for read length and a poor one: it moves with the
compressor, with how many reads a file holds, and not at all when a file is
missing. Losing the cDNA read -- the largest file, and so the first casualty of
a truncated copy -- shuffles the rest up, and the index read is renamed R1.

Read length says it outright, and is fixed by the chemistry rather than by the
run. These drive the shipped script against real gzipped FASTQ.
"""

import gzip
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "srr_pipeline_package" / "pipeline_next" / "rename_fastq.sh")

# What a 10x v3 dual-index run really looks like.
I1, R1, R2, I2 = 8, 28, 91, 10


def _fastq(path: Path, read_length: int, records: int = 4) -> None:
    body = []
    for i in range(records):
        body.append("@read%d\n%s\n+\n%s\n"
                    % (i, "A" * read_length, "I" * read_length))
    path.write_bytes(gzip.compress("".join(body).encode()))


class Site:
    def __init__(self, tmp_path, sample="SRR000001"):
        self.success = tmp_path / "success"
        self.sample = sample
        self.dir = self.success / sample
        self.dir.mkdir(parents=True)

    def files(self, *lengths, records=4):
        """`<sample>_1.fastq.gz` .. `_N`, in the order fastq-dump writes them."""
        for index, length in enumerate(lengths, start=1):
            _fastq(self.dir / ("%s_%d.fastq.gz" % (self.sample, index)),
                   length, records)
        (self.success / ("fastq_%d_files.tsv" % len(lengths))).write_text(
            self.sample + "\n")
        return self

    def run(self):
        """As step 7 is invoked, with its two directories pointed somewhere real."""
        import os
        env = dict(os.environ,
                   LOG_DIR=str(self.success.parent / "logs"),
                   REPORTS_DIR=str(self.success.parent / "reports"))
        return subprocess.run(["bash", str(SCRIPT), str(self.success)],
                              capture_output=True, text=True, env=env)

    def named(self):
        return {p.name for p in self.dir.iterdir()}

    def length_of(self, suffix):
        path = self.dir / ("%s_S1_%s_001.fastq.gz" % (self.sample, suffix))
        if not path.is_file():
            return None
        with gzip.open(path, "rt") as fh:
            fh.readline()
            return len(fh.readline().strip())

    def ineligible(self):
        path = self.success / "cellranger_ineligible.tsv"
        return path.read_text() if path.is_file() else ""


class TestTheCompleteFourFileCase:
    """I1, R1, R2, I2 as fastq-dump writes them."""

    def test_the_barcode_read_becomes_r1(self, tmp_path):
        site = Site(tmp_path).files(I1, R1, R2, I2)
        done = site.run()
        assert done.returncode == 0, done.stdout + done.stderr
        assert site.length_of("R1") == R1

    def test_the_cdna_read_becomes_r2(self, tmp_path):
        site = Site(tmp_path).files(I1, R1, R2, I2)
        site.run()
        assert site.length_of("R2") == R2

    def test_the_index_reads_are_left_where_they_are(self, tmp_path):
        site = Site(tmp_path).files(I1, R1, R2, I2)
        site.run()
        assert "SRR000001_1.fastq.gz" in site.named()
        assert "SRR000001_4.fastq.gz" in site.named()


class TestNormalLayoutsMatchTheFrozenPipeline:
    """Safety checks must not change the reads in ordinary 10x layouts."""

    @pytest.mark.parametrize(
        "lengths, frozen_r1_index, frozen_r2_index",
        [
            ((R1, R2), 1, 2),
            # The frozen size heuristic chooses the second-largest and largest
            # files here: the ordinary I1,R1,R2,I2 layout resolves to _2/_3.
            ((I1, R1, R2, I2), 2, 3),
        ],
    )
    def test_the_current_tree_hands_cell_ranger_the_same_read_bytes(
            self, tmp_path, lengths, frozen_r1_index, frozen_r2_index):
        site = Site(tmp_path).files(*lengths)
        before = {}
        for index in range(1, len(lengths) + 1):
            path = site.dir / f"{site.sample}_{index}.fastq.gz"
            with gzip.open(path, "rb") as fh:
                before[index] = fh.read()

        done = site.run()

        assert done.returncode == 0, done.stdout + done.stderr
        with gzip.open(
                site.dir / f"{site.sample}_S1_R1_001.fastq.gz", "rb") as fh:
            current_r1 = fh.read()
        with gzip.open(
                site.dir / f"{site.sample}_S1_R2_001.fastq.gz", "rb") as fh:
            current_r2 = fh.read()
        assert current_r1 == before[frozen_r1_index]
        assert current_r2 == before[frozen_r2_index]


class TestTheOrderOnDiskDoesNotDecide:
    """fastq-dump's numbering is not a contract, and neither is size."""

    @pytest.mark.parametrize("order", [
        (R2, R1), (R1, R2), (I1, R1, R2), (R2, R1, I1), (R1, I1, R2),
        (I1, R1, R2, I2), (R2, I2, R1, I1),
    ])
    def test_the_reads_are_found_wherever_they_sit(self, tmp_path, order):
        site = Site(tmp_path).files(*order)
        done = site.run()
        assert done.returncode == 0, done.stdout + done.stderr
        assert site.length_of("R1") == R1
        assert site.length_of("R2") == R2

    def test_a_bigger_file_is_not_the_cdna_read(self, tmp_path):
        """The barcode read, sequenced far deeper than the cDNA read.

        Sorting by size picks this one as R2. It is 28 bases; it is R1.
        """
        site = Site(tmp_path)
        _fastq(site.dir / "SRR000001_1.fastq.gz", R1, records=4000)
        _fastq(site.dir / "SRR000001_2.fastq.gz", R2, records=4)
        (site.success / "fastq_2_files.tsv").write_text("SRR000001\n")
        site.run()
        assert site.length_of("R1") == R1
        assert site.length_of("R2") == R2


class TestWhatUsedToPassSilently:

    def test_a_missing_cdna_read_is_refused_not_relabelled(self, tmp_path):
        """The case the old heuristic got exactly backwards.

        Three files, no cDNA read: the sort put the index read at the median
        and the barcode read at the top, so `_S1_R1_001` became an 8-base
        index and `_S1_R2_001` a 28-base barcode. Cell Ranger ran on that.
        """
        site = Site(tmp_path).files(I1, R1, I2)
        done = site.run()
        assert done.returncode == 0, done.stderr
        assert site.length_of("R1") is None, "nothing may be renamed"
        assert site.length_of("R2") is None
        assert "SRR000001" in site.ineligible()

    def test_the_refusal_says_what_it_saw(self, tmp_path):
        site = Site(tmp_path).files(I1, R1, I2)
        site.run()
        assert "barcode-length" in site.ineligible()

    def test_two_barcode_length_reads_are_refused(self, tmp_path):
        """Ambiguous is not a thing to pick between."""
        site = Site(tmp_path).files(R1, R1, R2)
        site.run()
        assert site.length_of("R1") is None
        assert "SRR000001" in site.ineligible()

    def test_a_truncated_file_is_refused_not_guessed(self, tmp_path):
        site = Site(tmp_path).files(I1, R1, R2)
        (site.dir / "SRR000001_3.fastq.gz").write_bytes(b"\x1f\x8b\x08nonsense")
        site.run()
        assert site.length_of("R1") is None
        assert "could not read" in site.ineligible()

    def test_an_empty_file_is_refused(self, tmp_path):
        site = Site(tmp_path).files(I1, R1, R2)
        (site.dir / "SRR000001_3.fastq.gz").write_bytes(b"")
        site.run()
        assert site.length_of("R1") is None


class TestTheTwoFileCaseIsStillPositional:
    """Two files from `--split-files` are the pair, in order."""

    def test_a_plain_pair_is_renamed(self, tmp_path):
        site = Site(tmp_path).files(R1, R2)
        assert site.run().returncode == 0
        assert site.length_of("R1") == R1
        assert site.length_of("R2") == R2


class TestTheGlobIsAnchoredToTheSample:
    """`*_1.fastq.gz` matches another sample's reads sitting in the directory."""

    def test_a_strays_reads_are_not_taken(self, tmp_path):
        site = Site(tmp_path).files(R1, R2)
        _fastq(site.dir / "SOMEONE_ELSE_1.fastq.gz", 12)
        done = site.run()
        assert done.returncode == 0, done.stderr
        assert site.length_of("R1") == R1, "the sample's own barcode read"
        assert "SOMEONE_ELSE_1.fastq.gz" in site.named(), "left alone"


class TestItRunsOnTheShellAClusterHas:
    """bash 3.2 is what macOS ships and what plenty of login nodes still have.

    While this needed bash 4 it could not be started outside the image, which
    is why a script that decides which reads Cell Ranger sees had no tests.
    """

    def test_no_bash_four_only_constructs(self):
        """Comments may name them; code may not."""
        code = "\n".join(line for line in SCRIPT.read_text().splitlines()
                         if not line.lstrip().startswith("#"))
        for construct in ("mapfile", "local -A", "declare -A", "readarray"):
            assert construct not in code, construct

    def test_it_parses_under_the_running_shell(self):
        done = subprocess.run(["bash", "-n", str(SCRIPT)],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr

    def test_the_second_line_is_read_portably(self):
        """BSD sed refuses `2{p;q}`, and which sed a cluster has is not a thing
        to discover from a failed run."""
        source = SCRIPT.read_text()
        assert "awk 'NR==2{print; exit}'" in source


class TestTheOtherWaysItCouldStillGuess:
    """Each of these was found after the read-length rule went in."""

    def test_a_neighbours_reads_are_not_collected(self, tmp_path):
        """The 3/4-file path globbed the whole directory.

        rename_two was anchored to the sample and this path was not, so a
        foreign barcode-length file was chosen as this sample's R1.
        """
        site = Site(tmp_path).files(I1, R2)
        _fastq(site.dir / "OTHERSAMPLE_2.fastq.gz", R1)
        (site.success / "fastq_3_files.tsv").write_text(site.sample + "\n")
        (site.success / "fastq_2_files.tsv").unlink()
        site.run()
        assert site.length_of("R1") is None, "a neighbour's read is not this sample's"
        assert (site.dir / "OTHERSAMPLE_2.fastq.gz").is_file()

    def test_two_cdna_length_reads_are_ambiguous(self, tmp_path):
        """The counter only moved when a LONGER one turned up, so a second
        cDNA-length read was invisible and the longest simply won."""
        site = Site(tmp_path).files(I1, R1, R2, 90)
        site.run()
        assert site.length_of("R1") is None
        assert "SRR000001" in site.ineligible()

    def test_two_cdna_reads_of_equal_length_are_ambiguous(self, tmp_path):
        site = Site(tmp_path).files(I1, R1, 90, 90)
        site.run()
        assert site.length_of("R1") is None

    def test_the_two_file_path_refuses_what_the_others_refuse(self, tmp_path):
        """98 and 8 bases is not a pair, and was renamed anyway.

        Cell Ranger got an 8-base R2 with a log line as the only record.
        """
        site = Site(tmp_path).files(R2, I1)
        site.run()
        assert site.length_of("R1") is None
        assert site.length_of("R2") is None
        assert "identify neither" in site.ineligible()

    def test_a_prenamed_neighbour_does_not_refuse_this_sample(self, tmp_path):
        """The token check globbed the directory, so someone else's R1 made
        this sample ineligible with a reason about itself."""
        site = Site(tmp_path).files(R1, R2)
        _fastq(site.dir / "OTHERSAMPLE_S1_R1_001.fastq.gz", R1)
        done = site.run()
        assert done.returncode == 0, done.stderr
        assert site.length_of("R1") == R1

    def test_the_header_does_not_describe_the_removed_heuristic(self):
        # The comment block at the top, which is what a reader reads first.
        header = "\n".join(SCRIPT.read_text().splitlines()[:40])
        assert "largest by size" not in header
        assert "length of the first read" in header
