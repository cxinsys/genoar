# GENOAR SRR Pipeline (Dockerized)

[Korean / 한국어](README.ko.md)

Dockerized port of the SRR pipeline (steps 1–9) originally described in `srr_pipeline/README.md`. Runs the same steps reproducibly in a local Docker environment without requiring SLURM.

## Implemented Steps (1–9)

1. `.sra` → per-SRR directory layout
2. `fastq-dump --split-files` in parallel (GNU Parallel)
3. `.fastq` → `.fastq.gz` parallel compression (pigz/gzip)
4. Success/failure classification based on `.fastq.gz` presence
5. Move successful samples to `/work/results/success`
6. Generate TSVs grouped by FASTQ file count per sample (1–4), and record which
   samples can never reach Cell Ranger
7. Rename to `<sample>_S1_R1_001.fastq.gz` / `_R2_001.fastq.gz`, refusing any
   layout that cannot be renamed safely
8. Run Cell Ranger via Snakemake in local mode
9. Handle failed samples (R1/R2 swap, move to `retry_failed/`, write failure list)

## Cell Ranger Eligibility

Cell Ranger needs a paired R1/R2 read set. A sample with a single FASTQ file
cannot be processed however well steps 1–5 went, and step 8 will not run a
`cellranger count` job for it. Rather than let that disappear into a clean exit
code, the pipeline takes a census and reports it:

- **Step 6** counts the FASTQ files per sample and writes every ineligible one to
  `success/cellranger_ineligible.tsv` with the reason (`no fastq.gz files found`,
  or `only 1 FASTQ file; Cell Ranger requires paired R1/R2 reads`).
- **Step 7** renames raw `fastq-dump` output. It leaves a sample untouched only
  when what is on disk is a **complete** layout: R1 *and* R2 for the same sample,
  either single lane (`<sample>_S1_R[12]_001.fastq.gz`) or multi-lane
  (`<sample>_S*_L*_R[12]_*.fastq.gz`) with every lane's R1 matched by an R2. An R1
  with no R2, an R2 with no R1, and a multi-lane set whose lanes do not pair up
  are all **refused**, logged, and appended to the same TSV. Files that carry
  `_R1_`/`_R2_`/`_I1_`/`_I2_` tokens but form no pair the script recognises are
  refused too. Renaming half of such a sample would destroy the only evidence of
  its real layout, and the size heuristics that work on raw `fastq-dump` output
  would silently pick the wrong two files.
- **Step 8** applies the same test again and writes the full census to
  `success/cellranger_eligibility.tsv` (one row per sample: eligible or
  ineligible with a reason) before Snakemake starts. Its first line is
  `# run_id <id>`, naming the run that made the selection. It also appends to
  `success/cellranger_ineligible.tsv` the exclusions the earlier steps cannot
  see: a sample with more than four FASTQ files is never listed by step 6 and
  never processed by step 7, so an incomplete lane set in one is discoverable
  only here.

Steps 7 and 8 read the FASTQ names the same way and agree on which layouts Cell
Ranger can be given. Eligibility means a **complete** read set. That is the
single-lane pair, or a multi-lane set in which every lane carrying an R1 also
carries its R2.
Cell Ranger is handed the sample directory (`--fastqs <dir> --sample <name>`) and
re-globs it, so a lane whose mate is missing cannot be hidden from it by choosing
files here.

If you are upgrading: step 8 used to ask only whether *some* `_L*_R1_` file and
*some* `_L*_R2_` file existed. A sample with its R1 in one lane and its R2 in
another satisfied that, was selected, and `cellranger count` failed on it. That
one failure failed the whole Snakemake step, so the run exited there and the
samples it had not yet reached were never analysed. Runs that used to fail this
way now finish. The sample is excluded before Snakemake starts, with the reason
`multi-lane FASTQ names do not pair R1 with R2 within the same lane`.

## What a Run Claims to Have Done

`/work/results` is a persistent volume, so "a BAM is on disk" says nothing about
which run produced it. Completion therefore means: **every sample this run was
asked to process carries Cell Ranger output whose provenance matches this run's
input.**

Each run fixes that expected set before step 1 moves anything, and records both
the plan and the outcome under `results/runs/<run_id>/`:

| Path | Contents |
|------|----------|
| `results/runs/LATEST` | The id of the most recent run |
| `results/runs/<run_id>/expected_samples.tsv` | One row per input sample, with a content fingerprint, fixed before step 1 |
| `results/runs/<run_id>/cellranger/<sample>.json` | The completion record: written by the Cell Ranger rule itself, for each sample it actually ran, naming the file that came out |
| `results/runs/<run_id>/outcome.json` | Per-sample status and counts, plus samples belonging to other runs |
| `results/runs/<run_id>/samples/<sample>.json` | The same record, one file per sample |
| `results/runs/<run_id>/outcome.env`, `reasons.txt` | What the final banner reads |
| `results/success/<sample>/.genoar_cellranger.json` | The receipt: ties this BAM to the input it came from and the run that made it. Written only for `fresh` and `adopted` outcomes |

Every path above is created by the run itself. Evidence flows one way: the Cell
Ranger rule writes the completion record, the orchestrator derives the receipt from
it, and only the receipt outlives the run.

The run id comes from `GENOAR_RUN_ID`, else the config's `run_id`, else a
generated one. It becomes a directory name under `results/runs/`, so it is held to
the same rule the crawler applies: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, with
surrounding whitespace stripped and `LATEST` reserved for the newest-run pointer.
The comparison is case-sensitive, so `latest` is fine. An id that fails, or that
already names a run directory, is a configuration fault. It exits `2` before any
step runs. A run record is never overwritten. A *generated* id that collides is
regenerated instead, since nobody chose it. A resume is a *new* run over the *same*
expected set. It takes a new id, finds the earlier output in place, and reports
those samples as cache hits.

**The id is reserved atomically.** The run claims `results/runs/<id>/` with a single
`mkdir`. The kernel serialises that call, so exactly one caller can win. Start two
runs on the same id at the same instant and one proceeds. The other exits `2`.
Checking for the directory and creating it later would be two acts, and between
them a second run could claim the same id and write its manifest and records into
the same directory.

A run directory that could not be created at all exits `1`. The banner states that
this does **not** mean the id is taken.

Each expected sample ends in exactly one state:

| State | Counts as complete? | Meaning |
|-------|:---:|---------|
| `fresh` | yes | This run's own Cell Ranger job produced the output and left a completion record saying so |
| `cache_hit` | yes | A receipt for verified work (`"provenance": "fresh"`) vouches for exactly this file against this input. A genuine resume, never reported as fresh work |
| `adopted` | **no** | Pre-existing output accepted on request (`GENOAR_ADOPT_PRIOR_RESULTS=1`). No evidence ties it to this run's input. It is recorded and never counted. Its receipt records the adoption, so every later run reports it as `adopted` again and never as a `cache_hit`. A Cell Ranger run that replaces the output is what ends the adoption |
| `unverified` | **no** | Nothing vouches for where the output came from |
| `stale` | **no** | A receipt exists and describes a different input |
| `ineligible` | no | Ruled out before Cell Ranger; the reason is in the record |
| `missing` | no | No output at all |

Adoption records reused output. It does not count toward verified completion and is
excluded from the exit-0 calculation. The banner prints the two counts on separate
lines:

```text
[result]   verified complete: 2 (fresh 1, cache hit 1)
[result]   adopted, NOT verified and NOT counted as complete: 1
```

Verified means the run can point at what makes the output its input's. That is
either its own Cell Ranger job's completion record or a receipt from the run that
did the work. Adopted means the operator told the run to use output it could not
check. A run whose only completion is adopted has no verified sample. It exits `4`.
Exit `3` reports a run in which nothing was reused, and this run reused output.

A modification time is not provenance. It cannot say which input produced a file,
and a foreign BAM written in the same second the run started satisfies it. Nor is
"the sample was eligible and Snakemake exited 0" evidence. Eligibility is decided
before any work happens, and Snakemake exits 0 precisely when it finds nothing left
to do. So the claim is not inferred at all. The rule that runs Cell Ranger writes a
completion record for every sample it runs, and the evidence is weighed in this
order:

1. **This run's completion record for this sample, still describing the file on
   disk.** The rule ran Cell Ranger here, in this run, and this is what came out.
   `fresh`. It is weighed first because it is this run's own direct observation.
   Everything below it is an inference about an earlier run.
