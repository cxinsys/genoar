#!/usr/bin/env bash
set -euo pipefail

# Simple YAML reader via Python (avoids installing yq)
yaml_get() {
  local key="$1"
  local default_value="${2:-}"
  local cfg="${CONFIG:-/work/config.yaml}"
  if [[ ! -f "$cfg" ]]; then
    echo -n "$default_value"
    return 0
  fi
  python3 - "$key" "$default_value" "$cfg" << 'PY'
import sys, os
try:
    import yaml
except Exception:
    yaml = None

key, default, path = sys.argv[1], sys.argv[2], sys.argv[3]
if not os.path.exists(path) or yaml is None:
    sys.stdout.write(default)
    sys.exit(0)
with open(path, 'r') as f:
    data = yaml.safe_load(f) or {}

def get_nested(d, dotted):
    cur = d
    for part in dotted.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur

val = get_nested(data, key)
if val is None:
    sys.stdout.write(default)
else:
    sys.stdout.write(str(val))
PY
}

echo "[run] Starting SRR pipeline"

# Load common helpers if available
if [[ -f "/pipeline_next/lib.sh" ]]; then
  # shellcheck source=/dev/null
  source "/pipeline_next/lib.sh"
else
  REPORTS_DIR="/work/results/reports"
fi

ensure_reports_dir || true

# ---------------------------------------------------------------------------
# Cell Ranger status tracking
#
# Snakemake exits 0 when its sample list is empty, so "step 8 exited 0" does not
# mean Cell Ranger ran. Everything below exists so the final banner can only
# claim what actually happened.
#   not_in_image       - no snakemake in this image (step1..step7 build targets)
#   missing_cellranger - snakemake present, cellranger binary not mounted
#   missing_ref        - snakemake present, reference genome not mounted
#   ran                - snakemake was invoked and exited 0
# ---------------------------------------------------------------------------
CELLRANGER_STAGE="not_reached"
CELLRANGER_SKIP_REASON=""

# Exit codes. 0/1/2/3 follow the repository-wide convention; 4 is Stage 3's.
#   0 - every sample this run was asked to process has verified Cell Ranger output
#   1 - a step failed, or the machine could not do what the run needed: the run
#       directory could not be created (read-only tree, full disk, mount gone).
#       The run asked for nothing unreasonable; it could not be given.
#   2 - the run is misconfigured (no cellranger executable, unusable reference,
#       an unusable run id, a run id another run holds).
#       A configuration fault is NOT a valid "nothing to do" result.
#   3 - valid, complete run in which no expected sample qualified (or none
#       existed) and nothing was reused in its place
#   4 - partial: this run cannot show verified output for every expected sample,
#       and some expected sample does carry output it accounts for - either
#       verified (some completed, others did not) or adopted, which is output
#       taken on the operator's word rather than on evidence
EXIT_NOTHING_PROCESSED=3
EXIT_CONFIG_ERROR=2
EXIT_PARTIAL=4
# A step's own exit code is not this contract and must never be handed straight
# out of the orchestrator. GNU parallel exits with the NUMBER OF FAILED JOBS and
# xargs with 123, so passing those through makes two unreadable .sra files exit 2
# ("misconfigured; nothing was attempted"), three exit 3 ("nothing qualified"),
# and four exit 4, which the wrapper reads as "some samples were analysed" when
# none were. Every step failure is this one code, and the step's own number and
# status are in the message.
EXIT_STEP_FAILED=1

BANNER_BAR="============================================================"

# ---------------------------------------------------------------------------
# Run provenance
#
# "A BAM exists somewhere under results/success" is not evidence that THIS run
# produced anything: results live on a persistent volume, so a single leftover
# from an earlier run would be enough for an exit 0. Completion means "every
# sample this run was asked to process carries Cell Ranger output whose
# provenance matches this run's input".
#
# The pieces:
#   run id            GENOAR_RUN_ID, else config run_id, else generated here.
#                     Validated as a directory name and refused if it already
#                     names a run record - see "Run identity" below.
#   run directory     <results>/runs/<run_id>/ - manifest, per-sample records,
#                     outcome.json. Sample outputs stay where the Snakefile puts
#                     them; nothing under results/success is ever deleted.
#   expected manifest expected_samples.tsv, fixed BEFORE step 1 moves anything,
#                     one row per input sample with a content fingerprint
#   completion record <results>/runs/<run_id>/cellranger/<sample>.json, written
#                     by the Cell Ranger rule itself for each sample it runs
#   receipt           <results>/success/<sample>/.genoar_cellranger.json, tying
#                     a BAM to the input fingerprint and the run that made it
#
# A BAM this run's own Cell Ranger job produced - which only that job can attest
# to, and does, in the completion record - is `fresh`. A BAM that predates this
# run counts only when a receipt for VERIFIED work vouches for it against the
# same input fingerprint, and is then reported as a `cache_hit`, never as fresh
# work. A BAM that predates the run with no receipt (e.g. produced before
# provenance tracking existed) is `unverified` and does NOT count; set
# GENOAR_ADOPT_PRIOR_RESULTS=1 to record an explicit adoption instead of
# silently trusting it.
#
# An adoption stays an adoption. Its receipt records that the output was taken
# on the operator's word, and every later run reads that word back: the sample
# is reported as `adopted` again - reused, and still unverified - rather than
# maturing into a cache hit because a receipt happened to be there. Nothing but
# a Cell Ranger run over the input can make that output verified.
#
# The two records do not compete. The completion record is about one run and is
# read only under that run's own directory; the receipt is the durable statement
# a later run reads, and it is derived from the completion record, never the
# other way round.
# ---------------------------------------------------------------------------

# Read the counts the verification wrote. A run whose provenance could not be
# computed must not fall through to a banner: without it there is no basis for
# any claim about what was completed.
load_provenance_outcome() {
  if [[ ! -f "$RUN_DIR/outcome.env" ]]; then
    echo "[result] ERROR: could not verify what this run produced; no outcome record" >&2
    echo "[result] ERROR: was written to $RUN_DIR/outcome.env." >&2
    echo "[result] ERROR: Refusing to report an outcome this run cannot evidence." >&2
    mark_err 8 "provenance verification did not produce an outcome record" || true
    exit 1
  fi
  # shellcheck source=/dev/null
  source "$RUN_DIR/outcome.env"
}

# genoar_provenance <subcommand> [args...]
genoar_provenance() {
  python3 - "$@" << 'PY'
import hashlib, json, os, shutil, sys, time

MANIFEST_HEADER = "# sample\tsource\tsize_bytes\tfingerprint"
BAM_REL = os.path.join("cellranger_output", "outs", "possorted_genome_bam.bam")
RECEIPT_NAME = ".genoar_cellranger.json"
# What the Cell Ranger rule files under this run's directory when it runs a
# sample. Kept in step with Snakefile_hs.smk, which writes them.
COMPLETION_DIR_NAME = "cellranger"
COMPLETION_RECORD_KIND = "genoar.cellranger.completion"
# Record layouts this reader understands. The rule stamps the version it wrote
# (COMPLETION_RECORD_VERSION in Snakefile_hs.smk); a record from a layout this
# reader has never seen is not a record it may interpret, because the fields it
# is about to trust could mean something else. Reading it as absent is the safe
# direction: the sample is reported unverified rather than credited on a guess.
COMPLETION_RECORD_VERSIONS = (1,)
STARTED_SUFFIX = ".started"

# What a receipt may say about where the output beside it came from. The receipt
# is the ONLY statement that crosses runs, so the KIND of provenance it records
# has to cross with it - a later run that read the fingerprint out of a receipt
# without asking what kind of receipt it was turned an adoption into a verified
# completion on the very next run:
#
#     run A: GENOAR_ADOPT_PRIOR_RESULTS=1 -> adopted, completed 0, exit 4
#     run B: no flag at all               -> cache_hit, completed 1, exit 0
#
# One deliberate adoption, and then every subsequent ordinary run reported the
# same unverified BAM as verified work. So a reader may only credit a receipt
# whose provenance it recognises, and each kind means one thing forever:
#
#   fresh    the run that wrote it ran Cell Ranger and its own completion record
#            said so. Reusable as a `cache_hit`: evidence, made once, still good.
#   adopted  the operator told a run to use output nobody could account for. It
#            stays `adopted` on every later run - genuinely reused, and still
#            not something any run has verified. Reuse does not accumulate into
#            proof, no matter how many runs pass it along.
#
# Anything else - a receipt with no provenance field, or one written by a
# version that means something this reader does not know - is not a receipt this
# reader may act on, exactly as with a completion record in an unknown layout.
# The sample is reported `unverified`, which credits nobody and says why.
RECEIPT_VERIFIED = "fresh"
RECEIPT_ADOPTED = "adopted"

# The note a release writes into an existing receipt. It never replaces the
# provenance already there -- what produced the output does not change because
# the output was later thrown away.
RELEASE_KEY = "released"
RELEASE_KIND = "genoar.retention.release"

# What each retention policy leaves behind, under <sample>/cellranger_output/outs.
#
# A run that cannot delete anything can only process as many samples as the
# filesystem holds at once, which on a shared facility is far fewer than a
# corpus. Rotation is what makes a long run possible: analyse, keep what the
# analysis was for, release the rest, take the next batch.
#
#   all       nothing is released. The default, and what every run did before
#             this existed.
#   analysis  the BAM and its index go. Everything a second pass over the
#             counts could want stays -- both matrices, molecule_info, the
#             analysis directory. The BAM is the one output that is both the
#             largest by far and reproducible from the input.
#   summary   only the filtered matrix and the two summaries stay. For a site
#             that is going to carry results out over a link and re-derive
#             anything else where the compute is.
#
# `keep` is a whitelist and `drop` a blacklist, on purpose. Dropping the BAM is
# naming one file, and a future release adding outputs beside it should not
# silently change what `analysis` means. Keeping a summary is naming what is
# wanted, and there the same future release adding outputs should not silently
# start carrying them.
# `reads` is the other half, and the half that actually decides whether a
# corpus fits. Step 5 copies the whole sample directory into results/success/,
# so a completed sample holds its .sra and every FASTQ made from it a second
# time, beside the outputs. Releasing only what is under outs/ freed the BAM
# and left the reads, which on this pipeline are the larger pile -- the
# rotation ran, reported gigabytes freed, and the disk did not empty.
RETENTION_POLICIES = {
    "all": {"drop": (), "keep": None, "reads": False},
    "analysis": {"drop": ("possorted_genome_bam.bam",
                          "possorted_genome_bam.bam.bai"), "keep": None,
                 "reads": True},
    "summary": {"drop": (), "keep": ("filtered_feature_bc_matrix",
                                     "filtered_feature_bc_matrix.h5",
                                     "metrics_summary.csv",
                                     "web_summary.html"),
                "reads": True},
}

# What a released sample no longer needs beside its outputs: the input it was
# copied from and everything unpacked out of it. Named by suffix, at the top of
# the sample directory only, so nothing under cellranger_output is reached by
# this rule and nothing recursive is deleted on a pattern.
READ_SUFFIXES = (".sra", ".fastq", ".fastq.gz", ".fq", ".fq.gz")

# What to say when the ONLY thing that disagrees is the inode number, and the
# record is too old to carry the digests that would settle it.
#
# Two things produce that state - a filesystem that does not keep an inode
# number still for an unchanged file (a macOS Docker Desktop bind mount is one:
# measured, same size, same timestamp, a different number after the job), or a
# file swapped for one of the same size and timestamp - and a record without
# digests cannot tell them apart. So it says both, with the action for each,
# and says plainly which part it does not know. A record written by the current
# rule does not end up here: its digests decide it, and then the run says what
# was established rather than what it suspects.
#
# Naming the fix matters more than naming the technology: someone who has never
# heard of virtiofs can still act on "put the results directory on a Docker
# volume or a Linux filesystem".
INODE_ONLY_REASON = (
    "this run's Cell Ranger produced this sample's output, and what is on disk "
    "has the same size and the same timestamp as the file it wrote - only the "
    "inode number this filesystem uses to identify that file has changed, and "
    "this run's record predates the content digests that would settle it. "
    "Either the filesystem does not keep that number stable (macOS Docker "
    "Desktop bind mounts do not: recomputing will NOT help, put the results "
    "directory on a Docker volume or a Linux filesystem and run again), or the "
    "output really was swapped for a file of the same size and timestamp "
    "(remove the cellranger_output directory to recompute it). This run cannot "
    "tell which, so it credits the output to nobody."
)


def fingerprint(path, mode):
    st = os.stat(path)
    if mode == "size":
        return "size-mtime:%d-%d" % (st.st_size, int(st.st_mtime)), st.st_size
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest(), st.st_size


def input_sources(sample, input_dir):
    """Every file whose bytes make up this sample's input.

    An SRA sample is one file. A sample handed to the pipeline as FASTQ is the
    whole read set: fingerprinting only the largest read file meant a sample
    whose other files changed -- a different R1 against the same R2, a lane
    swapped out -- fingerprinted identically to the one before it, and its
    output was then credited to the new input.
    """
    loose = os.path.join(input_dir, sample + ".sra")
    if os.path.isfile(loose):
        return [loose]
    bare = os.path.join(input_dir, sample)
    if os.path.isfile(bare):
        return [bare]
    d = os.path.join(input_dir, sample)
    if os.path.isdir(d):
        for name in (sample + ".sra", sample):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return [p]
        return [os.path.join(d, n) for n in sorted(os.listdir(d))
                if not n.startswith(".") and os.path.isfile(os.path.join(d, n))]
    return []


def fingerprint_set(paths, mode):
    """One fingerprint for the whole input, and its total size.

    A single-file input keeps the single-file fingerprint exactly, so SRA
    samples fingerprint as they did before. A multi-file input hashes each
    file's name and fingerprint in a fixed order, so changing, renaming, adding
    or removing any one of them changes the result.
    """
    if len(paths) == 1:
        return fingerprint(paths[0], mode)
    h = hashlib.sha256()
    total = 0
    for path in paths:
        fp, size = fingerprint(path, mode)
        h.update(os.path.basename(path).encode())
        h.update(b"\0")
        h.update(fp.encode())
        h.update(b"\n")
        total += size
    return "set%d:sha256:%s" % (len(paths), h.hexdigest()), total


def cmd_manifest(input_dir, out_tsv, mode):
    """Freeze the set of samples this run was asked to process.

    Called before step 1, which moves files around, so the manifest describes
    the run's input and not the state left behind by earlier steps.
    """
    samples = {}
    if os.path.isdir(input_dir):
        for name in sorted(os.listdir(input_dir)):
            if not name.startswith("SRR"):
                continue
            path = os.path.join(input_dir, name)
            if os.path.isfile(path):
                samples[name[:-4] if name.endswith(".sra") else name] = None
            elif os.path.isdir(path):
                samples[name] = None
    rows = []
    for sample in sorted(samples):
        srcs = input_sources(sample, input_dir)
        if not srcs:
            rows.append((sample, "", "0", "absent"))
            continue
        fp, size = fingerprint_set(srcs, mode)
        src = srcs[0] if len(srcs) == 1 else os.path.dirname(srcs[0])
        rows.append((sample, os.path.relpath(src, input_dir), str(size), fp))
    with open(out_tsv, "w") as fh:
        fh.write(MANIFEST_HEADER + "\n")
        for row in rows:
            fh.write("\t".join(row) + "\n")
    print(len(rows))


def read_manifest(path):
    expected = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) >= 4 and f[0]:
                expected.append((f[0], f[3]))
    return expected


