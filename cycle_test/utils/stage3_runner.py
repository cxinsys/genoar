#!/usr/bin/env python3
"""
Stage 3 Runner - Execute Docker SRR pipeline on downloaded SRA files

Runs the genoar-srr:step9 Docker image to process SRA files through Cell Ranger.
"""

import subprocess
import logging
import json
import os
import re
import time
import uuid
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

try:
    from cycle_test.utils.container_runtime import (
        build_run_command, image_available, singularity_engine, sif_name_for, DOCKER, SINGULARITY)
except ImportError:  # direct-script execution
    from container_runtime import (
        build_run_command, image_available, singularity_engine, sif_name_for, DOCKER, SINGULARITY)

logger = logging.getLogger(__name__)

# Repository root: cycle_test/utils/stage3_runner.py -> <repo>
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Exit codes run_docker_pipeline.sh uses.
#   2 - the run is misconfigured (no cellranger executable, unusable reference).
#       Deliberately NOT a valid "nothing to do": a fault the user must fix.
#   3 - the run was valid and complete but Cell Ranger processed zero samples
#   4 - partial: some expected samples completed and others did not
EXIT_CONFIG_ERROR = 2
EXIT_NOTHING_PROCESSED = 3
EXIT_PARTIAL = 4

# Entries a usable 10x reference must have. Checked here AND by step 0 of
# run_docker_pipeline.sh, so a layout accepted by one is accepted by the other.
REFERENCE_REQUIRED_FILES = ("reference.json",)
REFERENCE_REQUIRED_DIRS = ("fasta", "genes", "star")

# Where the pipeline records what a given run was asked to do and what it
# achieved. Written under <results>/runs/<run_id>/.
RUN_RECORD_DIRNAME = "runs"
RUN_OUTCOME_NAME = "outcome.json"
# <results>/runs/LATEST names the newest run, so no run may be called that.
RUN_ID_RESERVED = "LATEST"

# A run id becomes a directory name under <results>/runs/. This is the crawler's
# IDENTITY_PATTERN (genoar_crawler.py) and the run-identity check in
# run_docker_pipeline.sh: one rule, three places that build a path from an id.
#
# Matched with fullmatch, never match. The anchors are not enough on their own:
# Python's `$` also matches immediately before a trailing newline, so
# `RUN_ID_PATTERN.match("good\n")` succeeds -- and that id then names a
# directory and travels into the container as GENOAR_RUN_ID, as a different
# string from the one that was approved. "LATEST\n" passes the same way and is
# not equal to "LATEST", so it slips past the reserved-name check too.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# What to tell the operator of a run that left no record of itself. Both causes
# are named because the runner cannot tell them apart from outside, and each has
# a different fix; "no run record" on its own would send the user looking for a
# file they have never heard of.
#
# The first cause is real and is in the wild: images built before run records
# existed run happily and process nothing, and a BAM from an earlier run lying
# on the persistent results volume is enough to make that look like success.
RUN_RECORD_ABSENT_ADVICE = (
    "Two things produce a run with no record, and each has its own fix. Either "
    "this Stage 3 image predates run records, in which case no run it performs "
    "can ever be verified — rebuild it with `make build-srr` (docker build -f "
    "srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 "
    ".) and run again. Or this run ended before it could write its record — the "
    "container output in docker_stdout.log, under this run's logs directory, "
    "says how far it got. Until one of the two is settled, this run has "
    "demonstrated nothing and its results directory should not be read as "
    "output."
)

# The file Cell Ranger leaves for a sample it really analysed. An `outs`
# directory can exist without it (a run killed partway), so the BAM is what the
# pipeline itself counts and what this runner counts too.
CELLRANGER_BAM_NAME = "possorted_genome_bam.bam"

# Written under results/success by the pipeline. The first is step 8's census,
# the second step 6's earlier one; either lets the runner say *why* nothing
# qualified instead of only that nothing did.
ELIGIBILITY_REPORTS = ("cellranger_eligibility.tsv", "cellranger_ineligible.tsv")


def _read_ineligible_reasons(success_dir: Path,
                             only: Optional[Set[str]] = None) -> Dict[str, int]:
    """Ineligible sample counts grouped by reason, from the pipeline's census.

    Both report layouts put the sample in column 1 and the reason in column 3,
    with an empty column 3 for eligible samples, so one reader covers both.
    `only` restricts the tally to the samples this run was asked to process:
    the success directory is persistent, so it can hold rows about samples that
    have nothing to do with this run.
    Best effort: a missing or unreadable census only costs the explanation.
    """
    reasons: Dict[str, int] = {}
    for name in ELIGIBILITY_REPORTS:
        report = success_dir / name
        if not report.is_file():
            continue
        try:
            with open(report) as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    fields = line.rstrip("\n").split("\t")
                    if len(fields) < 3 or not fields[2].strip():
                        continue
                    if only is not None and fields[0].strip() not in only:
                        continue
                    reason = fields[2].strip()
                    reasons[reason] = reasons.get(reason, 0) + 1
        except OSError:
            continue
        return reasons
    return reasons


def expected_samples_in(sra_dir: Path) -> List[str]:
    """The samples a run over `sra_dir` is being asked to process.

    Mirrors the manifest run_docker_pipeline.sh freezes inside the container:
    every SRR* entry, a file's name minus its .sra suffix, a directory's name.
    Deriving it here as well means the runner can scope its verdict to this
    run's samples even when it cannot read the container's manifest.
    """
    if not sra_dir.is_dir():
        return []
    samples = set()
    for entry in sra_dir.iterdir():
        if not entry.name.startswith("SRR"):
            continue
        if entry.is_dir():
            samples.add(entry.name)
        elif entry.is_file():
            samples.add(entry.name[:-4] if entry.name.endswith(".sra") else entry.name)
    return sorted(samples)


def validate_reference(ref_path: Optional[Path]) -> List[str]:
    """Everything wrong with a Cell Ranger reference directory.

    A directory called `ref` is not a reference. These are the same criteria
    step 0 of run_docker_pipeline.sh applies, so the two cannot disagree about
    whether a given install is usable.
    """
    if ref_path is None:
        return ["Reference genome path unresolved — install into <repo>/ref/ "
                "or pass --ref-genome-path explicitly."]
    if not ref_path.is_dir():
        return [f"Reference genome directory not found: {ref_path}"]
    problems = []
    for name in REFERENCE_REQUIRED_FILES:
        entry = ref_path / name
        if not entry.is_file() or entry.stat().st_size == 0:
            problems.append(f"{name} missing or empty in {ref_path}")
    for name in REFERENCE_REQUIRED_DIRS:
        if not (ref_path / name).is_dir():
            problems.append(f"{name}/ directory missing in {ref_path}")
    return problems