2. **A receipt that already vouches for exactly this file, and says verified work
   produced it.** Same input fingerprint, same BAM, and `"provenance": "fresh"`
   on the receipt. Work an earlier run did for this exact input, found still in
   place: `cache_hit`, reused and never claimed as new work. A receipt saying
   the output was adopted is `adopted` again. A receipt that says neither is
   `unverified`, because it cannot tell reused work from output taken on
   somebody's word.
3. **A receipt that contradicts.** Output made from a different input (`stale`),
   or made from this input but no longer the file on disk (`unverified`: it has
   been replaced or touched since, and this run did not produce what is there
   now).
4. **Adoption**, if it was asked for explicitly. Recorded as `adopted`, reported on
   its own line, and counted towards nothing.
5. Otherwise the evidence is absent. Absent evidence does not favour the sample.
   It is `unverified`, and uncounted.

**"Still describing the file on disk"** means the file the output occupies and
64 KiB of each end of it. The completion record stamps the device and inode the BAM
sits on, its size, a timestamp floor, and the SHA-256 of its first and last 64 KiB.
Only the ends are hashed. A Cell Ranger BAM is several gigabytes, and hashing one
on every run would charge every honest run for it.

The inode decides the match. One case is decided by the digests. Where everything
agrees except the inode number, the run compares the recorded digests. A digest is
computed from the file's bytes, so it does not depend on the filesystem's
bookkeeping. Some filesystems do not keep
an inode number stable for an unchanged file. A macOS Docker Desktop bind mount is
one. On those, the digests identify the output, and a healthy run on a bind-mounted
results directory completes normally and exits `0`.

The rule warns at the time if it could not read the file's ends, and that record
carries no digests. On a filesystem that renumbers the file, nothing then settles
the match. The sample is reported `unverified`. The reason names both states that
produce it. One is a filesystem that renumbers the file. The other is an output
swapped for one of the same size and timestamp. The reason gives the action for
each, and states that the run cannot determine which of the two it was. Cell Ranger
is Linux-x86-64 only, so a Mac is a rehearsal environment for Stage 3 in any case.

So a sample with Cell Ranger output that neither a completion record nor a receipt
accounts for is `unverified` and uncounted. That includes output made before this
mechanism existed. A job killed midway leaves only a `<sample>.started` marker
beside the records, and that grants nothing: the run began the work and cannot show
it finished.

Receipts are written only where the run can vouch for the output: what it
produced (`fresh`) and what the operator adopted on the record (`adopted`).

Output that belongs to no expected sample of this run is listed in
`other_samples_with_output`, left untouched, and never counted.

> **Upgrading.** The input fingerprint now covers **every** file of a multi-file
> FASTQ input. Earlier versions covered only the largest one. A receipt an earlier
> version wrote for such a sample therefore no longer matches, and the sample reads
> as `unverified` on the first run after the upgrade. Re-run it, or accept the
> existing output with `GENOAR_ADOPT_PRIOR_RESULTS=1`. Single-file `.sra` inputs
> fingerprint exactly as they did before and are unaffected.

## Reporting and Exit Codes

Snakemake exits 0 when its sample list is empty, so "step 8 exited 0" does not
mean Cell Ranger ran. The pipeline measures the expected set above, and the exit
status follows it:

| Exit | Final banner |
|------|--------------|
| `0` | Every expected sample has verified Cell Ranger output. Also returned when this image has no Cell Ranger stage at all (a steps 1–7 build target), which is stated explicitly |
| `1` | A step failed and the banner names it. Also: the run directory could not be created at all (read-only tree, full disk, mount gone) |
| `2` | The run is misconfigured: no usable `cellranger` executable, `cellranger --version` answered no version, a QC floor (`min_cells`, `min_valid_barcodes`, `min_transcriptome`) that is not a number, `/ref` is not a usable reference, or the run id is unusable, reserved, already names a run, or was taken by another run at the same moment. Detected before any step runs |
| `3` | The run was valid and complete, and no expected sample was analysed. A run given no input at all reports this |
| `4` | Partial. This run cannot show verified output for every expected sample, and some expected sample carries output it accounts for. That output is either verified (some completed, others did not) or adopted. The reasons are printed grouped by cause |

A misconfigured install is deliberately **not** exit `3`. "Nothing to do" and
"cannot do anything" are different answers, and only the second is a fault to fix.

