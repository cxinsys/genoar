# GENOAR Cycle Test

[Korean / 한국어](README.ko.md)

An integrated test framework that runs **Stage 1 → Stage 2 → Stage 3** end-to-end in a single command.

## Overview

Cycle Test chains the three GENOAR pipelines sequentially:

```
Stage 1 (Crawler)     →     Stage 2 (Analysis)     →     Stage 3 (SRR Pipeline)
  NCBI GEO scraping         UMLS concept matching        SRA download + Cell Ranger
  META / SMTX / SRR         Annotated CSV tables         scRNA-seq processing
```

---

## 1. Prerequisites

### Scope

- **Archive:** only **NCBI SRA** runs (SRR-prefixed accessions) are processed. ENA (ERR) and DDBJ (DRR) accessions are **filtered out** in both Stage 2 → Stage 3 handoff and the downloader.
- **Organism / assay:** Stage 2 retains Homo sapiens + TRANSCRIPTOMIC samples only.
- **Stage 3 chemistry:** 10x Genomics single-cell RNA-seq via Cell Ranger.

### System Requirements

| Item | Minimum | Recommended |
|------|---------|-------------|
| CPU | 8 cores | 16+ cores |
| RAM | 64 GB | 128 GB+ |
| Disk | 100 GB | 500 GB+ |
| OS | Linux (Ubuntu 20.04+) | Linux (Ubuntu 22.04) |
| Architecture | **AMD64 required for Stage 3** | Linux / Windows x86_64 |

> **Platform note.** Stages 1 and 2 run on any architecture (including Apple Silicon ARM64 Macs — use `Dockerfile.arm64` for the crawler). **Stage 3 is AMD64-only** because Cell Ranger is not distributed for ARM. Run `--run-stage3` on a Linux or Windows x86_64 server; ARM Macs can still exercise the Stages 1+2 path locally.

### Docker host requirements (Stage 1)

The crawler container runs headless Chrome and needs these privileges on the host:

- `--cap-add=SYS_ADMIN`
- `--security-opt seccomp=unconfined`
- `--user root` inside the container

These are applied automatically by `stage1_runner.py`. **Hardened hosts that forbid these flags cannot run Stage 1** — either relax the policy or run the crawler on a machine that allows them.

### Software

```bash
docker --version    # Docker 20.10+
python3 --version   # Python 3.9+
```

### Docker Images

Build the required images before running the cycle test. The Stage 3 image is only needed when you plan to pass `--run-stage3` (AMD64 hosts only).

```bash
cd /path/to/genoar

# Stage 1: GEO Crawler (required)
#   AMD64 (Linux / Windows / Intel Mac):
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/
#   ARM64 (Apple Silicon Mac):
# docker build -t genoar-crawler:arm64 -f genoar_crawler/Dockerfile.arm64 genoar_crawler/

# Stage 2: UMLS Analysis (required, any architecture)
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

# Stage 3: SRR Pipeline (required for --run-stage3; AMD64 only)
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .

# Verify
docker images | grep genoar
# Expected:
#   genoar-crawler    amd64    ...   ~1.2 GB
#   genoar-analysis   latest   ...   ~500 MB
#   genoar-srr        step9    ...   ~700 MB
```

---

## 2. Environment Setup

### 2.1 Cell Ranger (Stage 3 only)