def read_census(success_dir):
    """Ineligibility reasons the pipeline recorded, by sample.

    Both census layouts put the sample in column 1 and the reason in column 3,
    with an empty column 3 for eligible samples, so one reader covers both.
    """
    reasons = {}
    for name in ("cellranger_eligibility.tsv", "cellranger_ineligible.tsv"):
        path = os.path.join(success_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as fh:
                for line in fh:
                    if line.startswith("#"):
                        continue
                    f = line.rstrip("\n").split("\t")
                    if len(f) >= 3 and f[0] and f[2].strip():
                        reasons.setdefault(f[0], f[2].strip())
        except OSError:
            continue
    return reasons


def read_selected(success_dir, run_id, dispatched):
    """The samples THIS run SELECTED for Cell Ranger. Not what it produced.

    Step 8 stamps its eligibility report with the id of the run that wrote it,
    and the orchestrator passes dispatched=1 only after snakemake exited 0 in
    this invocation. Both are required: the success directory is persistent, so
    a report left behind by an earlier run is not evidence about this one.

    Selection cannot stand for "this run produced the output", with or without
    the BAM's modification time beside it: the report is written when the
    workflow is parsed, before a single job runs, and snakemake exits 0 exactly
    when there is nothing left for it to do. So selection only explains a sample
    that did NOT complete -- it separates "this run asked Cell Ranger for this
    sample and got nothing back" from "this run never chose it" -- and it adds
    nothing to the completed count.
    """
    if dispatched != "1" or not run_id:
        return set()
    path = os.path.join(success_dir, "cellranger_eligibility.tsv")
    stamped = None
    samples = set()
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("#"):
                    f = line[1:].rstrip("\n").split("\t")
                    if len(f) >= 2 and f[0].strip() == "run_id":
                        stamped = f[1].strip()
                    continue
                f = line.rstrip("\n").split("\t")
                if len(f) >= 2 and f[0] and f[1].strip() == "eligible":
                    samples.add(f[0])
    except OSError:
        return set()
    return samples if stamped == run_id else set()


def release_ledger_has(runs_root, releasing_run, sample):
    """Whether the run named on a release note recorded releasing this sample.

    The note lives beside the output it describes, and so does everything else
    in that directory: one file saying a sample was released is one file. The
    run that did the releasing also wrote release.json under its own run
    directory, listing every sample it touched, and the two are written at
    different times to different places. Requiring both turns a claim into a
    claim that has to be consistent with a separate record.

    This is not proof against someone who can write anywhere -- nothing kept in
    the same tree as the data can be. What it is proof against is the case this
    exists for: output that went missing, or a receipt left behind by an
    interrupted run, being read as work that was done.

    Fail-closed. A ledger that is absent or unreadable is not a ledger that
    agrees.
    """
    if not releasing_run:
        return False
    path = os.path.join(runs_root, releasing_run, "release.json")
    try:
        with open(path) as fh:
            ledger = json.load(fh)
    except (OSError, ValueError):
        return False
    if ledger.get("kind") != RELEASE_KIND:
        return False
    listed = ledger.get("samples")
    # A list, not merely something `in` works on: `sample in "SRR000001,..."`
    # is substring containment, and a ledger whose samples field is a string
    # answered yes to every sample whose name appears anywhere in it.
    if not isinstance(listed, list):
        return False
    return sample in listed


def read_environment(run_dir):
    """What Cell Ranger and which reference this run is analysing with.

    Step 0 writes it. Absent -- an image that predates this, a run that never
    reached step 0 -- the comparison below cannot be made, and says so rather
    than passing silently.
    """
    try:
        with open(os.path.join(run_dir, "environment.json")) as fh:
            found = json.load(fh)
    except (OSError, ValueError):
        return None
    if found.get("kind") != "genoar.environment":
        return None
    return {"cellranger_version": found.get("cellranger_version") or "",
            "reference": found.get("reference") or "",
            "min_cells": found.get("min_cells"),
            "min_valid_barcodes": found.get("min_valid_barcodes"),
            "min_transcriptome": found.get("min_transcriptome")}


def environment_differs(receipt, current):
    """How the environment on a receipt differs from this run's, if it does.

    Reuse is the one place this matters. A BAM produced by Cell Ranger 7.0.1
    against refdata-gex-GRCh38-2020-A is a different measurement from one
    produced by 8.0.1 against 2024-A -- different gene counts for the same
    reads -- so a `cache_hit` must not hand one back on the strength of the
    input fingerprint alone. The same input does not make the same answer.

    Returns a description of the difference, or None when they agree or when
    there is nothing recorded to compare.
    """
    if not current:
        return None
    recorded = receipt.get("environment")
    if not isinstance(recorded, dict):
        return None
    parts = []
    for key, label in (("cellranger_version", "Cell Ranger"),
                       ("reference", "reference")):
        was, now = recorded.get(key) or "", current.get(key) or ""
        if was and now and was != now:
            parts.append("%s %s, this run has %s" % (label, was, now))
    return "; ".join(parts) or None


# Which metric answers which floor. The names are Cell Ranger's own.
QC_FLOORS = (
    ("min_cells", "Estimated Number of Cells", "cells"),
    ("min_valid_barcodes", "Valid Barcodes", "valid barcodes"),
    ("min_transcriptome", "Reads Mapped Confidently to Transcriptome",
     "reads mapped to the transcriptome"),
)


def below_the_floor(record, environment):
    """What Cell Ranger's own summary says is wrong with this output, if any.

    A BAM exists for every way of being complete and wrong -- the wrong mates,
    the wrong species, an input that was cut short -- and until now that was
    the entire definition of success. The numbers Cell Ranger writes beside it
    say otherwise, and they were never read.

    No metrics recorded is not a failure: output made before this existed, or
    by a run whose summary could not be read, says nothing either way and is
    left to the other rungs. A floor of 0 is off.
    """
    if not environment:
        return None
    metrics = (record or {}).get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return None
    complaints = []
    for key, metric, label in QC_FLOORS:
        floor = environment.get(key)
        if not isinstance(floor, (int, float)) or floor <= 0:
            continue
        value = metrics.get(metric)
        if isinstance(value, (int, float)) and value < floor:
            complaints.append("%s %s, below the %s this run requires"
                              % (_pretty(value), label, _pretty(floor)))
    return "; ".join(complaints) or None


def _pretty(value):
    if isinstance(value, float) and value < 1:
        return "%.1f%%" % (value * 100)
    return "%g" % value


def environment_recorded(receipt):
    """Whether this receipt says which Cell Ranger and reference made it."""
    recorded = receipt.get("environment")
    return (isinstance(recorded, dict)
            and all(recorded.get(key) for key in ("cellranger_version",
                                                  "reference")))


def comparable_environment(receipt, current):
    """Whether both sides recorded enough to be compared at all.

    Empty strings are what a site whose `cellranger --version` does not parse
    leaves behind, and treating them as "no difference" turns the whole check
    off exactly where it is most needed.
    """
    recorded = receipt.get("environment")
    if not isinstance(recorded, dict) or not current:
        return False
    return all(recorded.get(key) and current.get(key)
               for key in ("cellranger_version", "reference"))


def released_receipt(receipt_path, fingerprint, sample, runs_root):
    """The receipt for output that was verified and then deliberately removed.

    A BAM that is simply gone is `missing`, and has to stay `missing`: absence
    says nothing about whether the work was ever done, and a reader that treats
    it as completion can be satisfied by deleting everything. What separates a
    release is that the run which removed the file wrote down that it was doing
    so -- into the receipt that already said a run's own Cell Ranger produced
    this output from this exact input, and into its own ledger.

    Each of these is a way in, and each is checked:

      the sample     a receipt names the sample it is about, and one copied
                     from a neighbour is about the neighbour
      the ledger     see release_ledger_has
      the BAM        `released` has to say the BAM was released. Accept a note
                     that removed something else -- `retain: all` with
                     `release_input: true` writes exactly that -- and a BAM lost
                     any ordinary way afterwards reads as work that was done and
                     thrown away
      the identity   bam_size and bam_mtime are what a real verification always
                     records, and stand in for the file nobody can stat now

    Adoption is never released. It was never verification, and a receipt saying
    `adopted` cannot be promoted to a completion by having its BAM deleted.
    """
    receipt = load_receipt(receipt_path)
    if not receipt:
        return None
    if receipt.get("provenance") != RECEIPT_VERIFIED:
        return None
    if receipt.get("input_fingerprint") != fingerprint:
        return None
    if receipt.get("sample") != sample:
        return None
    # `isinstance(True, int)` is true, so a receipt whose sizes were booleans
    # passed a check meant to establish that a verification had measured a file.
    for field in ("bam_size", "bam_mtime"):
        value = receipt.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            return None
    note = receipt.get(RELEASE_KEY)
    if not isinstance(note, dict):
        return None
    if note.get("kind") != RELEASE_KIND:
        return None
    if note.get("policy") not in RETENTION_POLICIES:
        return None
    if not all(isinstance(note.get(field), str) and note.get(field)
               for field in ("by_run_id", "at")):
        return None
    removed = note.get("removed")
    if not isinstance(removed, list):
        return None
    if os.path.basename(BAM_REL) not in removed:
        return None
    if not release_ledger_has(runs_root, note.get("by_run_id"), sample):
        return None
    return receipt


def cellranger_record(run_dir, run_id, sample, bam):
    """What this run's Cell Ranger rule left behind for one sample.

    Returns (state, record):
      "recorded"    the rule ran Cell Ranger for this sample and recorded the
                    BAM it produced; `record` says which file that was
      "interrupted" the rule started and never recorded finishing
      "unusable"    a record exists but is not a complete one, is not this run's,
                    is not about this sample, is not about this file, or is
                    written in a layout this reader does not know
      "none"        this run's Cell Ranger never started this sample

    Every field is required. A record that does not parse is a record that was
    never finished, and a record naming another run, another sample or another
    path is not about the file in front of us -- all are as good as absent,
    because the only thing that may be treated as proof is a record this run
    completed for this sample and this output.

    Checking the path it names costs one string comparison and closes the case
    where a record is correct about a file that is not the one being credited:
    a sample directory restored from another tree, a success directory moved,
    the same run directory pointed at a different results volume.
    """
    directory = os.path.join(run_dir, COMPLETION_DIR_NAME)
    path = os.path.join(directory, sample + ".json")
    started = os.path.isfile(os.path.join(directory, sample + STARTED_SUFFIX))
    if not os.path.isfile(path):
        return ("interrupted" if started else "none"), None
    try:
        with open(path) as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return "unusable", None
    if not isinstance(record, dict):
        return "unusable", None
    if (record.get("record") != COMPLETION_RECORD_KIND
            or record.get("record_version") not in COMPLETION_RECORD_VERSIONS
            or record.get("complete") is not True
            or record.get("run_id") != run_id
            or record.get("sample") != sample):
        return "unusable", None
    recorded_bam = record.get("bam")
    if (not isinstance(recorded_bam, str)
            or os.path.realpath(recorded_bam) != os.path.realpath(bam)):
        return "unusable", None
    for field in ("bam_size", "bam_device", "bam_inode", "bam_mtime"):
        if not isinstance(record.get(field), int):
            return "unusable", None
    return "recorded", record


def record_describes(record, st):
    """Whether the file now on disk is the one the record was written about.

    Identity is the device and inode it occupies and its size. Its modification
    time is only required not to have gone backwards: snakemake touches every
    output file after the job that made it ("we know they must be new"), so the
    timestamp on disk is snakemake's and not Cell Ranger's, and demanding an
    exact match would fail honest runs whenever that touch crossed a second.

    A file that occupies the same inode is the same file, so this is the whole
    test when it passes. When it fails on the inode alone, cmd_verify asks the
    recorded digests of the file's two ends instead - see bam_edge_digests and
    edges_match - because an inode is only an identity where the filesystem
    keeps it still, and some do not: a macOS Docker Desktop bind mount
    renumbers an unchanged file across the job boundary, measured with the same
    size and the same timestamp on both sides.

    Known residual risk, accepted deliberately, and narrower than it was. A BAM
    rewritten IN PLACE, to the same size, without moving its timestamp
    backwards, AND leaving the first and last 64 KiB byte-for-byte as they were,
    is credited to this run. Only the middle can hide, and only from a writer
    who keeps all four of those properties. Closing even that means hashing the
    whole file, and a Cell Ranger BAM is several gigabytes -- a cost every run
    would pay, including every resume, to defend against a writer who already
    has the results volume mounted and could rewrite this record beside the BAM.
    The realistic version of that writer is a second Cell Ranger job, which does
    `rm -rf cellranger_output` first and therefore lands on a different inode
    with different bytes, and IS detected. What is cheap has been taken instead:
    the recorded path, the record's own version, device, inode, size, a
    timestamp floor, and 128 KiB of the file's own contents.
    """
    return (record["bam_size"] == st.st_size
            and record["bam_device"] == st.st_dev
            and record["bam_inode"] == st.st_ino
            and int(st.st_mtime) >= record["bam_mtime"])


def read_exactly(fh, offset, length):
    """`length` bytes from `offset`, or None if the file cannot give them.

    Same as the rule's. A short read is not a small answer, it is a different
    file than the one we were told about, so nothing partial is ever returned to
    be hashed and compared as if it were complete.
    """
    fh.seek(offset)
    data = b""
    while len(data) < length:
        chunk = fh.read(length - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def bam_edge_digests(path, size, window):
    """sha256 of the first and last `window` bytes, or None if unreadable.

    The counterpart of bam_edge_digests in Snakefile_hs.smk, computing the same
    two windows over the file as it is now. The two are driven against each
    other end to end by TestTheRuleAndTheVerifierAgree, which is what keeps
    them from drifting apart.
    """
    span = min(size, window)
    try:
        with open(path, "rb") as fh:
            head = read_exactly(fh, 0, span)
            tail = read_exactly(fh, max(0, size - span), span)
    except OSError:
        return None
    if head is None or tail is None:
        return None
    return (hashlib.sha256(head).hexdigest(),
            hashlib.sha256(tail).hexdigest())


def edges_match(record, path, st):
    """Whether the bytes at both ends are the ones the rule recorded.

    True, False, or None for "this record cannot answer". None covers a record
    written before digests existed, a digest field that is not what it should
    be, and a file whose ends cannot be read. None is never a match: it leaves
    the sample exactly where it was without them.
    """
    window = record.get("digest_bytes")
    head, tail = record.get("bam_head"), record.get("bam_tail")
    if not isinstance(window, int) or window < 0 or window > st.st_size:
        return None
    if not isinstance(head, str) or not isinstance(tail, str):
        return None
    edges = bam_edge_digests(path, st.st_size, window)
    if edges is None:
        return None
    return edges == (head, tail)


def mismatch_is_inode_only(record, st):
    """Whether the ONLY thing that differs is the inode number.

    Same size, same device, and a modification time that has not gone
    backwards: everything that would show a file had actually been rewritten
    agrees, and the one number that disagrees is the one some filesystems do
    not keep still. A file whose size changed is a changed file, and no probe
    result may explain that away.
    """
    return (record["bam_size"] == st.st_size
            and record["bam_device"] == st.st_dev
            and int(st.st_mtime) >= record["bam_mtime"]
            and record["bam_inode"] != st.st_ino)


def load_receipt(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_receipt(path, payload):
    """Write a record atomically. Returns whether it is now on disk.

    The return value exists because retention deletes on the strength of a
    receipt it has just written. A full or failing filesystem is exactly when
    a run is releasing space, and a warning that scrolls past while the
    deletion goes ahead anyway leaves output with nothing to vouch for it.
    """
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        sys.stderr.write("[step8] WARNING: could not write %s: %s\n" % (path, exc))
        return False


def cmd_verify(manifest, success_dir, run_id, started, run_dir, adopt, input_dir,
               dispatched):
    """Decide, per expected sample, what this run may claim about its output.

    Neither a modification time nor a dispatch list is provenance. A timestamp
    cannot say which input produced a file, or who wrote it, and is satisfied by
    a foreign BAM created in the second the run started, carrying a clock-skewed
    future stamp, or simply touched. A dispatch list is written when the workflow
    is parsed, before any job runs, and says only which samples qualified. The
    conjunction of the two is still only a guess, and snakemake exiting 0 --
    which it does precisely when it finds nothing to do -- does not narrow it.

    So the claim is not inferred here at all. The Cell Ranger rule writes
    a completion record for each sample it actually runs, under this run's own
    directory, naming the BAM as it stood when Cell Ranger returned. That record
    is the only thing that can make a sample this run's own work.

    The evidence, in the order it is weighed:

      1. This run's completion record for this sample, which still describes the
         file on disk: the rule ran Cell Ranger here, in this run, and this is
         what came out. `fresh`.
      2. A receipt that already vouches for exactly this file -- same input
         fingerprint, same BAM -- AND says a run's own Cell Ranger produced it
         (provenance `fresh`). Work an earlier run did for this exact input,
         found still in place. `cache_hit` -- reused, never claimed as new work.
      2b. The same receipt, saying instead that the output was adopted: reused,
         and still `adopted`, because nothing has been established about it
         since. A receipt whose provenance this reader does not recognise
         establishes nothing either: `unverified`.
      3. A receipt that contradicts: output made from a different input. `stale`.
      4. Adoption, if the operator asked for it explicitly.
      5. Otherwise the evidence is absent, not favourable: `unverified`, which
         is not counted as completed.

    (1) is weighed before (2) because it is this run's own direct observation
    rather than an inference about an earlier one; the two can only both hold if
    Cell Ranger rewrote a byte-identical file with an identical timestamp, and
    the run that observed the work is then the better witness.

    Receipts are written only where the run has something to say about the
    output: what it produced (1) and what the operator adopted on the record
    (4). Each says WHICH of the two it is, and a receipt that is carried
    forward is never rewritten, so the run that adopted an output stays named
    on it however many runs later reuse it.

    Adoption is reported, and it is not completion. `completed` counts only the
    samples whose output this run can point at evidence for -- its own work (1)
    and a receipt for verified work that matches (2). An adopted sample is the
    operator saying "use this anyway"; counting it as completed made a BAM with
    no provenance whatsoever finish the run and produce an exit 0, which is the
    exact claim this whole mechanism exists to refuse to make. Reading the
    adoption receipt back as a cache hit made the very next run do it again,
    which is the same claim by a longer route.
    """
    started = int(started)
    adopt = adopt == "1"
    environment = read_environment(run_dir)
    expected = read_manifest(manifest)
    census = read_census(success_dir)
    # Which samples this run chose for Cell Ranger. Explains failures; never
    # completes anything. See read_selected.
    selected = read_selected(success_dir, run_id, dispatched)
    samples_dir = os.path.join(run_dir, "samples")
    os.makedirs(samples_dir, exist_ok=True)

    counts = dict(expected=len(expected), completed=0, fresh=0, cache_hit=0,
                  released=0, adopted=0, ineligible=0, missing=0,
                  unverified=0, stale=0, suspect=0)
    records = {}
    for sample, fp in expected:
        bam = os.path.join(success_dir, sample, BAM_REL)
        receipt_path = os.path.join(success_dir, sample, RECEIPT_NAME)
        rec = {"sample": sample, "input_fingerprint": fp, "bam": bam}
        state, record = cellranger_record(run_dir, run_id, sample, bam)
        if not os.path.isfile(bam):
            released = released_receipt(receipt_path, fp, sample,
                                        os.path.dirname(os.path.abspath(run_dir)))
            drift = environment_differs(released, environment) if released else None
            if released and environment_recorded(released) and not drift \
                    and not comparable_environment(released, environment):
                # The receipt knows what produced it and this run does not know
                # what it has. For a cache hit that is a note on an artefact
                # somebody can still look at; for a released sample there is no
                # artefact, so it is refused instead.
                rec["status"] = "unverified"
                rec["reason"] = ("this sample's output was released by a run "
                                 "that recorded the Cell Ranger version and "
                                 "reference it used, and this run has not "
                                 "recorded its own, so the two cannot be "
                                 "compared and nothing is left to inspect; "
                                 "re-run it")
            elif released and drift:
                rec["status"] = "stale"
                rec["reason"] = ("this sample was analysed with %s and its "
                                 "output has been released, so there is "
                                 "nothing here that answers for this "
                                 "environment" % drift)
            elif released:
                note = released[RELEASE_KEY]
                rec["status"] = "released"
                rec["source_run_id"] = released.get("run_id", "unknown")
                rec["reason"] = ("analysed by run %s and released afterwards "
                                 "under retain=%s by run %s: the output was "
                                 "verified against this input before it was "
                                 "removed, and %s"
                                 % (released.get("run_id", "unknown"),
                                    note.get("policy", "?"),
                                    note.get("by_run_id", "unknown"),
                                    "the input was released with it"
                                    if note.get("input_released")
                                    else "the input was left in place"))
            elif sample in census:
                rec["status"] = "ineligible"
                rec["reason"] = census[sample]
            else:
                rec["status"] = "missing"
                if state == "recorded":
                    rec["reason"] = ("this run's Cell Ranger produced output "
                                     "for this sample and it is no longer on "
                                     "disk")
                elif state == "interrupted":
                    rec["reason"] = ("this run started Cell Ranger for this "
                                     "sample and the job did not finish")
                elif sample in selected:
                    rec["reason"] = ("this run selected this sample for Cell "
                                     "Ranger but no output was produced")
                else:
                    rec["reason"] = ("no Cell Ranger output and no "
                                     "ineligibility reason was recorded for "
                                     "this sample")
            counts[rec["status"]] += 1
            records[sample] = rec
            continue

        st = os.stat(bam)
        bam_size, bam_mtime = st.st_size, int(st.st_mtime)
        receipt = load_receipt(receipt_path)
        receipt_fp = receipt.get("input_fingerprint") if receipt else None
        # This run's Cell Ranger produced this exact file. Not an inference: the
        # rule that ran it wrote the record, and the file is still the one it
        # described.
        #
        # Identity is layered, not replaced. The inode still decides it, exactly
        # as before. Only one case is newly decidable: everything agrees except
        # the inode number, which is what a filesystem that renumbers an
        # unchanged file looks like AND what a swapped file of the same size and
        # timestamp looks like. There the recorded digests of the file's two
        # ends are asked, and they answer from the bytes rather than from the
        # filesystem's bookkeeping. A record with no digests answers nothing,
        # and nothing is not a match.
        produced_here = False
        edges = None
        if state == "recorded":
            if record_describes(record, st):
                produced_here = True
            elif mismatch_is_inode_only(record, st):
                edges = edges_match(record, bam, st)
                produced_here = edges is True
        suspect = below_the_floor(record, environment) if produced_here else None
        if produced_here and suspect:
            # This run's own Cell Ranger produced it, and what it produced is
            # not a result. Told apart from every other status because the
            # remedy is different: nothing here is missing or unverifiable, the
            # inputs are what need looking at.
            rec["status"] = "suspect"
            rec["reason"] = ("this run's Cell Ranger produced this output and "
                             "Cell Ranger's own summary says it is not a "
                             "result: %s. Check which reads were given as R1 "
                             "and R2, and which reference was used." % suspect)
            rec["metrics"] = record.get("metrics")
        elif produced_here:
            rec["status"] = "fresh"
            rec["evidence"] = os.path.join(COMPLETION_DIR_NAME,
                                           sample + ".json")
            if edges is True:
                # Worth saying out loud in the record: the file was identified
                # by its contents because this filesystem did not keep its
                # number still.
                rec["reason"] = (
                    "this run's Cell Ranger produced this output; the "
                    "filesystem gave the file a different inode number "
                    "afterwards, and it was identified instead by its size, "
                    "its timestamp and the bytes at both of its ends")
            payload = {"sample": sample, "run_id": run_id, "provenance": "fresh",
                       "input_fingerprint": fp, "bam_size": bam_size,
                       "bam_mtime": bam_mtime, "environment": environment,
                       "cellranger_completed_at": record.get("completed_at"),
                       "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                     time.gmtime(bam_mtime))}
            write_receipt(receipt_path, payload)
        elif (receipt and receipt_fp == fp
                and receipt.get("bam_size") == bam_size
                and receipt.get("bam_mtime") == bam_mtime):
            # A receipt about this input and about exactly this file. What it
            # entitles this run to claim depends on what it says produced the
            # output, and never on the fingerprint alone.
            source = receipt.get("run_id", "unknown")
            rec["source_run_id"] = source
            provenance = receipt.get("provenance")
            if receipt.get("sample") not in (None, sample):
                # `released_receipt` checks this and says why; the reuse rung
                # did not, which was an asymmetry rather than a decision.
                provenance = None
            if provenance == RECEIPT_VERIFIED:
                # Already recorded, for this input, as exactly this file: an
                # earlier run's own Cell Ranger work, reused. Snakemake had
                # nothing to do for it.
                #
                # Unless it was produced somewhere else. Same input, different
                # Cell Ranger or different reference release, is a different
                # measurement, and handing it back as this run's own result is
                # the quiet version of the mistake step 0's note exists to make
                # loud.
                drift = environment_differs(receipt, environment)
                if drift:
                    rec["status"] = "stale"
                    rec["reason"] = ("output of run %s was produced with %s, "
                                     "so it is not a result for this "
                                     "environment; remove the "
                                     "cellranger_output directory to "
                                     "recompute it" % (source, drift))
                else:
                    rec["status"] = "cache_hit"
                    rec["reason"] = ("output of run %s reused: same input "
                                     "fingerprint, same BAM" % source)
                    if environment and not isinstance(
                            receipt.get("environment"), dict):
                        rec["reason"] += ("; the run that produced it recorded "
                                          "no Cell Ranger or reference "
                                          "version, so that could not be "
                                          "compared")
            elif provenance == RECEIPT_ADOPTED:
                # Reused, and still unverified. Run %s was told to use this
                # output without evidence; passing it on cannot turn it into
                # evidence, so it is reported for what it is and counted where
                # adoption is counted - which is nowhere near `completed`.
                rec["status"] = "adopted"
                rec["reason"] = ("output adopted by run %s on request "
                                 "(GENOAR_ADOPT_PRIOR_RESULTS=1) and still in "
                                 "place: reused for the same input, and still "
                                 "not verified - no run has tied this output "
                                 "to the input it claims to come from. Remove "
                                 "the cellranger_output directory to recompute "
                                 "it, which is the only thing that can" % source)
            else:
                rec["status"] = "unverified"
                rec["reason"] = ("the provenance receipt beside this output "
                                 "does not say what produced it (provenance "
                                 "%r, written by run %s), so this run cannot "
                                 "tell reused work from output taken on "
                                 "somebody's word; remove the "
                                 "cellranger_output directory to recompute it"
                                 % (provenance, source))
        elif receipt and receipt_fp not in (None, fp):
            rec["status"] = "stale"
            rec["reason"] = ("existing Cell Ranger output was produced from a "
                             "different input (%s), not from this run's input"
                             % receipt_fp)
        elif receipt and receipt_fp == fp:
            # The receipt is about this input, but not about this file: the BAM
            # has been replaced or touched since it was recorded, and this run
            # did not produce what is there now.
            rec["status"] = "unverified"
            rec["reason"] = ("the recorded provenance matches this run's input "
                             "but not the Cell Ranger output now on disk (its "
                             "size or timestamp has changed since), so it "
                             "cannot be credited to this run; remove the "
                             "cellranger_output directory to recompute it")
        elif adopt:
            rec["status"] = "adopted"
            rec["reason"] = ("pre-existing output adopted on request "
                             "(GENOAR_ADOPT_PRIOR_RESULTS=1); its provenance "
                             "could not be verified")
            write_receipt(receipt_path,
                          {"sample": sample, "run_id": run_id,
                           "provenance": "adopted", "input_fingerprint": fp,
                           "bam_size": bam_size, "bam_mtime": bam_mtime,
                           "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                         time.gmtime(bam_mtime))})
        elif state == "recorded":
            # The run really did produce output for this sample, and what is on
            # disk now is not it. Saying "this run did not produce it" would be
            # as untrue as claiming it, so the record says exactly what happened
            # -- including the case where nothing happened to the file at all
            # and the filesystem simply will not name it the same way twice.
            rec["status"] = "unverified"
            if edges is False:
                # Settled, and not by guesswork: the file is the right size and
                # the right age, and the bytes at its ends are not the ones the
                # rule wrote. Whatever renumbered it, this is not that file.
                rec["reason"] = (
                    "this run's Cell Ranger produced output for this sample, "
                    "and what is on disk is a different file: the same size "
                    "and timestamp, but the bytes at its start and end are not "
                    "the ones the rule recorded. Something replaced it; remove "
                    "the cellranger_output directory to recompute it")
            elif mismatch_is_inode_only(record, st):
                rec["reason"] = INODE_ONLY_REASON
            else:
                rec["reason"] = ("this run's Cell Ranger produced output for "
                                 "this sample, but the file on disk is no "
                                 "longer the one it wrote (it has been replaced "
                                 "since), so it cannot be credited to this run; "
                                 "remove the cellranger_output directory to "
                                 "recompute it")
        elif state == "interrupted":
            rec["status"] = "unverified"
            rec["reason"] = ("this run started Cell Ranger for this sample and "
                             "never recorded finishing it (the job was "
                             "interrupted), so the output on disk cannot be "
                             "credited to this run; remove the "
                             "cellranger_output directory to recompute it")
        elif state == "unusable":
            rec["status"] = "unverified"
            rec["reason"] = ("this run left no usable record that its Cell "
                             "Ranger produced this sample's output, so the "
                             "output on disk cannot be credited to it; remove "
                             "the cellranger_output directory to recompute it")
        else:
            rec["status"] = "unverified"
            rec["reason"] = ("this run did not produce this Cell Ranger output "
                             "and it carries no provenance record, so it cannot "
                             "be credited to this run (its modification time is "
                             "not evidence of which input produced it); re-run "
                             "with GENOAR_ADOPT_PRIOR_RESULTS=1 to adopt it "
                             "explicitly, or remove the cellranger_output "
                             "directory to recompute it")
        counts[rec["status"]] += 1
        records[sample] = rec

    # Verified completion: this run's own recorded work, plus output an earlier
    # run's receipt vouches for against this same input, plus work that was
    # verified and then released on the record. Adoption is none of those, and
    # is counted only as itself.
    #
    # Released counts because the question `completed` answers is whether this
    # sample was analysed, not whether its BAM is still on the disk. A rotation
    # that dropped its own samples out of the completed count would report every
    # batch after the first as a partial run.
    counts["completed"] = (counts["fresh"] + counts["cache_hit"]
                           + counts["released"])

    # Results that exist but belong to other runs. Reported so the user can see
    # they were preserved, never counted towards this run.
    others = []
    if os.path.isdir(success_dir):
        for name in sorted(os.listdir(success_dir)):
            if name in records or not name.startswith("SRR"):
                continue
            if os.path.isfile(os.path.join(success_dir, name, BAM_REL)):
                others.append(name)

    for sample, rec in records.items():
        write_receipt(os.path.join(samples_dir, sample + ".json"), rec)

    outcome = {
        "run_id": run_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_dir": input_dir,
        "success_dir": success_dir,
        "expected_samples": [s for s, _ in expected],
        "counts": counts,
        "samples": records,
        "other_samples_with_output": others,
    }
    with open(os.path.join(run_dir, "outcome.json"), "w") as fh:
        json.dump(outcome, fh, indent=2, sort_keys=True)
        fh.write("\n")

    with open(os.path.join(run_dir, "outcome.env"), "w") as fh:
        for key in ("expected", "completed", "fresh", "cache_hit", "released",
                    "adopted", "ineligible", "missing", "unverified", "stale",
                    "suspect"):
            fh.write("PROV_%s=%d\n" % (key.upper(), counts[key]))
        fh.write("PROV_OTHER=%d\n" % len(others))

    # Pre-rendered banner lines: one per reason, grouped, worst news first.
    with open(os.path.join(run_dir, "reasons.txt"), "w") as fh:
        for status in ("suspect", "missing", "stale", "unverified",
                       "ineligible", "cache_hit", "released", "adopted"):
            grouped = {}
            for rec in records.values():
                if rec["status"] == status and rec.get("reason"):
                    grouped.setdefault(rec["reason"], []).append(rec["sample"])
            for reason, names in sorted(grouped.items()):
                fh.write("[result]   - %d %s sample(s): %s\n"
                         % (len(names), status, reason))
                fh.write("[result]       %s\n" % ", ".join(sorted(names)))


def _tree_size(path):
    """Bytes held by a file or a directory, symlinks counted but never followed."""
    if os.path.islink(path) or os.path.isfile(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _is_empty(path):
    """A name that is there and holds nothing is not an output.

    A zero-byte metrics_summary.csv and an empty matrix directory passed the
    "is it there" test, so `summary` released the BAM and the input against
    outputs that carry nothing -- and the sample was still reported complete.
    """
    if os.path.isdir(path):
        return not any(os.listdir(path))
    try:
        return os.path.getsize(path) == 0
    except OSError:
        return True


def _remove(path):
    """Remove a file, a link or a whole directory. Missing is not an error."""
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def _under(child, parent):
    """Whether child really resolves to somewhere strictly inside parent.

    Every path this deletes is built from a name that came out of the manifest,
    and a manifest is a file on disk like any other. Resolving both ends and
    comparing is what keeps a name like `../..` from turning a retention policy
    into a way to delete the results directory.

    Strictly inside: `child == parent` is not containment for a deleter. A
    manifest whose source column read `.` resolved to the input directory
    itself, and releasing that sample emptied every other sample's input on the
    way to failing on the last rmdir -- reported, at the end, as a successful
    release.

    realpath on both ends, so this is also the test that catches a symlink
    pointing out of the tree. It has to be applied to every path that gets
    deleted, not only to the one the name was built from: a `cellranger_output`
    or an `outs` that is itself a link puts the deletion somewhere else while
    the directory holding it is still where it should be.
    """
    parent = os.path.realpath(parent)
    child = os.path.realpath(child)
    return child != parent and child.startswith(parent + os.sep)


def sample_inputs(input_dir, sample):
    """This sample's own input, by the two names the pipeline gives it.

    Built from the sample's identity, never read from a file. Taking the path
    from the manifest's source column and checking only that it lands somewhere
    under the input directory lets SRR1's row point at SRR2's directory and
    delete SRR2's input -- both are inside the tree the check is asking about.
    A path that arrives as data is not a path to delete.
    """
    found = []
    for candidate in (os.path.join(input_dir, sample),
                      os.path.join(input_dir, sample + ".sra")):
        if os.path.exists(candidate) and _under(candidate, input_dir):
            found.append(candidate)
    return found


def cmd_release(manifest, success_dir, run_id, run_dir, policy, release_input,
                input_dir):
    """Free the disk a completed sample no longer needs, on the record.

    Runs after the verifier, never before it, and only over the samples the
    verifier just credited to this run -- read from the outcome it wrote rather
    than from what happens to be on the disk. A sample that did not complete
    keeps everything it has, because that is what a re-run needs.

    Three things are written, in an order chosen so that every point it can be
    interrupted at leaves a state the verifier reads correctly:

      1. the ledger, naming what this run intends to release. Interrupted here,
         nothing has been deleted and the ledger describes work not done --
         which no sample's receipt claims, so nothing is credited.
      2. the sample's receipt, amended to say what is about to go. Interrupted
         here, the file is still there and the ordinary rungs credit it.
      3. the deletion. Only now is anything gone, and both records already
         explain it.

    Written the other way round, a deleted file is left with nothing to account
    for it, and the pipeline reports that as a failure -- correctly, which is
    the problem.

    A receipt that could not be written stops that sample's deletion. A
    filesystem that is full or failing is exactly when a run is trying to
    release space, and going ahead on a warning is how output ends up with
    nothing to vouch for it.
    """
    spec = RETENTION_POLICIES.get(policy)
    if spec is None:
        sys.stderr.write("[retain] unknown retain policy %r (known: %s)\n"
                         % (policy, ", ".join(sorted(RETENTION_POLICIES))))
        sys.exit(2)
    release_input = release_input == "1"
    if not spec["drop"] and spec["keep"] is None and not spec["reads"] \
            and not release_input:
        print("[retain] retain=%s, release_input=no: nothing to release." % policy)
        return

    outcome_path = os.path.join(run_dir, "outcome.json")
    try:
        with open(outcome_path) as fh:
            outcome = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("[retain] WARNING: no usable outcome at %s (%s); "
                         "nothing released.\n" % (outcome_path, exc))
        return

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    eligible = sorted(name for name, rec in outcome.get("samples", {}).items()
                      if rec.get("status") in ("fresh", "cache_hit"))
    ledger_path = os.path.join(run_dir, "release.json")
    intent_path = os.path.join(run_dir, "release_intent.json")

    def write_ledger(path, samples, freed, not_released):
        return write_receipt(path, {
            "kind": RELEASE_KIND, "run_id": run_id, "policy": policy,
            "input_released": release_input, "at": stamp,
            "samples": samples, "bytes_freed": freed,
            "not_released": [{"sample": s, "why": w} for s, w in not_released]})

    # (1) Intent, before anything is touched -- and into a file of its own,
    # which nothing reads as evidence. The committed ledger is written once, at
    # the end, naming only what actually went.
    #
    # One file serving both meant a run killed between amending a receipt and
    # deleting the file left a receipt and a ledger that already agreed the BAM
    # had gone while it was still there. Nothing was wrong yet -- the ordinary
    # rungs credit a sample whose BAM exists -- but the next ordinary loss of
    # that file would have been read as this release.
    if not write_ledger(intent_path, eligible, 0, []):
        sys.stderr.write("[retain] FATAL: could not write %s; nothing released.\n"
                         % intent_path)
        sys.exit(1)

    freed, released, kept_back = 0, [], []
    for sample, rec in sorted(outcome.get("samples", {}).items()):
        status = rec.get("status")
        if status not in ("fresh", "cache_hit"):
            # `released` needs no note: a sample rotated by an earlier run has
            # nothing left to release. Adoption is excluded because it was
            # never verified.
            if status != "released":
                kept_back.append((sample, "not credited to this run (%s)" % status))
            continue
        sample_dir = os.path.join(success_dir, sample)
        outs = os.path.join(sample_dir, "cellranger_output", "outs")
        # Against the sample's OWN directory, not merely against the results
        # root: SRR1's cellranger_output pointed at SRR2's deleted SRR2's BAM,
        # and both are inside the tree.
        if not _under(sample_dir, success_dir) or not _under(outs, sample_dir):
            kept_back.append((sample, "output path does not resolve inside %s"
                              % sample_dir))
            continue
        if not os.path.isdir(outs):
            kept_back.append((sample, "no output directory"))
            continue

        if spec["keep"] is not None:
            present = set(os.listdir(outs))
            # What the policy keeps has to be there before what it drops goes.
            # An outs/ holding only a BAM -- an interrupted Cell Ranger, a
            # partially retrieved tree -- matched nothing in the keep list, so
            # `summary` removed the BAM as "not kept" and left a completed
            # sample with no outputs at all, still counted as complete.
            absent = [n for n in spec["keep"] if n not in present
                      or _is_empty(os.path.join(outs, n))]
            if absent:
                kept_back.append((sample, "the outputs retain=%s keeps are not "
                                          "there (%s); nothing was removed"
                                  % (policy, ", ".join(absent))))
                continue
            victims = [n for n in sorted(present) if n not in spec["keep"]]
        else:
            victims = [n for n in spec["drop"]
                       if os.path.exists(os.path.join(outs, n))]
        victims = [n for n in victims
                   if _under(os.path.join(outs, n), outs)
                   or os.path.islink(os.path.join(outs, n))]
        targets = [(os.path.join(outs, n), n) for n in victims]

        # The reads step 5 copied in beside the outputs. Top level of the
        # sample directory only, by suffix, so nothing under cellranger_output
        # is reached and nothing is matched recursively.
        reads = []
        if spec["reads"]:
            for name in sorted(os.listdir(sample_dir)):
                if not name.lower().endswith(READ_SUFFIXES):
                    continue
                path = os.path.join(sample_dir, name)
                if os.path.isfile(path) and _under(path, sample_dir):
                    reads.append(name)
                    targets.append((path, name))

        inputs = sample_inputs(input_dir, sample) if release_input else []
        planned_bytes = sum(_tree_size(path) for path, _ in targets)
        planned_bytes += sum(_tree_size(path) for path in inputs)
        if not targets and not inputs:
            continue

        note = {"policy": policy, "by_run_id": run_id, "at": stamp,
                "removed": [name for _, name in targets],
                "reads_released": reads,
                "bytes_freed": planned_bytes,
                "input_released": bool(inputs),
                "kind": RELEASE_KIND}

        # (2) The receipt, before the deletion -- but only when the BAM is one
        # of the things going. The note is a statement about the BAM, so a
        # policy that keeps it has nothing to claim: `retain: all` with
        # `release_input: true` frees the input and leaves every output where
        # it is, and the verifier reads that sample exactly as it did before.
        #
        # Only verified work may have its BAM released: without a receipt
        # saying a run's own Cell Ranger produced this, removing the BAM leaves
        # a sample nothing can ever vouch for.
        bam_name = os.path.basename(BAM_REL)
        releasing_bam = bam_name in note["removed"]
        receipt_path = os.path.join(sample_dir, RECEIPT_NAME)
        receipt = load_receipt(receipt_path)
        if releasing_bam:
            if not receipt or receipt.get("provenance") != RECEIPT_VERIFIED:
                kept_back.append((sample, "no verified receipt to amend"))
                continue
            receipt[RELEASE_KEY] = note
            if not write_receipt(receipt_path, receipt):
                kept_back.append((sample, "its receipt could not be written, "
                                          "so nothing was deleted"))
                continue

        # (3) The deletion, in an order chosen for what a failure half way
        # through leaves behind.
        #
        # The BAM is the last output to go, and the input goes only once every
        # output deletion has succeeded. It is the BAM's absence that the
        # `released` rung reads, and the input is the only thing a re-run could
        # use to rebuild any of it -- so a failure anywhere earlier leaves a
        # sample that still has both, and the note comes back off. Removing the
        # BAM first meant an EIO on the index left the sample with its BAM gone
        # and its release cancelled: `missing`, permanently, with the input
        # possibly gone too.
        ordered = ([t for t in targets if t[1] != bam_name]
                   + [t for t in targets if t[1] == bam_name])
        failed = []
        for path, name in ordered:
            try:
                _remove(path)
            except OSError as exc:
                failed.append(name)
                sys.stderr.write("[retain] WARNING: %s/%s: %s\n"
                                 % (sample, name, exc))
                break
        # The note is a statement about the BAM, so what decides whether it
        # stands is whether the BAM went. Rolling it back on ANY failure meant
        # an unremovable input -- a read-only mount, a stale NFS handle --
        # disowned a BAM that had already been deleted, and the sample was
        # `missing` from then on with nothing left to recompute from.
        bam_gone = not os.path.isfile(os.path.join(sample_dir, BAM_REL))
        input_failures = []
        if bam_gone or not releasing_bam:
            for path in inputs:
                try:
                    _remove(path)
                except OSError as exc:
                    input_failures.append(os.path.basename(path))
                    sys.stderr.write("[retain] WARNING: input %s: %s\n"
                                     % (path, exc))
        if releasing_bam and not bam_gone:
            # The BAM was meant to go and is still there, so nothing has been
            # released. The note comes off: left on, a later ordinary loss of
            # that file would be read as this release -- the laundering the
            # `removed` check exists to stop, arriving by the other door.
            receipt.pop(RELEASE_KEY, None)
            write_receipt(receipt_path, receipt)
            kept_back.append((sample, "release failed; these remain: %s"
                              % ", ".join(failed or ["the output"])))
            continue
        if failed or input_failures:
            kept_back.append(
                (sample, "released, but these could not be removed: %s"
                 % ", ".join(failed + input_failures)))
        freed += planned_bytes
        released.append(sample)
        # Committed here, per sample, not once at the end. The ledger is what
        # the verifier requires, so committing once at the end leaves a run
        # killed part way through the corpus with every already-deleted sample
        # unanswered for -- `missing`, and with the input gone too, unrecoverable.
        write_ledger(ledger_path, released, freed, kept_back)

    # The final state, over whatever the last per-sample commit left.
    write_ledger(ledger_path, released, freed, kept_back)

    # A rotation that did not free what the plan assumed leaves the disk in a
    # state nothing downstream knows about, and the next task in the array
    # meets it. The analysis is still whatever it was -- this does not fail the
    # run -- but the job script reads this and refuses to carry on as though
    # the space were free.
    incomplete = [s for s, why in kept_back
                  if "release failed" in why or "could not be removed" in why]
    marker = os.path.join(run_dir, "retention_incomplete")
    if incomplete:
        write_receipt(marker, {"kind": "genoar.retention.incomplete",
                               "run_id": run_id, "samples": incomplete,
                               "at": stamp})
    elif os.path.exists(marker):
        try:
            os.remove(marker)
        except OSError:
            pass
    print("[retain] retain=%s: released %d sample(s), %.1f GiB freed."
          % (policy, len(released), freed / float(1 << 30)))
    if kept_back:
        print("[retain] %d sample(s) kept whole: %s"
              % (len(kept_back), ", ".join("%s (%s)" % kv for kv in kept_back[:5])
                 + (", ..." if len(kept_back) > 5 else "")))


sub = sys.argv[1]
if sub == "manifest":
    cmd_manifest(sys.argv[2], sys.argv[3], sys.argv[4])
elif sub == "verify":
    cmd_verify(*sys.argv[2:])
elif sub == "release":
    cmd_release(*sys.argv[2:])
else:
    sys.stderr.write("unknown provenance subcommand: %s\n" % sub)
    sys.exit(2)
PY
}

# Config path can be overridden by env CONFIG or arg --config
CONFIG=${CONFIG:-/work/config.yaml}
if [[ ${1:-} == "--config" && -n ${2:-} ]]; then
  CONFIG="$2"; shift 2
fi

# Fallbacks for the three paths configs/example.yaml sets, for a config that omits them.
INPUT_DIR_DEFAULT="/work/data/sra"
OUTPUT_DIR_DEFAULT="/work/results"
LOG_DIR_DEFAULT="/work/logs"

# Ensure PyYAML is available for yaml parsing; fall back to defaults if not.
python3 - << 'PY' || true
try:
    import yaml  # noqa
    print("PyYAML available")
except Exception:
    print("PyYAML not available; using defaults if keys missing")
PY

INPUT_DIR="$(yaml_get input_dir "$INPUT_DIR_DEFAULT")"
OUTPUT_DIR="$(yaml_get output_dir "$OUTPUT_DIR_DEFAULT")"
LOG_DIR="$(yaml_get log_dir "$LOG_DIR_DEFAULT")"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR" "$LOG_DIR"

REF_DEFAULT="/ref"
REF_PATH="$(yaml_get ref_path "$REF_DEFAULT")"
CELLRANGER_BIN_DEFAULT="/opt/cellranger/cellranger"
CELLRANGER_BIN="$(yaml_get cellranger_bin "$CELLRANGER_BIN_DEFAULT")"
# Cell Ranger has to be mounted as its whole installation tree — it reads
# .env.json, external/, lib/ and mro/ beside the launcher, and mounting only
# bin/ fails at startup with "Couldn't find the `.env.json` file". Where the
# launcher sits inside that tree differs by release: 7.x keeps it in bin/.
# So when the configured path is absent, the other layout is tried before
# giving up, rather than making every site edit the config.
if [[ ! -x "$CELLRANGER_BIN" ]]; then
  CELLRANGER_ROOT="$(dirname "$CELLRANGER_BIN")"
  if [[ -x "$CELLRANGER_ROOT/bin/$(basename "$CELLRANGER_BIN")" ]]; then
    CELLRANGER_BIN="$CELLRANGER_ROOT/bin/$(basename "$CELLRANGER_BIN")"
  fi
fi
CELLRANGER_VERSION_EXPECTED="$(yaml_get cellranger_version "")"
REFERENCE_VERSION_EXPECTED="$(yaml_get reference_version "")"
# What a completed sample leaves on the disk, and whether its input goes with
# it. `all` and `no` are the defaults, so a config that says nothing about
# retention behaves exactly as every run did before this existed: a site opts
# into rotation, it is never opted in for them.
# Read here and passed to snakemake below. The Snakefile has no `configfile:`
# directive, so a key step 8 does not put on the command line is a key it never
# sees: --localmem was 100 at every site whatever hpc_config.yaml said, and a
# node with less than that was told it had it.
CELLRANGER_THREADS="$(yaml_get cellranger_threads 16)"
CELLRANGER_MEM="$(yaml_get cellranger_mem 100)"
# The floors below which Cell Ranger's own summary says the output is not a
# result. Deliberately far below anything a real experiment produces: this is
# meant to catch reads handed over as the wrong mates, a reference for the
# wrong species, an input that arrived truncated -- all of which produce a
# perfectly good BAM and near-zero numbers. Set any of them to 0 to switch that
# check off.
MIN_CELLS="$(yaml_get min_cells 100)"
MIN_VALID_BARCODES="$(yaml_get min_valid_barcodes 0.10)"
MIN_TRANSCRIPTOME="$(yaml_get min_transcriptome 0.05)"

# Each floor is written into environment.json in a JSON *number* position. A
# value that is not a number produces a file nothing can parse, and the reader
# that would notice answers "no environment was recorded" rather than raising:
# all three floors and the Cell Ranger/reference cross-check then apply to
# nothing, and the run exits 0 having checked none of them. So the value is
# judged here, where the run can still refuse.
#
# The likeliest way to get one is following the advice above. YAML reads `off`,
# `no` and `false` as booleans, so a config that switches a check off in words
# rather than with `0` arrives here as `False`.
require_numeric_floor() {
  local key="$1" value="$2"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] && return 0
  echo "$BANNER_BAR"
  echo "[run] CONFIGURATION ERROR - $key must be a number, and is '$value'."
  echo "[run] These floors are recorded as numbers beside the run and decide"
  echo "[run] whether Cell Ranger's own metrics say its output is a result."
  echo "[run] A value that is not a number disables all of them silently."
  echo "[run] To switch the check off, set $key: 0 -- not off, no or false,"
  echo "[run] which YAML reads as booleans."
  echo "[run] This is a configuration fault, not an empty result."
  echo "[run] Exiting $EXIT_CONFIG_ERROR (misconfigured run; nothing was attempted)."
  echo "$BANNER_BAR"
  mark_err 0 "configuration error: $key is not a number ('$value')" || true
  exit "$EXIT_CONFIG_ERROR"
}
require_numeric_floor min_cells "$MIN_CELLS"
require_numeric_floor min_valid_barcodes "$MIN_VALID_BARCODES"
require_numeric_floor min_transcriptome "$MIN_TRANSCRIPTOME"
RETAIN="$(yaml_get retain "all")"
RELEASE_INPUT_RAW="$(yaml_get release_input "false")"
case "$(printf '%s' "$RELEASE_INPUT_RAW" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) RELEASE_INPUT=1 ;;
  *)             RELEASE_INPUT=0 ;;
esac

echo "[run] CONFIG=$CONFIG"
echo "[run] INPUT_DIR=$INPUT_DIR"
echo "[run] OUTPUT_DIR=$OUTPUT_DIR"
echo "[run] LOG_DIR=$LOG_DIR"
echo "[run] RETAIN=$RETAIN (release_input=$RELEASE_INPUT)"

# ---------------------
# Run identity
#
# One id per invocation. A resume is a NEW run over the SAME expected set: it
# gets its own id and its own record, finds the earlier output still in place,
# and reports those samples as cache hits rather than redoing the work. Earlier
# run directories are never touched.
#
# The id becomes a directory name under <results>/runs/, so it is held to the
# same rule the crawler holds GENOAR_RUN_ID and GENOAR_WORKER_ID to
# (IDENTITY_PATTERN in genoar_crawler.py): a letter or digit, then letters,
# digits, '.', '_' or '-', at most 64 characters. The two halves of the pipeline
# take their ids from the same environment variable and must agree about what
# one is. An id that fails is a configuration fault, exit 2, before any work.
#
# Taken on trust, `../escape` writes this run's records outside the run tree and
# over whatever was there, and an id belonging to an existing run silently
# replaces that run's record - the very evidence this design exists to keep. So:
#   * the id is validated before it is used to build a path;
#   * `LATEST` is reserved, because <results>/runs/LATEST is the pointer file;
#   * an id that already has a run record is refused rather than overwritten. A
#     GENERATED id that collides is regenerated instead: the operator did not
#     choose it, so a collision is this script's problem to solve, not theirs.
#
# Surrounding whitespace is stripped, and then the STRIPPED value is the id -
# validated, used to build the path, written into the record, and handed on.
# The crawler does exactly this (`(env.get('GENOAR_RUN_ID') or '').strip()`),
# and the two halves read the same variable, so ` pilot ` has to mean the same
# run in both or `make run-full` splits down the middle: Stage 1 crawls under
# `pilot` and Stage 3 refuses the value that produced the crawl.
#
# What must not happen is validating one form and using another. Stripping
# first and judging what is underneath keeps that: `LATEST\n` strips to the
# reserved name and is refused as reserved, `../escape\n` strips to a path and
# is refused as a path, and no directory this script creates can contain
# whitespace, because the value that names it is the value the pattern passed.
# Inner whitespace is not stripped and is not in the character class, so `a b`
# is refused rather than quietly becoming something else. A value that is only
# whitespace says nothing and means "no id was given".
# ---------------------

# The crawler's IDENTITY_PATTERN, as an ERE. Anchored at both ends: bash's =~
# ends `$` at the end of the string, trailing newline included, so this judges
# the whole value and not a prefix of it.
run_id_is_usable() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

# Leading and trailing whitespace only, as the crawler's .strip() does. Inner
# whitespace is left alone so that "a b" is refused rather than quietly becoming
# a different id than the one the operator set.
trim_ws() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  printf %s "${s%"${s##*[![:space:]]}"}"
}

# <results>/runs/LATEST names the newest run. A run called LATEST would need a
# directory where that file is.
RUN_ID_RESERVED="LATEST"

refuse_run_id() {
  local value="$1" why="$2"
  echo "$BANNER_BAR"
  echo "[run] CONFIGURATION ERROR - run id '$value' cannot be used:"
  echo "[run]   $why"
  echo "[run] A run id becomes a directory under $OUTPUT_DIR/runs/ and holds the"
  echo "[run] only record of what this run did. Use letters, digits, '.', '_' or"
  echo "[run] '-', starting with a letter or digit, at most 64 characters, and"
  echo "[run] not '$RUN_ID_RESERVED'. This is the rule the crawler applies to"
  echo "[run] GENOAR_RUN_ID and GENOAR_WORKER_ID."
  echo "[run] Set a different GENOAR_RUN_ID (or run_id in $CONFIG), or unset it"
  echo "[run] and let this run generate its own."
  echo "[run] This is a configuration fault, not an empty result."
  echo "[run] Exiting $EXIT_CONFIG_ERROR (misconfigured run; nothing was attempted)."
  echo "$BANNER_BAR"
  mark_err 0 "configuration error: run id '$value': $why" || true
  exit "$EXIT_CONFIG_ERROR"
}

# The other way a run directory fails to appear. Kept apart from refuse_run_id
# on purpose: telling someone their run id is taken when their results volume is
# read-only sends them to change the one thing that was fine.
#
# Exit 1, not 2. Exit 2 means the user gave the run something it could not use -
# an unusable id, a missing Cell Ranger, a directory that is not a reference.
# Here the id was fine and the machine could not honour it: the tree is
# read-only, the disk is full, the mount went away. That is a failure, which is
# what 1 is for, and it is also what the crawler's launchers return for it.
refuse_run_dir() {
  local dir="$1" why="$2"
  echo "$BANNER_BAR"
  echo "[run] FAILED - this run could not claim a run directory:"
  echo "[run]   $why"
  echo "[run] $dir is where this run would record what it did, so it cannot"
  echo "[run] start without it. This does NOT say the run id is taken: nothing"
  echo "[run] holds it and the directory could not be created. Check that"
  echo "[run] $OUTPUT_DIR is mounted, writable and not full."
  echo "[run] Nothing was attempted. Exiting 1 (the run id was usable; creating"
  echo "[run] its directory failed)."
  echo "$BANNER_BAR"
  mark_err 0 "run directory could not be created: $why" || true
  exit 1
}

# ---------------------
# Reserving the id
#
# Asking whether <results>/runs/<id> exists and creating it later are two
# separate acts, and between them another run can do the same. Two Stage 3 runs
# started with one id therefore both passed the check and then wrote their
# manifests, records and outcome into the same directory: 26 of 30 concurrent
# trials of the previous version reserved the same id twice, and the second
# writer's expected-sample manifest replaced the first's - a run record that
# describes a run nobody asked for.
#
# So the check and the creation are one act. `mkdir` without -p creates the
# directory or fails; the kernel serialises that, and exactly one caller can
# win. The reservation is the run directory itself rather than a separate lock
# because there is then only one object with only one lifetime: a lock would
# have to be released, would have to be cleaned up after a crash that happened
# while it was held, and would leave a window in which an id was taken but no
# record of it existed. mkdir also leaves the loser's hands empty - unlike a
# file redirection, it changes nothing when it fails.
#
# EEXIST is not the only way mkdir can fail, and the two are not the same news:
# "another run has this id" is about the id the user chose, and is a
# configuration fault (exit 2); "the directory could not be created" is about
# the volume (read-only mount, no permission, full disk) and is a failure of the
# machine to do what was asked (exit 1). Each says which one it is, in as many
# words, because sending someone to change a run id that was never the problem
# costs them the whole search.
# ---------------------

# reserve_run_dir <dir>
#   0  reserved by this process, and by no one else
#   1  the id is taken (the directory is already there)
#   2  the directory could not be created at all; RESERVE_ERROR says why
RESERVE_ERROR=""
reserve_run_dir() {
  local dir="$1" err
  if err="$(mkdir "$dir" 2>&1)"; then
    return 0
  fi
  # mkdir failed. If the directory is there now, someone else has it - whether
  # they created it a week ago or a microsecond ago.
  if [[ -e "$dir" ]]; then
    return 1
  fi
  RESERVE_ERROR="$err"
  return 2
}

# The runs/ directory is shared by every run and is not a reservation, so -p.
runs_err="$(mkdir -p "$OUTPUT_DIR/runs" 2>&1)" || \
  refuse_run_dir "$OUTPUT_DIR/runs" \
    "$OUTPUT_DIR/runs could not be created ($runs_err)."

# Where the id came from, so the error names the thing the user has to change.
# Stripped here, once, before anything looks at it or builds a path from it.
RUN_ID_SOURCE="GENOAR_RUN_ID"
RUN_ID="$(trim_ws "${GENOAR_RUN_ID:-}")"
if [[ -z "$RUN_ID" ]]; then
  RUN_ID_SOURCE="run_id in $CONFIG"
  RUN_ID="$(trim_ws "$(yaml_get run_id "")")"
fi

reserved=0
if [[ -n "$RUN_ID" ]]; then
  run_id_is_usable "$RUN_ID" || \
    refuse_run_id "$RUN_ID" "$RUN_ID_SOURCE is not usable as a directory name."
  [[ "$RUN_ID" != "$RUN_ID_RESERVED" ]] || \
    refuse_run_id "$RUN_ID" "$RUN_ID_SOURCE is reserved for the newest-run pointer."
  reserve_run_dir "$OUTPUT_DIR/runs/$RUN_ID" || reserved=$?
  case "$reserved" in
    1) refuse_run_id "$RUN_ID" \
         "$OUTPUT_DIR/runs/$RUN_ID already exists: an earlier run recorded itself under this id, or another run reserved it at the same moment. A run record is never shared and never overwritten." ;;
    2) refuse_run_dir "$OUTPUT_DIR/runs/$RUN_ID" \
         "$OUTPUT_DIR/runs/$RUN_ID could not be created ($RESERVE_ERROR)." ;;
  esac
else
  # Generated. $$ is 1 in a container, so two runs starting in the same second
  # would otherwise share an id; the random suffix is what keeps them apart, and
  # the loop covers the rest. A collision here is not the operator's mistake, so
  # it costs a new id rather than the run.
  RUN_ID_SOURCE="generated"
  for _ in 1 2 3 4 5; do
    RUN_ID="run-$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM$RANDOM"
    reserved=0
    reserve_run_dir "$OUTPUT_DIR/runs/$RUN_ID" || reserved=$?
    [[ "$reserved" -eq 1 ]] || break
  done
  case "$reserved" in
    1) refuse_run_id "$RUN_ID" "no free generated run id was found in $OUTPUT_DIR/runs/." ;;
    2) refuse_run_dir "$OUTPUT_DIR/runs/$RUN_ID" \
         "$OUTPUT_DIR/runs/$RUN_ID could not be created ($RESERVE_ERROR)." ;;
  esac
