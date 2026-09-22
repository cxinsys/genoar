#!/usr/bin/env python3
"""
Generate an HPC handoff bundle for running Stage 3 (Cell Ranger) remotely.

Takes the local .sra output plus an HPC settings file and writes a bundle of
ready-to-run files (config, job script, transfer/submit and retrieve scripts).
Does not connect to the HPC. See srr_pipeline_package/hpc/README.md.

The scheduler comes from hpc_config.yaml. Slurm is the default. Every rule about
what a scheduler wants lives in schedulers.py, which the automatic runner reads
too, so the script a bundle carries and the commands the runner sends come from
one description of the cluster.
"""

import argparse
import json
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import schedulers  # noqa: E402
import site_settings  # noqa: E402

RUN_SCRIPT_SRC = HERE.parent / "singularity" / "run_singularity_pipeline.sh"

# `scheduler` is absent on purpose. It has a default, and a config that omits it
# gets Slurm. Every other key names something only the site can tell us.
REQUIRED_KEYS = [
    "host", "user", "remote_base", "remote_sif", "remote_ref", "remote_cellranger",
    "transfer_tool", "queue", "ncpus", "mem_gb", "walltime",
    "cellranger_threads", "cellranger_mem",
]

# The name of the file that carries this bundle's run id.
RUN_ID_FILE = "run_id.txt"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# What the pipeline was validated against, from DOCKER_USAGE_GUIDE.md. Written
# into the config this bundle carries so step 0 on the cluster can say when the
# site's Cell Ranger or reference release is a different one. A site may state
# its own pair in hpc_config.yaml; leaving either empty accepts anything
# silently.
#
# Deliberately a plain copy of the pair in cycle_test/utils/stage3_runner.py
# rather than an import: this script is handed to sites on its own, beside the
# bundle, and has to run without the rest of the tree. A test asserts the two
# still agree.
DEFAULT_CELLRANGER_VERSION = "8.0.1"
DEFAULT_REFERENCE_VERSION = "refdata-gex-GRCh38-2024-A"


def load_config(path: Path) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    missing = [k for k in REQUIRED_KEYS if k not in cfg or cfg[k] in (None, "")]
    if missing:
        raise SystemExit(f"hpc_config is missing required keys: {', '.join(missing)}")
    try:
        schedulers.validate(cfg)
    except ValueError as e:
        raise SystemExit(str(e))
    if cfg["transfer_tool"] not in ("rsync", "scp"):
        raise SystemExit("transfer_tool must be 'rsync' or 'scp'")
    return cfg


