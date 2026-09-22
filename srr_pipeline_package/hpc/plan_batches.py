#!/usr/bin/env python3
"""Split a corpus into batches a facility can actually hold.

A run that fits on the disk and inside the queue's wall clock is one job. A
corpus is neither: the inputs are larger than any quota a shared facility hands
out, and one job cannot run for the weeks the whole set needs. So the work is
rotated — take a batch, analyse it, keep what the analysis was for, release the
rest, take the next — and the size of a batch is decided by whichever runs out
first, the disk or the clock.

This works that size out, says which of the two bound it, and writes the
batches. It measures what it can measure and asks about the rest; nothing is
guessed silently, and every number in the plan is printed with where it came
from. See site_settings.py for the ladder.

    python3 plan_batches.py --input-dir ./sra --config hpc_config.yaml --out ./plan

Nothing here connects anywhere or moves any data. It reads the input directory
and writes a plan.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import site_settings as st  # noqa: E402

try:
    import yaml
except Exception:  # pragma: no cover - yaml is present wherever the bundle is
    yaml = None

GIB = float(1 << 30)

# How much of a sample's input survives the run, by retention policy, as a
# fraction of that input. Starting points only: a plan measures the real figure
# from an earlier run's output whenever one is there to measure, and says which
# of the two it used. They are deliberately generous — a plan that overestimates
# what it keeps produces a smaller batch, and a small batch wastes queue time
# where a large one wastes the run.
RESIDUE_RATIO = {"all": 1.5, "analysis": 0.20, "summary": 0.03}

RETAIN_POLICIES = ("all", "analysis", "summary")


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------

def find_samples(input_dir: Path):
    """(sample, bytes) for everything in the input directory, largest first.

    Both layouts the pipeline accepts: `<sample>/<sample>.sra` as step 1 leaves
    it, and a bare `<sample>.sra` as a download leaves it. A sample present as
    both -- a directory beside a leftover archive of the same name -- is one
    sample, counted once from the directory, not two totalling twice its size.

    Only `.sra`. Accepting `.fastq.gz` as well looked more generous and was
    wrong twice over: `Path.stem` strips one suffix, so the sample came out
    named `..._R1_001.fastq`, and R1 and R2 became two samples with names no
    array task can find on disk.

    Largest first, because the batches are packed largest-first and a plan that
    leaves the big ones to the end is a plan whose last batch does not fit.
    """
    found, seen_targets = {}, {}
    if not input_dir.is_dir():
        return []
    for entry in sorted(input_dir.iterdir()):
        # Resolved, so a link and its target are not counted as two samples.
        try:
            target = str(entry.resolve())
        except OSError:
            continue
        if entry.is_dir():
            size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            # A zero-byte sample directory is broken input, and dropping it
            # here made it vanish from the plan without ever being mentioned.
            found[entry.name] = size
            seen_targets[target] = entry.name
        elif entry.is_file() and entry.suffix.lower() == ".sra":
            if target in seen_targets or entry.stem in found:
                continue
            found[entry.stem] = entry.stat().st_size
            seen_targets[target] = entry.stem
    return sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))


def measure_residue_ratio(success_dir: Path, samples, floor=None):
    """What a finished sample really left behind, if any finished here.

    An estimate that can be replaced by a measurement should be, so a site's
    second plan is built from its own first run rather than from a table
    written elsewhere.

    Never below `floor`, which is what the retention policy about to be used
    would leave. Nothing here can tell which policy produced the tree it is
    measuring, and the dangerous direction is one way round: measuring a
    `summary` run and then planning an `all` run underestimates what will
    accumulate by fifty times and overfills the disk. Measuring high and
    planning low only costs a smaller batch.

    Returns None rather than 0.0 when there is nothing to measure. A ratio of
    zero is not a measurement, and passing it on stopped the whole plan on a
    "must be greater than zero".
    """
    if not success_dir or not Path(success_dir).is_dir():
        return None
    sizes = dict(samples)
    kept, came_from = 0, 0
    for entry in sorted(Path(success_dir).iterdir()):
        if not entry.is_dir() or entry.name not in sizes:
            continue
        # The whole sample directory. Step 5 copies the .sra and every FASTQ in
        # beside the outputs, so counting only cellranger_output/outs measured
        # the smaller half and planned batches against it.
        kept += sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        came_from += sizes[entry.name]
    if not came_from or not kept:
        return None
    ratio = kept / float(came_from)
    if floor is not None and ratio < floor:
        return floor, ("measured from %s as %.3f, held at the %.3f this "
                       "retention policy keeps" % (success_dir, ratio, floor))
    return ratio, "measured from %s" % success_dir


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def plan(samples, disk_gb, walltime_min, minutes_per_gb, residue_ratio,
         fastq_expansion, clock_margin=0.8, release_input=False,
         concurrent_jobs=1):
    """How the corpus is split, and whether it fits at all.

    The wall clock decides the batch size: a queue stops a job whatever it is
    doing, and a sample cut in half is a sample to do again.

    The disk has both cumulative and per-batch terms. Kept output accumulates --
    batch two's matrices sit beside batch one's -- while expanded FASTQ and the
    temporary copy made by step 5 exist only for the batches in flight. A
    smaller batch can reduce those transient terms, but cannot make the resident
    inputs or accumulated retained output disappear.

    So the whole run is simulated: the input that is on the cluster, the
    residue that has piled up, and the scratch/copy for samples in flight, at
    every point. The maximum of those points is the quota figure to take to the
    facility. It can move with `retain`, `release_input`, concurrency, the batch
    size, or the amount of corpus being run; each changes a different term.

    `transfer_and_submit.sh` uploads the inputs before the array is submitted,
    so they start out all present; with `release_input` they come off as the
    batches that used them finish.
    """
    empty = {"batch_size": 0, "batches": [], "bound_by": "nothing to do",
             "per_sample_gb": 0.0, "by_clock": 0, "scratch_gb": 0.0,
             "corpus_gb": 0.0, "peak_gb": 0.0, "fits": False,
             "residue_total_gb": 0.0}
    if not samples:
        return empty

    total_bytes = sum(size for _, size in samples)
    corpus_gb = total_bytes / GIB
    per_sample_gb = (total_bytes / float(len(samples))) / GIB
    largest_gb = max(size for _, size in samples) / GIB

    # Steps 1 to 7 are phases over the WHOLE batch, not a loop over samples:
    # step 2 dumps every sample to uncompressed FASTQ before step 3 compresses
    # any of it. So the working set at that boundary is the batch's inputs
    # expanded, not one sample's -- pricing one sample understated it by more
    # than an order of magnitude, on the figure the quota is asked for.
    #
    # Multiplied by the tasks the scheduler may start together: the array
    # shares one filesystem.
    scratch = 0.0  # computed per batch below, from the batch's own size
    # Time is per gigabyte, not per sample. Cell Ranger's work is proportional
    # to the reads it is given, and this corpus is not uniform: the median
    # sample is 11 GB and the largest is 170. A flat per-sample estimate --
    # taken from a 2 GB pilot -- said 163 samples fit in five days, when the
    # true figure at the corpus mean is about fifteen. Batches are packed by
    # accumulated time instead of by count.
    #
    # Not the whole wall clock, either. A batch sized to fill it exactly times
    # out whenever a sample runs slower than the estimate, which is half of
    # them, and a task killed at the limit loses what it was in the middle of.
    budget_min = walltime_min * clock_margin
    if minutes_per_gb <= 0 or budget_min <= 0:
        return dict(empty, bound_by="clock", per_sample_gb=per_sample_gb,
                    corpus_gb=corpus_gb)
    # A sample that cannot finish inside one job at all, however alone it runs.
    over_clock = [name for name, size in samples
                  if (size / GIB) * minutes_per_gb > budget_min]
    # Pack by time, largest first, so the batches that hold the big samples
    # are the short ones.
    runnable = [(n, sz) for n, sz in samples if n not in set(over_clock)]
    chunks, current, current_min = [], [], 0.0
    for name, size in runnable:
        cost = (size / GIB) * minutes_per_gb
        if current and current_min + cost > budget_min:
            chunks.append(current)
            current, current_min = [], 0.0
        current.append((name, size))
        current_min += cost
    if current:
        chunks.append(current)
    if not chunks:
        return dict(empty, bound_by="clock", per_sample_gb=per_sample_gb,
                    corpus_gb=corpus_gb, over_clock=over_clock)

    largest_chunk_gb = max(sum(sz for _, sz in c) / GIB for c in chunks)
    scratch = largest_chunk_gb * fastq_expansion * concurrent_jobs
    by_clock = max(len(c) for c in chunks)

    batches, peak = [], 0.0
    input_on_disk, residue = corpus_gb, 0.0
    for chunk in chunks:
        chunk_gb = sum(size for _, size in chunk) / GIB
        transient = largest_chunk_gb * concurrent_jobs
        during = (input_on_disk + residue + chunk_gb * residue_ratio
                  + transient + scratch)
        peak = max(peak, during)
        residue += chunk_gb * residue_ratio
        if release_input and concurrent_jobs == 1:
            input_on_disk -= chunk_gb
        batches.append({
            "batch": len(batches) + 1,
            "samples": [name for name, _ in chunk],
            "input_gb": round(chunk_gb, 2),
            "peak_gb": round(during, 2),
            "transient_gb": round(transient, 2),
            "estimated_minutes": int(chunk_gb * minutes_per_gb),
        })

    disk_fits = peak <= disk_gb
    return {"batch_size": max(len(b["samples"]) for b in batches),
            "smallest_batch": min(len(b["samples"]) for b in batches),
            "batches": batches, "bound_by": "clock",
            "by_clock": by_clock, "per_sample_gb": per_sample_gb,
            "largest_sample_gb": largest_gb, "scratch_gb": scratch,
            "corpus_gb": corpus_gb, "residue_total_gb": corpus_gb * residue_ratio,
            "transient_gb": max(b["transient_gb"] for b in batches),
            "peak_gb": peak, "fits": disk_fits and not over_clock,
            "concurrent_jobs": concurrent_jobs,
            "over_clock": over_clock, "minutes_per_gb": minutes_per_gb,
            "release_input": bool(release_input)}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def settings_for(input_dir: Path, out_dir: Path, success_dir, samples, policy):
    """Every value this needs, with how to find it when nobody wrote it down."""
    return {
        "retain": st.Setting(
            "retain", "What a completed sample keeps (all/analysis/summary)",
            "hpc_config.yaml", st.one_of(*RETAIN_POLICIES),
            # `all`, because that is what the pipeline does when the key is
            # absent. A planner that assumed `analysis` planned against a fifth
            # of what the run would actually keep.
            default="all"),
        "release_input": st.Setting(
            "release_input",
            "Whether a sample's input is deleted once its output is verified",
            "hpc_config.yaml", st.yes_or_no, default=False),
        "disk_gb": st.Setting(
            "disk_gb", "The cluster quota this run may occupy, in GB",
            "hpc_config.yaml", st.positive_number, unit="GB"),
        "walltime": st.Setting(
            "walltime", "The queue's wall clock limit for one job",
            "hpc_config.yaml", st.walltime, unit="HH:MM:SS"),
        "minutes_per_gb": st.Setting(
            "minutes_per_gb",
            "Measure compute minutes per gigabyte on this cluster, or supply "
            "an explicit conservative site value",
            "hpc_config.yaml", st.positive_number, unit="minutes per GB"),
        "fastq_expansion": st.Setting(
            "fastq_expansion",
            "Uncompressed FASTQ from one .sra, as a multiple of it",
            "hpc_config.yaml", st.positive_number, default=4.0),
        "clock_margin": st.Setting(
            "clock_margin",
            "Fraction of the wall clock one batch may fill",
            "hpc_config.yaml", st.positive_number, default=0.8),
        "max_concurrent_jobs": st.Setting(
            "max_concurrent_jobs",
            "How many array tasks the scheduler may run at the same time",
            "hpc_config.yaml", st.positive_int, default=1),
        "residue_ratio": st.Setting(
            "residue_ratio",
            "What a finished sample keeps, as a fraction of its input",
            "hpc_config.yaml", st.positive_number,
            detect=lambda: measure_residue_ratio(
                success_dir, samples,
                floor=RESIDUE_RATIO.get(policy, RESIDUE_RATIO["analysis"])),
            default=RESIDUE_RATIO.get(policy, RESIDUE_RATIO["analysis"])),
    }


def load_config(path):
    if not path:
        return {}
    if yaml is None:
        raise st.Unknown("PyYAML is not installed, so %s cannot be read.\n"
                         "  Pass the values as flags instead." % path)
    try:
        with open(path) as fh:
            return yaml.safe_load(fh) or {}
    except OSError as exc:
        # A mistyped --config is an operator's slip, not a bug in this. It gets
        # the same treatment as a value nobody could supply.
        raise st.Unknown("%s could not be read (%s).\n"
                         "  Copy hpc_config.example.yaml and point --config at "
                         "it, or pass the values as flags." % (path, exc))
    except yaml.YAMLError as exc:
        raise st.Unknown("%s is not valid YAML (%s)." % (path, exc))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Plan the batches a corpus has to be split into.")
    ap.add_argument("--input-dir", type=Path, required=True,
                    help="where the inputs are (.sra files, or per-sample dirs)")
    ap.add_argument("--out", type=Path, default=Path("batch_plan"),
                    help="where to write the plan (default: ./batch_plan)")
    ap.add_argument("--config", type=Path,
                    help="hpc_config.yaml, for anything already written down")
    ap.add_argument("--success-dir", type=Path,
                    help="an earlier run's results, to measure what a sample keeps")
    ap.add_argument("--review", action="store_true",
                    help="ask about every value, offering what was found")
    ap.add_argument("--non-interactive", action="store_true",
                    help="never ask; stop instead, naming the key to write")
    for key in ("retain", "release-input", "disk-gb", "walltime",
                "minutes-per-gb", "fastq-expansion", "clock-margin",
                "max-concurrent-jobs", "residue-ratio"):
        ap.add_argument("--" + key)
    args = ap.parse_args(argv)

    interactive = sys.stdin.isatty() and not args.non_interactive
    config = load_config(args.config)
    samples = find_samples(args.input_dir)
    if not samples:
        sys.stderr.write("No inputs found under %s.\n" % args.input_dir)
        return 3

    # The container finds its work with `find ... -name 'SRR*'`. Anything else
    # is staged, runs, finds nothing, and exits 0 having analysed nothing --
    # visible afterwards only as `unaccounted`, and only to somebody who runs
    # the reconciliation. Refused here, where it costs nothing.
    strangers = [name for name, _ in samples if not name.startswith("SRR")]
    if strangers:
        sys.stderr.write(
            "These are not SRR accessions, and the pipeline's own sample "
            "discovery\nwill not see them (`find ... -name 'SRR*'`):\n  %s%s\n"
            "A job over them completes having analysed nothing.\n"
            % (", ".join(strangers[:8]),
               ", ..." if len(strangers) > 8 else ""))
        return 2

    def flag(name):
        return getattr(args, name.replace("-", "_"))

    # retain first: it decides what "what a sample keeps" is even asking about.
    defs = settings_for(args.input_dir, args.out, args.success_dir, samples, "analysis")
    policy, policy_src = st.resolve(defs["retain"], flag("retain"), config,
                                    interactive, args.review)
    defs = settings_for(args.input_dir, args.out, args.success_dir, samples, policy)

    resolved, sources = {"retain": policy}, {"retain": policy_src}
    # The plan is also the execution contract.  Record the canonical spelling
    # here, after accepting the older config spellings through the shared
    # resolver, so the handoff never has to infer which tree the arithmetic
    # was meant to describe.
    mode_keys = ("legacy_pipeline", "pipeline_mode", "safe_mode")
    mode_was_written = any(
        key in config and config[key] is not None and str(config[key]).strip()
        for key in mode_keys)
    resolved["legacy_pipeline"] = st.pipeline_tree_of(config) == "legacy"
    sources["legacy_pipeline"] = (
        "hpc_config.yaml" if mode_was_written else "default")
    # Every unanswerable question at once. Reporting the first one meant a
    # site fixed it, ran again, and was told about the next -- one round trip
    # per missing key, on a facility where a round trip is days.
    missing = []
    for key in ("release_input", "disk_gb", "walltime", "minutes_per_gb",
                "fastq_expansion", "clock_margin", "max_concurrent_jobs",
                "residue_ratio"):
        try:
            value, source = st.resolve(defs[key], flag(key), config,
                                       interactive, args.review)
        except st.Unknown as unknown:
            missing.append(str(unknown))
            continue
        resolved[key], sources[key] = value, source
    if missing:
        raise st.Unknown("\n\n".join(missing))

    result = plan(samples, resolved["disk_gb"], resolved["walltime"],
                  resolved["minutes_per_gb"], resolved["residue_ratio"],
                  resolved["fastq_expansion"], resolved["clock_margin"],
                  resolved["release_input"], resolved["max_concurrent_jobs"])

    args.out.mkdir(parents=True, exist_ok=True)
    # A replan that produces fewer batches would otherwise leave the extra
    # lists from last time, and prepare_hpc_handoff ships whatever is here:
    # samples run twice, by tasks reading a plan nobody made.
    stale = sorted(args.out.glob("batch_*.txt"))
    for one in stale:
        one.unlink()
    if stale:
        sys.stderr.write("Replaced %d batch list(s) from an earlier plan in "
                         "%s.\n" % (len(stale), args.out))
    # The sizes go into the id, not only the names. On names alone, a corpus
    # whose samples are replaced by ten-times-larger ones keeps the same id, and
    # a bundle built from it carries a disk figure worked out for the old files.
    fingerprint = hashlib.sha256(
        ("\n".join("%s\t%d" % (name, size) for name, size in samples)
         + repr(sorted(resolved.items()))).encode()).hexdigest()
    plan_id = "plan-%s" % fingerprint[:12]
    # How much of the corpus would fit, found by asking the same arithmetic
    # about prefixes of it. Solving the formula by hand drifts away from the
    # model the moment the model changes.
    if (not result["fits"] and result["batches"]
            and not result.get("over_clock")):
        low, high = 0, len(samples)
        while low < high:
            middle = (low + high + 1) // 2
            trial = plan(samples[:middle], resolved["disk_gb"],
                         resolved["walltime"], resolved["minutes_per_gb"],
                         resolved["residue_ratio"], resolved["fastq_expansion"],
                         resolved["clock_margin"], resolved["release_input"],
                         resolved["max_concurrent_jobs"])
            if trial["fits"] and trial["batches"]:
                low = middle
            else:
                high = middle - 1
        result["largest_subset"] = low

    payload = {"kind": "genoar.batch.plan", "plan_id": plan_id,
               "input_dir": str(args.input_dir),
               "samples": len(samples),
               "sample_names": [name for name, _ in samples],
               "input_fingerprint": fingerprint,
               "sample_bytes": {name: size for name, size in samples},
               "total_input_gb": round(sum(s for _, s in samples) / GIB, 2),
               "settings": resolved, "sources": sources, "plan": result}
    (args.out / "plan.json").write_text(json.dumps(payload, indent=2,
                                                   sort_keys=True) + "\n")
    for batch in result["batches"]:
        (args.out / ("batch_%03d.txt" % batch["batch"])).write_text(
            "\n".join(batch["samples"]) + "\n")

    report(payload, sys.stdout)
    if not result["batches"]:
        return 4
    # A plan that does not fit is not a plan. Exiting 0 on it is how a corpus
    # gets submitted against a quota it was already known to exceed.
    return 0 if result["fits"] else 4


def report(payload, out):
    result = payload["plan"]
    settings, sources = payload["settings"], payload["sources"]
    quota = float(settings["disk_gb"])
    out.write("Corpus: %d samples, %.1f GB of input, %.2f GB each on average, "
              "largest %.2f GB\n"
              % (payload["samples"], payload["total_input_gb"],
                 result["per_sample_gb"], result.get("largest_sample_gb", 0.0)))
    out.write("\nWhat this plan was built from:\n")
    units = {"disk_gb": "GB", "walltime": "minutes",
             "minutes_per_gb": "minutes per GB",
             "residue_ratio": "of the input kept",
             "fastq_expansion": "x the input, as uncompressed FASTQ",
             "clock_margin": "of the wall clock per batch"}
    for key in sorted(settings):
        out.write("  %-20s %-10s %-22s (%s)\n"
                  % (key, settings[key], units.get(key, ""),
                     sources.get(key, "?")))

    over_clock = result.get("over_clock", [])
    if over_clock:
        out.write(
            "\nCANNOT PLAN: %d sample%s cannot finish within one job's "
            "wall clock:\n"
            % (len(over_clock), "" if len(over_clock) == 1 else "s"))
        for sample in over_clock:
            out.write("  %s\n" % sample)
        out.write(
            "Increase the queue walltime or change the explicit runtime "
            "estimate before submitting this corpus.\n")
        return

    if not result["batches"]:
        out.write("\nNothing can be planned: the wall clock does not allow one "
                  "sample.\n")
        return

    jobs = len(result["batches"])
    out.write("\n%d job%s of up to %d samples. The wall clock decides that, "
              "and only that.\n"
              % (jobs, "" if jobs == 1 else "s", result["batch_size"]))
    total_minutes = sum(b["estimated_minutes"] for b in result["batches"])
    out.write("  %.1f days of compute, if the jobs run one after another.\n"
              % (total_minutes / 60.0 / 24.0))

    out.write("\nDisk at its fullest point across the simulated run:\n")
    out.write("  %8.1f GB  inputs%s\n"
              % (result["corpus_gb"],
                 ", released as their batches finish" if result.get("release_input")
                 else " (uploaded before the array is submitted, and they stay)"))
    out.write("  %8.1f GB  output kept, accumulated over every batch\n"
              % result["residue_total_gb"])
    out.write("  %8.1f GB  a second copy of a batch's reads, while it runs "
              "(step 5 copies them)\n" % result.get("transient_gb", 0.0))
    out.write("  %8.1f GB  a batch's reads expanded to FASTQ (steps 2-3 do the\n"
              "                whole batch before compressing any of it)\n"
              % result["scratch_gb"])
    out.write("  %8.1f GB  peak, against a %.0f GB quota\n"
              % (result["peak_gb"], quota))

    if result["fits"]:
        out.write("\nIt fits, with %.1f GB to spare.\n"
                  % (quota - result["peak_gb"]))
        return

    out.write("\nIT DOES NOT FIT. Short by %.1f GB.\n"
              % (result["peak_gb"] - quota))
    out.write("  Two of those four are per batch -- the expanded FASTQ and "
              "the copied reads --\n  and they shrink with a smaller batch: a "
              "shorter walltime, or a lower\n  clock_margin, buys disk at the "
              "cost of more jobs. The inputs and the kept\n  output do not "
              "shrink that way; what moves them is `release_input: true`, a\n  "
              "stricter `retain`, fewer concurrent tasks, or running less of "
              "the corpus.\n")
    fits = result.get("largest_subset")
    if fits:
        out.write("  At this quota and these settings, about %d of the %d "
                  "samples would fit.\n" % (fits, payload["samples"]))


def cli(argv=None):
    """main(), with an unanswerable question reported rather than raised.

    A traceback tells an operator that this is broken. What is true is that a
    number is missing and they are the only one who has it, so that is what
    they are told, and the exit code says the run was misconfigured rather than
    that it failed.
    """
    try:
        return main(argv)
    except st.Unknown as unknown:
        sys.stderr.write("Cannot plan yet.\n\n%s\n" % unknown)
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("\nStopped. Nothing was written.\n")
        return 130


if __name__ == "__main__":
    sys.exit(cli())
