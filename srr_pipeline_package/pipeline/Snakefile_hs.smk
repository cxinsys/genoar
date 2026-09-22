import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time

success_dir = config.get("success_dir", "/work/results/success")
ref_path = config.get("ref_path", "/ref")
cellranger_bin = config.get("cellranger_bin", "/opt/cellranger/cellranger")
cellranger_threads = int(config.get("cellranger_threads", 16))
cellranger_mem = int(config.get("cellranger_mem", 100))
# The id of the run that is dispatching these samples. Stamped into the
# eligibility report so the provenance check can tell "this run handed these
# samples to Cell Ranger" from "a report left behind by an earlier run".
run_id = str(config.get("run_id", ""))
# This run's own record directory, <results>/runs/<run_id>/. The orchestrator
# owns it and passes it in; the Cell Ranger rule writes its completion evidence
# under it. Empty when snakemake is driven by hand, in which case no sample can
# be credited to a run -- which is the safe direction to be wrong in.
run_dir = str(config.get("run_dir", ""))

# Canonical, sample-independent reasons. The orchestrator groups the ineligible
# rows of the report by this exact string, so keep per-sample numbers out of it.
REASON_NO_FASTQ = "no fastq.gz files found"
REASON_SINGLE_FASTQ = "only 1 FASTQ file; Cell Ranger requires paired R1/R2 reads"
REASON_NO_PAIR = (
    "no R1/R2 pair found (expected <sample>_S1_R[12]_001.fastq.gz "
    "or <sample>_S*_L*_R[12]_*.fastq.gz)"
)
# Step 7 refuses to rename exactly this layout, with the same meaning. The two
# steps have to agree: step 7 recording a sample as ineligible while step 8
# selected it anyway is how an unpairable sample reached Cell Ranger.
REASON_LANE_INCOMPLETE = (
    "multi-lane FASTQ names do not pair R1 with R2 within the same lane; "
    "Cell Ranger needs both reads of every lane it is given"
)

# ---------------------------------------------------------------------------
# What Cell Ranger actually did, recorded where it is known
#
# "The sample was eligible, snakemake exited 0, and the BAM's timestamp is not
# older than the run" used to stand for "this run produced it". All three can
# hold for output the run never touched: eligibility is decided before any work
# happens, snakemake exits 0 precisely when it finds nothing left to do, and a
# modification time says when a file was last written, never by whom.
#
# The rule below is the one place that knows: it ran Cell Ranger, for this
# sample, in this run. So the rule writes the evidence.
#
#   <run_dir>/cellranger/<sample>.started   before Cell Ranger is started
#   <run_dir>/cellranger/<sample>.json      after it returns, renamed into place
#
# The completion record names the run, the sample, and the file Cell Ranger
# produced -- the device and inode it occupies, and its size. The orchestrator
# credits a sample to this run only when such a record exists AND still
# describes the file on disk, so the record cannot be stretched to cover a file
# it was not written about. (Not the modification time: snakemake bumps that
# itself after every job. See record_cellranger_completed.)
#
# A job killed between the two writes leaves only the `.started` marker, which
# grants nothing: the run says it began the work and cannot show it finished.
# The completion record is written under a temporary name and renamed, so a
# half-written record can only ever appear under a name nothing reads; a record
# that does not parse, or that lacks any required field, is treated as absent.
#
# This is the only thing that decides freshness, and it is written nowhere else.
# The receipt beside the sample (.genoar_cellranger.json) is a different claim
# for a different reader -- "this BAM came from this input" -- which the
# orchestrator derives FROM this record and which is the only part that outlives
# the run. Evidence flows one way: rule -> completion record -> receipt.
# ---------------------------------------------------------------------------
COMPLETION_DIR_NAME = "cellranger"
COMPLETION_RECORD_KIND = "genoar.cellranger.completion"
COMPLETION_RECORD_VERSION = 1
STARTED_SUFFIX = ".started"
# How much of each end of the BAM is hashed into the completion record. Read by
# both halves: the orchestrator recomputes the same two windows over the file it
# finds. See bam_edge_digests.
BAM_DIGEST_BYTES = 64 * 1024