def new_run_id() -> str:
    """A run id for one submission.

    The pipeline holds this to its own rule: a letter or digit, then letters,
    digits, '.', '_' or '-', at most 64 characters. It refuses an id that
    already has a run record, so a bundle submitted twice does not fold two
    attempts into one set of records.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"hpc-{stamp}-{os.urandom(4).hex()}"


def run_scoped_config(cfg: dict, run_id: str) -> dict:
    """Give one submission its own remote work tree.

    ``remote_base`` is a reusable site-owned parent.  Uploading two runs into
    the same ``data/sra`` directory merges their inputs, so the second run
    silently analyses samples from the first.  Every generated bundle owns
    ``<remote_base>/runs/<run_id>``; the large SIF, reference and Cell Ranger
    paths remain shared exactly as configured.
    """
    if (not RUN_ID_PATTERN.fullmatch(str(run_id)) or run_id == "LATEST"):
        raise SystemExit(
            "run id must start with a letter or digit, contain only letters, "
            "digits, '.', '_' or '-', and be at most 64 characters")
    if cfg.get("_genoar_remote_run_id") == run_id:
        return dict(cfg)
    scoped = dict(cfg)
    parent = str(cfg["remote_base"]).rstrip("/")
    if not parent:
        raise SystemExit("remote_base must not be empty or '/'")
    scoped["remote_base"] = "%s/runs/%s" % (parent, run_id)
    # Outputs are deliberately shared across attempts: their verified receipts
    # are the cache that lets a retry avoid rerunning Cell Ranger. Only mutable
    # inputs, scripts and logs live under the run-scoped work tree.
    scoped["_genoar_results_dir"] = "%s/results" % parent
    scoped["_genoar_remote_run_id"] = run_id
    return scoped


def bundle_run_id(bundle) -> str:
    """The run id a generated bundle carries, or None when it carries none."""
    path = Path(bundle) / RUN_ID_FILE
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return value or None


def render_config_yaml(cfg: dict, run_id: str = None) -> str:
    """Pipeline config for the remote run (paths match the singularity bind targets)."""
    data = {
        "input_dir": "/work/data/sra",
        "output_dir": "/work/results",
        "ref_path": "/ref",
        "cores": int(cfg["cellranger_threads"]),
        "gzip_threads": 2,
        "cellranger_threads": int(cfg["cellranger_threads"]),
        "cellranger_mem": int(cfg["cellranger_mem"]),
        "cellranger_bin": "/opt/cellranger/cellranger",
        # What the results are meant to be comparable with. Step 0 reads the
        # installed version and reference release and reports a difference;
        # while these keys were absent from the generated config it had nothing
        # to compare against and said nothing, on every handoff run — which is
        # the one path where nobody from here is watching the log.
        # str(): an unquoted `cellranger_version: 9.10` in hpc_config.yaml
        # parses as a float and comes back out as 9.1, which then reads as a
        # different release from the 9.10 the site wrote.
        "cellranger_version": str(cfg.get("cellranger_version",
                                          DEFAULT_CELLRANGER_VERSION)),
        "reference_version": str(cfg.get("reference_version",
                                         DEFAULT_REFERENCE_VERSION)),
        # Retention is a site's decision about its own disk, so it is stated
        # once, in hpc_config.yaml, beside the quota the decision is about.
        # Carried through here because the pipeline reads it from the config it
        # is handed inside the container -- a key a site can only set by
        # editing a generated file is a key a site cannot set.
        # Which Stage 3 the image runs. True is the tree that went through
        # K-BDS and came back complete; false is the one with everything found
        # since, which is better reasoned and has never run on a cluster.
        # Which tree runs. `legacy_pipeline` says it in the word; the older
        # is the older spelling and still works.
        "legacy_pipeline": site_settings.pipeline_tree_of(cfg) == "legacy",
        # The floors below which Cell Ranger's own summary says the output is
        # not a result. A site with unusual data raises or lowers them; 0 is
        # off.
        "min_cells": float(cfg.get("min_cells", 100)),
        "min_valid_barcodes": float(cfg.get("min_valid_barcodes", 0.10)),
        "min_transcriptome": float(cfg.get("min_transcriptome", 0.05)),
        "retain": str(cfg.get("retain", "all")),
        # site_settings.yes_or_no, not bool(): `bool("false")` is True, so a
        # quoted `release_input: "false"` deleted every input it was told to
        # keep.
        "release_input": site_settings.yes_or_no(cfg.get("release_input", False)),
        "download": False,
        "log_dir": "/work/logs",
    }
    if run_id:
        # The job script also exports GENOAR_RUN_ID, which the pipeline prefers.
        # This is the same value, and it survives a container engine that drops
        # the environment.
        data["run_id"] = run_id
    return "# Generated by prepare_hpc_handoff.py\n" + yaml.dump(data, default_flow_style=False, sort_keys=False)


def render_job_script(cfg: dict, run_id: str) -> str:
    """The scheduler job script for this config."""
    return schedulers.render_job_script(cfg, run_id)


def render_transfer(cfg: dict, sra_dir: Path, local_sif: str) -> str:
    host = f"{cfg['user']}@{cfg['host']}"
    base = cfg["remote_base"]
    results = schedulers.results_dir(cfg)
    port = cfg.get("port", 22)
    ssh_key = f" -i {cfg['ssh_key']}" if cfg.get("ssh_key") else ""
    scp = f"scp -P {port}{ssh_key}"
    ssh = f"ssh -p {port}{ssh_key}"
    script = schedulers.job_script_name(cfg)
    remote_run = base
    remote_sra = f"{base}/data/sra"
    transfer_guard = f"{base}/.genoar-transfer-claimed"
    remote_run_id = cfg.get("_genoar_remote_run_id")
    safe_remote_sra = (f"*/runs/{remote_run_id}/data/sra"
                       if remote_run_id else "*/runs/*/data/sra")
    # An array bundle is a job script plus the lists it reads. Uploading the
    # first without the second produced a bundle where every task exited 41
    # looking for a file that had never left the laptop -- and nothing caught
    # it, because the staging block was only ever tested against a tree the
    # test had built by hand.
    up_batches = (f"\n# 4b. Upload the batch lists the array tasks read\n"
                  f"{ssh} \"$HOST\" 'mkdir -p {base}/batches'\n"
                  f"{scp} batches/batch_*.txt \"$HOST:{base}/batches/\""
                  if schedulers.array_size(cfg) else "")
    rsh = f" -e 'ssh -p {port}{ssh_key}'"
    if cfg["transfer_tool"] == "rsync":
        # --delete makes a repeated transfer an exact synchronization. The
        # target is one bundle's run-scoped input tree, never the reusable site
        # root; the shell guard below refuses anything outside that shape.
        up_sra = (f"rsync -av --delete --progress{rsh} '{sra_dir}/' "
                  f"'{host}:{remote_sra}/'")
        reset_sra = ""
        up_sif = f"# rsync -av{rsh} '{local_sif}' '{host}:{cfg['remote_sif']}'"
    else:
        # scp cannot delete entries absent from the source. Replace only the
        # already-guarded run-scoped target before copying, otherwise a second
        # transfer silently merges inputs left by the first.
        reset_sra = (f"{ssh} \"$HOST\" \"rm -rf -- '$REMOTE_SRA' && "
                     f"mkdir -p '$REMOTE_SRA'\"")
        up_sra = f"{scp} -r '{sra_dir}/.' '{host}:{remote_sra}/'"
        up_sif = f"# {scp} '{local_sif}' '{host}:{cfg['remote_sif']}'"

    if schedulers.is_slurm(cfg):
        # A dry submission catches a partition, account, QOS or resource limit
        # the site refuses, here, where the message is still on screen, rather
        # than after the operator has walked away.
        submit = f"""# 6. Ask the scheduler to judge the request without queueing it
{ssh} "$HOST" 'cd {base} && sbatch --test-only {script}' || {{
  echo "sbatch rejected the request before queueing it." >&2
  echo "The partition, walltime, memory, account or QOS is not permitted here." >&2
  exit 2
}}