def reference_identity(ref_path: Path) -> Tuple[List[str], str]:
    """The genomes and release a reference names in its own reference.json.

    Returns ([], "") when the file cannot be read as JSON — validate_reference
    has already established that the file exists and is non-empty, so an
    unreadable one is a separate fault and reported as such by the caller.
    """
    try:
        data = json.loads((ref_path / "reference.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], ""
    genomes = data.get("genomes") or []
    if isinstance(genomes, str):
        genomes = [genomes]
    return [str(g) for g in genomes], str(data.get("version") or "")


def reference_matches_run(ref_path: Optional[Path], species: str,
                          reference_version: str) -> List[str]:
    """Why this reference is not the one the run says it is going to use.

    Two questions, both answered from reference.json rather than from the
    directory name, because a reference renamed to `ref/` carries no name:

      * does it cover `species`? A run that asks for mouse and is handed
        GRCh38 completes and reports counts against the wrong transcriptome.
      * is it the release named in `reference_version`? Two releases of one
        genome carry different gene annotations, so they are different
        measurements. The same comparison step 0 makes — every genome named
        must appear in the expected name, and so must the release — but a
        refusal here rather than a note there, because a note is read after
        the run it should have prevented.

    An empty `reference_version` is the opt-out and stays silent about the
    release; the species question is still asked, since nothing else asks it.
    """
    if ref_path is None:
        return []
    problems = validate_species(species)
    if problems:
        return problems

    genomes, release = reference_identity(ref_path)
    if not genomes:
        return [f"reference.json in {ref_path} names no genomes, so there is "
                f"nothing to check {species} against."]

    pattern = SPECIES_GENOME_PATTERNS[species]
    if not any(re.search(pattern, genome, re.I) for genome in genomes):
        problems.append(
            f"Reference {ref_path} is built on {', '.join(genomes)}, which is "
            f"not {species}. Point --ref-genome-path at a {species} reference "
            f"or set species to what this reference is."
        )

    if reference_version:
        wrong_genome = [g for g in genomes if g not in reference_version]
        if wrong_genome or not release or release not in reference_version:
            problems.append(
                f"Reference {ref_path} is {', '.join(genomes)} "
                f"{release or 'of an unstated release'}, and this run states "
                f"reference_version={reference_version!r}. Results from the two "
                f"are not the same measurement. Correct one of them, or clear "
                f"reference_version to accept whatever is installed."
            )

    return problems


def normalize_cellranger_root(cellranger_path: Optional[Path]) -> Optional[Path]:
    """The installation root, given either it or its bin/ directory.

    Cell Ranger runs out of its whole tree — it reads .env.json, external/,
    lib/ and mro/ beside the launcher — so the root is what has to be mounted.
    Mounting bin/ alone fails at startup with "Couldn't find the `.env.json`
    file", which is what happened on K-BDS, where 7.x keeps the launcher in
    bin/ and the path handed in pointed there.
    """
    if cellranger_path is None:
        return None
    if (cellranger_path / ".env.json").is_file():
        return cellranger_path
    parent = cellranger_path.parent
    if cellranger_path.name == "bin" and (parent / ".env.json").is_file():
        return parent
    return cellranger_path


def cellranger_binary(cellranger_root: Path) -> Optional[Path]:
    """The launcher inside an installation root, wherever the release puts it."""
    for candidate in (cellranger_root / "cellranger",
                      cellranger_root / "bin" / "cellranger"):
        if candidate.is_file():
            return candidate
    return None


def _cellranger_bin_in_container(cellranger_path: Optional[Path]) -> str:
    """The launcher's path once the installation root is mounted at /opt/cellranger."""
    if cellranger_path is None:
        return "/opt/cellranger/cellranger"
    root = normalize_cellranger_root(cellranger_path)
    binary = cellranger_binary(root)
    if binary is None:
        return "/opt/cellranger/cellranger"
    return "/opt/cellranger/" + str(binary.relative_to(root))


def validate_cellranger(cellranger_path: Optional[Path]) -> List[str]:
    """Everything wrong with a Cell Ranger install directory."""
    if cellranger_path is None:
        return ["Cell Ranger path unresolved — install into <repo>/cellranger/ "
                "or pass --cellranger-path explicitly."]
    cellranger_path = normalize_cellranger_root(cellranger_path)
    binary = cellranger_binary(cellranger_path)
    if binary is None:
        return [f"cellranger executable not found under {cellranger_path} "
                f"(looked there and in its bin/)"]
    if not os.access(binary, os.X_OK):
        return [f"cellranger at {binary} is not executable"]
    # A launcher is not an installation. Finding one and stopping there is how
    # a bin/ directory passed validation and then failed at Cell Ranger
    # startup — the run was declared ready by the same code that had just
    # looked at the wrong directory. .env.json is the file Cell Ranger names in
    # that error, so it is the file checked here.
    if not (cellranger_path / ".env.json").is_file():
        return [f"{cellranger_path} holds a cellranger launcher but no "
                f".env.json, so it is not a complete installation. Mounting it "
                f"fails at startup with \"Couldn't find the `.env.json` file\". "
                f"Point at the directory holding .env.json, external/, lib/ "
                f"and mro/."]
    return []


def normalize_run_id(run_id: Optional[str]) -> str:
    """The one form of a run id, from whatever form it arrived in.

    Surrounding whitespace is stripped, exactly as genoar_crawler.py strips it
    (`(env.get('GENOAR_RUN_ID') or '').strip()`) and as the shell gate does. The
    two halves of the pipeline read the same environment variable, so ` pilot `
    has to name the same run in both: refusing it here while the crawler ran on
    it would split a full-pipeline run in half, after the crawl.

    The hazard is not the stripping but validating one form and using another:
    `re.match`, whose `$` also matches before a trailing newline, approves
    "good\\n", which is then used, newline and all, to name a directory and set
    GENOAR_RUN_ID. Callers normalize here and keep what they get; what comes out
    of this function is what is validated, what names the path, and what the
    container is given.

    A value that is only whitespace says nothing, so it means "no id was set".
    """
    return "" if run_id is None else run_id.strip()


def validate_run_id(run_id: str) -> List[str]:
    """Everything wrong with a run id, before a container is started for it.

    The id names the directory that will hold this run's only record of what it
    did, so an id that is a path escapes the record tree, and an id that already
    names a run would write over that run's evidence. The pipeline refuses both
    (exit 2); refusing here as well means the caller is told before a container
    starts, and by the same rule.

    The value handed in is the value that will be used -- callers normalize with
    normalize_run_id first and keep what it returns -- so this judges the whole
    string it is given and refuses anything that is not already exactly a usable
    id. `validate_run_id(normalize_run_id(v))` is the question the shell gate
    asks of the same v, and the two answer alike for every v.
    """
    if not run_id:
        return ["Run id is empty."]
    if not RUN_ID_PATTERN.fullmatch(run_id):
        return [f"Run id {run_id!r} is not usable as a directory name; use "
                "letters, digits, '.', '_' or '-', starting with a letter or "
                "digit (max 64 characters)."]
    if run_id == RUN_ID_RESERVED:
        return [f"Run id {run_id!r} is reserved for the newest-run pointer "
                f"({RUN_RECORD_DIRNAME}/{RUN_ID_RESERVED})."]
    return []


def _cellranger_version_key(path: Path):
    """Sort key that orders `cellranger-8.0.1` after `cellranger-7.0.1`.

    Plain alphabetical ordering puts "7" before "8" and "10" before "7", so a
    site with two releases installed side by side gets whichever name happens
    to sort first — which on K-BDS would have meant 7.0.1 even with 8.0.1
    present. The numbers in the name are compared as numbers instead.

    Only the digits after `cellranger-` are read. Taking every number in the
    name would let anything else in it outrank the release — a directory named
    `cellranger-7.0.1-2024rebuild` would beat `cellranger-8.0.1`.
    """
    match = re.search(r"cellranger[-_]?v?(\d+(?:\.\d+)*)", path.name, re.I)
    if not match:
        return (0,)
    return tuple(int(n) for n in match.group(1).split("."))


# The genome builds each species is shipped under, so "which reference is the
# right one" is a lookup against a named species rather than a rule welded into
# a sort key. A species this table does not know is refused: ranking every
# reference equally would hand the run whichever the glob returned first, which
# is the failure the ranking exists to prevent.
SPECIES_GENOME_PATTERNS = {
    "human": r"GRCh\d+|(?<![A-Za-z])hg\d+",
    "mouse": r"GRCm\d+|(?<![A-Za-z])mm\d+",
}
# What a run is about unless it says otherwise.
DEFAULT_SPECIES = "human"


def validate_species(species: str) -> List[str]:
    """Why `species` cannot be used to choose a reference."""
    if species in SPECIES_GENOME_PATTERNS:
        return []
    return [f"Unknown species {species!r}; known: "
            f"{', '.join(sorted(SPECIES_GENOME_PATTERNS))}."]


def _species_genome_pattern(species: str) -> str:
    problems = validate_species(species)
    if problems:
        raise ValueError(problems[0])
    return SPECIES_GENOME_PATTERNS[species]


def _reference_release_key(path: Path, species: str = DEFAULT_SPECIES):
    """Sort key for 10x references: the run's species first, then release.

    A reference is named `refdata-gex-<genome>-<year>-<letter>`, and the
    release — the trailing year and letter — is what carries the gene
    annotation. Reading every number in the name instead compares the genome
    build first, and 39 beats 38: `refdata-gex-GRCm39-2024-A` outranks
    `refdata-gex-GRCh38-2024-A`, and a human run gets the mouse transcriptome.
    That run completes. Its numbers mean nothing, and nothing in the log says
    so.

    So the genome decides first — a reference for `species` wins whatever its
    release — and the release only breaks ties within one genome. `-2020-B`
    beats `-2020-A` there, where taking whichever the glob returned first used
    to. Which genome that is comes from `species`, not from this function: a
    checkout holding both GRCh38 and GRCm39 has a right answer for either run,
    and hard-coding one of them made the other unreachable.

    A combined reference ranks below a single-species one rather than beside
    it. 10x ships `refdata-gex-GRCh38-and-GRCm39-2024-A` for barnyard
    experiments; it names GRCh38, so testing for that alone called it human and
    let it beat a human-only reference of an earlier release. Counting one
    species' reads against it puts roughly half the features behind the other's
    prefix — again a run that completes and means something other than what it
    says.
    """
    name = path.name
    wanted = _species_genome_pattern(species)
    others = [pattern for key, pattern in SPECIES_GENOME_PATTERNS.items()
              if key != species]
    has_wanted = bool(re.search(wanted, name, re.I))
    has_other = bool(re.search("|".join(others + ["-and-"]), name, re.I))
    if has_wanted:
        rank = 1 if has_other else 2
    else:
        rank = 0
    match = re.search(r"-(\d{4})-([A-Za-z]+)$", name)
    if not match:
        return (rank, 0, "")
    return (rank, int(match.group(1)), match.group(2).upper())


def detect_cellranger_path() -> Optional[Path]:
    """Locate a Cell Ranger install relative to the project root.

    Returns the directory containing the `cellranger` binary, or None if
    no candidate exists. Search order:
      1. <repo>/cellranger/cellranger                          (README convention)
      2. <repo>/cellranger-*/cellranger                        (un-renamed extract)
      3. <repo>/srr_pipeline_package/tools/cellranger-*/cellranger
    """
    # Newest first within each group: an explicit <repo>/cellranger still wins,
    # but among versioned extracts the highest version is the one meant.
    candidates = [PROJECT_ROOT / "cellranger"]
    candidates.extend(sorted(PROJECT_ROOT.glob("cellranger-*"),
                             key=_cellranger_version_key, reverse=True))
    candidates.extend(sorted((PROJECT_ROOT / "srr_pipeline_package" / "tools").glob("cellranger-*"),
                             key=_cellranger_version_key, reverse=True))
    for candidate in candidates:
        if (candidate / "cellranger").exists() or (candidate / "bin" / "cellranger").exists():
            return candidate
    return None


def detect_ref_genome_path(species: str = DEFAULT_SPECIES) -> Optional[Path]:
    """Locate a 10x reference genome for `species` relative to the project root.

    Search order:
      1. <repo>/ref                     (README convention)
      2. <repo>/refdata-gex-*           (un-renamed extract)

    A `<repo>/ref` that is not this species' reference is not caught here — the
    name says nothing about what is inside it. check_prerequisites reads
    reference.json and refuses on the answer.
    """
    ref_dir = PROJECT_ROOT / "ref"
    if ref_dir.is_dir():
        return ref_dir
    # Newest first for the same reason: refdata-gex-GRCh38-2024-A must not lose
    # to 2020-A just because "2020" sorts earlier. The two carry different gene
    # annotations, so the choice changes the numbers. The run's own species
    # first, before that: another species' reference sitting beside it is not a
    # fallback, it is a wrong answer that runs to completion.
    for candidate in sorted(PROJECT_ROOT.glob("refdata-gex-*"),
                            key=lambda path: _reference_release_key(path, species),
                            reverse=True):
        if candidate.is_dir():
            return candidate
    return None


# What this pipeline was developed and validated against, from
# DOCKER_USAGE_GUIDE.md, which installs cellranger-8.0.1 and
# refdata-gex-GRCh38-2024-A. Step 0 compares the installed pair against these
# and says so when they differ: a Cell Ranger release changes which arguments
# exist, and a reference release changes the gene counts, so a run on another
# pair is a different measurement rather than a different setup.
#
# These are written into every generated config. configs/example.yaml carries
# the same two values, but nothing copies that file — every real run gets its
# config from generate_config() here or from render_config_yaml() in
# prepare_hpc_handoff.py, and while neither wrote the keys, step 0 had nothing
# to compare and the check silently did nothing on both paths.
VALIDATED_CELLRANGER_VERSION = "8.0.1"
VALIDATED_REFERENCE_VERSION = "refdata-gex-GRCh38-2024-A"

# Module-level defaults, resolved at import time. May be None when nothing is
# installed yet; callers must surface a clear error in that case.
DEFAULT_CELLRANGER_PATH: Optional[Path] = detect_cellranger_path()
DEFAULT_REF_GENOME_PATH: Optional[Path] = detect_ref_genome_path()


class Stage3PipelineRunner:
    """Run SRR Docker pipeline on downloaded SRA files."""

    DOCKER_IMAGE = "genoar-srr:step9"

    def __init__(
        self,
        sra_dir: Path,
        results_dir: Path,
        logs_dir: Path,
        cellranger_path: Optional[Path] = None,
        ref_genome_path: Optional[Path] = None,
        cores: int = 16,
        gzip_threads: int = 2,
        cellranger_threads: int = 16,
        cellranger_mem: int = 100,
        runtime: str = DOCKER,
        sif_dir: str = ".",
        run_id: Optional[str] = None,
        cellranger_version: str = VALIDATED_CELLRANGER_VERSION,
        reference_version: str = VALIDATED_REFERENCE_VERSION,
        species: str = DEFAULT_SPECIES,
    ):
        """
        Initialize pipeline runner.

        Args:
            sra_dir: Directory containing downloaded SRA files
            results_dir: Output directory for pipeline results
            logs_dir: Directory for pipeline logs
            cellranger_path: Path to Cell Ranger installation. When None,
                falls back to DEFAULT_CELLRANGER_PATH (auto-detected from
                the project root).
            ref_genome_path: Path to Cell Ranger reference genome. When None,
                auto-detected from the project root for `species`.
            cores: Number of cores for parallel processing
            gzip_threads: Threads for gzip compression
            cellranger_threads: Threads for Cell Ranger
            cellranger_mem: Memory (GB) for Cell Ranger
            cellranger_version: what the results are meant to be comparable
                with. Step 0 reports a difference against what is installed.
                Empty accepts anything without comment.
            reference_version: the same, for the transcriptome release, and a
                refusal rather than a note: a reference whose own
                reference.json disagrees stops the run before a container
                starts. Empty opts out of the release comparison only — the
                species check below still applies.
            species: what the run is about. Decides which reference an
                auto-detected install resolves to, and is checked against the
                reference's own reference.json before a container starts.
        """
        species_problems = validate_species(species)
        if species_problems:
            raise ValueError(species_problems[0])
        self.species = species

        self.sra_dir = Path(sra_dir)
        self.results_dir = Path(results_dir)
        self.logs_dir = Path(logs_dir)

        resolved_cellranger = cellranger_path if cellranger_path is not None else DEFAULT_CELLRANGER_PATH
        # Detected against this run's species rather than reusing the
        # module-level default, which was resolved for DEFAULT_SPECIES.
        resolved_ref_genome = (ref_genome_path if ref_genome_path is not None
                               else detect_ref_genome_path(self.species))
        self.cellranger_path: Optional[Path] = Path(resolved_cellranger) if resolved_cellranger else None
        self.ref_genome_path: Optional[Path] = Path(resolved_ref_genome) if resolved_ref_genome else None

        self.cores = cores
        self.gzip_threads = gzip_threads
        self.cellranger_threads = cellranger_threads
        self.cellranger_mem = cellranger_mem
        self.runtime = runtime
        self.sif_dir = sif_dir
        self.cellranger_version = cellranger_version
        self.reference_version = reference_version

        # One id per invocation, shared with the container so that its run
        # record and this runner's report describe the same thing. A resume is
        # a new run over the same expected set: it gets its own id, finds the
        # earlier output in place, and reports those samples as cache hits.
        #
        # Normalized once, here, and used everywhere after: run_record_dir, the
        # GENOAR_RUN_ID the container is given, the generated config, and the
        # value check_prerequisites validates are all this one string.
        self.run_id = normalize_run_id(run_id) or "run-{}-{}".format(
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            uuid.uuid4().hex[:8],
        )

    @property
    def run_record_dir(self) -> Path:
        return self.results_dir / RUN_RECORD_DIRNAME / self.run_id

    def check_prerequisites(self) -> Dict[str, Any]:
        """Check all prerequisites before running pipeline.

        `configuration_errors` are faults the user has to fix — a missing
        executable, a reference directory that is not a reference, a run id that
        is not usable as one. They are separated from the rest because a
        misconfigured run must never be reported as a valid run that simply had
        nothing to do.
        """
        checks = {
            "docker_available": False,
            "docker_image_exists": False,
            "sra_dir_exists": False,
            "sra_files_count": 0,
            "cellranger_exists": False,
            "ref_genome_exists": False,
            "run_id_usable": False,
            "configuration_errors": [],
            "errors": []
        }

        # Check container engine (docker or singularity/apptainer)
        if self.runtime == SINGULARITY:
            engine = singularity_engine()
            try:
                subprocess.run([engine, "--version"], capture_output=True, check=True)
                checks["docker_available"] = True
            except Exception:
                checks["errors"].append(f"{engine} not available")
        else:
            try:
                subprocess.run(["docker", "--version"], capture_output=True, check=True)
                checks["docker_available"] = True
            except Exception:
                checks["errors"].append("Docker not available")

        # Check image (docker image, or .sif for singularity)
        if checks["docker_available"]:
            if image_available(self.DOCKER_IMAGE, runtime=self.runtime, sif_dir=self.sif_dir):
                checks["docker_image_exists"] = True
            elif self.runtime == SINGULARITY:
                checks["errors"].append(
                    f"Singularity image not found: {Path(self.sif_dir) / sif_name_for(self.DOCKER_IMAGE)}")
            else:
                checks["errors"].append(f"Docker image {self.DOCKER_IMAGE} not found")

        # Check SRA directory
        if self.sra_dir.exists():
            checks["sra_dir_exists"] = True
            sra_files = list(self.sra_dir.glob("*/*.sra"))
            checks["sra_files_count"] = len(sra_files)
            if not sra_files:
                checks["errors"].append("No SRA files found in SRA directory")
        else:
            checks["errors"].append(f"SRA directory not found: {self.sra_dir}")

        # Check Cell Ranger: the executable itself, not a directory of that name
        cellranger_problems = validate_cellranger(self.cellranger_path)
        checks["cellranger_exists"] = not cellranger_problems
        checks["configuration_errors"].extend(cellranger_problems)

        # Check the reference genome's structure, not just that a path exists
        ref_problems = validate_reference(self.ref_genome_path)
        checks["ref_genome_exists"] = not ref_problems
        checks["configuration_errors"].extend(ref_problems)

        # And then what it actually is. Structure only says a reference is
        # usable; this says it is the one this run claims to be using. Asked
        # only once the structure holds, or reference.json would not be there
        # to read.
        if not ref_problems:
            checks["configuration_errors"].extend(
                reference_matches_run(self.ref_genome_path, self.species,
                                      self.reference_version))

        # Check the run id before a container is started under it. This is a
        # courtesy, not the reservation: between this look and the container's
        # first write another run could take the id, so the pipeline itself
        # claims the run directory with a single atomic mkdir and refuses (exit
        # 2) when that fails. What this buys is a clear message seconds earlier,
        # for the case the operator can actually see coming.
        run_id_problems = validate_run_id(self.run_id)
        if not run_id_problems and self.run_record_dir.exists():
            run_id_problems = [
                f"Run id {self.run_id!r} already has a run record at "
                f"{self.run_record_dir}; a run record is never overwritten. "
                "Choose a different id."
            ]
        checks["run_id_usable"] = not run_id_problems
        checks["configuration_errors"].extend(run_id_problems)

        if cellranger_problems or ref_problems:
            checks["configuration_errors"].append(
                "Cell Ranger needs an executable and a 10x reference containing "
                + ", ".join(list(REFERENCE_REQUIRED_FILES)
                            + [d + "/" for d in REFERENCE_REQUIRED_DIRS])
                + "."
            )
        checks["errors"].extend(checks["configuration_errors"])

        return checks

    def generate_config(self) -> Path:
        """
        Generate cycle-specific config.yaml for Docker pipeline.

        Returns:
            Path to generated config file
        """
        config = {
            "input_dir": "/work/data/sra",
            "output_dir": "/work/results",
            "ref_path": "/ref",
            "cores": self.cores,
            "gzip_threads": self.gzip_threads,
            "cellranger_threads": self.cellranger_threads,
            "cellranger_mem": self.cellranger_mem,
            # Where the launcher sits inside the mounted root differs by
            # release; 7.x keeps it in bin/. The container also falls back,
            # but naming it here keeps the config honest about what will run.
            "cellranger_bin": _cellranger_bin_in_container(self.cellranger_path),
            # What the results are meant to be comparable with. Step 0 reads
            # the installed version and reference release and reports any
            # difference; without these keys it has nothing to compare against
            # and stays silent, which is what it did before.
            "cellranger_version": self.cellranger_version,
            "reference_version": self.reference_version,
            # What the run is about, and so which reference is the right one.
            "species": self.species,
            "download": False,
            "log_dir": "/work/logs",
            # Second channel for the run id, for runtimes where env does not
            # cross the boundary. The pipeline prefers GENOAR_RUN_ID.
            "run_id": self.run_id,
        }

        # Ensure parent directory exists
        self.results_dir.parent.mkdir(parents=True, exist_ok=True)

        config_path = self.results_dir.parent / "config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)

        logger.info(f"Generated config: {config_path}")
        return config_path

    def build_docker_command(self, config_path: Path) -> List[str]:
        """
        Build Docker run command with all required mounts.

        Args:
            config_path: Path to generated config.yaml

        Returns:
            List of command arguments
        """
        return build_run_command(
            self.DOCKER_IMAGE,
            mounts=[
                (str(self.sra_dir.resolve()), "/work/data/sra", False),
                (str(config_path.resolve()), "/work/config.yaml", False),
                (str(self.ref_genome_path.resolve()), "/ref", False),
                # The installation root, never its bin/: Cell Ranger reads
                # .env.json and the rest of the tree beside the launcher.
                (str(normalize_cellranger_root(self.cellranger_path).resolve()),
                 "/opt/cellranger", False),
                (str(self.logs_dir.resolve()), "/work/logs", False),
                (str(self.results_dir.resolve()), "/work/results", False),
            ],
            runtime=self.runtime,
            sif_dir=self.sif_dir,
            docker_flags=["-e", f"GENOAR_RUN_ID={self.run_id}"],
        )

    def container_env(self) -> Dict[str, str]:
        """The environment the container is started in.

        The run id is set here as well as on the command line, because the two
        runtimes take it differently: `docker run` forwards nothing it was not
        given (`-e`), while apptainer/singularity hands the host environment
        straight to the container — and drops the `-e` flags, which is why the
        id also travels in the generated config.

        An operator who exports GENOAR_RUN_ID for the crawl therefore has it
        inherited by a Stage 3 SIF, which prefers it over the config and records
        itself under THAT id while this runner looks under its own. The run
        record is written, correctly, where nobody is looking, and the runner
        sees no record for its run — reported as a run that left no record, the
        right refusal to the wrong question. One id, set everywhere the
        container could read one, keeps the record and the lookup in one place.
        """
        env = dict(os.environ)
        for name in ("GENOAR_RUN_ID", "SINGULARITYENV_GENOAR_RUN_ID",
                     "APPTAINERENV_GENOAR_RUN_ID"):
            env[name] = self.run_id
        return env

    def load_run_outcome(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """(outcome, problem): the run's own record, or why there is none.

        Authoritative when present: it was written inside the run, against the
        input manifest frozen before step 1, with provenance receipts tying each
        BAM to the bytes it came from.

        `problem` is a sentence naming what is wrong when there is no usable
        record, because the three ways that happens need three different
        answers from the operator: no file at all (an image that cannot write
        one, or a run that died first), a file that does not parse (a run
        interrupted mid-write), and a record about some other run (this run
        never wrote its own).
        """
        path = self.run_record_dir / RUN_OUTCOME_NAME
        try:
            with open(path) as f:
                outcome = json.load(f)
        except FileNotFoundError:
            return None, f"{path} was never written"
        except OSError as exc:
            return None, f"{path} could not be read ({exc})"
        except ValueError:
            return None, (f"{path} is not readable as a run record; the run "
                          "was interrupted while writing it")
        if not isinstance(outcome, dict):
            return None, f"{path} is not a run record"
        if outcome.get("run_id") != self.run_id:
            return None, (f"{path} records run {outcome.get('run_id')!r}, not "
                          f"this run ({self.run_id!r})")
        return outcome, None

    def read_run_outcome(self) -> Optional[Dict[str, Any]]:
        """This run's record, or None when it left none."""
        return self.load_run_outcome()[0]

    def parse_results(self) -> Dict[str, Any]:
        """
        Parse pipeline results from reports directory.

        Returns:
            Dict with parsed results
        """
        results = {
            "step_status": {},
            "step_messages": {},
            "success_samples": [],
            "failed_samples": [],
            "total_samples": 0,
            "successful_samples": 0,
            "failed_samples_count": 0,
            # Samples with real Cell Ranger output on disk. Always present, so a
            # caller can require it without having to know whether the success
            # directory happened to exist.
            "cellranger_completed": 0,
            "cellranger_samples": [],
            "cellranger_ineligible": 0,
            "cellranger_ineligible_reasons": {},
            # The samples THIS run was asked to process. Completion is measured
            # against this set, never against whatever happens to be on the
            # persistent results volume.
            "expected_samples": [],
            "expected_count": 0,
            "cellranger_fresh": 0,
            "cellranger_cache_hits": 0,
            "cellranger_adopted": 0,
            "cellranger_adopted_samples": [],
            "cellranger_missing": 0,
            "cellranger_unverified": 0,
            "cellranger_unverified_samples": [],
            "cellranger_stale": 0,
            # True when the counts come from the container's run record, i.e.
            # each completion was checked against the input it came from.
            "provenance_verified": False,
            "other_samples_with_output": [],
            # Where this run's record should be, and — when there is nothing
            # usable there — what is wrong with it and what to do about it.
            # A run with a problem here has proved nothing, whatever is on disk.
            "run_record_path": "",
            "run_record_problem": None,
        }

        reports_dir = self.results_dir / "reports"

        # Parse step status files. The pipeline writes exactly one sentinel per
        # step: .ok did the work, .err failed, .warn ran to completion without
        # doing the work it exists for (step 8 with zero eligible samples). Only
        # reading .ok/.err made a .warn step indistinguishable from one that
        # never ran, which is the wrong answer for the right reason.
        for i in range(0, 10):
            step_ok = reports_dir / f"step{i}.ok"
            step_err = reports_dir / f"step{i}.err"
            step_warn = reports_dir / f"step{i}.warn"
            step_msg = reports_dir / f"step{i}.msg"

            if step_ok.exists():
                results["step_status"][f"step{i}"] = "ok"
            elif step_err.exists():
                results["step_status"][f"step{i}"] = "error"
            elif step_warn.exists():
                results["step_status"][f"step{i}"] = "warn"
            else:
                results["step_status"][f"step{i}"] = "not_run"

            if step_msg.exists():
                try:
                    message = step_msg.read_text().strip()
                except OSError:
                    message = ""
                if message:
                    results["step_messages"][f"step{i}"] = message

        # Parse success/failed lists
        fastq_success = self.results_dir / "fastq_success.txt"
        if fastq_success.exists():
            with open(fastq_success) as f:
                results["success_samples"] = [line.strip() for line in f if line.strip()]

        fastq_failed = self.results_dir / "fastq_failed.txt"
        if fastq_failed.exists():
            with open(fastq_failed) as f:
                results["failed_samples"] = [line.strip() for line in f if line.strip()]

        # Count Cell Ranger outputs for the samples this run was asked to
        # process. Counting every BAM under results/success instead meant a
        # single leftover from an earlier run could carry a run that analysed
        # nothing: results live on a persistent volume.
        success_dir = self.results_dir / "success"
        expected = expected_samples_in(self.sra_dir)
        results["expected_samples"] = expected
        results["expected_count"] = len(expected)
        expected_set = set(expected)

        outcome, record_problem = self.load_run_outcome()
        results["run_record_path"] = str(self.run_record_dir / RUN_OUTCOME_NAME)
        if outcome is not None:
            counts = outcome.get("counts", {})
            samples = outcome.get("samples", {})
            if outcome.get("expected_samples"):
                results["expected_samples"] = list(outcome["expected_samples"])
                results["expected_count"] = len(results["expected_samples"])
                expected_set = set(results["expected_samples"])
            results["provenance_verified"] = True
            results["cellranger_completed"] = counts.get("completed", 0)
            results["cellranger_fresh"] = counts.get("fresh", 0)
            results["cellranger_cache_hits"] = counts.get("cache_hit", 0)
            results["cellranger_adopted"] = counts.get("adopted", 0)
            results["cellranger_missing"] = counts.get("missing", 0)
            results["cellranger_unverified"] = counts.get("unverified", 0)
            results["cellranger_stale"] = counts.get("stale", 0)
            results["cellranger_ineligible"] = counts.get("ineligible", 0)
            # The samples counted as completed, and only those. `adopted` is
            # pre-existing output the operator told the run to use without any
            # provenance to check it against, so it is reported (as
            # cellranger_adopted) and never counted as a completion.
            results["cellranger_samples"] = sorted(
                name for name, rec in samples.items()
                if rec.get("status") in ("fresh", "cache_hit")
            )
            results["cellranger_adopted_samples"] = sorted(
                name for name, rec in samples.items()
                if rec.get("status") == "adopted"
            )
            reasons: Dict[str, int] = {}
            for rec in samples.values():
                if rec.get("status") in ("ineligible", "missing", "unverified", "stale"):
                    reason = rec.get("reason") or rec.get("status")
                    reasons[reason] = reasons.get(reason, 0) + 1
            results["cellranger_ineligible_reasons"] = reasons
            results["other_samples_with_output"] = list(
                outcome.get("other_samples_with_output", []))
            # The record's own arithmetic, checked rather than taken. `completed`
            # decides success, and it is a scalar sitting beside the per-sample
            # statuses it is supposed to summarise: reading one without the
            # other is how `adopted` was once counted as completed. They can
            # only disagree if something wrote a record this reader must not
            # act on, so the disagreement is the answer.
            if samples:
                counted = len(results["cellranger_samples"])
                if counted != results["cellranger_completed"]:
                    results["run_record_problem"] = (
                        f"the run record {results['run_record_path']} "
                        "contradicts itself: it counts "
                        f"{results['cellranger_completed']} completed sample(s) "
                        f"and lists {counted} whose status is a verified one "
                        "(fresh or cache_hit). A record that does not agree "
                        "with itself cannot say what this run produced, so "
                        "nothing in it is credited. Report this run as failed "
                        "and re-run it."
                    )
                    # Nothing in it is credited, so nothing in it is carried
                    # into the counts that mean "verified".
                    results["cellranger_completed"] = 0
                    results["cellranger_fresh"] = 0
                    results["cellranger_cache_hits"] = 0
                    results["cellranger_samples"] = []
        else:
            # No usable run record. That is not a quiet fallback case: this run
            # made no statement about what it did, and nothing on the results
            # volume can stand in for one. The volume is persistent, so the
            # output of every earlier run is sitting there whether or not this
            # run analysed a thing — counting those BAMs by name was exactly the
            # false success the run record exists to prevent, and it survived
            # here as the "no run record" branch of the very code that replaced
            # it.
            #
            # What is on disk is still worth reporting, as what it is: output
            # this run cannot account for. It explains the failure; it never
            # softens it.
            if success_dir.exists():
                on_disk = sorted(
                    bam.parents[2].name
                    for bam in success_dir.glob(
                        f"*/cellranger_output/outs/{CELLRANGER_BAM_NAME}")
                    if bam.is_file()
                )
                uncredited = [s for s in on_disk if s in expected_set]
                results["cellranger_unverified"] = len(uncredited)
                results["cellranger_unverified_samples"] = uncredited
                results["other_samples_with_output"] = [s for s in on_disk
                                                        if s not in expected_set]
                reasons = _read_ineligible_reasons(success_dir, only=expected_set)
                results["cellranger_ineligible_reasons"] = reasons
                results["cellranger_ineligible"] = sum(reasons.values())
                results["cellranger_missing"] = max(
                    0, len(expected_set) - len(uncredited) - sum(reasons.values()))
            else:
                results["cellranger_missing"] = len(expected_set)
            uncredited = results.get("cellranger_unverified_samples") or []
            results["run_record_problem"] = (
                f"{record_problem}, so this run made no statement about what it "
                "processed. Nothing on the results volume can be credited to "
                "it: results are persistent, so output from earlier runs is "
                "there whether or not this run analysed anything"
                + (f" ({len(uncredited)} of the {len(expected_set)} expected "
                   "sample(s) already hold Cell Ranger output that nothing "
                   "accounts for: " + ", ".join(uncredited) + ")"
                   if uncredited else "")
                + ". " + RUN_RECORD_ABSENT_ADVICE
            )

        results["successful_samples"] = len(results["success_samples"])
        results["failed_samples_count"] = len(results["failed_samples"])
        results["total_samples"] = results["successful_samples"] + results["failed_samples_count"]

        return results

    @staticmethod
    def _decide_outcome(result: Dict[str, Any]) -> None:
        """Set status / success / partial / nothing_processed, in place.

        Stage 3 exists to produce Cell Ranger output for the samples it was
        given, so success means every one of them has it. Counting BAMs alone
        called a run successful when one sample of two finished — and, before
        the expected set existed, when the only BAM on the volume belonged to a
        different run entirely.

        The outcomes, in the order they are decided:
          error set                     -> failed
          no usable run record          -> failed (this run said nothing)
          expected == 0                 -> nothing_processed (no work was asked for)
          completed == expected         -> success
          0 < completed < expected      -> partial_success, with the reasons named
          completed == 0                -> nothing_processed, with the reason named

        `completed` counts verified completions only: this run's own Cell Ranger
        work, and output an earlier run's receipt vouches for against the same
        input. Samples adopted through GENOAR_ADOPT_PRIOR_RESULTS=1 are output
        the operator told the run to use without evidence, so they are named in
        every verdict they affect and counted towards none of them. A run whose
        only "completion" is adopted therefore analysed nothing, and says so.

        Where the pipeline's own verdict and the filesystem disagree, the
        disagreement itself is the failure: neither claim can be trusted enough
        to call the run successful.
        """
        if result["error"] is not None:
            result["success"] = False
            result["status"] = ("config_error" if result.get("configuration_error")
                                else "failed")
            return

        if result.get("run_record_problem"):
            # The container came back without a usable account of itself. There
            # is nothing here to grade: not a success, not a valid empty run,
            # not a partial one — those are all verdicts about work, and no
            # verdict is available. The pipeline's own banner refuses on the
            # same grounds (load_provenance_outcome exits 1 when its outcome
            # record is missing); this is that refusal, on this side of the
            # container boundary, where an older image can still be the thing
            # that came back.
            result["success"] = False
            result["status"] = "failed"
            result["error"] = result["run_record_problem"]
            logger.error(result["error"])
            return

        completed = result["cellranger_completed"]
        expected = result.get("expected_count", 0)
        adopted = result.get("cellranger_adopted", 0)
        adopted_note = (
            f"; {adopted} sample(s) hold pre-existing output adopted on request "
            "(GENOAR_ADOPT_PRIOR_RESULTS=1), which is not verified and does not "
            "count as completed" if adopted else ""
        )

        if result["returncode"] == EXIT_NOTHING_PROCESSED and completed > 0:
            # The pipeline exits 3 only when no expected sample completed, so
            # finding some here means one of the two counts is wrong.
            result["success"] = False
            result["status"] = "failed"
            result["error"] = (
                f"Pipeline exited {EXIT_NOTHING_PROCESSED} (nothing processed) but "
                f"{completed} sample(s) have Cell Ranger output; "
                "the run and its results disagree."
            )
            logger.error(result["error"])
            return

        if completed and completed == expected:
            result["success"] = True
            result["status"] = "success"
            return

        result["success"] = False

        reasons = result.get("cellranger_ineligible_reasons") or {}
        census = "; ".join(
            f"{count} sample(s): {reason}" for reason, count in sorted(reasons.items())
        )

        if 0 < completed < expected:
            # Documented as partial_success and now measurable: some of the
            # samples this run was given were analysed and others were not.
            result["status"] = "partial_success"
            result["partial"] = True
            result["partial_reason"] = (
                f"{completed} of {expected} expected sample(s) have verified "
                f"Cell Ranger output{'; ' + census if census else ''}"
                f"{adopted_note}"
            )
            logger.warning(f"Stage 3 partial: {result['partial_reason']}")
            return

        if completed > expected:
            # More output than samples asked for: the counts describe different
            # sets, so neither can be trusted to declare the run complete.
            result["status"] = "failed"
            result["error"] = (
                f"{completed} sample(s) have Cell Ranger output but only {expected} "
                "were expected for this run; the run and its results disagree."
            )
            logger.error(result["error"])
            return

        result["nothing_processed"] = True
        result["status"] = "nothing_processed"

        step8 = result.get("step_status", {}).get("step8", "not_run")

        if expected == 0:
            result["nothing_processed_reason"] = (
                "this run was given no samples to process: no SRR input was "
                f"found in {result.get('sra_dir', 'the SRA directory')}"
            )
            return

        if step8 == "ok":
            # The pipeline claims step 8 did its work but left no BAM behind.
            # The two cannot both be true, and a contradiction is not a clean
            # empty result, so it is reported as a failure.
            result["error"] = (
                "Pipeline marked step8 ok but no sample has Cell Ranger output "
                f"({CELLRANGER_BAM_NAME} missing under results/success)."
            )
            result["nothing_processed"] = False
            logger.error(result["error"])
            return

        if result["returncode"] == EXIT_NOTHING_PROCESSED:
            reason = "the pipeline reported a valid run in which no sample qualified for Cell Ranger"
        elif step8 == "warn":
            reason = "step 8 finished without running a single Cell Ranger job"
        elif step8 == "not_run":
            reason = (
                "the Cell Ranger stage did not run — this image may be a "
                "step1..step7 build target, which performs no analysis"
            )
        elif adopted:
            reason = (
                "no expected sample has verified Cell Ranger output; this run "
                "analysed nothing"
            )
        else:
            reason = "no sample has Cell Ranger output"

        result["nothing_processed_reason"] = (
            f"{reason}{'; ' + census if census else ''}{adopted_note}")

    def run_pipeline(self, timeout_hours: int = 24) -> Dict[str, Any]:
        """
        Run complete SRR pipeline via Docker.

        Args:
            timeout_hours: Maximum hours to run pipeline

        Returns:
            Dict with pipeline results
        """
        result = {
            "success": False,
            "prerequisites_ok": False,
            # did work / did nothing / partly did it / broke / misconfigured
            "status": "failed",
            "run_id": self.run_id,
            "sra_dir": str(self.sra_dir),
            # total/successful/failed_samples count FASTQ *conversion*, which is
            # what fastq_success.txt / fastq_failed.txt record. They say nothing
            # about Cell Ranger; cellranger_completed does.
            "total_samples": 0,
            "successful_samples": 0,
            "failed_samples": 0,
            "success_list": [],
            "failed_list": [],
            "step_status": {},
            "expected_samples": [],
            "expected_count": 0,
            "cellranger_completed": 0,
            # True when the run was valid and complete but analysed no sample.
            # Distinct from an error, and never a success.
            "nothing_processed": False,
            "nothing_processed_reason": None,
            # True when some expected samples were analysed and others were not.
            "partial": False,
            "partial_reason": None,
            # True when the install itself is unusable. Never a valid empty run.
            "configuration_error": False,
            # Set when this run left no record this runner may act on. Every
            # count below is then zero, whatever is on the results volume.
            "run_record_path": "",
            "run_record_problem": None,
            "cellranger_unverified_samples": [],
            "returncode": None,
            "duration_seconds": 0,
            "error": None
        }

        try:
            # Check prerequisites
            logger.info("Checking prerequisites...")
            prereq = self.check_prerequisites()

            if prereq["errors"]:
                result["error"] = "; ".join(prereq["errors"])
                if prereq.get("configuration_errors"):
                    result["configuration_error"] = True
                    result["status"] = "config_error"
                    logger.error(
                        "Stage 3 is misconfigured and cannot run. This is not an "
                        "empty result; fix the install and run again:")
                    for problem in prereq["configuration_errors"]:
                        logger.error(f"  - {problem}")
                else:
                    logger.error(f"Prerequisites check failed: {result['error']}")
                return result

            result["prerequisites_ok"] = True
            logger.info(f"Prerequisites OK. Found {prereq['sra_files_count']} SRA files")

            # Create directories
            self.results_dir.mkdir(parents=True, exist_ok=True)
            self.logs_dir.mkdir(parents=True, exist_ok=True)

            # Generate config
            config_path = self.generate_config()

            # Build Docker command
            docker_cmd = self.build_docker_command(config_path)
            logger.info(f"Docker command: {' '.join(docker_cmd)}")

            # Run Docker with real-time log streaming.
            # Cell Ranger runs take hours, so we forward stdout line-by-line
            # to the master log (like Stage 1/2) and persist to docker_stdout.log.
            # stderr is merged into stdout so a single stream captures everything.
            logger.info("Starting Docker pipeline...")
            start_time = time.time()

            stdout_log = self.logs_dir / "docker_stdout.log"
            stderr_log = self.logs_dir / "docker_stderr.log"  # kept for backward compat

            with open(stdout_log, 'w') as log_handle:
                log_handle.write(f"=== Stage 3 Docker Execution ===\n")
                log_handle.write(f"Command: {' '.join(docker_cmd)}\n\n")
                log_handle.flush()

                proc = subprocess.Popen(
                    docker_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=self.container_env(),
                )

                deadline = time.monotonic() + timeout_hours * 3600
                try:
                    while True:
                        line = proc.stdout.readline()
                        if not line and proc.poll() is not None:
                            break
                        if line:
                            line = line.rstrip("\n")
                            log_handle.write(line + "\n")
                            log_handle.flush()
                            logger.info(f"[cellranger] {line}")
                            for handler in logging.getLogger("cycle_test").handlers:
                                handler.flush()
                        if time.monotonic() > deadline:
                            proc.kill()
                            raise subprocess.TimeoutExpired(docker_cmd, timeout_hours * 3600)

                    proc.wait()
                finally:
                    if proc.stdout:
                        proc.stdout.close()

            # Retain stderr_log as an empty marker so older docs still resolve.
            if not stderr_log.exists():
                stderr_log.touch()

            result["duration_seconds"] = time.time() - start_time
            result["returncode"] = proc.returncode

            if proc.returncode == EXIT_NOTHING_PROCESSED:
                # A complete run over samples none of which qualified for Cell
                # Ranger. Nothing malfunctioned, so it is not an error, but it is
                # emphatically not a success either.
                logger.warning(
                    "Stage 3 pipeline exited 3: the run was valid and complete "
                    "but no sample qualified for Cell Ranger."
                )
                logger.warning(f"See logs: {stdout_log}")
            elif proc.returncode == EXIT_PARTIAL:
                # Some expected samples were analysed and others were not. The
                # per-sample reasons are in the run record; not an error, but
                # never a success either.
                logger.warning(
                    "Stage 3 pipeline exited 4: only some of the expected samples "
                    "were analysed."
                )
                logger.warning(f"See logs: {stdout_log}")
            elif proc.returncode == EXIT_CONFIG_ERROR:
                result["configuration_error"] = True
                result["error"] = (
                    "Stage 3 is misconfigured: the pipeline exited "
                    f"{EXIT_CONFIG_ERROR} before processing anything (no usable "
                    "cellranger executable or reference genome). See "
                    f"{stdout_log}."
                )
                logger.error(result["error"])
            elif proc.returncode != 0:
                result["error"] = f"Docker exited with code {proc.returncode}"
                logger.error(result["error"])
                logger.error(f"See logs: {stdout_log}")
            else:
                logger.info("Docker pipeline completed")

            # Parse results
            parsed = self.parse_results()
            result.update({
                "total_samples": parsed["total_samples"],
                "successful_samples": parsed["successful_samples"],
                "failed_samples": parsed["failed_samples_count"],
                "success_list": parsed["success_samples"],
                "failed_list": parsed["failed_samples"],
                "step_status": parsed["step_status"],
                "step_messages": parsed["step_messages"],
                "expected_samples": parsed["expected_samples"],
                "expected_count": parsed["expected_count"],
                "cellranger_completed": parsed["cellranger_completed"],
                "cellranger_samples": parsed["cellranger_samples"],
                "cellranger_fresh": parsed["cellranger_fresh"],
                "cellranger_cache_hits": parsed["cellranger_cache_hits"],
                "cellranger_adopted": parsed["cellranger_adopted"],
                "cellranger_adopted_samples": parsed["cellranger_adopted_samples"],
                "cellranger_missing": parsed["cellranger_missing"],
                "cellranger_unverified": parsed["cellranger_unverified"],
                "cellranger_unverified_samples": parsed["cellranger_unverified_samples"],
                "run_record_path": parsed["run_record_path"],
                "run_record_problem": parsed["run_record_problem"],
                "cellranger_stale": parsed["cellranger_stale"],
                "cellranger_ineligible": parsed["cellranger_ineligible"],
                "cellranger_ineligible_reasons": parsed["cellranger_ineligible_reasons"],
                "provenance_verified": parsed["provenance_verified"],
                "other_samples_with_output": parsed["other_samples_with_output"],
            })

            self._decide_outcome(result)

            logger.info("=" * 50)
            logger.info("Pipeline Summary")
            logger.info("=" * 50)
            logger.info(f"  Run id:             {result['run_id']}")
            logger.info(f"  Expected samples:   {result['expected_count']}")
            logger.info(f"  FASTQ samples:      {result['total_samples']}")
            logger.info(f"  FASTQ converted:    {result['successful_samples']}")
            logger.info(f"  FASTQ failed:       {result['failed_samples']}")
            logger.info(
                f"  Verified output:    {result['cellranger_completed']} of "
                f"{result['expected_count']} expected "
                f"(fresh {result.get('cellranger_fresh', 0)}, "
                f"cache hit {result.get('cellranger_cache_hits', 0)})"
            )
            logger.info(
                f"  Adopted, unverified: {result.get('cellranger_adopted', 0)}"
                " (not counted as completed)"
            )
            logger.info(f"  Duration:           {result['duration_seconds']/60:.1f} minutes")
            if result.get("other_samples_with_output"):
                logger.info(
                    f"  {len(result['other_samples_with_output'])} sample(s) from "
                    "earlier runs also hold output; preserved, not counted here."
                )
            if result.get("run_record_problem"):
                logger.error("  NO USABLE RUN RECORD: %s",
                             result["run_record_problem"])
                if result.get("cellranger_unverified_samples"):
                    logger.error(
                        "  The Cell Ranger output on disk is left exactly where "
                        "it is. It is not deleted and it is not counted."
                    )
            if result["partial"]:
                logger.warning(f"  PARTIAL: {result['partial_reason']}")
            if result["nothing_processed"]:
                logger.warning(f"  NO ANALYSIS: {result['nothing_processed_reason']}")

        except subprocess.TimeoutExpired:
            result["error"] = f"Pipeline timed out after {timeout_hours} hours"
            result["status"] = "failed"
            result["success"] = False
            logger.error(result["error"])
        except Exception as e:
            result["error"] = str(e)
            result["status"] = "failed"
            result["success"] = False
            logger.error(f"Pipeline failed: {e}", exc_info=True)

        return result


def run_stage3_pipeline(
    sra_dir: Path,
    results_dir: Path,
    logs_dir: Path,
    cellranger_path: Optional[Path] = None,
    ref_genome_path: Optional[Path] = None,
    cores: int = 16,
    cellranger_threads: int = 16,
    cellranger_mem: int = 100,
    timeout_hours: int = 24,
    runtime: str = DOCKER,
    sif_dir: str = ".",
    run_id: Optional[str] = None,
    species: str = DEFAULT_SPECIES,
) -> Dict[str, Any]:
    """
    Convenience function to run Stage 3 pipeline.

    Args:
        sra_dir: Directory with SRA files
        results_dir: Output directory
        logs_dir: Log directory
        cellranger_path: Cell Ranger installation path
        ref_genome_path: Reference genome path
        cores: Parallel processing cores
        cellranger_threads: Cell Ranger threads
        cellranger_mem: Cell Ranger memory (GB)
        timeout_hours: Pipeline timeout
        species: What the run is about; decides which reference is the right one

    Returns:
        Dict with pipeline results
    """
    runner = Stage3PipelineRunner(
        sra_dir=sra_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        cellranger_path=cellranger_path,
        ref_genome_path=ref_genome_path,
        cores=cores,
        cellranger_threads=cellranger_threads,
        cellranger_mem=cellranger_mem,
        runtime=runtime,
        sif_dir=sif_dir,
        run_id=run_id,
        species=species,
    )
    return runner.run_pipeline(timeout_hours=timeout_hours)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Stage 3 SRR pipeline")
    parser.add_argument("sra_dir", type=Path, help="Directory with SRA files")
    parser.add_argument("results_dir", type=Path, help="Results output directory")
    parser.add_argument("logs_dir", type=Path, help="Logs directory")
    parser.add_argument("--cellranger-path", type=Path, default=DEFAULT_CELLRANGER_PATH)
    # Left unset so --species decides what is detected. DEFAULT_REF_GENOME_PATH
    # is what that detection returns for the default species.
    parser.add_argument("--ref-genome-path", type=Path, default=None,
                        help="10x reference directory (default: auto-detected "
                             f"for --species; currently {DEFAULT_REF_GENOME_PATH or 'not found'})")
    parser.add_argument("--cores", type=int, default=16)
    parser.add_argument("--cellranger-threads", type=int, default=16)
    parser.add_argument("--cellranger-mem", type=int, default=100)
    parser.add_argument("--species", default=DEFAULT_SPECIES,
                        choices=sorted(SPECIES_GENOME_PATTERNS),
                        help="What this run is about; decides which reference "
                             f"is the right one (default: {DEFAULT_SPECIES})")
    parser.add_argument("--check-only", action="store_true", help="Only check prerequisites")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    runner = Stage3PipelineRunner(
        sra_dir=args.sra_dir,
        results_dir=args.results_dir,
        logs_dir=args.logs_dir,
        cellranger_path=args.cellranger_path,
        ref_genome_path=args.ref_genome_path,
        cores=args.cores,
        cellranger_threads=args.cellranger_threads,
        cellranger_mem=args.cellranger_mem,
        species=args.species
    )

    if args.check_only:
        prereq = runner.check_prerequisites()
        print(json.dumps(prereq, indent=2))
    else:
        result = runner.run_pipeline()
        print(json.dumps(result, indent=2))