ELIGIBILITY_REPORT = os.path.join(success_dir, "cellranger_eligibility.tsv")
# Step 6 writes this census and step 7 appends to it. Step 8 appends the samples
# only it can see (a sample step 6 never listed, e.g. one with more than four
# FASTQ files) so that every exclusion is explained in the pipeline's own output.
INELIGIBLE_CENSUS = os.path.join(success_dir, "cellranger_ineligible.tsv")


def lane_of(path):
    """The lane token of a FASTQ name, read the way step 7 reads it.

    Step 7 takes everything after the last `_L` up to the next `_`; step 8 must
    split lanes identically or the two steps can disagree about what a lane is.
    """
    name = os.path.basename(path)
    return name.rsplit("_L", 1)[-1].split("_", 1)[0]


def layout_verdict(sample):
    """(eligible, reason) for the FASTQ layout on disk.

    Cell Ranger is handed the sample directory itself (`--fastqs <dir>
    --sample <name>`), so it re-globs every read file there; a lane whose mate
    is missing cannot be hidden from it by choosing files here. Eligibility
    therefore means a COMPLETE set:

      * single lane: <sample>_S1_R1_001.fastq.gz and its R2, or
      * multi lane:  every lane carrying R1 also carries R2, and vice versa.

    An R1 in L001 and an R2 in L002 satisfied the old check -- neither lane was
    complete, yet the sample was selected and Cell Ranger failed on it. A
    partially complete set (L001 complete, L002 missing its R2) is refused for
    the same reason: the lane Cell Ranger cannot pair is one it will still see.
    """
    sample_dir = os.path.join(success_dir, sample)
    if not os.path.isdir(sample_dir):
        return False, ""
    r1 = os.path.join(sample_dir, f"{sample}_S1_R1_001.fastq.gz")
    r2 = os.path.join(sample_dir, f"{sample}_S1_R2_001.fastq.gz")
    if os.path.exists(r1) and os.path.exists(r2):
        return True, ""

    r1_lanes = {lane_of(p) for p in
                glob.glob(os.path.join(sample_dir, f"{sample}_S*_L*_R1_*.fastq.gz"))}
    r2_lanes = {lane_of(p) for p in
                glob.glob(os.path.join(sample_dir, f"{sample}_S*_L*_R2_*.fastq.gz"))}
    if not r1_lanes and not r2_lanes:
        return False, ""
    if r1_lanes == r2_lanes:
        return True, ""
    return False, REASON_LANE_INCOMPLETE


def has_r1r2(sample):
    """Whether Cell Ranger can be run on this sample at all."""
    return layout_verdict(sample)[0]


def classify_sample(sample):
    """Say whether a sample can be handed to Cell Ranger, and if not, why.

    Returns (eligible, reason, detail). `reason` is one of the REASON_*
    constants for ineligible samples and "" for eligible ones.
    """
    sample_dir = os.path.join(success_dir, sample)
    fastqs = glob.glob(os.path.join(sample_dir, "*.fastq.gz"))
    detail = "fastq=%d" % len(fastqs)
    eligible, reason = layout_verdict(sample)
    if eligible:
        return True, "", detail
    if not fastqs:
        return False, REASON_NO_FASTQ, detail
    if len(fastqs) == 1:
        return False, REASON_SINGLE_FASTQ, detail
    if reason:
        return False, reason, detail
    return False, REASON_NO_PAIR, detail


def survey_samples():
    """Split the success directory into Cell Ranger-eligible and ineligible sets.

    Returns (eligible, ineligible) where `eligible` is a list of sample names and
    `ineligible` is a list of (sample, reason, detail) triples.
    """
    if not os.path.isdir(success_dir):
        return [], []
    eligible = []
    ineligible = []
    for name in sorted(os.listdir(success_dir)):
        p = os.path.join(success_dir, name)
        if not (os.path.isdir(p) and name.startswith("SRR")):
            continue
        ok, reason, detail = classify_sample(name)
        if ok:
            eligible.append(name)
        else:
            ineligible.append((name, reason, detail))
    return eligible, ineligible


def list_samples():
    """The samples Cell Ranger will actually be run on."""
    return survey_samples()[0]