Exit `1` and exit `2` are kept apart for the same reason. Exit `2` is about the id
you chose. Exit `1` is about the volume. A full disk reported as a taken run id
sends you to change the one thing that was working.

> Exit `4` means "partial" here. The parallel crawl scripts use `4` for
> something else entirely (workers finished cleanly and *that run* collected no
> file).
> `0`–`3` are the same across the project. The root README's
> [Reading a Run's Outcome](../README.md#reading-a-runs-outcome) has the
> per-entry-point table.

### Prerequisites, checked before anything runs

Step 0 refuses to start unless both of these hold. It lists every problem it
found, and does not stop at the first:

- `/opt/cellranger/cellranger` or `/opt/cellranger/bin/cellranger` (the host's
  `./cellranger/` installation) exists and is executable. The complete install
  root is mounted because Cell Ranger needs files beside the launcher.
- `/ref` (i.e. `./ref/`) holds a non-empty `reference.json` and the directories
  `fasta/`, `genes/` and `star/` at its top level. A reference still packed, or
  extracted one level down into `refdata-gex-*/`, does not qualify.

`make run-stage3` applies the same two checks on the host before it starts the
container, so an install one accepts the other accepts too.

### Which pipeline runs

The public default is the maintained/current tree (`legacy_pipeline: false`).
The earlier cluster-validated tree remains frozen as a comparison baseline;
select it explicitly with `legacy_pipeline: true` and use a separate results
directory. Existing `pipeline_mode: verified|corrected` and
`safe_mode: true|false` configurations remain compatible.

## Directory Layout

```
srr_pipeline_package/
├─ docker/
│  └─ Dockerfile                   # Multi-stage; final target: step9
├─ pipeline_entry.sh               # Selects current or frozen tree from config
├─ pipeline/                        # Frozen earlier tree (legacy_pipeline: true)
│  ├─ run_docker_pipeline.sh       # Orchestrates steps 1–9
│  ├─ lib.sh                       # Logging/report helpers
│  ├─ make_directories.sh          # Step 1
│  ├─ fastq_dump_parallel.sh       # Step 2
│  ├─ fastq_gzip_parallel.sh       # Step 3
│  ├─ parse_fastq_logs.py          # Step 4
│  ├─ move_success_dirs.sh         # Step 5
│  ├─ check_counts.sh              # Step 6
│  ├─ rename_fastq.sh              # Step 7
│  ├─ Snakefile_hs.smk             # Step 8
│  └─ rename_move_failed_fastqs.py # Step 9
├─ pipeline_next/                   # Maintained/current tree (public default)
├─ requirements.base.txt           # Pinned PyYAML
├─ requirements.snakemake.txt      # Pinned Snakemake
├─ configs/
│  └─ example.yaml                 # Sample configuration (paths, cores, …)
└─ docs/
   ├─ README.md                    # Original English overview
   ├─ USER_GUIDE.md                # User guide
   └─ TROUBLESHOOTING.md           # Troubleshooting guide
```

## Quick Start

From the project root, `make build-srr` and the Stage 3 Make targets wrap the two
commands below and check the prerequisites first. `make fetch-sra` selects the
quality-filtered Stage 2 accessions, caches them under `sample_sra/`, and builds
an exact view for `make run-fetched-stage3`. Direct `make run-stage3` processes
user-managed inputs in `sample_sra/`. The raw Docker invocation follows for
anyone driving the image directly.

1) Prepare data and configuration
- Host `./sample_sra/SRRxxxxxx.sra`
- Host `./ref` (Cell Ranger reference genome)
- Host `./cellranger/` installation, with an executable at either
  `cellranger` or `bin/cellranger`
- Review and edit `srr_pipeline_package/configs/example.yaml`

2) Build the image
```bash
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 \
  -t genoar-srr:step9 .
```

3) Run steps 1–9
- Required mounts
  - `-v $(pwd)/sample_sra:/work/data/sra`
  - `-v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml`
  - `-v $(pwd)/ref:/ref`
  - `-v $(pwd)/cellranger:/opt/cellranger`
- Optional mounts
  - `-v $(pwd)/logs:/work/logs`
  - `-v $(pwd)/results:/work/results`
