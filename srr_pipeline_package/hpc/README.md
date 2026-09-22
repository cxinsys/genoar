# Stage 3 HPC handoff (transfer, submit, retrieve)

> Setting up on an unfamiliar cluster, or preparing a run that someone else
> will start? **[SITE_SETUP.md](SITE_SETUP.md)** gathers the values that have
> to be supplied, where to find each one, and the procedure for when the run
> cannot be driven over SSH. It also names the failures that happen before
> Cell Ranger starts — `unsquashfs`, bind destinations, image architecture —
> and what each one actually means.

When Stage 3 (Cell Ranger) runs on a separate HPC instead of the local machine,
the single-host data flow no longer connects the stages: the `.sra` inputs must
be uploaded, a job submitted, and results pulled back. This directory produces a
transfer-ready handoff for that, and can also run it automatically.

Two modes:

- Semi-auto: generate the files and commands; you run them (works without HPC
  access, and can be inspected offline).
- Full-auto (`run_hpc_stage3.py --execute`): also runs upload, submit, poll, and
  retrieve over SSH. If SSH or the scheduler is unavailable, it uses the
  generated bundle instead.

## Schedulers

`scheduler` in `hpc_config.yaml` chooses the dialect. Slurm is the default.

| Value | Directives | Submit | Poll | Final state |
|-------|-----------|--------|------|-------------|
| `slurm` (default) | `#SBATCH` | `sbatch --test-only`, then `sbatch` | `squeue` | `sacct` |
| `pbspro` | `#PBS -l select=...` | `qsub` | `qstat` | `qstat -x -f` |
| `torque` | `#PBS -l nodes=...:ppn=...` | `qsub` | `qstat` | `qstat -x -f` |

Slurm is the default for two reasons. The only cluster this handoff targets
(K-BDS) runs Slurm. And the Slurm path reproduces a bundle that has run on that
cluster, while the PBS path has only ever been exercised against a mock. A
config that names `pbspro` or `torque` still gets the PBS bundle it always got.

All three dialects are rendered by `schedulers.py`, which the generator and the
automatic runner both read. The script a bundle carries and the commands the
runner sends come from one description of the cluster.

### What the Slurm path reproduces

The `#SBATCH` block, the submission sequence and the `sacct --format` fields are
those of the one-sample K-BDS pilot bundle. A config that names the pilot's
settings generates the pilot's `#SBATCH` block byte for byte.
`cycle_test/tests/test_hpc_scheduler_support.py` holds a copy of that block and
asserts the correspondence, so a change to either side fails a test.

Three Slurm facts shape the generated script.

Slurm reads `#SBATCH` lines before any shell runs. A variable written there is
never expanded, so every directive carries a concrete value resolved at
generation time.

Slurm's directive parser splits on whitespace. A `remote_base` holding a space
truncates `--output` and `--error` silently, so the generator refuses one. The
configured path is a reusable workspace parent; each generated bundle works
under `remote_base/runs/<run-id>/`, keeping consecutive uploads disjoint.
Verified outputs stay in `remote_base/results/`, where their receipts can be
reused deliberately by a later run.

Slurm records the final state, the elapsed time and the peak memory in its
accounting database, after the job leaves the queue. `squeue` forgets a finished
job. `sacct` is the only place the peak memory of a finished job is written.

## Prerequisites

- Stage 3a already produced `.sra` files locally (e.g. via
  `cycle_test/run_cycle_test.py ... --run-stage3 --download-only`).
- The `.sif` image, reference genome, and Cell Ranger are staged on the HPC once.
  See [../singularity/README.md](../singularity/README.md).

## Usage (semi-auto)

```bash
# 1. Configure
cp srr_pipeline_package/hpc/hpc_config.example.yaml hpc_config.yaml
$EDITOR hpc_config.yaml        # host, user, port, remote paths, scheduler, partition, resources

# 2. Generate the handoff bundle
python3 srr_pipeline_package/hpc/prepare_hpc_handoff.py \
  --sra-dir path/to/stage3_sra \
  --hpc-config hpc_config.yaml \
  --out hpc_handoff

# 3. Run the generated commands (from a machine that can reach the HPC)
bash hpc_handoff/transfer_and_submit.sh   # upload .sra + run files, then sbatch
# wait for the job (squeue -u <user>)
bash hpc_handoff/retrieve_results.sh      # pull results back
```

`HANDOFF.md` inside the bundle names the commands for the scheduler you
configured.

## Usage (full-auto)

`run_hpc_stage3.py` always writes the bundle and, with `--execute`, runs it
end-to-end over SSH: upload, submit, poll, retrieve.