def write_eligibility_report(eligible, ineligible):
    """Persist the census so the orchestrator can report it after the run."""
    try:
        os.makedirs(success_dir, exist_ok=True)
        with open(ELIGIBILITY_REPORT, "w") as fh:
            # Which run selected these samples. Without it, a report from an
            # earlier run reads as this run's dispatch record.
            fh.write("# run_id\t%s\n" % run_id)
            fh.write("# sample\tstatus\treason\tdetail\n")
            for name in eligible:
                fh.write("%s\teligible\t\t\n" % name)
            for name, reason, detail in ineligible:
                fh.write("%s\tineligible\t%s\t%s\n" % (name, reason, detail))
    except OSError as exc:
        sys.stderr.write(
            "[step8] WARNING: could not write %s: %s\n" % (ELIGIBILITY_REPORT, exc)
        )


def append_ineligible_census(ineligible):
    """Add step 8's exclusions to the census steps 6 and 7 write.

    Steps 6 and 7 cannot see every reason: a sample with more than four FASTQ
    files is never listed by step 6 and never processed by step 7, so an
    incomplete lane set in one could only be discovered here. One row per
    sample, so re-running step 8 cannot pile up duplicates.
    """
    if not ineligible:
        return
    try:
        os.makedirs(success_dir, exist_ok=True)
        seen = set()
        if os.path.isfile(INELIGIBLE_CENSUS):
            with open(INELIGIBLE_CENSUS) as fh:
                for line in fh:
                    if not line.startswith("#"):
                        seen.add(line.split("\t")[0].strip())
        else:
            with open(INELIGIBLE_CENSUS, "w") as fh:
                fh.write("# sample\tfastq_count\treason\n")
        with open(INELIGIBLE_CENSUS, "a") as fh:
            for name, reason, detail in ineligible:
                if name in seen:
                    continue
                count = detail.split("=")[-1] if "=" in detail else "0"
                fh.write("%s\t%s\t%s\n" % (name, count, reason))
    except OSError as exc:
        sys.stderr.write(
            "[step8] WARNING: could not append to %s: %s\n" % (INELIGIBLE_CENSUS, exc)
        )