- Run command
  ```bash
  docker run --rm -it \
    -v $(pwd)/sample_sra:/work/data/sra \
    -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \
    -v $(pwd)/ref:/ref \
    -v $(pwd)/cellranger:/opt/cellranger \
    -v $(pwd)/logs:/work/logs \
    -v $(pwd)/results:/work/results \
    genoar-srr:step9
  ```

## Reproducibility

- Pinned Python packages: `pyyaml==6.0.2`, `snakemake==7.32.4`
- System packages: `sra-toolkit`, `parallel`, `gzip`, `pigz`
- Pinned base image: `python:3.11.9-slim`

## Outputs & Diagnostics

- Results (`/work/results`):
  - `runs/<run_id>/` (this run's expected-sample manifest and outcome; see
    [What a Run Claims to Have Done](#what-a-run-claims-to-have-done)), and
    `runs/LATEST`
  - `success/SRRxxxxxx/` (renamed FASTQ, plus `cellranger_output/outs/...` on
    success and `.genoar_cellranger.json`, the receipt)
  - `retry_failed/SRRxxxxxx/` (staged for retry after step 9)
  - `fastq_success.txt`, `fastq_failed.txt`
  - `success/fastq_[1-4]_files.tsv`
  - `success/cellranger_ineligible.tsv` (step 6's census, appended to by step 7)
  - `success/cellranger_eligibility.tsv` (step 8's full census, every sample)
- Logs (`/work/logs`): `stepN_*.log`
- Reports (`/work/results/reports`):
  - `stepN.ok` — the step did its work
  - `stepN.err` — the step failed
  - `stepN.warn` — the step completed without doing its work (step 8 with zero
    eligible samples). Written *instead of* `stepN.ok`, so anything checking for
    `.ok` correctly finds nothing
  - `stepN.msg` (the reason, beside a `.warn` or `.err`)
  - `stepN_failed.txt` (failed items for that step)
  - `summary.jsonl` (per-step status summary)
- For detailed error categories and recovery, see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Design Changes vs. the Original Pipeline

- Orchestration: multi-step scripts + SLURM → single entrypoint + `config.yaml`
- Parallelization: SLURM arrays → GNU Parallel (steps 2–3), Snakemake local mode (step 8)
- Logging / classification: SLURM log parsing → run-scoped provenance/outcome validation + standard logs/reports
- Packaging: host-dependent → version-pinned Docker image
- Cell Ranger: not bundled in the image (licensing) → host-mounted at `/opt/cellranger`, reference at `/ref`

## Technical Overview

- Multi-stage Dockerfile: incremental steps 1…9; `step9` carries both the
  maintained `/pipeline_next` tree and the frozen `/pipeline` comparison tree
- `/pipeline_entry.sh`: selects `/pipeline_next` by default; the frozen tree is
  available only through explicit `legacy_pipeline: true`
- `/pipeline_next/run_docker_pipeline.sh`: loads config → runs steps 1–9 sequentially and records each step plus the run outcome
- Step scripts use GNU Parallel / pigz; failed items accumulate in `reports/stepN_failed.txt`

## Migration Cheat Sheet

| Original | Dockerized location |
|----------|----------------------|
| `make_directories.sh` | `/pipeline_next/make_directories.sh` |
| `slurm_fastq_submit.sh` → `fastq_dump_job.sh` | `/pipeline_next/fastq_dump_parallel.sh` |
| `slurm_gzip_submit.sh` → `fastq_gz_job.sh` | `/pipeline_next/fastq_gzip_parallel.sh` |
| `parse_fastq_logs.py` | `/pipeline_next/parse_fastq_logs.py` |
| `move_success_dirs.sh` | `/pipeline_next/move_success_dirs.sh` |
| `check_counts.sh` | `/pipeline_next/check_counts.sh` |
| `rename_fastq.sh` | `/pipeline_next/rename_fastq.sh` |
| `run_cellranger_hs.sh` / `Snakefile_hs.smk` | `/pipeline_next/Snakefile_hs.smk` (local mode) |
| `rename_move_failed_fastqs.py` | `/pipeline_next/rename_move_failed_fastqs.py` |

## Limitations & Next Steps

- Large-scale performance and capacity still need validation against each site's scheduler, quota and storage policy
- The generated HPC handoff is fail-closed, but it has not yet been exercised on every supported scheduler/site combination
- Partial execution: evaluate `--from-step` / `--to-step` flags