# 7. Submit
{ssh} "$HOST" 'cd {base} && sbatch {script}'"""
    else:
        submit = f"""# 6. Submit the job
{ssh} "$HOST" 'cd {base} && qsub {script}'"""

    watch = schedulers.watch_command(cfg, cfg["user"])
    return f"""#!/usr/bin/env bash
# Upload Stage 3 inputs to the HPC and submit the job.
set -euo pipefail
HOST="{host}"
REMOTE_RUN="{remote_run}"
REMOTE_SRA="{remote_sra}"
TRANSFER_GUARD="{transfer_guard}"

# Input replacement is deliberately limited to this bundle's run-scoped
# directory. Never broaden this guard: the scp path removes the target because
# scp has no exact-sync operation.
case "$REMOTE_SRA" in
  {safe_remote_sra}) ;;
  *) echo "Refusing to synchronize unsafe SRA target: $REMOTE_SRA" >&2; exit 2 ;;
esac

# 1. Claim this run before changing its inputs. The claim deliberately remains
# after every outcome, including a local network failure: without scheduler
# access this script cannot prove whether a submission happened just before a
# connection broke. Recovery is therefore explicit and fail-closed.
{ssh} "$HOST" "mkdir -p '$REMOTE_RUN'" || {{
  echo "Cannot prepare the remote run directory; no remote input was changed." >&2
  exit 2
}}
if ! {ssh} "$HOST" "mkdir '$TRANSFER_GUARD'"; then
  echo "This run was already transferred/submitted, or its guard cannot be created;" >&2
  echo "no remote input was changed by this invocation." >&2
  echo "If an earlier transfer failed, confirm that no job is queued or running." >&2
  echo "Only then remove $TRANSFER_GUARD on the HPC and retry." >&2
  echo "After a successful submission, generate a new bundle with a new run id." >&2
  exit 2
fi

# 2. Ensure remote working dirs exist
{ssh} "$HOST" 'mkdir -p {base}/data/sra {base}/logs {results}'

# 3. Make this run's remote inputs exactly match the local input directory
{reset_sra}
{up_sra}

# 4. Upload the run files
{scp} config.yaml {script} run_singularity_pipeline.sh "$HOST:{base}/"
{up_batches}

# 5. (one-time) Upload the .sif image, reference genome, and Cell Ranger if not
#    already staged on the HPC. Uncomment/adjust as needed:
{up_sif}
# {scp} -r <local_ref>/.        "$HOST:{cfg['remote_ref']}/"
# {scp} -r <local_cellranger>/. "$HOST:{cfg['remote_cellranger']}/"

{submit}
echo "Submitted. Check with: {ssh} $HOST '{watch}'"
"""


# What each retrieval mode pulls back, as rsync filters. The names match the
# pipeline's `retain` policies, so a site that released on the cluster and a
# site that keeps everything and filters on the way out ask for the same thing
# by the same word.
#
# `runs/` is always included whatever the mode: it holds outcome.json, which is
# the only thing that says what the run counted as complete and why anything
# else did not. A retrieval that brings back results without it brings back
# numbers nobody can check.
RETRIEVE_FILTERS = {
    "all": [],
    "analysis": ["--exclude=possorted_genome_bam.bam",
                 "--exclude=possorted_genome_bam.bam.bai"],
    "summary": ["--include=*/",
                "--include=runs/***",
                "--include=filtered_feature_bc_matrix/***",
                "--include=filtered_feature_bc_matrix.h5",
                "--include=metrics_summary.csv",
                "--include=web_summary.html",
                # The receipt travels too. Without it a retrieved tree cannot
                # be re-verified anywhere else: a released sample would read as
                # output that went missing, which is the confusion retention
                # exists to prevent.
                "--include=.genoar_cellranger.json",
                "--exclude=*"],
}


def render_retrieve(cfg: dict, results_dir: str) -> str:
    host = f"{cfg['user']}@{cfg['host']}"
    base = cfg["remote_base"]
    remote_results = schedulers.results_dir(cfg)
    port = cfg.get("port", 22)
    ssh_key = f" -i {cfg['ssh_key']}" if cfg.get("ssh_key") else ""
    logs_dir = f"{results_dir}/logs"
    default_mode = cfg.get("retrieve", "all")
    if default_mode not in RETRIEVE_FILTERS:
        raise SystemExit("retrieve: %r is not one of %s"
                         % (default_mode, ", ".join(sorted(RETRIEVE_FILTERS))))
    if cfg["transfer_tool"] == "rsync":
        rsh = f" -e 'ssh -p {port}{ssh_key}'"
        cases = "\n".join(
            "    %s) FILTER=(%s) ;;"
            % (mode, " ".join("'%s'" % f for f in filters))
            for mode, filters in sorted(RETRIEVE_FILTERS.items()))
        body = f"""case "$MODE" in
{cases}
    *) echo "unknown mode: $MODE (all, analysis, summary)" >&2; exit 2 ;;