def completion_dir():
    """Where this run records what Cell Ranger did, or "" if it has nowhere.

    Both halves of the identity are required. A record filed under no run
    directory has nowhere to live, and a record that cannot name its run cannot
    be told from another run's, which is the whole point of writing it.
    """
    if not run_dir or not run_id:
        return ""
    return os.path.join(run_dir, COMPLETION_DIR_NAME)


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def note_cellranger_started(sample):
    """Record that this run began Cell Ranger for this sample.

    Best effort, on purpose. Bookkeeping must never fail the job: snakemake
    deletes the output of a failed job, so raising here over a marker file would
    destroy a real Cell Ranger result. A marker that could not be written costs
    the run the ability to claim the sample, and it says so.
    """
    directory = completion_dir()
    if not directory:
        return
    try:
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, sample + STARTED_SUFFIX), "w") as fh:
            json.dump({"run_id": run_id, "sample": sample,
                       "started_at": utc_now()}, fh, indent=2, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write(
            "[step8] WARNING: could not record the start of %s: %s\n"
            % (sample, exc)
        )


def read_exactly(fh, offset, length):
    """`length` bytes from `offset`, or None if the file cannot give them.

    A short read is not a small answer, it is a different file than the one we
    were told about, so it returns nothing rather than a partial buffer that
    could go on to be hashed and compared as though it were complete.
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

    The BAM's identity is the device and inode it occupies and its size, and on
    some filesystems the inode number is not stable (a macOS Docker Desktop bind
    mount renumbers an unchanged file across the job boundary). These two
    digests give the orchestrator a way to answer "is this the file I wrote?"
    from the bytes rather than from the filesystem's bookkeeping.

    Only the ends, and only 64 KiB of each: a Cell Ranger BAM is several
    gigabytes, and hashing one on every run - including every resume - would
    make the honest path pay for it forever. Reading 128 KiB costs nothing at
    any BAM size.

    A file smaller than two windows has them overlap, which is not a problem:
    both are still well defined, both must match, and for a file under 128 KiB
    they cover all of it. Anything that cannot be read comes back as None,
    which the orchestrator treats as "no answer", never as a match.
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
            hashlib.sha256(tail).hexdigest(), span)


def record_cellranger_completed(sample, bam, fastqs, started_at):
    """Record that this run's Cell Ranger produced exactly this BAM.

    Written only after Cell Ranger returned successfully, from the job that ran
    it, and stamped with what identifies the file it produced: the device and
    inode it occupies, its size, and the bytes at both of its ends. That is what
    lets the orchestrator tell the file this run wrote from a file that replaced
    it afterwards - and, where the filesystem renumbers an unchanged file, tell
    those two apart at all.

    The modification time is recorded too, but as a floor rather than an
    identity. Snakemake touches every output file after the job returns, on
    purpose ("due to archive expansion or cluster clock skew, ... we know they
    must be new"), so the timestamp on disk is snakemake's, not Cell Ranger's,
    and requiring it to match would have failed honest runs at random -- exactly
    the direction this whole mechanism exists to avoid being wrong in.

    Renamed into place so no reader ever sees a partial record. As above, a
    failure here is reported and not raised: the analysis succeeded, and the
    correct consequence of losing the paperwork is that the run cannot claim the
    sample, not that the output is thrown away.
    """
    directory = completion_dir()
    if not directory:
        sys.stderr.write(
            "[step8] WARNING: no run directory was configured, so this run "
            "cannot record that it produced %s's Cell Ranger output; the "
            "sample will be reported as unverified.\n" % sample
        )
        return
    try:
        st = os.stat(bam)
        payload = {
            "record": COMPLETION_RECORD_KIND,
            "record_version": COMPLETION_RECORD_VERSION,
            "complete": True,
            "run_id": run_id,
            "sample": sample,
            "bam": os.path.abspath(bam),
            "bam_size": st.st_size,
            "bam_device": st.st_dev,
            "bam_inode": st.st_ino,
            "bam_mtime": int(st.st_mtime),
            "fastqs": sorted(os.path.basename(f) for f in fastqs),
            "digest_bytes": None,
            "bam_head": None,
            "bam_tail": None,
            "cellranger_bin": cellranger_bin,
            "started_at": started_at,
            "completed_at": utc_now(),
        }
        # Recorded here, by the job that produced the file, in the same moment
        # as its size. Digests that could not be read are left as null rather
        # than omitted or faked: the orchestrator must be able to see that this
        # record has no answer, and treat that as no answer.
        edges = bam_edge_digests(bam, st.st_size, BAM_DIGEST_BYTES)
        if edges is None:
            sys.stderr.write(
                "[step8] WARNING: could not read the ends of %s to record what "
                "it contains; if this filesystem also renumbers the file, the "
                "sample will be reported as unverified.\n" % bam
            )
        else:
            payload["bam_head"], payload["bam_tail"], payload["digest_bytes"] = \
                edges
        os.makedirs(directory, exist_ok=True)
        tmp = os.path.join(directory, sample + ".json.partial")
        with open(tmp, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, os.path.join(directory, sample + ".json"))
    except OSError as exc:
        sys.stderr.write(
            "[step8] WARNING: Cell Ranger finished %s but this run could not "
            "record it (%s); the sample will be reported as unverified.\n"
            % (sample, exc)
        )


def report_census(eligible, ineligible):
    """Print the census. Zero eligible samples must never look like a clean run."""
    bar = "[step8] " + "=" * 62
    write = sys.stderr.write
    write("[step8] Cell Ranger sample census in: %s\n" % success_dir)
    write("[step8]   eligible for Cell Ranger: %d\n" % len(eligible))
    write("[step8]   ineligible:               %d\n" % len(ineligible))
    for name, reason, detail in ineligible:
        write("[step8]     - %s: %s (%s)\n" % (name, reason, detail))
    if not eligible:
        write(bar + "\n")
        write("[step8] WARNING: NO SAMPLE QUALIFIES FOR CELL RANGER.\n")
        write("[step8] WARNING: Snakemake will finish without running a single\n")
        write("[step8] WARNING: 'cellranger count' job. This is not a successful analysis.\n")
        write(bar + "\n")
    write("[step8] Eligibility report: %s\n" % ELIGIBILITY_REPORT)
    sys.stderr.flush()


ELIGIBLE, INELIGIBLE = survey_samples()
SAMPLES = ELIGIBLE
write_eligibility_report(ELIGIBLE, INELIGIBLE)
append_ineligible_census(INELIGIBLE)
report_census(ELIGIBLE, INELIGIBLE)


onsuccess:
    if SAMPLES:
        sys.stderr.write(
            "[step8] Snakemake finished; %d sample(s) were eligible for Cell Ranger.\n"
            % len(SAMPLES)
        )
    else:
        sys.stderr.write(
            "[step8] Snakemake finished WITHOUT running Cell Ranger: 0 eligible samples.\n"
        )


rule all:
    input:
        expand("{success_dir}/{sample}/cellranger_output/outs/possorted_genome_bam.bam",
               success_dir=success_dir, sample=SAMPLES)

def get_sample_fastqs(wildcards):
    """The FASTQ files this sample's Cell Ranger job depends on.

    Only ever asked for samples the census declared eligible, i.e. samples whose
    layout is a complete pair set, so this returns the whole set: the single
    lane pair, or every read of every lane. It reads the same layout the census
    read -- if this listed files the census did not accept, Snakemake would
    depend on one set and Cell Ranger (which re-globs the directory) would be
    given another.
    """
    sample = wildcards.sample
    sample_dir = os.path.join(success_dir, sample)

    r1_standard = os.path.join(sample_dir, f"{sample}_S1_R1_001.fastq.gz")
    r2_standard = os.path.join(sample_dir, f"{sample}_S1_R2_001.fastq.gz")
    if os.path.exists(r1_standard) and os.path.exists(r2_standard):
        return [r1_standard, r2_standard]

    r1_files = sorted(glob.glob(
        os.path.join(sample_dir, f"{sample}_S*_L*_R1_*.fastq.gz")))
    r2_files = sorted(glob.glob(
        os.path.join(sample_dir, f"{sample}_S*_L*_R2_*.fastq.gz")))
    return r1_files + r2_files

def cellranger_version():
    """The version string `cellranger --version` reports, or "" if unavailable."""
    try:
        out = subprocess.run([cellranger_bin, "--version"], capture_output=True,
                             text=True, timeout=60).stdout
    except Exception:
        return ""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return m.group(0) if m else ""


def cellranger_bam_flag():
    """`--create-bam` where it exists, nothing where it does not.

    Cell Ranger 8.0 made BAM output opt-in and introduced `--create-bam`;
    7.x has no such argument and refuses to start when handed one
    ("Found argument '--create-bam' which wasn't expected"). 7.x writes the
    BAM by default, so omitting the flag there asks for the same thing.

    Issue 2 of the K-BDS pilot, fixed by hand on the cluster to get it through.
    """
    version = cellranger_version()
    major = 0
    if version:
        try:
            major = int(version.split(".")[0])
        except ValueError:
            major = 0
    if major >= 8:
        return " --create-bam=true"
    if major == 0:
        # Version unreadable: assume the newer form, which is what this
        # pipeline was written against, and let Cell Ranger say if it is wrong.
        return " --create-bam=true"
    return ""


rule cellranger_count:
    input:
        fastqs=get_sample_fastqs
    output:
        bam=os.path.join(success_dir, "{sample}", "cellranger_output", "outs", "possorted_genome_bam.bam")
    params:
        sample_dir=lambda wc: os.path.join(success_dir, wc.sample),
        ref=lambda wc: ref_path,
        cr=lambda wc: cellranger_bin,
        # Cell Ranger's own --id, i.e. the name of the output directory it
        # creates. Not this pipeline's run id, which is `run_id` above.
        cr_id="cellranger_output",
        mem_gb=cellranger_mem,
        bam_flag=lambda wc: cellranger_bam_flag()
    threads: cellranger_threads
    run:
        # `run:` rather than `shell:` so that the job which runs Cell Ranger is
        # also the job that records having run it. The command issued below is
        # the one this rule has always issued, argument for argument.
        started_at = utc_now()
        note_cellranger_started(wildcards.sample)
        shell(
            "cd {params.sample_dir} && "
            "rm -rf {params.cr_id} && "
            "{params.cr} count "
            "--id {params.cr_id} "
            "--transcriptome {params.ref} "
            "--fastqs {params.sample_dir} "
            "--sample {wildcards.sample} "
            "--localcores {threads} "
            "--localmem {params.mem_gb}"
            "{params.bam_flag}"
        )
        record_cellranger_completed(wildcards.sample, output.bam,
                                    list(input.fastqs), started_at)