Download Cell Ranger from the [10x Genomics website](https://www.10xgenomics.com/support/software/cell-ranger/downloads), then extract it into the project root as `cellranger/`:

```bash
tar -xzvf cellranger-8.0.1.tar.gz
mv cellranger-8.0.1 cellranger    # becomes <genoar>/cellranger/
```

Auto-detection expects `<genoar>/cellranger/cellranger` (the binary). If you installed elsewhere, pass `--cellranger-path /custom/path`.

### 2.2 Reference genome (Stage 3 only)

Download the 10x Genomics human reference and extract it as `ref/` at the project root:

```bash
wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz
tar -xzvf refdata-gex-GRCh38-2024-A.tar.gz
mv refdata-gex-GRCh38-2024-A ref  # becomes <genoar>/ref/

ls ref/
# Expected: fasta/  genes/  reference.json  star/
```

Auto-detection expects `<genoar>/ref/`. Override with `--ref-genome-path /custom/path` if needed.

### 2.3 UMLS data (Stage 2 required input)

**Included.** The three UMLS tables are in the repository under `<genoar>/all_query_results/` and work out of the box:

```bash
ls all_query_results/umls_*_df.csv
# umls_celltype_df.csv  umls_disease_df.csv  umls_tissue_df.csv
```

To extend or regenerate the concept set for your own research, see [UMLS Reference Data](../README.md#umls-reference-data) in the main README.

`run_cycle_test.py` automatically discovers this directory at the project root and mounts it into the Stage 2 container — no flags required.

---

## 3. Running the Cycle Test

All commands below are run from the repository root. Examples use `--output-dir test_full_pipeline` consistently; the script's built-in default (when `--output-dir` is omitted) is `test_cycles`.

### 3.1 Dry run (inspect the plan without executing)

Always start here to confirm paths are detected correctly.

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --run-stage3 --dry-run
```

The log shows the resolved `Cell Ranger:` and `Ref Genome:` paths. If auto-detection fails, you'll see `(auto-detect failed — pass --cellranger-path)` and you must supply the flag explicitly.

> Dry-run does **not** create `--output-dir` or write a master log — the plan prints to stdout only. You can run it freely without leaving artifacts.

### 3.2 Stage 1 + 2 only (fast path, no Cell Ranger)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline
```

### 3.3 Full pipeline (Stage 1 → 2 → 3)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --run-stage3 \
  --cellranger-cores 8 --cellranger-mem 64
```

### 3.4 Small end-to-end smoke test

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --run-stage3 --max-samples 2
```

### 3.5 Stage 3 only (reuse existing Stage 2 output)

Point `--output-dir` at a directory that already contains completed `cycle_*/stage2/` results:

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --stage3-only \
  --output-dir test_full_pipeline
```

### 3.6 Download SRA only (skip Cell Ranger)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --run-stage3 --download-only \
  --output-dir test_full_pipeline
```

### 3.7 Reuse existing Stage 1 output (skip crawl)

`--skip-stage1` expects **pre-existing META files** at `<output-dir>/cycle_NNN_pages_.../stage1/META/*.txt`. The script now fails fast with a clear error if those are missing — pass a `--output-dir` that already holds a completed Stage 1 run:

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --skip-stage1
```

---

## 4. CLI Options

### General

| Option | Description | Default |
|--------|-------------|---------|
| `--cycles N` | Number of cycles to run | (required) |
| `--pages-per-cycle N` | GEO pages per cycle | 100 |
| `--start-page N` | Starting page number | 1 |
| `--wait-minutes N` | Wait time between cycles (min) | 10 |
| `--output-dir PATH` | Output root directory | `test_cycles` |
| `--dry-run` | Print execution plan only (no output dir / master log) | – |
| `--skip-stage1` | Skip Stage 1 — requires existing `stage1/META` in `--output-dir` | – |
| `--stage2-timeout SECS` | Stage 2 container timeout in seconds | 1800 |
| `--runtime {docker,singularity}` | Container runtime for the stages (Docker unchanged; Singularity uses `.sif` from `--sif-dir`) | `docker` |
| `--sif-dir PATH` | Directory holding `.sif` images when `--runtime singularity` | `.` |

### Stage 3

| Option | Description | Default |
|--------|-------------|---------|
| `--run-stage3` | Enable Stage 3 pipeline | `False` |
| `--stage3-only` | Run Stage 3 only | `False` |
| `--download-only` | Download SRA only (skip Cell Ranger) | `False` |
| `--max-samples N` | Max samples to download | all |
| `--max-downloads N` | Concurrent downloads | 4 |
| `--cellranger-path` | Cell Ranger install directory | auto-detected from project root |
| `--ref-genome-path` | Reference genome directory | auto-detected from project root |
| `--cellranger-cores N` | Cell Ranger CPU cores | 16 |
| `--cellranger-mem N` | Cell Ranger memory (GB) | 100 |
| `--stage3-hpc HPC_CONFIG` | Run Stage 3 on a remote HPC: generate a transfer, submit and retrieve handoff bundle from this `hpc_config.yaml` instead of running Cell Ranger locally | – |
| `--stage3-hpc-execute` | With `--stage3-hpc`, also run the handoff end-to-end over SSH (falls back to the bundle if SSH or the scheduler is unavailable) | `False` |

> Requesting more pages than GEO currently holds is not an error: the crawler caps the range, warns, and crawls what exists (Stage 1 status `capped`). A `--start-page` past the end has nothing to crawl at all, which ends the cycle as `no_data` — see [Cycle status and exit codes](#cycle-status-and-exit-codes).
> `make fetch-sra` is a separate route outside cycle_test: it reads the filtered Stage 2 tables written by `make run`, caches downloads in `sample_sra/`, and builds the exact input view used by `make run-fetched-stage3`. It is not connected to the `stage3/sra/` download described here. Direct `make run-stage3` remains the path for user-managed inputs already in `sample_sra/`.
> Auto-detection searches for `cellranger/` and `ref/` at the repository root. Override with the flags above if installed elsewhere.
> For HPC Stage 3 under Singularity, see [../srr_pipeline_package/hpc/README.md](../srr_pipeline_package/hpc/README.md) and [../srr_pipeline_package/singularity/README.md](../srr_pipeline_package/singularity/README.md).

---

## 5. Results

### Output layout

```
test_full_pipeline/
├── master_YYYYMMDD_HHMMSS.log        # Full execution log
├── final_report_*.json               # Aggregate report
│
└── cycle_001_pages_0001-0005/
    ├── stage1/
    │   ├── META/                     # GSE metadata
    │   ├── SMTX/                     # Series Matrix files
    │   └── SRR/                      # SRR ID lists
    │
    ├── stage2/
    │   ├── HS_cell_type_1st_pass_meta_table.csv
    │   ├── HS_tissue_1st_pass_meta_table.csv
    │   └── HS_disease_1st_pass_meta_table.csv
    │
    ├── stage3/
    │   ├── srr_list.txt              # Extracted SRR IDs
    │   ├── gse_srr_mapping.json      # GSE ↔ SRR map
    │   ├── sra/                      # Downloaded SRA files
    │   ├── results/success/          # Cell Ranger outputs
    │   └── logs/                     # Pipeline logs
    │
    ├── logs/                         # Per-stage logs
    └── summary.json                  # Cycle summary
```

### Cycle status and exit codes

A run ends in one of three states, not two: it did work, it correctly did nothing, or it failed. `summary.json` and `final_report_*.json` carry a `status` per cycle:

| Status | Meaning |
|--------|---------|
| `success` | Every requested stage did its work |
| `partial_success` | A stage completed with some samples or tables missing. For Stage 3, some of the samples the run was asked to process have **verified** Cell Ranger output and others do not; `partial_reason` says which |
| `no_data` | Valid and complete, but there was nothing to do; `no_data_reason` says what |
| `failed_stage1` | The crawl failed |
| `failed_stage3` | SRA download or Cell Ranger failed, the Cell Ranger install is unusable, or the run left no account of itself; `failed_reason` says which |
| `interrupted` | Ctrl+C reached the run mid-cycle |

Three fields carry the reason, one per status that has one. `no_data_reason` separates the ways nothing can happen: no page fell in the requested range, Stage 2's tables held no SRR accessions, or no sample qualified for Cell Ranger. `partial_reason` says how much of the expected work is there. `failed_reason` carries the failure text. All three are in `summary.json`. `final_report_*.json` repeats `no_data_reason` alone, and carries the full Stage 3 result under `stage3_pipeline` or `stage3_handoff`.

A missing or incomplete Cell Ranger install is never `no_data`. The runner checks that `cellranger/cellranger` is executable and that the reference holds `reference.json`, `fasta/`, `genes/` and `star/`. These are the same checks the pipeline makes at step 0. A failure there is a configuration fault to fix, so the cycle is `failed_stage3`.

#### The Stage 3 outcome decides the cycle status

Stage 3 grades its own run and records the grade under `stages.stage3_pipeline.status`. Each grade earns the cycle status that is true of it:

| Stage 3 outcome | Cycle status | What it means |
|-----------------|--------------|---------------|
| `success` | `success` | Every expected sample has verified Cell Ranger output |
| `partial_success` | `partial_success` | Some expected samples were analysed and others were not, both measurably |
| `nothing_processed` | `no_data` | A complete, valid run that analysed nothing |
| `config_error` | `failed_stage3` | Cell Ranger or its reference is unusable, so the run could not have analysed anything |
| `failed` | `failed_stage3` | The run broke, or its counts contradict each other, or it came back with no record of itself |

A grade the table does not list is `failed_stage3`. So is a result that names the `success` grade and denies success in the same breath, because the two cannot both hold.

A Stage 3 run that comes back with no usable record of itself is `failed_stage3`, not `partial_success`: it analysed nothing the cycle can point at, so `partial_success` would credit it with work no evidence supports. The same goes for a Stage 3 run that reports no success and claims no partial work either.

Stage 3 also decides FASTQ conversion failures. A run that succeeded overall with `failed_samples` above zero is `partial_success` when some samples succeeded and `failed_stage3` when none did. Samples that never became FASTQ produced no output, so they count.

#### A worse status wins

Stage 3's verdict may raise the cycle to a worse status. It never lowers one an earlier stage already set, so "Stage 3 correctly did nothing" cannot overwrite "Stage 2 broke". The order, from best to worst:

```
running < success < no_data < partial_success < failed_stage3 < failed_stage1 < interrupted
```

When a verdict is refused on these grounds the master log says so: `Cycle stays <status>: an earlier stage already reported a problem`.

#### Stage 3 on HPC

With `--stage3-hpc`, Stage 3 is graded from the exit code of `run_hpc_stage3.py` rather than from a local pipeline run, and `stages.stage3_handoff` carries the runner's own result. With `--stage3-hpc-execute`, `0` is `success`, `3` is `no_data`, `4` is `partial_success`, and `1` and `2` are `failed_stage3`. Without it the cycle asked for a transfer-ready bundle rather than an analysis, so writing the bundle is `success` and the analysis happens on the cluster afterwards. A bundle that was not written is `failed_stage3`, and `failed_reason` says why. Either way `stages.stage3_handoff.analysis_status` reports what the analysis achieved, and it is `not_attempted` when no job was run. See [../srr_pipeline_package/hpc/README.md](../srr_pipeline_package/hpc/README.md#what-the-runner-reports).

Stage 1 records its own outcome under `stages.stage1.status`:

| Status | Meaning |
|--------|---------|
| `success` | The whole requested range was crawled |
| `capped` | The range was trimmed to the pages GEO actually holds, and all of those were crawled |
| `no_pages_in_range` | Nothing fell in the requested range; the cycle becomes `no_data` |
| `interrupted` | Ctrl+C |
| `failed` | Anything else |

`capped` counts towards a `success` cycle — it is a complete crawl of every page that exists. `stages.stage1.capped_pages` records what the run covered.

The process exit code is coarser, for automation:

| Exit | Meaning |
|------|---------|
| `0` | Every cycle succeeded |
| `130` | Interrupted, including between cycles |
| `1` | Anything else |

`no_data` is deliberately a `1`. Work was asked for and did not happen, and a scheduled run should not keep producing empty cycles unnoticed. Which of the two it was is legible from the status, not from the code.

### Monitoring and inspection

Stage 1, Stage 2, **and Stage 3** stream Docker output line-by-line into the master log as the run progresses — no need to wait for Cell Ranger to finish before seeing progress.

```bash
# Live master log (covers all stages, including Cell Ranger progress)
tail -f test_full_pipeline/master_*.log

# Stage 3 Docker output persisted alongside the master log
tail -f test_full_pipeline/cycle_*/stage3/logs/docker_stdout.log

# Per-cycle summary
cat test_full_pipeline/cycle_001_pages_0001-0005/summary.json | python -m json.tool

# Aggregate report
cat test_full_pipeline/final_report_*.json | python -m json.tool

# Stage 2 tables
head test_full_pipeline/cycle_*/stage2/HS_tissue_1st_pass_meta_table.csv

# Stage 3 Cell Ranger outputs
ls test_full_pipeline/cycle_*/stage3/results/success/
open test_full_pipeline/cycle_*/stage3/results/success/*/cellranger_output/outs/web_summary.html
```

---

## 6. Expected Runtime

| Stage | Work | Typical duration |
|-------|------|------------------|
| Stage 1 | GEO crawl (5 pages) | 60–90 min |
| Stage 2 | UMLS matching | 1–2 min |
| Stage 3a | SRA download (per sample) | 3–10 min |
| Stage 3b | Cell Ranger (per sample) | 1–4 h |

**Small full-pipeline test (2 samples):** ~3–10 hours total.

> Cell Ranger runs are long — a cycle stuck "at Stage 3" for 30+ minutes is normal, not a hang. Check `test_full_pipeline/cycle_*/stage3/logs/docker_stdout.log` before interrupting.

---

## 7. Troubleshooting

Start with the exit code and the cycle `status`, not the logs — they tell you whether there is anything to diagnose. See [Cycle status and exit codes](#cycle-status-and-exit-codes).

### Docker images missing

```bash
docker images | grep genoar
# If any are missing, rebuild — see §1 Prerequisites.
```

### Stage 2 failure

```bash
ls all_query_results/umls_*_df.csv  # UMLS tables present?
cat test_full_pipeline/cycle_*/logs/stage2.log
```

### Stage 3 download failure

```bash
cat test_full_pipeline/cycle_*/stage3/sra/download_failed.txt
curl -I https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR000001/SRR000001
```

### Cell Ranger failure

```bash
cat test_full_pipeline/cycle_*/stage3/logs/docker_stderr.log

# Paths still resolve?
ls cellranger/cellranger
ls ref/fasta/ ref/genes/ ref/star/
```

### Auto-detect failed

If the dry-run log shows `(auto-detect failed — pass --cellranger-path)`, either:
- Install into the expected directories (`./cellranger/`, `./ref/`), or
- Always supply `--cellranger-path` / `--ref-genome-path` on the command line.

### Cycle finished but nothing was processed

Status `no_data`, not a failure. Read `no_data_reason` first:

```bash
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('status'), '-', d.get('no_data_reason'))" \
  test_full_pipeline/cycle_*/summary.json
```

A `partial_success` cycle carries `partial_reason` in the same place, and a `failed_stage3` cycle carries `failed_reason`.

For the Cell Ranger case, Stage 3 exits `3` and writes the census beside the results:

```bash
cat test_full_pipeline/cycle_*/stage3/results/success/cellranger_eligibility.tsv
cat test_full_pipeline/cycle_*/stage3/results/success/cellranger_ineligible.tsv
```

The usual reason is a sample with a single FASTQ file — Cell Ranger requires a paired R1/R2 read set, so it can never be processed.

Stage 3 measures itself against the samples *this cycle* handed it. Whatever else is on the results volume does not enter its verdict. Which samples those were, and what became of each, is in the run record:

```bash
cat test_full_pipeline/cycle_*/stage3/results/runs/<run_id>/outcome.json
```

Samples left over from earlier runs appear there under `other_samples_with_output`. They are preserved, and not counted towards this cycle. A Stage 3 run that leaves no `outcome.json` is reported as a failure. It made no statement about what it processed, and the results volume is persistent, so the output sitting there is output from earlier runs whether or not this run analysed anything. Two things produce a run with no record, and the report names both. The Stage 3 image predates run records, in which case rebuild it with `make build-srr` and run again. Or the run ended before it could write its record, in which case the container output in `docker_stdout.log` under this run's logs directory says how far it got.

The summary counts verified output only. Samples adopted with `GENOAR_ADOPT_PRIOR_RESULTS=1` are reported on their own line, `Adopted, unverified: N (not counted as completed)`, and listed in `cellranger_adopted_samples`. They count towards no cycle verdict. A cycle whose only Stage 3 completion is adopted analysed nothing and says so.

---

## 8. Directory Structure

```
cycle_test/
├── run_cycle_test.py           # Main entry point
├── utils/
│   ├── stage1_runner.py        # Stage 1 Docker launcher
│   ├── stage2_runner.py        # Stage 2 Docker launcher
│   ├── stage3_preparer.py      # Stage 3 SRR extraction
│   ├── stage3_downloader.py    # SRA download
│   ├── stage3_runner.py        # Stage 3 Docker launcher (auto-detect logic lives here)
│   └── report_generator.py     # summary.json / final_report_*.json / console summary
├── README.md                   # This file (English)
├── README.ko.md                # Korean version
├── TUTORIAL.md                 # Guided first run (English)
└── TUTORIAL.ko.md              # Guided first run (Korean)
```