esac
# `${{FILTER[@]}}` with no filters expands to one empty argument under `set -u`
# on bash 3, which rsync reads as a path. Spelling the empty case out avoids
# depending on which bash the site has.
if [ ${{#FILTER[@]}} -eq 0 ]; then
  rsync -av{rsh} '{host}:{remote_results}/' '{results_dir}/'
else
  rsync -av{rsh} "${{FILTER[@]}}" '{host}:{remote_results}/' '{results_dir}/'
fi
rsync -av{rsh} '{host}:{base}/logs/' '{logs_dir}/'
# The plan and the run id, so the retrieved tree can be reconciled on its own.
# Without them corpus_report.py cannot run at all, and a transfer that dropped
# one outcome.json is indistinguishable from a batch that never ran.
rsync -av{rsh} '{host}:{base}/batches/' '{results_dir}/batches/' 2>/dev/null || true"""
    else:
        # scp copies trees whole. Rather than pretend otherwise, it says so.
        body = f"""if [ "$MODE" != "all" ]; then
  echo "scp cannot filter what it copies; set transfer_tool: rsync to use --only." >&2
  exit 2
fi
scp -P {port}{ssh_key} -r '{host}:{remote_results}/.' '{results_dir}/'
scp -P {port}{ssh_key} -r '{host}:{base}/logs/.' '{logs_dir}/'"""
    return f"""#!/usr/bin/env bash
# Pull Stage 3 results (and logs) back from the HPC.
#
#   --only all        everything, BAMs included. Hundreds of GB per hundred
#                     samples, and the BAM is reproducible from the input.
#   --only analysis   everything but the BAMs.
#   --only summary    the filtered matrix and the two summaries -- what the
#                     downstream analysis actually reads.
#
# The run records come back whatever the mode: without outcome.json the results
# are numbers with nothing to say which run produced them or what it missed.
set -euo pipefail
MODE="{default_mode}"
while [ $# -gt 0 ]; do
  case "$1" in
    --only)
      # `$2` under `set -u` is an unbound variable, not an empty one, so a
      # bare --only ended the script on a shell error rather than on a usage
      # message.
      if [ $# -lt 2 ]; then echo "--only needs a mode (all, analysis, summary)" >&2; exit 2; fi
      MODE="$2"; shift 2 ;;
    --only=*) MODE="${{1#*=}}"; shift ;;
    -h|--help) sed -n '/^# Pull Stage 3/,/^set -/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
mkdir -p '{results_dir}' '{logs_dir}'
{body}
echo "Results retrieved to: {results_dir} (mode: $MODE, logs under {logs_dir})"
"""


def render_handoff_md(cfg: dict, sra_dir: Path, results_dir: str, run_id: str) -> str:
    script = schedulers.job_script_name(cfg)
    batches = schedulers.array_size(cfg)
    watch = schedulers.watch_command(cfg, cfg["user"])
    submit_tool = schedulers.submit_tool(cfg)
    retain = str(cfg.get("retain", "all"))
    if schedulers.is_slurm(cfg):
        final = (
            "The queue forgets a finished job. Its final state, elapsed time and\n"
            "peak memory come from the accounting database instead:\n\n"
            "```bash\n"
            f"sacct -j <job id> --format={schedulers.SACCT_FORMAT}\n"
            "```\n\n"
            "`MaxRSS` is the peak resident memory. It is the number to size the\n"
            "next request with, and it is readable from nowhere else."
        )
    else:
        final = (
            "The final state of a finished job comes from:\n\n"
            "```bash\n"
            "qstat -x -f <job id>\n"
            "```\n\n"
            "`Exit_status` is the pipeline's own exit code. `resources_used.mem`\n"
            "and `resources_used.walltime` report peak memory and elapsed time\n"
            "when the site records them."
        )
    if batches:
        if schedulers.is_slurm(cfg):
            concurrency_note = (
                "`max_concurrent_jobs` (%d) caps how many run at the same "
                "time." % schedulers.max_concurrent(cfg))
            rerun_command = "sbatch --array=<n> " + script
            attempt_shape = "<array-job-id>r<restart-count>"
        elif schedulers.scheduler_of(cfg) == "pbspro":
            concurrency_note = (
                "PBS Pro has no portable per-script concurrency cap; this "
                "plan assumes all tasks may run together. Ask the queue "
                "administrator for a site-side limit if needed.")
            rerun_command = "qsub -J <n>-<n> " + script
            attempt_shape = "<numeric-job-id>"
        else:
            concurrency_note = (
                "Torque has no portable per-script concurrency cap; this "
                "plan assumes all tasks may run together. Ask the queue "
                "administrator for a site-side limit if needed.")
            rerun_command = "qsub -t <n> " + script
            attempt_shape = "<numeric-job-id>"
        retry_guard = f"""
Because every array task shares the plan's filesystem quota, its first failed
task creates
`{cfg['remote_base']}/.genoar-array-stop/failure.txt`. Tasks that start later
exit 44 before staging any input. Read that file, fix the cause, and remove any
partial data the plan did not budget for. Only then remove the
`.genoar-array-stop` directory and submit the failed batch again.
"""
        staging_note = """
`transfer_and_submit.sh` uploads every input before the array is submitted, and
they stay for the run unless `release_input` takes them off as their batches
finish. Kept output accumulates across batches, while expanded FASTQ and the
step-5 copy are transient per-batch costs. `plan_batches.py` simulates both and
prints the maximum across the run. A smaller batch reduces the transient terms,
but not the resident inputs or accumulated output.
"""
        corpus_note = f"""
This bundle is an **array of {batches} tasks**, one per batch. `{submit_tool}`
submits all of them at once; the scheduler decides when each runs.
{concurrency_note} Each task links only its own batch's inputs and writes to
its own results root, so the tasks do not share anything they could overwrite.

A task whose batch is missing even one of its inputs refuses to start. A batch
that runs short would still report N/N against its own manifest, so the
shortfall would only appear when the whole corpus is added back together.

A single task can be re-run on its own if it fails:

```bash
{rerun_command}
```
The scheduler gives that submission a **new scheduler job id**. Its pipeline
run id therefore becomes `{run_id}-b<n>-{attempt_shape}` and cannot overwrite
the failed attempt. Run the command from `{cfg['remote_base']}` on the HPC.
{retry_guard}

After retrieval, reconcile every batch against the plan:

```bash
python3 corpus_report.py --plan ./batches --results '{results_dir}' \\
  --run-id "$(cat run_id.txt)"
```
{staging_note}"""
        run_id_note = f"""This bundle carries the run id `{run_id}`, and each
array task adds its batch and scheduler attempt -- for example,
`{run_id}-b1-{attempt_shape}`. A manual resubmission receives a new scheduler
job id, so it writes a new run record. Successful samples are reused as cache
hits; prefer re-submitting only the failed batch numbers."""
        batches_row = f"""
| batches/ | one sample list per array task ({batches} of them), and plan.json |
| plan.json | what the batches were planned from, and against which quota |
| corpus_report.py | run it on the retrieved results to see what the corpus got |"""
    else:
        corpus_note = ""
        run_id_note = ("This bundle carries one run id. Submitting it twice is "
                       "refused by the pipeline\non the second attempt, because a "
                       "run record is never shared and never\noverwritten. "
                       "Generate a new bundle for a new attempt.")
        batches_row = ""

    if retain != "all" or cfg.get("release_input"):
        released = {"analysis": "the BAM and its index",
                    "summary": "everything but the filtered matrix and the two "
                               "summary files"}.get(retain, "nothing")
        retention_note = f"""
### This run deletes as it goes

`retain: {retain}` — once a sample's output has been **verified**, what goes
is {released}, together with the `.sra` and FASTQ copies that sit beside the
output{", and the sample's original input" if cfg.get("release_input") else ""}.
That is what lets a run longer than the quota finish.

Nothing is deleted before the verifier has decided, and nothing that did not
complete is touched. A released sample still counts as analysed: the removal is
recorded in the sample's own receipt first, and a later run reads it back as
`released` rather than as output that went missing.
"""
    else:
        retention_note = ""

    if batches:
        outcome_note = f"""Each array task writes its account to
`{results_dir}/batch_<n>/runs/{run_id}-b<n>-<attempt>/outcome.json` after
retrieval. `<attempt>` is `{attempt_shape}` for this scheduler. These are the
files `corpus_report.py` reconciles; there is no single array-wide
`results/runs/{run_id}/outcome.json`."""
    else:
        outcome_note = f"""The pipeline writes its account of the run to
`{results_dir}/runs/{run_id}/outcome.json` after retrieval."""

    return f"""# Stage 3 HPC handoff

Generated by `prepare_hpc_handoff.py`. Run these on a machine that can reach the HPC.

- HPC: `{cfg['user']}@{cfg['host']}`  (scheduler: {schedulers.scheduler_of(cfg)}, queue/partition: {cfg['queue']})
- Remote working dir: `{cfg['remote_base']}`
- Local SRA input: `{sra_dir}`
- Results will land in: `{results_dir}`
- Run id: `{run_id}`

## One-time staging (large, static)
Upload once and reuse across runs (see commented lines in `transfer_and_submit.sh`):
- `.sif` image to `{cfg['remote_sif']}`
- reference genome to `{cfg['remote_ref']}`
- Cell Ranger to `{cfg['remote_cellranger']}`

## Each run
```bash
bash transfer_and_submit.sh    # upload .sra + run files, then {submit_tool}
# wait for the job ({watch})
bash retrieve_results.sh       # pull results back
```

`transfer_and_submit.sh` is one-shot for this run id. Before changing any SRA
input it creates `{cfg['remote_base']}/.genoar-transfer-claimed`; a second
invocation refuses before touching the remote input tree. If the first command
failed, confirm no job is queued or running before removing exactly that guard
directory and retrying. After a successful submission, generate a new bundle
with a new run id instead.
{corpus_note}
### Bringing back less than everything

```bash
bash retrieve_results.sh --only summary
```

`all` brings the BAMs too — gigabytes per sample. `analysis` leaves them
behind. `summary` brings the filtered matrix and the two summary files, which
is what a downstream analysis reads. The run records come back whatever the
mode; without `outcome.json` the results are numbers with nothing to say what
the run missed. Filtering needs `transfer_tool: rsync`.
{retention_note}
## Reading the result
{final}

{outcome_note}
Each outcome says how many expected samples have verified Cell Ranger output.
It is the evidence of what the analysis achieved. The job's exit status
corroborates it and does not replace it.

{run_id_note}

## Files in this bundle
| File | Purpose |
|------|---------|
| config.yaml | pipeline config (paths match the container bind targets) |
| {script} | job script ({schedulers.scheduler_of(cfg)}); calls run_singularity_pipeline.sh |
| run_singularity_pipeline.sh | runs the pipeline via singularity/apptainer |
| transfer_and_submit.sh | rsync/scp upload + {submit_tool} |
| retrieve_results.sh | rsync/scp download of results |
| {RUN_ID_FILE} | the run id this bundle submits under |{batches_row}

Confirm with the HPC operator: the scheduler, the queue or partition, the
memory and walltime limits, and the `module load` name. Edit hpc_config.yaml and
regenerate if any of them is wrong.
"""


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _has_pipeline_choice(source: dict) -> bool:
    """Whether a config actually states a tree, in any supported spelling."""
    return any(
        key in (source or {}) and source[key] is not None
        and str(source[key]).strip()
        for key in ("legacy_pipeline", "pipeline_mode", "safe_mode"))


def _plan_pipeline_tree(settings: dict, path: Path) -> str:
    """Read an explicit plan choice, accepting old spellings but no guesses."""
    if not _has_pipeline_choice(settings):
        raise SystemExit(
            "%s does not record which Stage 3 pipeline it planned for.\n"
            "  Older plans predate that execution contract, and guessing "
            "would silently choose\n  a code tree. Run plan_batches.py again "
            "with the current tools."
            % path)

    try:
        if settings.get("legacy_pipeline") is not None \
                and str(settings.get("legacy_pipeline")).strip():
            return ("legacy" if site_settings.yes_or_no(
                    settings["legacy_pipeline"]) else "current")
        if settings.get("pipeline_mode") is not None \
                and str(settings.get("pipeline_mode")).strip():
            value = str(settings["pipeline_mode"]).strip().lower()
            if value not in ("verified", "corrected"):
                raise ValueError("must be verified or corrected")
            return "legacy" if value == "verified" else "current"
        return ("legacy" if site_settings.yes_or_no(settings["safe_mode"])
                else "current")
    except (KeyError, ValueError) as exc:
        raise SystemExit(
            "%s has an invalid pipeline choice (%s).\n"
            "  A plan is an execution contract, so an unreadable choice is "
            "not defaulted.\n  Run plan_batches.py again with the current "
            "tools."
            % (path, exc))


def _planned_walltime(settings: dict, path: Path) -> float:
    """Return the plan's wall clock in minutes, refusing pre-contract plans."""
    if "walltime" not in settings:
        raise SystemExit(
            "%s does not record the walltime its batches were sized for.\n"
            "  Run plan_batches.py again with the current tools before "
            "building a bundle." % path)
    try:
        value = float(settings["walltime"])
    except (TypeError, ValueError):
        raise SystemExit(
            "%s records an unreadable walltime. Run plan_batches.py again "
            "with the current tools." % path) from None
    if value <= 0 or value != value or value in (float("inf"), float("-inf")):
        raise SystemExit(
            "%s records an invalid walltime. Run plan_batches.py again with "
            "the current tools." % path)
    return value


def _walltime_hms(minutes: float) -> str:
    seconds = round(minutes * 60)
    hours, remainder = divmod(seconds, 3600)
    mins, secs = divmod(remainder, 60)
    return "%02d:%02d:%02d" % (hours, mins, secs)


def plan_contract(batches_dir, cfg: dict, sra_dir=None) -> dict:
    """The settings the plan's arithmetic assumed, applied to this bundle.

    A plan works out what the disk has to hold from `retain` and
    `release_input`. Building the bundle from a config that says something else
    produces a run whose disk use was never the one anybody checked -- and it
    did, silently, because the bundle only ever copied the batch lists and read
    none of the plan beside them.

    The plan wins where the two disagree, and says so. It is the document the
    quota was agreed against.
    """
    if not batches_dir:
        return cfg
    path = Path(batches_dir) / "plan.json"
    if not path.is_file():
        raise SystemExit(
            "%s has batch lists but no plan.json.\n"
            "  The bundle takes `retain` and `release_input` from the plan, "
            "because the disk\n  the plan checked depends on them. Re-run "
            "plan_batches.py to produce one." % batches_dir)
    with open(path) as fh:
        plan = json.load(fh)
    if not plan.get("plan", {}).get("fits", False):
        raise SystemExit(
            "%s does not fit: it needs %.1f GB at its peak against a quota of "
            "%s GB.\n  Building a bundle from it would submit a corpus already "
            "known to run out of\n  disk. Change `retain`, `release_input`, or "
            "the quota, and plan again."
            % (path, plan["plan"].get("peak_gb", 0.0),
               plan.get("settings", {}).get("disk_gb", "?")))
    # A plan is about particular files, not about a list of names. The same
    # samples re-downloaded ten times larger kept the plan valid and the disk
    # figure stale, so the sizes are checked back against what is actually
    # there.
    sizes = plan.get("sample_bytes") or {}
    if sizes and sra_dir:
        changed = []
        # The transfer script uploads the whole input directory.  Checking only
        # the samples already named by the plan lets an SRA added afterwards
        # consume resident quota without appearing in the arithmetic.  Compare
        # the complete logical sample set, while treating ``SRR1/`` and
        # ``SRR1.sra`` as the same sample just as the planner does.
        actual_names = set()
        for entry in Path(sra_dir).iterdir():
            if entry.is_dir():
                actual_names.add(entry.name)
            elif entry.is_file() and entry.suffix.lower() == ".sra":
                actual_names.add(entry.stem)
        planned_names = set(sizes)
        added = sorted(actual_names - planned_names)
        removed = sorted(planned_names - actual_names)
        if added:
            changed.append("unplanned sample(s) were added: %s"
                           % ", ".join(added[:6]))
        if removed:
            changed.append("planned sample(s) are missing: %s"
                           % ", ".join(removed[:6]))
        for name, planned in sorted(sizes.items()):
            entry = Path(sra_dir) / name
            if entry.is_dir():
                actual = sum(f.stat().st_size for f in entry.rglob("*")
                             if f.is_file())
            elif (Path(sra_dir) / (name + ".sra")).is_file():
                actual = (Path(sra_dir) / (name + ".sra")).stat().st_size
            else:
                changed.append("%s is not in %s any more" % (name, sra_dir))
                continue
            if actual != planned:
                changed.append("%s is %d bytes, the plan says %d"
                               % (name, actual, planned))
        if changed:
            raise SystemExit(
                "the inputs are not the ones %s was planned for:\n  %s%s\n"
                "  The disk figure the quota was agreed against was worked out "
                "from the old\n  files. Run plan_batches.py again."
                % (path, "\n  ".join(changed[:6]),
                   "\n  ..." if len(changed) > 6 else ""))
    settings = plan.get("settings", {})
    plan_tree = _plan_pipeline_tree(settings, path)
    if (_has_pipeline_choice(cfg)
            and site_settings.pipeline_tree_of(cfg) != plan_tree):
        raise SystemExit(
            "plan.json sets legacy_pipeline=%s, but hpc_config.yaml explicitly "
            "selects the %s pipeline.\n  The plan is the execution contract; "
            "silently replacing either choice could run a\n  different tree "
            "from the one whose disk use was planned. Use the same setting "
            "and plan again."
            % (str(plan_tree == "legacy").lower(),
               site_settings.pipeline_tree_of(cfg)))

    planned_minutes = _planned_walltime(settings, path)
    try:
        configured_minutes = site_settings.walltime(cfg.get("walltime"))
    except (TypeError, ValueError) as exc:
        raise SystemExit("hpc_config.yaml has an invalid walltime (%s)" % exc)
    if abs(planned_minutes - configured_minutes) > (1.0 / 120.0):
        raise SystemExit(
            "plan.json sized its batches for walltime %s, but "
            "hpc_config.yaml now requests %s.\n  A shorter job can time out "
            "before finishing a planned batch; a longer one is a\n  different "
            "queue contract. Use the same walltime and plan again."
            % (_walltime_hms(planned_minutes), cfg.get("walltime")))
    merged = dict(cfg)
    for key in ("retain", "release_input", "max_concurrent_jobs"):
        if key in settings and str(cfg.get(key, settings[key])) != str(settings[key]):
            sys.stderr.write("plan.json sets %s=%s; using that over the "
                             "config's %s.\n"
                             % (key, settings[key], cfg.get(key)))
        if key in settings:
            merged[key] = settings[key]
    merged["legacy_pipeline"] = plan_tree == "legacy"
    merged["plan_id"] = plan.get("plan_id")

    # The peak was worked out for a number of tasks running together. A
    # scheduler that cannot be told that number will run all of them, and the
    # arithmetic the quota was agreed against does not hold.
    # Retention, batching and the array all live in the corrected tree. A
    # bundle that runs the frozen one ignores every one of them, so the disk
    # figure the quota was agreed against describes a run that will not happen.
    # From the plan's own settings, by the same rule as everywhere else: a
    # plan written with `safe_mode: false` is asking for the corrected tree,
    # and reading only `pipeline_mode` refused it.
    if plan_tree == "legacy":
        raise SystemExit(
            "this plan needs the current pipeline and the bundle would run the "
            "earlier one.\n  Retention and the array are not in the earlier "
            "tree, so the run would ignore them\n  and use a different amount "
            "of disk than the plan checked.\n"
            "  Remove `legacy_pipeline` from hpc_config.yaml, and plan again "
            "if you meant\n  something else.")

    assumed = int(settings.get("max_concurrent_jobs") or 1)
    lists = len(sorted(Path(batches_dir).glob("batch_*.txt")))
    if not schedulers.can_throttle_array(merged) and assumed < lists:
        raise SystemExit(
            "the plan assumes at most %d array task(s) run at once, and %s "
            "cannot cap that\n  from the job script -- every one of the %d "
            "tasks may run together, needing that\n  many times the disk. Plan "
            "again with `max_concurrent_jobs: %d`, or use a scheduler\n  that "
            "can throttle, or have the site limit the queue and say so."
            % (assumed, schedulers.scheduler_of(merged), lists, lists))
    return merged


def copy_batches(batches_dir, out: Path) -> int:
    """Carry a plan's batch lists into the bundle. Returns how many there are.

    plan_batches.py writes one file per batch; the job script reads the one
    named for its array index. Copying them in is what turns a bundle for one
    job into a bundle for a corpus, and the count is what the array directive
    is sized from -- so an operator cannot end up submitting an array wider
    than the lists that were shipped.
    """
    if not batches_dir:
        return 0
    source = Path(batches_dir)
    lists = sorted(source.glob("batch_*.txt"))
    if not lists:
        raise SystemExit("no batch_*.txt under %s; run plan_batches.py first"
                         % source)
    expected = ["batch_%03d.txt" % n for n in range(1, len(lists) + 1)]
    if [p.name for p in lists] != expected:
        raise SystemExit(
            "the batch lists under %s are not numbered 1..%d without gaps "
            "(found %s). An array task whose list is missing has nothing to "
            "run, and finds that out after it is queued."
            % (source, len(lists), ", ".join(p.name for p in lists)))
    target = out / "batches"
    target.mkdir(parents=True, exist_ok=True)
    for one in lists:
        shutil.copy2(one, target / one.name)
    return len(lists)


def generate_bundle(cfg: dict, sra_dir, out_dir, results_dir: str = "stage3_results",
                    local_sif: str = "genoar-srr_step9.sif", run_id: str = None,
                    batches_dir=None) -> Path:
    # batches_dir last, and only ever passed by name: run_hpc_stage3.py calls
    # this positionally through local_sif, and a parameter inserted ahead of
    # that silently hands it a path where it expects an image name.
    """Write the handoff bundle files and return the output directory."""
    if not RUN_SCRIPT_SRC.exists():
        raise SystemExit(f"run_singularity_pipeline.sh not found at {RUN_SCRIPT_SRC}")
    sra_dir = Path(sra_dir).resolve()
    run_id = run_id or new_run_id()
    cfg = run_scoped_config(cfg, run_id)
    try:
        schedulers.validate(cfg)
    except ValueError as e:
        raise SystemExit(str(e))

    # Validate the plan before creating the output directory. A walltime or
    # input mismatch is an invalid execution contract, not a partial bundle an
    # operator should have to distinguish from a usable one.
    if batches_dir:
        cfg = plan_contract(batches_dir, cfg, sra_dir)
        try:
            schedulers.validate(cfg)
        except ValueError as e:
            raise SystemExit(str(e))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Before anything is rendered: the array directive is sized from the number
    # of lists that actually travelled.
    count = copy_batches(batches_dir, out)
    if count:
        cfg = dict(cfg, array_count=count)
        # Both places: `--plan bundle/batches` is the natural thing to type,
        # and without plan.json beside the lists it silently loses the
        # sample-list cross-check and the plan id.
        shutil.copy2(Path(batches_dir) / "plan.json", out / "plan.json")
        shutil.copy2(Path(batches_dir) / "plan.json", out / "batches" / "plan.json")
        # The only thing that can see a batch nobody submitted. It is no use
        # to us on this side of the transfer.
        shutil.copy2(HERE / "corpus_report.py", out / "corpus_report.py")
    (out / "config.yaml").write_text(render_config_yaml(cfg, run_id))
    _write_exec(out / schedulers.job_script_name(cfg), render_job_script(cfg, run_id))
    _write_exec(out / "transfer_and_submit.sh", render_transfer(cfg, sra_dir, local_sif))
    _write_exec(out / "retrieve_results.sh", render_retrieve(cfg, results_dir))
    (out / RUN_ID_FILE).write_text(run_id + "\n")
    (out / "HANDOFF.md").write_text(render_handoff_md(cfg, sra_dir, results_dir, run_id))
    shutil.copy2(RUN_SCRIPT_SRC, out / "run_singularity_pipeline.sh")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate an HPC handoff bundle for Stage 3")
    ap.add_argument("--sra-dir", type=Path, required=True, help="Local Stage 3a output (.sra files)")
    ap.add_argument("--batches", type=Path,
                    help="a plan_batches.py output directory; makes this an "
                         "array bundle, one task per batch_*.txt")
    ap.add_argument("--hpc-config", type=Path, required=True, help="HPC settings YAML (see hpc_config.example.yaml)")
    ap.add_argument("--out", type=Path, default=Path("hpc_handoff"), help="Output bundle directory (default: hpc_handoff)")
    ap.add_argument("--results-dir", type=str, default="stage3_results", help="Local dir to retrieve results into (default: stage3_results)")
    ap.add_argument("--local-sif", type=str, default="genoar-srr_step9.sif", help="Local .sif path (for the one-time upload hint)")
    ap.add_argument("--run-id", type=str, default=None, help="Run id to submit under (default: generated)")
    args = ap.parse_args()

    cfg = load_config(args.hpc_config)
    out = generate_bundle(cfg, args.sra_dir, args.out, args.results_dir,
                          args.local_sif,
                          run_id=args.run_id, batches_dir=args.batches)

    print(f"[handoff] bundle written to: {out}/")
    for p in sorted(out.iterdir()):
        print(f"  - {p.name}")
    print(f"[handoff] scheduler: {schedulers.scheduler_of(cfg)}; "
          f"job script: {schedulers.job_script_name(cfg)}")
    print(f"[handoff] run id: {bundle_run_id(out)}")
    print(f"[handoff] next: bash {out}/transfer_and_submit.sh")


if __name__ == "__main__":
    main()