fi

RUN_DIR="$OUTPUT_DIR/runs/$RUN_ID"
RUN_STARTED_EPOCH="$(date -u +%s)"
MANIFEST="$RUN_DIR/expected_samples.tsv"
MANIFEST_HASH_MODE="${GENOAR_MANIFEST_HASH:-sha256}"
ADOPT_PRIOR="${GENOAR_ADOPT_PRIOR_RESULTS:-0}"
mkdir -p "$RUN_DIR/samples"
# The one file a new run overwrites, and deliberately: it is a pointer to the
# run in progress, not a record of one. Every claim lives in the run directory
# it names, which no other run may write to. Renamed into place so a reader
# never sees half an id, and named after this run - which no other run can now
# be using, since reaching this line means this process holds the id.
printf '%s\n' "$RUN_ID" > "$OUTPUT_DIR/runs/.LATEST.$RUN_ID"
mv -f "$OUTPUT_DIR/runs/.LATEST.$RUN_ID" "$OUTPUT_DIR/runs/LATEST"
echo "[run] RUN_ID=$RUN_ID ($RUN_ID_SOURCE)"
echo "[run] RUN_DIR=$RUN_DIR"

# ---------------------
# Step 0: configuration pre-flight
#
# Only images that carry the Cell Ranger stage are held to this. A missing
# executable or a reference directory that is not a reference is a configuration
# fault: the run cannot do its job, so it must fail here rather than run steps
# 1-7 and then report a valid "nothing to process".
# ---------------------
if command -v snakemake >/dev/null 2>&1; then
  echo "[step0] Validating Cell Ranger prerequisites"
  preflight_errors=()
  if [[ ! -f "$CELLRANGER_BIN" ]]; then
    preflight_errors+=("cellranger executable not found at $CELLRANGER_BIN")
  elif [[ ! -x "$CELLRANGER_BIN" ]]; then
    preflight_errors+=("cellranger at $CELLRANGER_BIN is not executable")
  fi
  if [[ ! -d "$REF_PATH" ]]; then
    preflight_errors+=("reference directory not found at $REF_PATH")
  else
    [[ -s "$REF_PATH/reference.json" ]] || \
      preflight_errors+=("reference.json missing or empty in $REF_PATH")
    for ref_entry in fasta genes star; do
      [[ -d "$REF_PATH/$ref_entry" ]] || \
        preflight_errors+=("$ref_entry/ directory missing in $REF_PATH")
    done
  fi
  if [[ ${#preflight_errors[@]} -gt 0 ]]; then
    echo "$BANNER_BAR"
    echo "[step0] CONFIGURATION ERROR - Stage 3 cannot run:"
    for preflight_error in "${preflight_errors[@]}"; do
      echo "[step0]   - $preflight_error"
    done
    echo "[step0] Cell Ranger needs an executable and a 10x reference containing"
    echo "[step0] reference.json, fasta/, genes/ and star/."
    echo "[step0]   mount the Cell Ranger install at $(dirname "$CELLRANGER_BIN")"
    echo "[step0]     https://www.10xgenomics.com/support/software/cell-ranger/downloads"
    echo "[step0]   mount the reference at $REF_PATH"
    echo "[step0]     https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz"
    echo "[step0] This is a configuration fault, not an empty result."
    echo "[step0] Exiting $EXIT_CONFIG_ERROR (misconfigured run; nothing was attempted)."
    echo "$BANNER_BAR"
    mark_err 0 "configuration error: ${preflight_errors[*]}" || true
    exit "$EXIT_CONFIG_ERROR"
  fi
  # Cell Ranger's arguments and defaults move between major releases, so which
  # one produced a result is part of the result. Stating the expected version
  # in the config turns a silent difference into a line in the log.
  # `|| true`: grep exits 1 when it matches nothing, and under `set -e` that
  # would abort here before the run can say why. The empty result is judged
  # below instead.
  #
  # The number is taken next to the word "cellranger", and from the whole
  # output rather than line 1: `2>&1` merges a module system's own "Loading
  # singularity/3.8.7" into the first line, and reading the first three-part
  # number there records 3.8.7 as the Cell Ranger version. Snakefile_hs.smk
  # picks the version by the same rule, so the version step 0 reports is the
  # version the run adapted to.
  #
  # `timeout`: a launcher that blocks on a licence or telemetry probe would
  # otherwise hang step 0 indefinitely, after the allocation has been granted.
  CR_VERSION_CMD=("$CELLRANGER_BIN" --version)
  if command -v timeout >/dev/null 2>&1; then
    CR_VERSION_CMD=(timeout 60 "$CELLRANGER_BIN" --version)
  fi
  # stdout first, then stderr — not `2>&1`, which interleaves them in the order
  # they were written. Snakefile_hs.smk reads stdout and then stderr, so a
  # wrapper announcing "Loading cellranger/7.0.1" on stderr ahead of an 8.0.1
  # binary answering on stdout made the two disagree: step 0 logged 7.0.1 and
  # warned about it while the rule adapted to 8.0.1. The log has to name the
  # version the run used.
  #
  # `[[:blank:]]`, not `[[:space:]]`: space and tab, no newline. grep works a
  # line at a time and could not match across one anyway, and the Python side
  # is held to the same class so that neither accepts what the other refuses.
  CR_VERSION_STDERR="$(mktemp)"
  CR_VERSION_STDOUT="$("${CR_VERSION_CMD[@]}" 2>"$CR_VERSION_STDERR" || true)"
  CR_VERSION_STDERR_TEXT="$(cat "$CR_VERSION_STDERR" 2>/dev/null || true)"
  CELLRANGER_VERSION_FOUND="$(printf '%s\n%s\n' "$CR_VERSION_STDOUT" \
      "$CR_VERSION_STDERR_TEXT" \
    | grep -oiE 'cellranger[[:blank:]/_-]*v?[0-9]+\.[0-9]+\.[0-9]+' \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
  rm -f "$CR_VERSION_STDERR"
  # No version is not a version to adapt to. The Cell Ranger rule picks its
  # arguments from this number -- 8.0 added --create-bam, which 7.x rejects --
  # so with nothing read, both guesses fail, and they fail only after the
  # allocation has been spent. The commonest cause is a launcher that cannot
  # execute here at all: Cell Ranger ships Linux x86-64 only, and on Apple
  # Silicon the file is present, executable, and answers nothing, so the four
  # checks above all pass.
  if [[ -z "$CELLRANGER_VERSION_FOUND" ]]; then
    echo "$BANNER_BAR"
    echo "[step0] CONFIGURATION ERROR - could not read a version from $CELLRANGER_BIN."
    echo "[step0] It ran '$CELLRANGER_BIN --version' and no version number came back."
    if [[ -n "$CR_VERSION_STDOUT$CR_VERSION_STDERR_TEXT" ]]; then
      printf '%s\n%s\n' "$CR_VERSION_STDOUT" "$CR_VERSION_STDERR_TEXT" \
        | grep -v '^$' | head -3 | sed 's/^/[step0]   /'
    else
      echo "[step0]   (no output at all)"
    fi
    echo "[step0] Step 8 chooses Cell Ranger's arguments by release, so a run that"
    echo "[step0] cannot name the release cannot choose them. It stops here rather"
    echo "[step0] than after the allocation has been spent."
    echo "[step0] Cell Ranger is distributed for Linux x86-64 only. On Apple Silicon"
    echo "[step0] and other architectures the launcher exists and cannot run."
    echo "[step0] This is a configuration fault, not an empty result."
    echo "[step0] Exiting $EXIT_CONFIG_ERROR (misconfigured run; nothing was attempted)."
    echo "$BANNER_BAR"
    mark_err 0 "configuration error: no version from $CELLRANGER_BIN --version" || true
    exit "$EXIT_CONFIG_ERROR"
  fi
  # Compared at major.minor: 8.0.1 and 8.0.2 are the same interface, and
  # warning about the patch digit would be noise nobody reads. A version with
  # fewer parts is filled out rather than truncated — `${1%.*}` left "8" as
  # "8", which never equals the "8.0" a three-part version reduces to, so
  # `cellranger_version: "8"` warned about every 8.x install it was correct for.
  cr_series() {
    local v="${1:-}" major rest minor
    major="${v%%.*}"
    rest="${v#*.}"
    if [[ "$rest" == "$v" ]]; then minor=0; else minor="${rest%%.*}"; fi
    printf '%s.%s' "${major:-0}" "${minor:-0}"
  }
  if [[ -n "$CELLRANGER_VERSION_EXPECTED" && -n "$CELLRANGER_VERSION_FOUND" \
        && "$(cr_series "$CELLRANGER_VERSION_EXPECTED")" != "$(cr_series "$CELLRANGER_VERSION_FOUND")" ]]; then
    echo "[step0] NOTE: this pipeline was validated on Cell Ranger $CELLRANGER_VERSION_EXPECTED; this system has $CELLRANGER_VERSION_FOUND."
    echo "[step0]       It will run, but the numbers are not directly comparable across releases."
  fi
  # Read the reference's own reference.json rather than its directory name:
  # the directory is bind-mounted at /ref, so by the time the pipeline sees it
  # the name the site gave it is gone. reference.json carries {"genomes": [...],
  # "version": "2020-A"}, which is what 10x versions a release by.
  if [[ -f "$REF_PATH/reference.json" ]]; then
    # Tabs and CR as well as spaces and newlines: a reference.json that was
    # hand-edited or round-tripped through Windows still has to read the same,
    # and leaving a tab in put one inside the genome name, which then matched
    # nothing and drew the note on a correct reference.
    REF_JSON="$(tr -d '\n\r\t ' < "$REF_PATH/reference.json")"
    # Every entry of "genomes", not the first. 10x ships combined references —
    # refdata-gex-GRCh38-and-GRCm39-2024-A carries both — and reading only
    # entry one saw GRCh38, matched, and said nothing while half the features
    # counted were mouse. A reference is the one named only if everything in
    # it is named.
    #
    # `cut -d'"' -f4` on the release, not a second grep: the first match ends
    # in the closing quote, so `[^"]+$` — which needs the last character to be
    # something other than a quote — matched nothing, every time. The genome
    # came out empty on every run, the emptiness test below was therefore
    # always true, and the note fired even for sites running exactly the
    # validated reference. A check that reports a difference unconditionally
    # reports nothing.
    REF_GENOMES_RAW="$(printf '%s' "$REF_JSON" | grep -oE '"genomes":\[[^]]*\]' | head -1 || true)"
    REF_GENOME_FOUND="$(printf '%s' "${REF_GENOMES_RAW#*[}" \
      | grep -oE '"[^"]+"' | tr -d '"' | tr '\n' ',' | sed 's/,$//' || true)"
    REF_RELEASE_FOUND="$(printf '%s' "$REF_JSON" | grep -oE '"version":"[^"]+"' | cut -d'"' -f4 || true)"
    REFERENCE_FOUND="${REF_GENOME_FOUND:-unknown}${REF_RELEASE_FOUND:+ $REF_RELEASE_FOUND}"
    # The expected value is a directory name like refdata-gex-GRCh38-2024-A;
    # it matches when the release and *every* genome in the reference appear
    # in it.
    REF_GENOME_MISMATCH=0
    if [[ -z "$REF_GENOME_FOUND" ]]; then
      REF_GENOME_MISMATCH=1
    else
      while IFS= read -r one_genome; do
        [[ -n "$one_genome" ]] || continue
        if [[ "$REFERENCE_VERSION_EXPECTED" != *"$one_genome"* ]]; then
          REF_GENOME_MISMATCH=1
        fi
      done <<< "$(printf '%s' "$REF_GENOME_FOUND" | tr ',' '\n')"
    fi
    if [[ -n "$REFERENCE_VERSION_EXPECTED" \
          && ( "$REF_GENOME_MISMATCH" -eq 1 || -z "$REF_RELEASE_FOUND" \
               || "$REFERENCE_VERSION_EXPECTED" != *"$REF_RELEASE_FOUND"* ) ]]; then
      echo "[step0] NOTE: this pipeline was validated on $REFERENCE_VERSION_EXPECTED; this run uses $REFERENCE_FOUND."
      echo "[step0]       Gene annotations differ between reference releases, so gene counts will differ."
    fi
  fi
  echo "[step0] OK: cellranger=$CELLRANGER_BIN version=$CELLRANGER_VERSION_FOUND reference=$REF_PATH"
  # Written down, not only logged. Cell Ranger's release and the reference's
  # release both change the numbers, so output produced under one combination
  # is not output for another -- and reuse is decided by a reader with no other
  # way to know which combination produced what it found.
  mkdir -p "$RUN_DIR" 2>/dev/null || true
  printf '{"kind":"genoar.environment","cellranger_version":"%s","reference":"%s","min_cells":%s,"min_valid_barcodes":%s,"min_transcriptome":%s}\n' \
    "$CELLRANGER_VERSION_FOUND" "${REFERENCE_FOUND:-}" \
    "$MIN_CELLS" "$MIN_VALID_BARCODES" "$MIN_TRANSCRIPTOME" \
    > "$RUN_DIR/environment.json" 2>/dev/null || true
else
  echo "[step0] Skipped: this image has no Cell Ranger stage (steps 1-7 only)."
fi

# ---------------------
# Expected sample manifest
#
# Fixed here, before step 1 starts moving files, so that "what this run was
# asked to process" is decided by the run's own input and cannot drift as the
# steps rewrite the directory.
# ---------------------
EXPECTED_COUNT="$(genoar_provenance manifest "$INPUT_DIR" "$MANIFEST" "$MANIFEST_HASH_MODE")"
echo "[run] Expected samples for this run: $EXPECTED_COUNT (manifest: $MANIFEST)"

# ---------------------
# Step 1: make_directories
# ---------------------
echo "[step1] Organizing SRR files into per-sample directories"
set +e
"/pipeline_next/make_directories.sh" "$INPUT_DIR" 2>&1 | tee -a "$LOG_DIR/step1_make_directories.log"
status=${PIPESTATUS[0]}; set -e
if [[ $status -ne 0 ]]; then
  log_err "Step 1 failed with code $status" || true
  mark_err 1 "make_directories failed ($status)" || true
  exit "$EXIT_STEP_FAILED"
fi
mark_ok 1 "make_directories ok" || true
echo "[done] Step 1 completed."

# ---------------------
# Step 2: fastq-dump in parallel (if available)
# ---------------------
if [[ -x "/pipeline_next/fastq_dump_parallel.sh" && $(command -v fastq-dump || true) ]]; then
  CORES_DEFAULT="8"
  CORES="$(yaml_get cores "$CORES_DEFAULT")"
  echo "[step2] Running fastq-dump with CORES=$CORES"
  set +e
  LOG_DIR="$LOG_DIR" \
    "/pipeline_next/fastq_dump_parallel.sh" "$INPUT_DIR" "$CORES" 2>&1 | tee -a "$LOG_DIR/step2_fastq_dump.log"
  status=${PIPESTATUS[0]}; set -e
  if [[ $status -ne 0 ]]; then
    log_err "Step 2 failed with code $status" || true
    mark_err 2 "fastq-dump failed ($status)" || true
    exit "$EXIT_STEP_FAILED"
  fi
  mark_ok 2 "fastq-dump ok" || true
  echo "[done] Step 2 completed."
else
  echo "[step2] Skipped (fastq_dump_parallel.sh or fastq-dump not available in this image)."
fi

# ---------------------
# Step 3: gzip fastq in parallel (if available)
# ---------------------
if [[ -x "/pipeline_next/fastq_gzip_parallel.sh" ]]; then
  CORES_DEFAULT="8"
  GZIP_THREADS_DEFAULT="1"
  CORES="$(yaml_get cores "$CORES_DEFAULT")"
  GZIP_THREADS="$(yaml_get gzip_threads "$GZIP_THREADS_DEFAULT")"
  echo "[step3] Running gzip with CORES=$CORES, THREADS=$GZIP_THREADS"
  set +e
  LOG_DIR="$LOG_DIR" \
    "/pipeline_next/fastq_gzip_parallel.sh" "$INPUT_DIR" "$CORES" "$GZIP_THREADS" 2>&1 | tee -a "$LOG_DIR/step3_fastq_gzip.log"
  status=${PIPESTATUS[0]}; set -e
  if [[ $status -ne 0 ]]; then
    log_err "Step 3 failed with code $status" || true
    mark_err 3 "gzip failed ($status)" || true
    exit "$EXIT_STEP_FAILED"
  fi
  mark_ok 3 "gzip ok" || true
  echo "[done] Step 3 completed."
else
  echo "[step3] Skipped (fastq_gzip_parallel.sh not available in this image)."
fi

# ---------------------
# Step 4: parse fastq logs/status → success/failed lists
# ---------------------
if [[ -x "/usr/bin/python3" || -x "/usr/local/bin/python3" ]]; then
  echo "[step4] Parsing fastq results to create success/failed lists"
  set +e
  python3 /pipeline_next/parse_fastq_logs.py "$INPUT_DIR" "$OUTPUT_DIR" 2>&1 | tee -a "$LOG_DIR/step4_parse_fastq.log"
  status=${PIPESTATUS[0]}; set -e
  if [[ $status -ne 0 ]]; then
    log_err "Step 4 failed with code $status" || true
    mark_err 4 "parse fastq logs failed ($status)" || true
    exit "$EXIT_STEP_FAILED"
  fi
  mark_ok 4 "parse fastq logs ok" || true
  echo "[done] Step 4 completed."
else
  echo "[step4] Skipped (python3 not available)."
fi

# ---------------------
# Step 5: move success sample directories into results/success
# ---------------------
if [[ -x "/pipeline_next/move_success_dirs.sh" ]]; then
  echo "[step5] Moving success samples into $OUTPUT_DIR/success"
  set +e
  LOG_DIR="$LOG_DIR" \
    "/pipeline_next/move_success_dirs.sh" "$INPUT_DIR" "$OUTPUT_DIR" "$OUTPUT_DIR/fastq_success.txt" 2>&1 | tee -a "$LOG_DIR/step5_move_success.log"
  status=${PIPESTATUS[0]}; set -e
  if [[ $status -ne 0 ]]; then
    log_err "Step 5 failed with code $status" || true
    mark_err 5 "move success failed ($status)" || true
    exit "$EXIT_STEP_FAILED"
  fi
  mark_ok 5 "move success ok" || true
  echo "[done] Step 5 completed."
else
  echo "[step5] Skipped (move_success_dirs.sh not available in this image)."
fi

# ---------------------
# Step 6: count fastq.gz per sample and emit TSVs
# ---------------------
if [[ -x "/pipeline_next/check_counts.sh" ]]; then
  echo "[step6] Counting fastq.gz per sample under $OUTPUT_DIR/success"
  set +e
  LOG_DIR="$LOG_DIR" \
    "/pipeline_next/check_counts.sh" "$OUTPUT_DIR/success" 2>&1 | tee -a "$LOG_DIR/step6_check_counts.log"
  status=${PIPESTATUS[0]}; set -e
  if [[ $status -ne 0 ]]; then
    log_err "Step 6 failed with code $status" || true
    mark_err 6 "check counts failed ($status)" || true
    exit "$EXIT_STEP_FAILED"
  fi
  mark_ok 6 "check counts ok" || true
  echo "[done] Step 6 completed."
else
  echo "[step6] Skipped (check_counts.sh not available in this image)."
fi

# ---------------------
# Step 7: rename fastqs to *_S1_R[12]_001.fastq.gz based on counts
# ---------------------
if [[ -x "/pipeline_next/rename_fastq.sh" ]]; then
  echo "[step7] Renaming FASTQs under $OUTPUT_DIR/success"
  set +e
  LOG_DIR="$LOG_DIR" \
    "/pipeline_next/rename_fastq.sh" "$OUTPUT_DIR/success" 2>&1 | tee -a "$LOG_DIR/step7_rename_fastq.log"
  status=${PIPESTATUS[0]}; set -e
  if [[ $status -ne 0 ]]; then
    log_err "Step 7 failed with code $status" || true
    mark_err 7 "rename fastq failed ($status)" || true
    exit "$EXIT_STEP_FAILED"
  fi
  mark_ok 7 "rename fastq ok" || true
  echo "[done] Step 7 completed."
else
  echo "[step7] Skipped (rename_fastq.sh not available in this image)."
fi

# ---------------------
# Step 8: Snakemake Cell Ranger (local mode)
# ---------------------
if command -v snakemake >/dev/null 2>&1; then
  CORES_DEFAULT="8"
  CORES="$(yaml_get cores "$CORES_DEFAULT")"

  # Step 0 already refused to start without these. Kept as a guard in case the
  # mount disappeared mid-run.
  if [[ ! -x "$CELLRANGER_BIN" ]]; then
    CELLRANGER_STAGE="missing_cellranger"
    CELLRANGER_SKIP_REASON="cellranger binary not found at $CELLRANGER_BIN (mount the host cellranger dir at /opt/cellranger)"
    echo "[step8] Skipped: cellranger not found at $CELLRANGER_BIN (mount /opt/cellranger)."
  elif [[ ! -d "$REF_PATH" ]]; then
    CELLRANGER_STAGE="missing_ref"
    CELLRANGER_SKIP_REASON="reference genome not found at $REF_PATH (mount the host reference dir at /ref)"
    echo "[step8] Skipped: reference path not found: $REF_PATH (mount /ref)."
  else
    echo "[step8] Running Snakemake for Cell Ranger with CORES=$CORES"
    set +e
    snakemake -s /pipeline_next/Snakefile_hs.smk \
      --cores "$CORES" \
      --printshellcmds \
      --keep-going \
      --config success_dir="$OUTPUT_DIR/success" ref_path="$REF_PATH" cellranger_bin="$CELLRANGER_BIN" run_id="$RUN_ID" run_dir="$RUN_DIR" cellranger_threads="$CELLRANGER_THREADS" cellranger_mem="$CELLRANGER_MEM" \
      2>&1 | tee -a "$LOG_DIR/step8_cellranger.log"
    status=${PIPESTATUS[0]}; set -e
    # --keep-going above, and no `exit` here. The receipt that lets a later run
    # reuse a sample is written by the verifier, so skipping the verifier
    # whenever snakemake returns non-zero costs ONE bad sample in a batch of
    # twenty-four the other twenty-three's durable evidence. A re-run can then
    # neither reuse them (no receipt) nor redo them (their output is already
    # there, so snakemake has nothing to do): `unverified` for good.
    #
    # The verifier is built to report per sample. It runs whatever snakemake
    # returned, and the run's exit code comes from the pipeline's own contract
    # -- partial is 4 -- rather than from snakemake's.
    if [[ $status -ne 0 ]]; then
      log_err "Step 8: snakemake exited $status; some samples did not complete" || true
      # Best-effort: append samples missing BAM to step8_failed.txt
      if [[ -d "$OUTPUT_DIR/success" ]]; then
        for d in "$OUTPUT_DIR"/success/SRR*; do
          [[ -d "$d" ]] || continue
          if [[ ! -f "$d/cellranger_output/outs/possorted_genome_bam.bam" ]]; then
            if type append_failed_item >/dev/null 2>&1; then
              append_failed_item 8 "$(basename "$d")" || true
            else
              mkdir -p "${REPORTS_DIR:-/work/results/reports}" && echo "$(basename "$d")" >> "${REPORTS_DIR:-/work/results/reports}/step8_failed.txt" || true
            fi
          fi
        done
      fi
      mark_warn 8 "snakemake exited $status; verifying what did complete" || true
    fi
    CELLRANGER_STAGE="ran"
    # Snakemake exiting 0 only means it had nothing left to do. Ask the
    # filesystem which of THIS run's expected samples now carry Cell Ranger
    # output whose provenance matches this run's input. Only this call site may
    # report a dispatch: Snakemake has just run here, under this run's id, so
    # the eligibility report it stamped describes what THIS run asked for.
    genoar_provenance verify "$MANIFEST" "$OUTPUT_DIR/success" "$RUN_ID" \
      "$RUN_STARTED_EPOCH" "$RUN_DIR" "$ADOPT_PRIOR" "$INPUT_DIR" 1
    load_provenance_outcome
    if [[ "$PROV_COMPLETED" -eq "$PROV_EXPECTED" && "$PROV_EXPECTED" -gt 0 ]]; then
      mark_ok 8 "cellranger ok ($PROV_COMPLETED/$PROV_EXPECTED expected sample(s) with verified output)" || true
      echo "[done] Step 8 completed: $PROV_COMPLETED of $PROV_EXPECTED expected sample(s) have verified Cell Ranger output."
    elif [[ "$PROV_COMPLETED" -gt 0 ]]; then
      echo "[step8] WARNING: only $PROV_COMPLETED of $PROV_EXPECTED expected sample(s) have verified Cell Ranger output."
      mark_warn 8 "cellranger partial ($PROV_COMPLETED/$PROV_EXPECTED expected sample(s))" || true
      echo "[done] Step 8 finished with some expected samples unprocessed."
    else
      echo "[step8] WARNING: Snakemake exited 0 but no expected sample has verified Cell Ranger output."
      if [[ "$PROV_ADOPTED" -gt 0 ]]; then
        echo "[step8] WARNING: $PROV_ADOPTED sample(s) hold output adopted on request; adoption is"
        echo "[step8] WARNING: not verification and does not complete a sample."
      fi
      echo "[step8] WARNING: no 'cellranger count' job produced usable output. See the census above."
      mark_warn 8 "cellranger ran no jobs (0 of $PROV_EXPECTED expected sample(s) completed)" || true
      echo "[done] Step 8 finished WITHOUT processing any sample."
    fi
  fi
else
  CELLRANGER_STAGE="not_in_image"
  CELLRANGER_SKIP_REASON="snakemake is not installed in this image (build with --target step9 to include the Cell Ranger stage)"
  echo "[step8] Skipped: snakemake not available in this image."
fi

# ---------------------
# Step 9: handle failed samples (swap R1/R2 and move to retry)
# ---------------------
if [[ -f "/pipeline_next/rename_move_failed_fastqs.py" ]]; then
  echo "[step9] Detecting failed samples and preparing retry set"
  set +e
  python3 /pipeline_next/rename_move_failed_fastqs.py "$OUTPUT_DIR/success" "$OUTPUT_DIR" "$OUTPUT_DIR/retry_failed" 2>&1 | tee -a "$LOG_DIR/step9_retry_failed.log"
  status=${PIPESTATUS[0]}; set -e
  if [[ $status -ne 0 ]]; then
    log_err "Step 9 failed with code $status" || true
    mark_err 9 "retry failed handling failed ($status)" || true
    exit "$EXIT_STEP_FAILED"
  fi
  mark_ok 9 "retry failed handling ok" || true
  echo "[done] Step 9 completed."
else
  echo "[step9] Skipped (rename_move_failed_fastqs.py not available in this image)."
fi

# ---------------------
# Final status banner
#
# "Every step exited 0" is not the same claim as "Cell Ranger analysed the
# samples this run was given". The banner states only what is true of THIS run,
# measured against its expected-sample manifest, and the exit status matches it:
#   0 - every expected sample carries verified Cell Ranger output
#   2 - misconfigured (already exited at step 0)
#   3 - valid, complete run in which no expected sample was analysed and nothing
#       was reused in its place, or in which there were no expected samples
#   4 - partial: some expected samples have verified output and others do not,
#       or none do and some were adopted
# An image without the Cell Ranger stage (steps 1-7 build targets) still exits 0
# and says plainly that it performed no analysis.
#
# Verified and adopted are printed as two different things, always, because they
# are two different claims. Verified means this run can point at what makes the
# output its input's: its own Cell Ranger job's record, or a receipt from the
# run that did the work. Adopted means the operator told the run to use output
# it could not check (GENOAR_ADOPT_PRIOR_RESULTS=1). A banner that added them up
# reported "all expected samples have Cell Ranger output", and exited 0, for a
# BAM that carried no provenance at all.
# ---------------------
SUCCESS_ROOT="$OUTPUT_DIR/success"

# Step 8 runs the census only when Snakemake ran. Take it now for every other
# path, so the banner always speaks about the expected set. Reaching here means
# Cell Ranger was never dispatched in this invocation, so nothing found on disk
# can be this run's own work however recently it was written: dispatched=0.
if [[ ! -f "$RUN_DIR/outcome.env" ]]; then
  genoar_provenance verify "$MANIFEST" "$SUCCESS_ROOT" "$RUN_ID" \
    "$RUN_STARTED_EPOCH" "$RUN_DIR" "$ADOPT_PRIOR" "$INPUT_DIR" 0
fi
# ---------------------
# Retention (not a numbered step: the numbered steps are the analysis)
#
# Runs on what the verifier has already decided, so the outcome this run
# reports is written before anything is deleted and does not depend on what
# retention does. Skipped entirely under the defaults.
# ---------------------
if [[ "$RETAIN" != "all" || "$RELEASE_INPUT" -eq 1 ]]; then
  echo "[retain] Applying retention: retain=$RETAIN release_input=$RELEASE_INPUT"
  set +e
  genoar_provenance release "$MANIFEST" "$OUTPUT_DIR/success" "$RUN_ID" \
    "$RUN_DIR" "$RETAIN" "$RELEASE_INPUT" "$INPUT_DIR" \
    2>&1 | tee -a "$LOG_DIR/retention.log"
  status=${PIPESTATUS[0]}; set -e
  if [[ $status -ne 0 ]]; then
    # Retention failing does not unmake the analysis. The run is reported on
    # what it produced; the disk it could not free is a separate problem, and
    # saying so beats turning a completed run into a failed one.
    echo "[retain] WARNING: retention exited $status; results are untouched by it."
  fi
  # No step sentinel. The numbered steps are the analysis, step 9 already
  # belongs to retry handling, and clear_step_sentinels wipes a step's whole
  # set before writing -- so marking one here erased the retry step's result.
  # What retention did is in release.json and in its own log.
fi

load_provenance_outcome

print_sample_reasons() {
  [[ -s "$RUN_DIR/reasons.txt" ]] || return 0
  cat "$RUN_DIR/reasons.txt"
  echo "[result] Details: $RUN_DIR/outcome.json"
}

echo "$BANNER_BAR"
echo "[result] Run id: $RUN_ID"
echo "[result] Expected samples for this run: $PROV_EXPECTED"
echo "[result]   verified complete: $PROV_COMPLETED (fresh $PROV_FRESH, cache hit $PROV_CACHE_HIT, released ${PROV_RELEASED:-0})"
if [[ "${PROV_SUSPECT:-0}" -gt 0 ]]; then
  echo "[result]   produced but not a result: ${PROV_SUSPECT} (see the reasons below)"
fi
echo "[result]   adopted, NOT verified and NOT counted as complete: $PROV_ADOPTED"
echo "[result]   ineligible: $PROV_INELIGIBLE, missing: $PROV_MISSING, unverified: $PROV_UNVERIFIED, stale: $PROV_STALE"
if [[ "$PROV_OTHER" -gt 0 ]]; then
  echo "[result] $PROV_OTHER sample(s) from earlier runs also hold Cell Ranger output."
  echo "[result] They were left untouched and do NOT count towards this run."
fi

if [[ "$CELLRANGER_STAGE" == "not_in_image" ]]; then
  echo "[result] PIPELINE COMPLETED (steps 1-7 only)."
  echo "[result] Cell Ranger did NOT run: $CELLRANGER_SKIP_REASON"
  echo "[result] No single-cell analysis was performed by this run."
  echo "$BANNER_BAR"
  exit 0
fi

if [[ "$PROV_EXPECTED" -eq 0 ]]; then
  echo "[result] WARNING: THIS RUN WAS GIVEN NO SAMPLES TO PROCESS."
  echo "[result] No SRR input was found in $INPUT_DIR."
  echo "[result] This run produced no single-cell analysis. Do not report it as a success."
  echo "[result] Exiting $EXIT_NOTHING_PROCESSED (valid run, no input to process)."
  echo "$BANNER_BAR"
  exit "$EXIT_NOTHING_PROCESSED"
fi

if [[ "$PROV_COMPLETED" -eq "$PROV_EXPECTED" ]]; then
  echo "[result] PIPELINE COMPLETED - all $PROV_EXPECTED expected sample(s) have VERIFIED Cell Ranger output."
  if [[ "$PROV_FRESH" -eq 0 ]]; then
    echo "[result] No new analysis was needed: every sample was already complete for this input,"
    echo "[result] and a provenance receipt says so for each of them."
  fi
  echo "[result] Outputs: $SUCCESS_ROOT/<sample>/cellranger_output/outs/"
  print_sample_reasons
  echo "$BANNER_BAR"
  exit 0
fi

if [[ "$PROV_COMPLETED" -gt 0 ]]; then
  echo "[result] WARNING: PIPELINE PARTIALLY COMPLETED."
  echo "[result] $PROV_COMPLETED of $PROV_EXPECTED expected sample(s) have VERIFIED Cell Ranger output."
  if [[ "$PROV_ADOPTED" -gt 0 ]]; then
    echo "[result] A further $PROV_ADOPTED were ADOPTED, not verified: output taken on the operator's"
    echo "[result] word (GENOAR_ADOPT_PRIOR_RESULTS=1), by this run or by an earlier one whose"
    echo "[result] adoption receipt is still beside it. Adopted is not completed, and is not part"
    echo "[result] of the $PROV_COMPLETED above."
  fi
  echo "[result] The rest did not, for these reasons:"
  print_sample_reasons
  echo "[result] Do not report this run as a success."
  echo "[result] Exiting $EXIT_PARTIAL (partial: some expected samples were not analysed)."
  echo "$BANNER_BAR"
  exit "$EXIT_PARTIAL"
fi

if [[ "$PROV_ADOPTED" -gt 0 ]]; then
  # Nothing verified, but the operator asked for pre-existing output to be used
  # and it was. That is not "nothing qualified" (exit 3): output was reused and
  # is being reported. It is also not a completion, because no evidence ties any
  # of it to this run's input - which is what adoption means.
  echo "[result] WARNING: PIPELINE INCOMPLETE - NOTHING THIS RUN CAN VOUCH FOR."
  echo "[result] 0 of $PROV_EXPECTED expected sample(s) have VERIFIED Cell Ranger output."
  echo "[result] $PROV_ADOPTED sample(s) were ADOPTED: pre-existing output taken on the"
  echo "[result] operator's word (GENOAR_ADOPT_PRIOR_RESULTS=1), by this run or by an"
  echo "[result] earlier one whose adoption receipt is still beside the output."
  echo "[result] Adoption records what was reused. It is not evidence that the output was"
  echo "[result] produced from this run's input, so it is not a completion - and it does not"
  echo "[result] become one by being reused again."
  print_sample_reasons
  echo "[result] Do not report this run as a success."
  echo "[result] Exiting $EXIT_PARTIAL (no expected sample has verified output; $PROV_ADOPTED adopted)."
  echo "$BANNER_BAR"
  exit "$EXIT_PARTIAL"
fi

case "$CELLRANGER_STAGE" in
  missing_cellranger|missing_ref)
    echo "[result] WARNING: PIPELINE COMPLETED BUT CELL RANGER NEVER RAN."
    echo "[result] Reason: $CELLRANGER_SKIP_REASON"
    echo "[result] Steps 1-7 finished; 0 of $PROV_EXPECTED expected sample(s) were analysed."
    echo "[result] Exiting $EXIT_CONFIG_ERROR (misconfigured run, not an empty result)."
    echo "$BANNER_BAR"
    exit "$EXIT_CONFIG_ERROR"
    ;;
  *)
    if [[ "${PROV_SUSPECT:-0}" -gt 0 ]]; then
      # Cell Ranger ran, and what it produced is not a result. Saying it
      # "processed 0 samples" is the one thing that is not true here, and it
      # sends whoever reads it looking at the wrong end of the run.
      echo "[result] WARNING: CELL RANGER RAN AND PRODUCED NOTHING USABLE."
      echo "[result] $PROV_SUSPECT of $PROV_EXPECTED expected sample(s) produced output whose own"
      echo "[result] metrics say it is not a result. The output exists; it is the input that is wrong."
      echo "[result] Why:"
      print_sample_reasons
      echo "[result] Check which reads were given as R1 and R2, and which reference was used."
      echo "[result] Exiting $EXIT_NOTHING_PROCESSED (ran, nothing usable came out)."
      echo "$BANNER_BAR"
      exit "$EXIT_NOTHING_PROCESSED"
    fi
    echo "[result] WARNING: PIPELINE COMPLETED BUT CELL RANGER PROCESSED 0 SAMPLES."
    echo "[result] Steps 1-7 finished, but none of the $PROV_EXPECTED expected sample(s) was analysed."
    echo "[result] Why:"
    print_sample_reasons
    echo "[result] This run produced no single-cell analysis. Do not report it as a success."
    echo "[result] Exiting $EXIT_NOTHING_PROCESSED (valid run, nothing qualified for Cell Ranger)."
    echo "$BANNER_BAR"
    exit "$EXIT_NOTHING_PROCESSED"
    ;;
esac
