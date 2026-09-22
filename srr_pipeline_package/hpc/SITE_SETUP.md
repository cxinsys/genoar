# Running Stage 3 on a cluster

Stage 3 runs Cell Ranger inside a container, submitted to a batch scheduler.
Most of what it needs it works out for itself. This is the rest: the values
that cannot be known from outside a cluster, where to find each one, and what
goes wrong when it is wrong.

## Two ways to run it

The difference is whether the person analysing the data can log in to the
cluster.

**Driven remotely.** You have an account and can open an SSH session
unattended. [`prepare_hpc_handoff.py`](prepare_hpc_handoff.py) generates
scripts that upload, submit and retrieve; [`README.md`](README.md) covers
that path in full.

**Handed over.** Whoever holds the account is not the person who prepared the
run — a shared facility, a collaborator's allocation, a machine that asks for
a one-time password on every login or that routes bulk data through a separate
transfer node. Nothing can drive it from outside, so the run travels as files
and someone at the cluster starts it. See [Handing it over](#handing-it-over).

Either way the values below have to be filled in first.

If something fails before Cell Ranger even starts, skip to
[When it fails before the pipeline starts](#when-it-fails-before-the-pipeline-starts).
Those failures look like a broken pipeline and are not.

## How it fits together

One file is edited by hand. Everything else is generated from it.

```
hpc_config.yaml                    <- the only file a site edits
  |
  |-- plan_batches.py              -> plan/batch_001.txt, batch_002.txt, ...
  |     how many samples fit in one job, and whether the disk or the
  |     clock is what stops it being more
  |
  '-- prepare_hpc_handoff.py --batches plan/
        |
        '-> bundle/
              run_stage3.slurm     scheduler directives, the array, and the
                                   staging that gives each task its own batch
              config.yaml          the pipeline's own config, GENERATED
              batches/             one sample list per array task
              run_singularity_pipeline.sh
              retrieve_results.sh
              HANDOFF.md

  operator:  sbatch run_stage3.slurm
        |
        '-> array task N
              links batch N's inputs, takes its own run id and results root
              |
              '-> the container: run_docker_pipeline.sh
                    step 0   configuration and version pre-flight
                    steps 1-7  organise, fastq-dump, gzip, rename
                    step 8   Cell Ranger, then verify -> outcome.json
                    step 9   retention: release what `retain` does not keep
        |
        '-> ./retrieve_results.sh --only summary
```

**Two pipelines, one entry point.** The container starts a small dispatcher
that reads the primary `legacy_pipeline` setting (while accepting the older
`pipeline_mode` and `safe_mode` aliases) and hands the run to whichever pipeline
was asked for, with the arguments and environment exactly as they arrived.
Nothing else about the run changes with the switch.

**`config.yaml` is generated, not written.** It holds the paths as seen from
inside the container, which are the same at every site, plus the values carried
through from `hpc_config.yaml`. Editing it by hand changes one run and is
overwritten by the next bundle, which is why nothing here ever asks an operator
to.

Two things are worth knowing about the shape of a run:

- **The verifier decides, then retention acts.** Step 8 writes `outcome.json`
  before anything is deleted, so what a run reports never depends on what it
  released.
- **Each array task is its own run.** Its own input view, results root, log
  directory and run id — the pipeline refuses an id that already names a run,
  and seventy tasks sharing one would each invalidate the others.

## The values

Copy [`hpc_config.example.yaml`](hpc_config.example.yaml) to `hpc_config.yaml`
and fill it in. It is the only file to edit, and it is divided into the
sections below.

### Where things are

| Key | What it is | How to find it |
|---|---|---|
| `host` / `user` / `port` | the login node | the cluster's own account mail or user guide |
| `remote_base` | workspace parent; each bundle gets `runs/<run-id>/` below it | see [Space](#space) |
| `remote_sif` | the `.sif` image, uploaded once | `ls -lh` it, to confirm the upload finished |
| `remote_ref` | the 10x transcriptome reference | `ls -d ~/refdata-gex-* /scratch/*/refdata-gex-* 2>/dev/null` |
| `remote_cellranger` | the **Cell Ranger installation root** | see [Cell Ranger](#cell-ranger) |

### What the scheduler will accept

| Key | What it is | How to find it |
|---|---|---|
| `scheduler` | `slurm` or `pbspro` | `command -v sbatch` → Slurm; `command -v qsub` → PBS |
| `queue` | partition or queue to submit to | see [Choosing a queue](#choosing-a-queue) |
| `ncpus` | cores per job | the node's core count, where nodes are allocated whole |
| `mem_gb` | memory request, in GB | must exceed `cellranger_mem` |
| `walltime` | `HH:MM:SS` | must be under the queue's limit |
| `module_load` | module providing Singularity, if any | `module avail 2>&1 \| grep -iE 'singularity\|apptainer'` |

### Which pipeline runs

The image carries two Stage 3 pipelines, and one setting chooses between them.

| `legacy_pipeline` | What runs |
|---|---|
| `false` (default) | the current version, with the corrections made since |
| `true` | the earlier version whose results were verified on a cluster |

`pipeline_mode: verified|corrected` and `safe_mode: true|false` are older
spellings of the same setting and still work.

The earlier one is frozen: it is kept byte for byte as it was when its output
was checked, and a test fails if anything changes it. The current one carries
later checks and the corpus-scale features and is the maintained execution
path. The frozen tree remains available for an explicit comparison run.

Retention, batching and the corpus path all live in the current version. A
bundle built from a plan therefore refuses the legacy tree, because that tree
would ignore retention and use a different amount of disk than the plan
checked.

**Using both.** The two are also a way to find out whether the corrections
matter on real data. Put the same input through each, into separate results
directories, and compare the run records:

```yaml
legacy_pipeline: true       # reproduce the earlier run
legacy_pipeline: false      # current tree, different output_dir
```

Samples the two disagree about are samples where something that changed
changed the answer. That is worth knowing before a large run, and it cannot be
learned from tests — a fixture only shows that a fix does what it says, never
that the thing it fixes happens to your data.

### What the run may consume

Execution-policy keys are optional: left out, a run behaves as it always did,
with one job, nothing deleted and everything retrieved. Corpus planning needs
the site-specific capacity and runtime values marked as required below.

| Key | What it is | Default |
|---|---|---|
| `legacy_pipeline` | use the frozen earlier pipeline instead of the current one | `false` |
| `retain` | what a completed sample keeps — `all`, `analysis`, `summary` | `all` |
| `release_input` | delete a sample's input once its output is verified | `false` |
| `disk_gb` | the **cluster** quota this run may occupy | required for planning; never measured |
| `minutes_per_gb` | compute minutes per GB of input on this cluster | required for planning; no default |
| `fastq_expansion` | uncompressed FASTQ size as a multiple of `.sra` input | 4.0 |
| `clock_margin` | fraction of queue walltime one batch may consume | 0.8 |
| `residue_ratio` | what a finished sample keeps, as a fraction of its input | measured, else per `retain` |
| `max_concurrent_jobs` | array tasks that may run **at the same time** | 1 |
| `retrieve` | default mode for `retrieve_results.sh` | `all` |

`disk_gb` is not detected. The machine running the planner is not the cluster,
and its free space is not the number the batches have to fit inside — a plan
built from the wrong one is wrong in the direction that fills a disk.

`minutes_per_gb` is equally site-specific. Measure it from a representative
completed job (`elapsed minutes / total input .sra GiB`) on the target cluster.
If no measurement exists yet, write an explicit conservative value that makes
batches smaller, then replace it after the first measured run. The planner has
no cross-site default and refuses to plan without this value.

`max_concurrent_jobs` controls how many array tasks may share the quota at once.
The planner derives each task's batch from `minutes_per_gb`, `walltime` and
`clock_margin`, then includes every concurrent task's transient working set in
the peak-disk calculation.

## Choosing a queue

```bash
sinfo -o '%R %c %m %l %a %G'      # Slurm: name, cores, memory MB, time limit, state, GPUs
qstat -Q                          # PBS
```

Three things decide it, and only the first is obvious.

**Memory and cores** have to clear what Cell Ranger is asked for
(`cellranger_mem`, `cellranger_threads`).

**The time limit** has to clear `walltime`. Queues named `debug`, `short` or
`test` often pair large nodes with a one- or two-hour cap, so a queue that
looks generous by memory can reject a long job — and it rejects it at
submission, after the image has been converted and the data uploaded.

**GPUs.** Stage 3 never uses one. A partition advertising a `gres` of
`gpu:...` typically bills several times a CPU partition per node-hour, and
occupies accelerators this pipeline cannot use. Prefer a CPU-only partition
even where a GPU one would fit.

Where a cluster allocates nodes whole — one job per node, which many enforce —
request the node's full core count. Using half the cores costs the same and
takes twice as long.

## Cell Ranger

**Point at the installation root, not at the directory holding the launcher.**

Cell Ranger reads `.env.json`, `external/`, `lib/` and `mro/` beside its
launcher, and where the launcher sits inside that tree differs by release:
7.x keeps it in `bin/`. Mounting `bin/` alone fails at startup with
`Couldn't find the '.env.json' file`.

```bash
command -v cellranger                        # often empty until a module is loaded
module avail 2>&1 | grep -i cellranger
ls -d /scratch/tools/*cell* /apps/*cell* ~/cellranger-* 2>/dev/null
```

The root is the directory containing `.env.json`:

```bash
ls -a /path/to/cellranger-8.0.1     # .env.json  bin/  external/  lib/  mro/
```

**Where several versions are installed, name the one you want.** Sorting by
name puts `cellranger-7.0.1` ahead of `cellranger-8.0.1`, so "the first one
found" is not the newest. The digits after `cellranger-` are compared as
numbers and the highest is taken, but that is a default rather than a
decision.

## The reference

The transcriptome reference carries the gene annotation, so two releases give
different gene counts for the same reads. `refdata-gex-GRCh38-2024-A` and
`refdata-gex-GRCh38-2020-A` are not interchangeable, and a reference for
another species runs to completion and means nothing. Where the choice is left
to detection, a human reference is preferred over any other and the newest
release within it is taken — but a mouse reference sitting alone in the search
path is still a mouse reference, so name it where it matters.

```bash
ls -d /scratch/database/refdata-gex-* ~/refdata-gex-* 2>/dev/null
cat /path/to/refdata-gex-GRCh38-2024-A/reference.json   # {"genomes": [...], "version": "..."}
```

A reference directory holds `reference.json`, `fasta/`, `genes/` and `star/`.
A general genome bundle — GATK's `b37`/`b38`, a bare FASTA — is not one,
whatever its name suggests.

## Space

Stage 3 copies the input, expands it to FASTQ, and Cell Ranger writes
intermediates on top: plan on several times the input size. Point
`remote_base` at whatever the cluster calls scratch or project space. The
generator creates a distinct `runs/<run-id>/` work tree below it for every
bundle, so a later upload cannot merge its inputs with an earlier run. Verified
outputs remain under the shared `remote_base/results/` cache so an intentional
retry can reuse them.

```bash
df -h ~ /scratch 2>/dev/null
quota -s 2>/dev/null || lfs quota -h "$HOME" 2>/dev/null
```

Check the cluster's guide rather than assuming. On some systems `/scratch` is
a read-only shared area for databases and tools, and `$HOME` is where work is
expected to happen; on others it is the reverse.

## Handing it over

When the run has to be started by someone other than whoever prepared it,
everything needed travels with the files. A question costs a round trip, and
on a shared facility that can be days.

### What travels

1. **The handoff bundle**, from `prepare_hpc_handoff.py`. The config still has
   to be filled in; `host`, `user` and `port` can stay at their defaults,
   since nothing will connect anywhere. Of what it generates, only the job
   script, `config.yaml`, `run_singularity_pipeline.sh` and `HANDOFF.md` are
   used — plus `batches/` and `plan.json` for an array bundle, without which
   every task exits 41 before it starts, and `corpus_report.py` for reading
   the results afterwards. The job script is `run_stage3.slurm`, or
   `run_stage3.pbs` where
   `scheduler` is `pbspro` or `torque` — a bundle carries one of the two,
   named for the scheduler it was generated for.
2. **The image**, as a Docker archive: `docker save <image>:<tag> -o image.tar`.
   Not a tar of a directory — `docker-archive://` refuses that. Build it
   `--platform linux/amd64` if the build machine is ARM; an image is built for
   one architecture and will not run on another.
3. **The input**, as `.sra` files, with their SHA-256, so a truncated transfer
   can be told apart from a failed run.

### What the operator does

```bash
# once
singularity build genoar-srr_step9.sif docker-archive://image.tar

# per run
sbatch run_stage3.slurm     # qsub run_stage3.pbs, under PBS
```

The two values most likely to need changing are the Cell Ranger root and the
reference, and **neither of them is in `config.yaml`** — that file holds only
the paths as seen from inside the container, which are the same everywhere.
They are `remote_cellranger` and `remote_ref` from `hpc_config.yaml`, written
into the `--cellranger` and `--ref` lines of the job script. So either
regenerate the bundle once the site's paths are known, or say which two lines
of the job script to edit. Telling an operator to change `config.yaml` leaves
them editing a file where changing nothing has any effect, and the job then
binds the paths it was generated with.

### What comes back

- `logs/` — the scheduler's `.out` and `.err`, and the pipeline's step logs
- `results/runs/<run id>/outcome.json` — what the run counted as complete, and
  why anything else did not. An array task writes to
  `results/batch_<n>/runs/<run id>/outcome.json` instead, and its run id is
  `<bundle run id>-b<n>-<scheduler job id>`
- `results/success/<sample>/cellranger_output/outs/metrics_summary.csv` and
  `web_summary.html` — small, and the first thing worth reading

Not the BAMs: gigabytes each, and they prove nothing `outcome.json` does not.

### Reading the result

Read `outcome.json`, not the exit code alone. A sample counts as complete when
this run's own Cell Ranger produced it (`fresh`), when a verified earlier run
did and this run reused it (`cache_hit`), or when verified output was
deliberately released afterwards (`released` — under `retain: analysis`, which
is the usual choice, that is most of them). Output merely found on disk is reported as `adopted`
and does not count — see
[README.md](README.md#what-the-verdict-is-read-from).

### When the output is not a result

A run can be complete and wrong. Reads given as the wrong mates, a reference
for the wrong species, an input that arrived truncated — every one of those
produces a BAM, exits 0, and used to count as a finished sample, because the
BAM existing was the whole test.

Cell Ranger writes `metrics_summary.csv` beside the output and says otherwise.
The pipeline records those numbers with the run and compares them against
floors:

| Key | What it is | Default |
|---|---|---|
| `min_cells` | Estimated Number of Cells | 100 |
| `min_valid_barcodes` | Valid Barcodes | 0.10 |
| `min_transcriptome` | Reads Mapped Confidently to Transcriptome | 0.05 |

Below any of them the sample is reported `suspect` and is **not** counted as
complete:

```
[result]   - 1 suspect sample(s): this run's Cell Ranger produced this output and
             Cell Ranger's own summary says it is not a result: 0.4% valid barcodes,
             below the 10.0% this run requires. Check which reads were given as R1
             and R2, and which reference was used.
```

The defaults sit far below anything a real experiment reaches. The point is not
to judge the science — it is to notice when there isn't any. Raise them for a
site that knows its data, or set one to 0 to switch it off. Output produced
before this existed carries no metrics and is left to the other checks rather
than failed.

### Reuse is reuse of the same measurement

A sample already analysed is not re-run: the receipt beside its output says an
earlier run's own Cell Ranger produced it from this exact input, and it comes
back as `cache_hit`. That is what makes re-submitting a failed batch cheap.

It is only valid while the environment is the same one. Cell Ranger's release
and the reference's release both change the gene counts, so output made with
7.0.1 against `refdata-gex-GRCh38-2020-A` is not an answer for a run using
8.0.1 against `2024-A` — same reads, different numbers. Step 0 records what
this run is using, every result records what made it, and a difference is
reported as `stale` rather than reused:

```
[result]   - 1 stale sample(s): output of run <id> was produced with Cell Ranger
             7.0.1, this run has 8.0.1; ... remove the cellranger_output
             directory to recompute it
```

Output from before this existed carries no such record. It is still reused —
throwing away known-good work would be worse — and the reason says the
comparison could not be made, so it is visible rather than assumed.

**When comparing environments deliberately, use a clean results directory.**
Otherwise the first run's output is what the second one finds.

Read step 0's notes too. Where the cluster's Cell Ranger or reference differs
from what the pipeline was validated against, results are not directly
comparable with earlier ones, and that belongs with the numbers.

## When it fails before the pipeline starts

These look like a broken pipeline. None of them are.

### `exec: "/usr/sbin/unsquashfs": no such file or directory`

```
FATAL: while extracting ...sif: exec: "/usr/sbin/unsquashfs":
stat /usr/sbin/unsquashfs: no such file or directory
```

Singularity unpacks the `.sif` with `unsquashfs`, and the compute node cannot
reach the copy the login node has — `/usr/sbin` is often absent from a compute
node's path, or the binary is not installed there at all.

Put a copy where the account owns it and point Singularity at it:

```bash
cp /usr/sbin/unsquashfs ~/.local/bin/unsquashfs
chmod +x ~/.local/bin/unsquashfs
singularity config global --set "unsquashfs path" "$HOME/.local/bin/unsquashfs"
```

The setting persists, so this is once per account. Confirm it with a job that
only runs `singularity exec <sif> true` before spending an allocation.

### `destination doesn't exist in container`

Singularity creates a missing bind destination only where overlay or underlay
is enabled, and some clusters disable both. The image carries
`/work/data/sra`, `/ref`, `/opt/cellranger` and `/work/config.yaml` for that
reason; seeing this means the image predates that and needs rebuilding.

### `Couldn't find the '.env.json' file`

Cell Ranger was given its `bin/` rather than its installation root. See
[Cell Ranger](#cell-ranger).

### `error: Found argument '--create-bam' which wasn't expected`

That argument arrived in Cell Ranger 8.0 and 7.x refuses it. Current versions
read the installed version and omit it on 7.x, so this means the image
predates that change.

### `docker-archive://...: not a valid archive`

The tar has to come from `docker save`:

```bash
docker save <image>:<tag> -o image.tar
```

### `exec format error`, or the container dies immediately

The image was built for another architecture. Rebuild with
`docker build --platform linux/amd64 ...`.

## Running more than fits

One job holds what the disk holds and runs for as long as the queue allows. A
corpus is neither, so it is split, and the split is decided by whichever runs
out first.

```bash
cd srr_pipeline_package/hpc          # the commands below are run from here
python3 plan_batches.py --input-dir ./sra --config hpc_config.yaml --out ./plan
```

It measures the inputs, takes the rest from `hpc_config.yaml`, asks about
anything left, and stops — naming the key to write — when there is nobody to
ask. `--review` asks about everything with what it found already filled in,
which is worth doing once at an unfamiliar site before a week of queue time is
committed to numbers nobody looked at. `--non-interactive` never asks. In
particular, `minutes_per_gb` must be a measurement from this cluster or an
explicit conservative site value; it is never borrowed from another site.

What it prints matters more than the batch size:

```
4 jobs of up to 163 samples. The wall clock decides that, and only that.
  15.3 days of compute, if the jobs run one after another.
```

**The wall clock decides the batch size and nothing else does.** The quota
decides something separate — whether the run fits at all — and that is the
figure to take to the facility. See [Whether it fits at all](#whether-it-fits-at-all).

Then build the bundle from the plan:

```bash
python3 prepare_hpc_handoff.py --hpc-config ./hpc_config.yaml \\
  --sra-dir ./sra --batches ./plan --out ./bundle
sbatch run_stage3.slurm      # one array, one task per batch
```

Each task takes the batch list named for its index, stages only those inputs,
and writes to its own results root under its own run id. `max_concurrent_jobs`
governs how many run at once, and defaults to **1** — the tasks share the quota
the batch size was calculated against, so two at a time need twice the disk the
plan asked for.

A task whose batch is short of even one input **refuses to start**. A batch
that runs short still reports N/N against its own manifest, so the shortfall
shows up only when the whole corpus is added back together, if anyone adds it.

### Whether it fits at all

The wall clock and its safety margin decide the batch size. Disk is checked
across the resulting whole run, and it is the question worth taking to the
facility:

```
Disk at its fullest point across the simulated run:
    ... GB  inputs (uploaded before the array is submitted)
    ... GB  output kept, accumulated over completed batches
    ... GB  a second copy of the batches in flight
    ... GB  expanded FASTQ for the batches in flight
    ... GB  peak, against the configured quota

IT DOES NOT FIT. Short by ... GB.
  Smaller batches reduce the copied-read and expanded-FASTQ terms. They do not
  reduce resident inputs or output retained from the whole corpus. Those need a
  stricter `retain`, `release_input: true`, less corpus, or a larger quota.
```

Batching changes the transient working set, not the cumulative baseline. A plan
that checks only one batch still misses retained output from earlier batches;
one that checks only the end misses a larger transient peak that can occur while
a batch is in flight. The planner checks both.

`plan_batches.py` exits non-zero when the plan does not fit, and
`prepare_hpc_handoff.py` refuses to build a bundle from one — submitting a
corpus already known to run out of disk is the failure this is for.

**The plan is the contract.** The bundle takes `retain` and `release_input`
from `plan.json`, not from the config, because the disk figure the quota was
agreed against depends on them. A config that disagrees is overridden, with a
line saying so.

### Making room as it goes

Batches only help if finished samples stop occupying the disk. That is
`retain`, in `hpc_config.yaml` — set beside the quota the decision is about,
and carried into the generated `config.yaml` for the run:

| | What stays | When to use it |
|---|---|---|
| `all` | everything (default) | the quota is larger than the corpus |
| `analysis` | everything but the BAM and its index | the usual choice |
| `summary` | the filtered matrix, `metrics_summary.csv`, `web_summary.html` | results leave over a slow link |

Both `analysis` and `summary` also release **the reads copied in beside the
outputs**. Step 5 copies the whole sample — its `.sra` and every FASTQ made
from it — into `results/success/<sample>/`, so a completed sample holds them a
second time. On this pipeline they are the larger pile, and releasing only what
sits under `outs/` freed the BAM, reported gigabytes, and left the disk full.

`release_input: true` additionally deletes the sample's original input under
`data/sra/`.

Retention runs after the verifier, never before it, and only over samples the
verifier credited to the run. **A released sample still counts as analysed**,
and what makes that safe is that the claim has to hold in two places written at
different times: the sample's own receipt, and the releasing run's
`release.json`. The receipt must name that sample, carry the size and timestamp
of the BAM a verification recorded, and say the BAM itself was removed; the
ledger must list the sample under the run the receipt names. Any of those
missing and the sample is `missing`.

A BAM that is simply gone is still reported missing — absence is not evidence,
and a check satisfied by deletion is no check. Nothing kept in the same tree as
the data survives someone who can write anywhere in it; what this rules out is
the case it exists for, which is output lost quietly and counted as work done.

Nothing is deleted on a promise that failed. The ledger is written first, then
the sample's receipt, then the files — so every point an interruption can land
on leaves a state the verifier reads correctly — and a receipt that could not
be written stops that sample's deletion, because a full or failing filesystem
is exactly when a run is trying to free space.

### Bringing back only what is wanted

```bash
./retrieve_results.sh --only summary
```

`all`, `analysis` and `summary` mean what they mean above. `summary` also
leaves the step logs and the per-step failure lists (`step8_failed.txt` and
friends) on the cluster — take `analysis` when a run went wrong and you need to
see why. The run records come back whatever the mode: without `outcome.json` the results are numbers with
nothing to say what the run missed. Needs `transfer_tool: rsync` — scp copies
trees whole and says so rather than filtering wrongly.

### Adding the batches back together

```bash
python3 corpus_report.py --plan ./plan --results ./stage3_results \
  --run-id "$(cat bundle/run_id.txt)"
```

Each task reports against its own manifest, so seventy clean N/N reports can
still be a corpus with samples missing between them. This reads the plan and
every outcome together and separates four things:

- **complete** — a run credited it: its own work, reused work, or work it
  released on the record
- **incomplete** — a run reached it and would not credit it, with the reason
  that run gave
- **unaccounted** — no outcome mentions it at all. The one to look at first:
  nothing failed, because nothing ran. A whole batch here is a task that was
  never submitted or died before writing anything
- **duplicated** — **analysed** by more than one run: Cell Ranger spent the
  queue time twice, usually because of a stale batch list. A retry reporting
  `cache_hit` is not this — reusing an earlier run's output is what makes
  re-running a failed batch safe

The run id matters: a results tree is reused between runs, and without it a
single record left from an earlier one was enough to report this corpus
complete. Exit 0 only when every planned sample is complete and nothing was
analysed twice; 4 when some are or exact-once cannot be claimed; 3 when none
are. Re-running a batch is safe: the samples that finished come back as
cache hits.

## Recording what produced a result

These are written into the bundle's `config.yaml` automatically, with the pair
the pipeline was validated against:

```yaml
cellranger_version: "8.0.1"
reference_version: "refdata-gex-GRCh38-2024-A"
```

Nothing has to be added by hand. To state a different pair — a site whose
results are meant to be comparable with something else — set the same two keys
in `hpc_config.yaml` and regenerate; leave either empty to accept anything
without comment. ([`../configs/example.yaml`](../configs/example.yaml) is the
hand-written pipeline config, for runs driven locally with Docker rather than
from a bundle — that path has no `hpc_config.yaml`, so every key is written
there instead.)

Step 0 reads what is installed and compares:

```
[step0] NOTE: this pipeline was validated on Cell Ranger 8.0.1; this system has 7.0.1.
[step0]       It will run, but the numbers are not directly comparable across releases.
```

The run continues either way — Cell Ranger's arguments are chosen from the
installed version, so 7.x and 8.x both work. What does stop the run is a
version that cannot be read at all: which arguments exist depends on it, and
both guesses fail after the allocation has been spent rather than before it. What the note buys is that a
difference is on the record rather than discovered later, when two result sets
disagree and nobody remembers why. Leave either value empty to accept anything
without comment.