```bash
# generate bundle only (same as prepare_hpc_handoff.py)
python3 srr_pipeline_package/hpc/run_hpc_stage3.py \
  --sra-dir path/to/stage3_sra --hpc-config hpc_config.yaml

# full auto: upload, submit, poll, retrieve
python3 srr_pipeline_package/hpc/run_hpc_stage3.py \
  --sra-dir path/to/stage3_sra --hpc-config hpc_config.yaml \
  --execute --poll-interval 60 --max-hours 24 --json result.json
```

`--execute` needs working SSH (key or agent) to the HPC. It uses `BatchMode`
(no password prompts); without it, it falls back to the manual bundle. The
fallback leaves the bundle ready to run by hand and analyses nothing, so under
`--execute` it exits `1`. See [What the runner reports](#what-the-runner-reports).

## What the runner reports

`success` reports the analysis. It is true when this run's own record says every
expected sample has verified Cell Ranger output. A job the scheduler accepted has
produced nothing yet, so submission is reported on its own field, `submitted`.

`--json PATH` writes the whole result there as JSON. The same facts are printed
to stdout on every run.

| Field | Meaning |
|-------|---------|
| `success` | The analysis is evidenced as complete. True only when `analysis_status` is `success` |
| `submitted` | The scheduler accepted the job |
| `job_id` | The job id the scheduler returned |
| `job_exit` | The finished job's exit status, from `sacct` or `qstat -x -f`. `null` when the scheduler no longer holds it |
| `analysis_status` | What the run achieved, from the evidence the wrapper could read |
| `analysis_message` | That verdict in a sentence, naming what it was read from |
| `run_id` | The run whose record the wrapper read |
| `run_record` | Path to that record, `<results-dir>/runs/<run_id>/outcome.json` |
| `run_record_problem` | Why no record was read, when none was |
| `counts` | The `counts` block from that record: `expected`, `completed`, `adopted`, and the per-state tallies |
| `mode` | `auto` when the job ran over SSH, `manual` when the bundle is all that was produced |
| `bundle_dir` | Where the handoff bundle was written |
| `error` | The failure that stopped the wrapper, when one did |

### `analysis_status` and exit codes

Under `--execute` the runner was asked for an analysis, so it exits on the
pipeline's own contract. `0` did the work, `1` failed, `2` misconfigured, `3`
valid and nothing to do, `4` partial.

| `analysis_status` | Meaning | Exit under `--execute` |
|-------------------|---------|------------------------|
| `success` | Every expected sample has verified Cell Ranger output | `0` |
| `partial` | Some expected samples have verified output and others do not | `4` |
| `nothing_processed` | The run was complete and valid and analysed no expected sample | `3` |
| `config_error` | Cell Ranger or its reference is unusable, so the run could not have analysed anything | `2` |
| `failed` | The run broke, or its counts contradict each other | `1` |
| `unknown` | The wrapper cannot say what the run achieved | `1` |
| `not_attempted` | No job was run at all | `1` |

Without `--execute` the runner was asked for a bundle, so writing the bundle is
the whole job. It exits `0` when the bundle is written and `1` when it is not.
`analysis_status` stays `not_attempted` on that path, because the analysis
happens on the cluster afterwards.

`unknown` is neither a success nor a failure. The runner says so, and it exits
`1` so that a caller never reads it as done. The analysis may still have
completed on the cluster. Read the run record there to find out:
`results/runs/<run_id>/outcome.json`.

### What the verdict is read from

Two sources of evidence speak about a finished job.

The run record. The pipeline writes `results/runs/<run_id>/outcome.json` for
every run, and retrieval brings it back with the rest of the results. It counts
the samples the run expected and the samples it verified output for, so it is the
fuller account and it decides. Each bundle names its run, so the runner reads
this run's record by name. See [Run identity](#run-identity). A bundle generated
before run ids carries none. For that case the runner falls back to elimination:
it lists the run ids present before submitting and takes the one directory that
was not there before. The results volume is persistent and holds records from
earlier runs, which is why that listing is taken first.

The scheduler's account of the job. The runner reads it with `sacct` under Slurm
and `qstat -x -f` under PBS, after the job finishes. A non-zero status carries the
pipeline's own verdict, `2` for `config_error`, `3` for `nothing_processed`, `4`
for `partial`, anything else `failed`. Exit `0` is ignored on purpose. An image
built to a steps 1 to 7 target contains no Cell Ranger stage and also exits `0`
after analysing nothing, so a zero exit on its own is no evidence that the
analysis happened. A recorded state can override an exit code in the other
direction. Slurm records `OUT_OF_MEMORY` with an `ExitCode` of `0:125`, and that
job failed.

Where both sources speak, the weaker claim wins. The two describe one run, and a
disagreement never produces the stronger verdict. `analysis_message` prints both
readings when they differ.

Neither source is guaranteed. Retrieval can fail, an older image writes no
record, and a scheduler forgets a finished job. `run_record_problem` says why no
record was read. A run the wrapper cannot describe from either source is reported
`unknown`.

Under Slurm the runner asks `sbatch --test-only` first. A partition the account
cannot use, a walltime over the site limit and a memory request no node can
satisfy are all refused there, in a second, before the upload has any value. The
run reports `submitted: no` and exits 1.

## Elapsed time and peak memory

Sizing the real service needs the numbers a job leaves behind, so the wrapper
reads and prints them.

Under Slurm they come from
`sacct -j <id> --format=JobID,State,ExitCode,Elapsed,MaxRSS,ReqMem`, after the
job has left the queue. `MaxRSS` is the peak resident memory and is recorded per
step, so the wrapper takes the largest across the steps. Under PBS they come
from `resources_used.mem` and `resources_used.walltime` in `qstat -x -f`.

They arrive in `elapsed`, `max_rss`, `max_rss_kib` and `req_mem`, and on the
`[hpc] resources:` line.

Neither scheduler is obliged to answer. `sacct` needs an accounting database the
site may not run, and it forgets a job once the row is purged. `qstat` forgets a
finished job sooner. When a figure is missing the wrapper names it and prints
the command to ask again. It reports the analysis regardless, because the run
record is a separate source.

The job also writes `logs/job_status.txt` on its way out, from an exit trap
armed before anything can fail. It holds the run id, the job id, the stage the
job reached, and the pipeline's exit code. `retrieve_results.sh` brings it back.
A job the kernel kills outright leaves no such file, because `SIGKILL` reaches no
trap. That is the case `sacct` answers.

## Run identity

Each bundle names its run. The id goes into `run_id.txt`, into the generated
`config.yaml`, and into the job script, which exports it as
`SINGULARITYENV_GENOAR_RUN_ID` and `APPTAINERENV_GENOAR_RUN_ID`. Both container
engines read those prefixes, so `GENOAR_RUN_ID` reaches the pipeline inside the
container without a change to `run_singularity_pipeline.sh`.

The pipeline records what it produced under `results/runs/<run_id>/`, so the
wrapper reads this run's record by name. It used to identify the record by
elimination, by looking for the run directory that was not there before.

The pipeline refuses an id that already has a run record. Submitting one bundle
twice therefore fails on the second attempt instead of folding two attempts into
one set of records. Generate a new bundle for a new attempt.

An array bundle carries one id and each task appends its batch number to it —
`<run id>-b1`, `<run id>-b2`. That is not cosmetic. Seventy tasks sharing an id
would each refuse the record the others had written, so the array would report
one run's work and lose sixty-nine. The same rule then applies per task: a task
that has completed cannot be resubmitted from the same bundle, which is what
keeps a re-run of one failed task from overwriting what its neighbours
recorded.

## Via cycle_test

`cycle_test` can generate (and optionally run) the handoff after downloading
`.sra`, instead of running Cell Ranger locally:

```bash
# semi-auto: write the bundle to <output>/cycle_*/stage3/hpc_handoff/
python cycle_test/run_cycle_test.py --cycles 1 --pages-per-cycle 5 \
  --stage3-hpc hpc_config.yaml

# full-auto (falls back to the bundle if SSH or the scheduler is unavailable)
python cycle_test/run_cycle_test.py --run-stage3 \
  --stage3-hpc hpc_config.yaml --stage3-hpc-execute
```

`cycle_test` passes `--json` and reads the result back from
`<output>/cycle_*/stage3/hpc_handoff/hpc_result.json`, so
`stages.stage3_handoff` in `summary.json` carries the fields above verbatim. The
cycle status comes from the exit code. With `--stage3-hpc-execute`, `0` is
`success`, `3` is `no_data`, `4` is `partial_success`, and `1` and `2` are
`failed_stage3`. Without it the cycle was asked for a bundle, so `0` is
`success`. See
[Cycle status and exit codes](../../cycle_test/README.md#cycle-status-and-exit-codes).

## What gets generated (`hpc_handoff/`)

| File | Purpose |
|------|---------|
| `config.yaml` | Pipeline config; paths match the container bind targets (`/ref`, `/opt/cellranger/cellranger`), and it carries the run id |
| `run_stage3.slurm` or `run_stage3.pbs` | Job script filled from `hpc_config.yaml` |
| `run_singularity_pipeline.sh` | Copied from `../singularity/`; runs the pipeline via Singularity/Apptainer |
| `transfer_and_submit.sh` | `rsync`/`scp` upload and the submit command |
| `retrieve_results.sh` | `rsync`/`scp` download of results and logs |
| `run_id.txt` | The run id this bundle submits under |
| `batches/` | One sample list per array task, when the bundle was built `--batches` |
| `HANDOFF.md` | Step-by-step, tailored to your settings |

The job script is the same on both paths. It arms an exit trap before anything
can fail, checks that the compute node sees the image, the reference, Cell Ranger
and the inputs at the paths the submitting node saw, hands the run id to the
container, and ends on the pipeline's own exit code. It uses `set -uo pipefail`
rather than `set -euo pipefail`, because a body that reports what happened has to
survive it. Its own failures exit 41 and 42, which sit outside the pipeline's
contract, so a missing bind path never reads as a partial analysis.

## Config notes

- `scheduler`: `slurm` (default), `pbspro`, or `torque`.
- `queue`: the Slurm partition, or the PBS queue.
- `mem_gb`: memory request in GB. `mem_mb` overrides it where the site quoted MB.
- `account`, `qos`: Slurm only, and only where the site requires them.
- `transfer_tool`: `rsync` (resumable, recommended) or `scp`.
- `port`: SSH port, for sites that use a non-standard one (e.g. `10000`).
- `email`: optional job notifications (`--mail-type` under Slurm, `-m abe` under PBS).
- One-time uploads (`.sif`, reference, Cell Ranger) appear as commented lines in
  `transfer_and_submit.sh`; uncomment them for the first run.
- `retain`, `release_input`: what a completed sample keeps, and whether its
  input goes with it. Carried into the generated `config.yaml`, so they are set
  here rather than in the file the bundle ships. See
  [Running a corpus](#running-a-corpus).
- `retrieve`: what `retrieve_results.sh` pulls by default (`all`, `analysis`,
  `summary`), overridable per run with `--only`.
- `max_concurrent_jobs`: array tasks that may run at once. Defaults to 1.

`hpc_config.yaml` is the only file to edit. `config.yaml` is generated from it
on every bundle, so a value changed there is a value lost at the next
regeneration — which is why nothing here asks anyone to edit it.

### What a cache hit is allowed to be

A `cache_hit` requires the same input **and** the same environment. The receipt
records the Cell Ranger version and reference release that produced the output,
step 0 records what this run has, and a difference is reported `stale` instead
of reused. Without that, a 7.0.1/2020-A result satisfied an 8.0.1/2024-A run on
the strength of the input fingerprint alone — the input really was the same;
the answer was not. Output written before this existed has nothing recorded and
is still reused, with the reason saying the comparison could not be made.

## Which pipeline runs

The image carries two Stage 3 pipelines. The maintained/current tree is the
default (`legacy_pipeline: false`). The earlier, cluster-validated tree remains
frozen as a comparison baseline and can be selected explicitly with
`legacy_pipeline: true`. Existing `pipeline_mode: verified|corrected` and
`safe_mode: true|false` configurations remain compatible.

Running the same input under each, into separate results directories, and
comparing the run records shows which samples the corrections actually change.
A fixture can only show that a fix does what it says; it cannot show that the
thing it fixes happens to your data.

## Running a corpus

One job holds what the disk holds and runs for as long as the queue allows. A
corpus is neither, so it is split into batches and submitted as one array.

```bash
# 1. Work out how many samples fit in one job
python3 plan_batches.py --input-dir ./sra --config hpc_config.yaml --out ./plan

# 2. Build an array bundle from the plan
python3 prepare_hpc_handoff.py --hpc-config ./hpc_config.yaml \\
  --sra-dir ./sra --batches ./plan --out ./bundle
```

`plan_batches.py` measures the inputs, takes everything else from
`hpc_config.yaml`, asks about whatever is left, and — with nobody to ask —
stops and names the key to write rather than guessing. `--review` asks about
every value with what it found already filled in; `--non-interactive` never
asks.

`minutes_per_gb` has no cross-site default. Set it from a representative job
on the target cluster (`elapsed minutes / total input .sra GiB`). If the site
has no completed measurement yet, supply an explicit conservative value that
produces smaller batches, then replace it after the first measured run. An
interactive run asks for it; a non-interactive run refuses to plan without it.

What the planner reports is which of the two ceilings bound the batch:

```
4 jobs of up to 163 samples. The wall clock decides that, and only that.
  ...
    9165.6 GB  peak, against a 2000 GB quota
IT DOES NOT FIT. Short by 7165.6 GB.
```

Each array task links only its own batch's inputs and takes its own run id,
results root and log directory. The pipeline refuses a run id that already
names a run, so tasks sharing one would invalidate each other; nothing they
write is shared.

The inputs are uploaded before the array is submitted and stay for the run
unless `release_input` takes them off as their batches finish. Output kept by
each completed batch accumulates, while expanded FASTQ and the copy made during
step 5 are transient per-batch costs. `plan_batches.py` simulates every point
and reports the maximum. Smaller batches reduce the transient terms, but not
resident inputs or accumulated output. A plan that does not fit exits non-zero,
and the bundle generator refuses to build from it.

A task whose batch is missing even one input refuses to start: a short batch
still reports N/N against its own manifest. Each task's run id carries its
batch number and the scheduler's job id, so a failed task can be re-submitted
without colliding with the record its first attempt left.

Batches only help if finished samples stop occupying the disk, which is
`retain` — the outputs, and the reads step 5 copied in beside them. Retention runs after the verifier and only over samples it credited
as completed, and it records the removal in each sample's receipt **before**
removing anything — so a released sample still verifies as analysed, while a
BAM that merely went missing is still reported missing. A check that deleting
everything can satisfy is not a check.

When the results are back, reconcile them against the plan:

```bash
python3 corpus_report.py --plan ./plan --results ./stage3_results \
  --run-id "$(cat bundle/run_id.txt)"
```

Each task reports against its own manifest, so a corpus can lose samples
between seventy clean reports — a batch nobody submitted, a task killed before
it wrote anything. This separates what completed from what a run refused and
from what no run mentions at all, which is the case no single outcome can show.

Full detail, including how to find each value on an unfamiliar cluster, is in
[SITE_SETUP.md](SITE_SETUP.md#running-more-than-fits).

## Results and failures

Retrieval brings back both `results/` and the step logs (`/work/logs` to
`<results-dir>/logs/`, e.g. `step8_cellranger.log`), so failures can be reviewed
without SSHing to the HPC. Under Slurm the scheduler's own `slurm-<id>.out` and
`slurm-<id>.err` land in that same log directory and come back with it.

If Cell Ranger fails, the pipeline records the failed samples in
`results/reports/step8_failed.txt`, keeps the successful samples' outputs, and
prepares a retry set in `results/retry_failed/`. The runner still retrieves those
partial results. It then grades the run from the retrieved record and the
scheduler's account of the job, and reports the verdict as `analysis_status`, with the paths to
check printed beside it. See
[What the runner reports](#what-the-runner-reports).

The per-sample detail sits in the run record,
`<results-dir>/runs/<run_id>/outcome.json`. It names every expected sample, the
state the run left it in, and the reason. Samples adopted with
`GENOAR_ADOPT_PRIOR_RESULTS=1` are reported there separately and count towards
none of the verdicts.

## Status

No cluster is available to this repository, and nothing here requires one to be
tested. The tests put stand-ins for `ssh`, `scp`, `rsync`, `sbatch`, `squeue`,
`sacct`, `qsub` and `qstat` on `PATH` and run the wrapper end to end against
them, so the commands under test are the commands the wrapper builds.

The Slurm job script, submission sequence and `sacct` handling reproduce a bundle
that has run on K-BDS. The SSH driver around them has not run against a real
cluster, and neither has the PBS path. The fallback to the manual bundle applies
in the meantime.

## Items to confirm with the HPC

The scheduler and its dialect, the queue or partition name, whether the site
requires an account or a QOS, the node memory and walltime limits, the
Singularity/Apptainer version and `module load` name, the writable `--bind` paths
and scratch quota, and the data upload method.

For anything larger than a handful of samples, four more, because they decide
the shape of the run rather than its settings:

- **The quota, as a number.** It does not set the batch size — the wall clock
  does — but it decides whether the corpus fits at all, and `plan_batches.py`
  refuses to plan one that does not.
- **How many array tasks may run at once.** A site that allows several is a
  site where the corpus finishes sooner — and one that needs several times the
  disk, since each task holds its own batch.
- **How results get out**, and how large a transfer the site expects to serve.
  `retain` and `--only summary` exist to make that number smaller.
- **How long output is kept** after a job finishes. It decides whether "leave
  the BAMs on the cluster" is storage or deletion.
