# GENOAR

An integrated pipeline for automated NCBI GEO data collection, UMLS concept matching, and single-cell RNA-seq processing via Cell Ranger.

[Korean / 한국어](README.ko.md)

## Pipeline Overview

GENOAR consists of three sequential stages:

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                          GENOAR End-to-End Pipeline                              │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  [Stage 1: Crawler]          [Stage 2: Analysis]         [Stage 3: SRR Pipeline] │
│   ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐    │
│   │  NCBI GEO       │         │  UMLS Matching  │         │  Cell Ranger    │    │
│   │  Web Scraping   │────────>│  & Annotation   │────────>│  Processing     │    │
│   │                 │         │                 │         │                 │    │
│   └─────────────────┘         └─────────────────┘         └─────────────────┘    │
│          │                           │                           │               │
│          ▼                           ▼                           ▼               │
│   ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐    │
│   │ crawl_output/   │         │first_pass_output│         │ results/success │    │
│   │ • META/*.txt    │         │ • cell_type.csv │         │ • BAM files     │    │
│   │ • SMTX/*.gz     │         │ • tissue.csv    │         │ • Gene matrix   │    │
│   │ • SRR/*.txt     │         │ • disease.csv   │         │ • Clusters      │    │
│   └─────────────────┘         └─────────────────┘         └─────────────────┘    │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

| Stage | Component | Input | Output | Analysis |
|-------|-----------|-------|--------|----------|
| 1 | genoar_crawler | NCBI GEO search queries | META, SMTX, SRR files | Single-cell dataset detection |
| 2 | genoar_analysis | META files + UMLS data | Annotated metadata CSVs | Biomedical concept mapping |
| 3 | srr_pipeline_package | SRA files + Reference genome | Cell Ranger results | Gene expression analysis |

**Current end-to-end scope:** the automated public path is human (`Homo
sapiens`) transcriptomic data throughout, and Stage 3 is configured for a
GRCh38 reference. Other organisms require manual or custom changes and are not
supported by this end-to-end path.

### Where the pipeline splits

The three stages have separate prerequisites and an explicit handoff. Stages 1
and 2 run together on any machine with Docker, from a single `make run`. Stage 3
requires Cell Ranger, GRCh38 and downloaded SRA input. You can run each command
separately, or use `make run-full` to chain them with the bounded download
described below:

| Stage 3 needs | How you get it |
|---------------|----------------|
| `.sra` files | `make fetch-sra` caches the human transcriptomic accessions selected in Stage 2 and builds an exact input view for `make run-fetched-stage3` |
| Cell Ranger in `cellranger/` | Manual download from 10x Genomics (registration required) |
| A reference genome in `ref/` | Manual download, ~15GB |
| `srr_pipeline_package/configs/example.yaml` | Ships with the repository; edit the core count and paths |
| An x86_64 machine | Cell Ranger is not distributed for ARM |

`make run-stage3` and `make run-fetched-stage3` check all five before they start
and stop with the missing one named. It checks that they are usable. Presence
alone does not satisfy it. Either `cellranger/cellranger` or
`cellranger/bin/cellranger` has to exist and be executable, and `ref/` has to
hold a non-empty `reference.json` alongside `fasta/`, `genes/` and `star/`
directories. The architecture is checked first, because an x86_64 launcher is
present and executable on an ARM host as well and would only fail once the
container reaches step 0; set `GENOAR_ALLOW_NON_X86=1` to proceed anyway on a
host that emulates x86_64. The complete Cell Ranger installation root is
mounted, not just its launcher. The pipeline repeats both
checks inside the container as its step 0, so an install that one accepts the
other accepts too. `make run-full` runs `make run`, the bounded Stage 2 handoff
`make fetch-sra`, and then `make run-fetched-stage3`. It downloads two samples by
default. Cell Ranger and GRCh38 must already be installed, and the handoff needs
network access. Raising `MAX_SAMPLES` increases the download deliberately;
`MAX_SAMPLES=all` can require very large storage and transfer time.

See [Stage 3 Setup](#stage-3-setup-cell-ranger) for the commands.

---

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/cxinsys/genoar.git && cd genoar
cp .env.example .env         # Edit .env as needed

# 2. Initial setup (builds Docker image, checks system resources)
make setup

# 3. Run Stage 1+2 (crawl + analysis)
make run

# 4. Check results
make status
```

That is all of Stages 1+2. The pipeline runs in Docker and needs a network
connection; the only part that runs on the host is the summary printed when the
container exits, which uses the host's Python 3.9+ (see
[Prerequisites](#prerequisites)). The image `make setup` builds picks its browser
from the architecture it is built for (Google Chrome on amd64, Chromium on
arm64), so the same three commands work on an Apple Silicon Mac. See the
[platform note](#prerequisites) for the one Docker version requirement that
comes with it.

`make setup` hands the repository to Docker as a build context before it builds
anything. That context is well under 100MB: the RAG service's local database,
model cache and built frontend are excluded from it, so the "transferring
context" step passes quickly.

Continuing to Stage 3 means installing Cell Ranger and a reference genome first:
see [Stage 3 Setup](#stage-3-setup-cell-ranger), then
[Running with Make](#running-with-make).

---

## UMLS Reference Data

Stage 2 matches metadata strings against three UMLS (Unified Medical Language System) tables, one per field. **They are included in this repository under [`all_query_results/`](all_query_results/)**, so the pipeline runs as-is.

| File | Description |
|------|-------------|
| `umls_celltype_df.csv` | Cell type concepts (CUI, STR, SAB, STY) |
| `umls_tissue_df.csv` | Tissue concepts |
| `umls_disease_df.csv` | Disease concepts |

Each row is one English name (`STR`) of one concept (`CUI`) from one source vocabulary (`SAB`), with the concept's semantic type (`STY`). The tables were queried from the [UMLS Metathesaurus](https://www.nlm.nih.gov/research/umls/index.html) (release 2024AB) through the UTS REST API and processed for the pipeline: they cover the cell-type, tissue and disease concepts Stage 2 needs, not the Metathesaurus. See [Data Sources and Acknowledgements](#data-sources-and-acknowledgements) for the UMLS notice.

### Extending the Reference Set (Optional)

To re-query or expand the concept coverage for your own research, regenerate the CSVs yourself:

1. Create a free UMLS account at [UTS (UMLS Terminology Services)](https://uts.nlm.nih.gov/uts/).
2. Query concepts via the [UMLS REST API](https://documentation.uts.nlm.nih.gov/rest/home.html) or MetamorphoSys.
3. Export as CSV preserving the `CUI`, `STR`, `SAB`, `STY` columns.
4. Replace the files in `all_query_results/`, and note the UMLS release you used.

### Verification

```bash
ls all_query_results/umls_*_df.csv
# Expected: umls_celltype_df.csv  umls_disease_df.csv  umls_tissue_df.csv
```

> When running via `cycle_test/run_cycle_test.py`, the `all_query_results/` directory at the project root is automatically discovered and mounted into the Stage 2 container — no additional flags required.

---

## Configuration

### Environment Variables (.env)

Copy and edit the example file:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `PAGES` | 100 | Number of GEO pages to crawl |
| `WORKERS` | 1 | Parallel crawl workers |
| `SELENIUM_HEADLESS` | true | Chrome headless mode |
| `MODE` | complete | Execution mode: `complete`, `crawl`, `analyze`, `test` |
| `LOG_LEVEL` | INFO | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MAX_SAMPLES` | 2 | SRA files `make fetch-sra` downloads, or `all` |
| `MAX_CONCURRENT` | 4 | Parallel downloads for `make fetch-sra` |
| `SRA_SOURCE` | stage2 | Accession source for `make fetch-sra`; use `raw-stage1` only as an explicit unfiltered opt-in |
| `CPU_LIMIT` | 4 | Docker CPU core limit |
| `MEMORY_LIMIT` | 8G | Docker memory limit |
| `COMPOSE_PROJECT_NAME` | genoar | The Compose project this run belongs to. The project owns its containers, its network **and its result directories**. The default project writes to the checkout root. Any other project name writes under `projects/<name>/` |

Both Docker Compose and the Makefile read `.env`, so every line has to be a plain
unquoted `KEY=value` and every comment a line of its own — Make cannot parse
anything else, a line it chokes on breaks every `make` invocation, and a comment
placed after a value is read as part of that value, trailing spaces included. Values given on the command line still win:
`make run PAGES=50` overrides whatever `.env` says.

Requesting more pages than GEO currently holds is not an error. The crawler
detects the real total, caps the range, warns, and crawls what exists.

`WORKERS` defaults to `1` in both the Makefile and `docker-compose.yml`. Each
worker writes into its own directory under
`crawl_output/runs/<run_id>/workers/worker-<n>/`, so that the output directory,
`checkpoint.json`, `crawl_manifest.json` and the browser download area are never
shared -- sharing them costs results and completion evidence. A single aggregator
writes the whole-range manifest after every worker has exited. One worker is the
default, because a single crawler needs none of that machinery and is the path the
pipeline is exercised on. Raise `WORKERS` deliberately. See
[Parallel crawling](genoar_crawler/README.md#parallel-crawling) for the layout you
get. `.env.example` ships `WORKERS=1` too, so copying it changes nothing. A value
you set there does win over the built-in default, and the command line wins over both.

`COMPOSE_PROJECT_NAME` names the Compose project, and with it this run's container
(`<project>-genoar-<n>`), network (`<project>_genoar-network`) and results. The
default project writes `crawl_output/`, `first_pass_output/`, `logs/` and
`workflow_output/` in the checkout root, as it always has. Any other project writes
the same four under `projects/<name>/`. Two pipelines can therefore run on one host
at once. Neither can overwrite the other's run directories, manifests or analysis
tables.

```bash
make run                              # project genoar  -> ./crawl_output/ etc.
make run COMPOSE_PROJECT_NAME=second  # project second  -> ./projects/second/
```

**The targets that read those directories need the same name.** Without it they
report the default project's results as this one's:

```bash
make status COMPOSE_PROJECT_NAME=second
make logs   COMPOSE_PROJECT_NAME=second
make clean  COMPOSE_PROJECT_NAME=second
```

**A second run of the same project name is refused.** It would share the first
run's container and its whole output tree, and Compose would recreate the running
container. `make run` reports this and exits non-zero.

**Stage 3 is not scoped this way.** `make run-stage3` reads `sample_sra/` and
writes `results/` and `logs/` in the checkout root whatever the project is called.
`make fetch-sra` caches downloads in that same `sample_sra/` and builds its
current selection under `sample_sra/.genoar_selected/`. Stage 3 therefore runs
one at a time per checkout, however many Stage 1+2 projects are running.

Set it in `.env` or on the command line as you would `PAGES`. Compose reads `.env`
for itself, so the two cannot drift apart.

### Stage 3 Configuration

Edit `srr_pipeline_package/configs/example.yaml` for Cell Ranger pipeline settings (core count, paths, etc.).

---

## Full Execution

### Prerequisites

| Item | Minimum | Recommended |
|------|---------|-------------|
| CPU | 8 cores | 16+ cores |
| RAM | 64GB | 128GB+ |
| Disk | 100GB | 500GB+ |
| OS | Linux (Ubuntu 20.04+) | Linux (Ubuntu 22.04) |
| Architecture | **AMD64 required for Stage 3** (Linux / Windows) | — |

Software: Docker 23+ (see the BuildKit note below), Python 3.9+

> **Platform note.** Stage 1 (crawler) and Stage 2 (analysis) run on both architectures. The root `Dockerfile` is the image behind `make setup`, `make run` and `docker compose up`. It installs **Google Chrome on amd64** and **Chromium plus `chromium-driver` on arm64**, and points the crawler at whichever it installed. It selects that stage from the build's `TARGETARCH`, which **requires BuildKit**. Docker 23 and later enable BuildKit by default. On anything older you have to enable it yourself (`DOCKER_BUILDKIT=1`), or the build fails while resolving the stage name. The standalone `genoar_crawler/Dockerfile.amd64` and `Dockerfile.arm64` still exist for running the crawler on its own. **Stage 3 is AMD64-only** because Cell Ranger is not distributed for ARM. Plan to run Stage 3 on a Linux or Windows x86_64 server. ARM Macs can still develop and validate Stages 1+2 locally.

### Stage 3 Setup (Cell Ranger)

**Cell Ranger** (required for Stage 3):

```bash
# Download from https://www.10xgenomics.com/support/software/cell-ranger/downloads
tar -xzvf cellranger-8.0.1.tar.gz
mv cellranger-8.0.1 cellranger/
```

**Reference genome** (required for Stage 3):

```bash
wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz
tar -xzvf refdata-gex-GRCh38-2024-A.tar.gz
mv refdata-gex-GRCh38-2024-A ref/
```

#### Alternative: STARsolo

Cell Ranger is only distributed for Linux x86_64, which can be a barrier for
groups without a matching machine. [STARsolo](https://github.com/alexdobin/STAR/blob/master/docs/STARsolo.md)
is a widely used alternative that produces comparable count matrices from the
same 10x reads and builds on a broader range of platforms.

GENOAR's Stage 3 is built around Cell Ranger and does not ship a STARsolo path,
so using it means running the quantification step yourself and supplying the
resulting matrices. It is noted here for groups whose compute environment cannot
accommodate Cell Ranger; where a Linux x86_64 cluster is available, the bundled
Cell Ranger pipeline stays the supported route.

### Running with Make

Stages 1+2 need nothing beyond `make setup`:

```bash
# Crawl + analyse
make run

# Custom parameters
make run PAGES=50

# Follow the running pipeline's log
make logs

# Summarise what is on disk
make status
```

Stage 3 is the separate half. `make fetch-sra` is the bridge between them: it
extracts and de-duplicates SRR accessions from Stage 2's quality-filtered
`first_pass_output/HS_*_1st_pass_meta_table.csv` tables, then downloads those
runs into the `sample_sra/` cache and builds `sample_sra/.genoar_selected/`
from exactly that bounded selection. Older raw or custom downloads remain in
the cache but are not passed to `make run-fetched-stage3`. If Stage 2 output is
absent or contains no eligible SRR accessions, the handoff stops; it never
silently falls back to the raw crawl.

```bash
# Download Stage 2's eligible SRR accessions (2 samples by default)
make fetch-sra

# See what it would download, without downloading
make fetch-sra DRY_RUN=1

# More samples, more parallelism
make fetch-sra MAX_SAMPLES=10 MAX_CONCURRENT=8

# Everything Stage 2 selected - this can be a very large download
make fetch-sra MAX_SAMPLES=all

# Optional, unfiltered diagnostic/custom path: use raw Stage 1 lists explicitly
make fetch-sra SRA_SOURCE=raw-stage1 DRY_RUN=1

# Build the Stage 3 image, then run it
make build-srr
make run-fetched-stage3

# Alternatively, process all user-managed inputs currently in sample_sra/
make run-stage3

# Stage 1+2, a bounded 2-sample download, then Stage 3
# (requires network access plus Cell Ranger and GRCh38 already installed)
make run-full
```

`make fetch-sra` is deliberately capped because SRA downloads can run to tens
of GB per sample. `make run-full` keeps that two-sample cap unless you pass a
different `MAX_SAMPLES`; using `MAX_SAMPLES=all` is an explicit request to lift
it. To process `.sra` files you already manage in `sample_sra/`, skip the fetch
and call `make run-stage3` directly. `make run-fetched-stage3` is the integrated
path because it mounts only the latest selection, not every cached download.

### Running with Docker Compose

```bash
# Stage 1+2
docker compose -p genoar up

# With custom parameters
PAGES=50 docker compose -p genoar up
```

`-p` names the project. It is what `make run` passes, and what keeps a second run
from recreating the first one's container.

`-p` does not carry the results. The bind mounts hang off `GENOAR_OUTPUT_ROOT`, and
`make run` derives that from the project name. Compose called by hand defaults it
to `.`, the checkout root, so a second project started this way writes into the
first project's directories. Set it yourself:

```bash
GENOAR_OUTPUT_ROOT=./projects/second docker compose -p second up
```

### Watching a Run

The pipeline container has no fixed name. `docker logs genoar-pipeline` used to
work because the service pinned that name. A container name is host-global. It is
not scoped to the project. A second `docker compose up` therefore recreated the
first run's container and never started one of its own, and a stopped container of
that name blocked the next run outright. Compose names the container after the
project now. Ask Compose for the name:

```bash
make logs                                  # follow this project's pipeline log
docker compose -p genoar logs -f genoar    # the same command, spelled out
docker compose -p genoar ps                # the container this project has
```

`make logs` follows whichever project it is given, so a second run is
`make logs COMPOSE_PROJECT_NAME=second`. Ctrl+C stops watching. The run continues.

A parallel crawl started from `genoar_crawler/` is different again. Its workers are
containers of their own, named `genoar-worker-<run_id>-<n>` and labelled
`genoar.run=<run_id>`. Select them by that label with
`docker ps --filter label=genoar.run=<run_id>`. Never select them by
`--filter name=`. That matches substrings and so reaches other people's runs. See
[Parallel crawling](genoar_crawler/README.md#parallel-crawling).

> **Upgrading an existing checkout.** On the first `make run` after this change,
> Compose recognises a leftover `genoar-pipeline` container by its own labels and
> replaces it with `<project>-genoar-1`. That is a one-time migration and costs
> nothing. The old globally named `genoar-network` goes with `make docker-clean`.

### Running with Cycle Test

> **Prerequisites:** Build the required Docker images first:
> ```bash
> # Stage 1 crawler image (required)
> docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/
>
> # Stage 2 analysis image (required)
> docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .
>
> # Stage 3 SRR pipeline image (required only when using --run-stage3)
> docker build -f srr_pipeline_package/docker/Dockerfile --target step9 \
>   -t genoar-srr:step9 .
> ```
>
> See [cycle_test/README.md](cycle_test/README.md) for the full English walkthrough.

```bash
# Basic test (Stage 1+2)
python cycle_test/run_cycle_test.py --cycles 1 --pages-per-cycle 5

# Full pipeline (Stage 1+2+3)
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --run-stage3 --cellranger-cores 8 --cellranger-mem 64

# Dry run (plan only)
python cycle_test/run_cycle_test.py --cycles 1 --run-stage3 --dry-run
```

### Running Stage 3 on HPC (Singularity)

Same codebase and same results. Only the run method differs. HPC centers that
disallow Docker run Stage 3 (Cell Ranger) with Singularity/Apptainer. The one
extra step is a one-time conversion of the Docker image to a `.sif`.

```bash
# 1. Convert the Docker image to a .sif (once, on a Linux host with Singularity/Apptainer)
srr_pipeline_package/singularity/build_sif.sh --from daemon --image genoar-srr:step9

# 2a. Run directly with Singularity (mirrors `make run-stage3`)
srr_pipeline_package/singularity/run_singularity_pipeline.sh \
  --sif genoar-srr_step9.sif --sra ./sample_sra/.genoar_selected --config <config.yaml> \
  --ref ./ref --cellranger ./cellranger

# 2b. Or via cycle_test, using the same command with a runtime flag
python cycle_test/run_cycle_test.py --cycles 1 --run-stage3 --runtime singularity --sif-dir /path/to/sifs
```

The direct command uses the exact bounded selection made by `make fetch-sra`.
If you are supplying and managing every input yourself instead, pass
`--sra ./sample_sra`.

To run Stage 3 on a remote HPC (upload, submit, poll, retrieve), fill in an
`hpc_config.yaml` and use the handoff:

```bash
# semi-auto: generate the transfer/submit/retrieve bundle
python cycle_test/run_cycle_test.py --cycles 1 --run-stage3 --stage3-hpc hpc_config.yaml
# full-auto over SSH (falls back to the bundle if SSH or the scheduler is unavailable)
python cycle_test/run_cycle_test.py --cycles 1 --run-stage3 --stage3-hpc hpc_config.yaml --stage3-hpc-execute
```

`hpc_config.yaml` is the only file to edit on this path — the pipeline config
the bundle carries is generated from it. A corpus too large for one job is
split first with `plan_batches.py` and submitted as one array; see
[hpc/README.md](srr_pipeline_package/hpc/README.md#running-a-corpus).

`scheduler` in `hpc_config.yaml` selects the dialect. It takes `slurm`, `pbspro`
or `torque`, and it defaults to `slurm` when the key is absent. Under Slurm the
handoff submits with `sbatch --test-only` and then `sbatch`, polls `squeue`, and
reads the final state, the elapsed time and the peak memory from `sacct`. Under
PBS it submits with `qsub`, polls `qstat`, and reads the final state from
`qstat -x -f`.

Each bundle names its run, and the pipeline refuses a run id that already has a
record. Submitting one bundle twice fails on the second attempt. Generate a new
bundle for a new attempt.

Stages hand off via files, so runtimes can be mixed. For example, run the crawler
and analysis on Docker locally and Cell Ranger on Singularity on the HPC. See
[singularity/](srr_pipeline_package/singularity/README.md) and
[hpc/](srr_pipeline_package/hpc/README.md).

---

## CLI Options

### General Options

| Option | Description | Default |
|--------|-------------|---------|
| `--cycles N` | Number of cycles to run | (required) |
| `--pages-per-cycle N` | GEO pages per cycle | 100 |
| `--start-page N` | Starting page number | 1 |
| `--wait-minutes N` | Wait time between cycles (min) | 10 |
| `--output-dir PATH` | Output directory | test_cycles |
| `--dry-run` | Print execution plan only | - |
| `--skip-stage1` | Skip Stage 1 (use existing data) | - |

### Stage 3 Options

| Option | Description | Default |
|--------|-------------|---------|
| `--run-stage3` | Enable Stage 3 pipeline | False |
| `--stage3-only` | Run Stage 3 only | False |
| `--download-only` | Download SRA only (skip Cell Ranger) | False |
| `--max-samples N` | Max samples to download | all |
| `--max-downloads N` | Concurrent downloads | 4 |
| `--cellranger-path` | Cell Ranger install path | `./cellranger/` (auto-detected) |
| `--ref-genome-path` | Reference genome path | `./ref/` (auto-detected) |
| `--cellranger-cores N` | Cell Ranger CPU cores | 16 |
| `--cellranger-mem N` | Cell Ranger memory (GB) | 100 |

> Auto-detection looks for `cellranger/` and `ref/` at the project root (matching the `Stage 3 Setup` layout above). Override with the flags if installed elsewhere. If detection fails, the dry-run log prints `(auto-detect failed — pass --cellranger-path)` so you can spot it before a real run.

---

## Expected Runtime

| Stage | Work | Typical duration |
|-------|------|------------------|
| Stage 1 | GEO crawl (5 pages) | 60–90 min |
| Stage 2 | UMLS matching | 1–2 min |
| Stage 3a | SRA download (per sample) | 3–10 min |
| Stage 3b | Cell Ranger (per sample) | 1–4 h |

A small end-to-end test (2 samples, full pipeline) typically takes ~3–10 hours. Cell Ranger phases are long — a cycle quiet "at Stage 3" for 30+ minutes is normal; check `cycle_*/stage3/logs/docker_stdout.log` before interrupting.

---

## Results

### Output Directory Structure

```
test_full_pipeline/
├── master_YYYYMMDD_HHMMSS.log        # Execution log
├── final_report_*.json               # Final report
└── cycle_001_pages_0001-0005/
    ├── stage1/                        # Stage 1: crawled data
    │   ├── META/                      # GSE metadata
    │   ├── SMTX/                      # Series Matrix files
    │   └── SRR/                       # SRR ID lists
    ├── stage2/                        # Stage 2: annotated tables
    │   ├── HS_cell_type_1st_pass_meta_table.csv
    │   ├── HS_tissue_1st_pass_meta_table.csv
    │   └── HS_disease_1st_pass_meta_table.csv
    ├── stage3/                        # Stage 3: Cell Ranger output
    │   ├── sra/                       # Downloaded SRA files
    │   ├── results/success/           # Successful Cell Ranger runs
    │   └── logs/                      # Pipeline logs
    └── summary.json                   # Cycle summary
```

### Checking Results

```bash
# Monitor logs
tail -f test_full_pipeline/master_*.log

# View cycle summary
cat test_full_pipeline/cycle_*/summary.json | python -m json.tool

# Check Stage 2 tables
head test_full_pipeline/cycle_*/stage2/HS_tissue_1st_pass_meta_table.csv

# Check Stage 3 Cell Ranger output
ls test_full_pipeline/cycle_*/stage3/results/success/
```

---

## Run Ids and Run Records

A parallel crawl and a Stage 3 run each happen under a run id and keep their
record under it. A crawl uses `crawl_output/runs/<id>/`. Stage 3 uses
`results/runs/<id>/`. Left alone, every run mints a timestamped id of its own.
Name one yourself with `GENOAR_RUN_ID`:

```bash
cd genoar_crawler
GENOAR_RUN_ID=pilot-2026-08 ./run_parallel_crawl.sh 4 40
```

The id becomes a directory name, so it has to be usable as one:
`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. That is a letter or digit first, then
letters, digits, `.`, `_` or `-`, at most 64 characters. Surrounding whitespace is
stripped, and an id that is unset, empty, or nothing but whitespace all mean "no
id was given", so one is minted. Whitespace *inside* an id is refused. It is never
quietly turned into a different id than the one you set. `LATEST` is reserved,
because Stage 3 keeps `runs/LATEST` as its pointer to the newest run. The
comparison is case-sensitive, so `latest` is still yours to use. A violation exits
`2` before anything is created.

**A run record is never overwritten.** An id whose `crawl_output/runs/<id>/run.json`
exists already cannot be reused. That file says what that run set out to do and is
what the aggregator judges it against, so the launcher exits `2` and leaves the
record where it is. Stage 3 refuses an id that already has a `results/runs/<id>/`
directory in the same way, and for the same reason.

**The id is reserved atomically**, in both halves of the pipeline. Each claims the
run directory with a single `mkdir`. The kernel serialises that call. Start two runs
on one id at the same instant and one proceeds. The other exits `2`.

Exit `1` is a separate answer. It means the run directory could not be created at
all: a read-only tree, a full disk, a mount that went away. It explicitly does
**not** mean the id is taken. Both halves say which of the two happened. Sending you
to change a run id that was never the problem costs the whole search.

One command reuses an id, and only `run_parallel_crawl.sh` has it:

```bash
GENOAR_RUN_ID=pilot-2026-08 GENOAR_RESUME=1 ./run_parallel_crawl.sh 4 40
```

Each worker then continues from its own checkpoint. The original `run.json` stands
as written. A resume may not redefine what the run promised. Each continuation
appends a line to `runs/<id>/resumes.jsonl`.

**A resume is held to the plan it is entering.** Give it a different worker count or
a different end page and it exits `2`, naming both numbers. The page split is
computed from those two values, and the split is what binds a checkpoint to a page
range. Resumed under different ones, worker 2 would open the checkpoint it wrote for
pages 51-100 and continue it through pages 21-40, and the run's manifest would then
claim pages nobody crawled. GEO is not re-probed for the same reason. A larger
corpus today would move the same boundaries, so the plan's own split stands. Each
worker's recorded `assignment.json` is checked against the recomputed slice as well.
A resume that would move any worker off the pages it started is also refused.

That record has a limit. At run level you can tell the original run from its
resumes. A resumed worker overwrites its own `crawl_manifest.json` and `exit_code`
under `workers/worker-<n>/`, so which resume produced a given worker's slice is
not recorded. The manifest that stands covers the worker's whole assignment. Its
`completed_pages` is how much of the assignment is finished, and the pages before
the resume point were crawled by the invocations that wrote the checkpoint this
one continued. `crawled_pages` records the pages the last invocation walked, and
completion is never decided from it. `run_crawl.sh` and `run_docker_parallel.sh`
cannot resume at all. There, a fresh id is the only way forward.

Those two launchers start containers, and so also refuse an id whose containers are
still on the host. That covers a running container, and a stopped one from a run
that left no record. For the second case `GENOAR_RECLAIM_RUN=1` discards the
leftover containers so the id can be used again. It only ever removes containers,
never a run record, and only containers carrying that run's own label.

---

## Reading a Run's Outcome

A run has three possible outcomes, not two: it did work, it correctly did
nothing, or it failed. GENOAR keeps them apart everywhere, so that a crawl of a
page range GEO no longer has, or a Stage 3 run where no sample qualified for Cell
Ranger, is never reported as a success.

Codes `0`, `1`, `2` and `3` mean the same thing wherever they appear:

| Code | Meaning |
|------|---------|
| `0` | Real work was done |
| `1` | Failure |
| `2` | Invalid arguments, or an invalid page range |
| `3` | A valid, complete run that had nothing to do |
| `130` | Interrupted (Ctrl+C) |

Code `4` and the meaning of `2` differ by component, and the `make` targets do
not preserve any of it. What each entry point actually returns:

| Entry point | Codes it returns |
|-------------|------------------|
| `genoar_crawler.py` (one crawler or one worker) | `0` `1` `2` `3` `130` |
| `run_parallel_crawl.sh`, `run_docker_parallel.sh`, `run_crawl.sh` | `0` `1` `2` `3` `4`. `2` also covers a run id that is unusable, already used, or held by another run right now. `1` also covers a run directory that could not be created at all. `4` = every worker exited cleanly and *this run* collected no file |
| The pipeline container (`docker compose up`, `genoar:latest`) | `0` `1` `2` `3`. A parallel crawl's `4` is recorded as `3`, "nothing came out" |
| Stage 3 (`genoar-srr:step9`) | `0` `1` `2` `3` `4`. `2` = misconfigured install, or a run id it may not use. `1` also covers a run directory that could not be created at all. `4` = partial: this run cannot show verified output for every expected sample, and some expected sample carries output it accounts for, either verified or adopted |
| `run_cycle_test.py` | `0` all cycles succeeded, `130` interrupted, `1` anything else |
| `make run`, `make run-stage3`, `make run-full` | **`0` on success, non-zero on failure. The exact code is not passed through** |

`make run` runs Compose with `--abort-on-container-exit --exit-code-from genoar`,
so a container that fails fails the command. GNU Make replaces a failed
recipe's status with its own, so
`make` can only promise the success/failure distinction. The container's real
code is not lost. `make run` prints `the genoar container exited <n>` before
stopping, and Make's own `*** [...] Error <n>` line repeats it. **When you need
the exact code, in CI or in a scheduler, do not go through `make`. Call the script
or the container directly.**

What "nothing to do" (`3`) means depends on the stage:

- **Crawler.** No page fell in the requested range once it was capped to the
  pages GEO currently holds. Lower the start page or the worker count.
- **Parallel crawl.** No worker had a page to process. Exit `4` is the sharper
  warning. The workers ran fine and this run still collected nothing, which points
  upstream. The summary prints two counts. One is the files this run collected.
  The other is the files in the output directory across all runs. Only the first
  decides the exit status.
- **Stage 3.** No sample this run was asked to process reached Cell Ranger. A
  missing or unusable `cellranger/` or `ref/` is **not** this case. That is a
  configuration fault and exits `2` before any step runs.

`cycle_test` reports the same distinction per cycle rather than by exit code. A
cycle carries a status of `success`, `no_data`, `partial_success`,
`failed_stage1`, `failed_stage3`, or `interrupted`; a `no_data` cycle also
carries `no_data_reason` saying whether Stage 1 found no pages or Stage 3 found
no eligible sample. Stage 1 additionally records `capped` when the requested
range was trimmed to what GEO has — that is a complete crawl of every page that
exists, so the cycle still counts as a success. A Stage 3 that analysed some of
its expected samples and not others is `partial_success`. A Stage 3 that could
not run at all because the install is unusable is `failed_stage3`. It is never
`no_data`.

`no_data` is deliberately a `1`. It is never a `0`. Automation asked for work that
did not happen, and a scheduled job should not keep producing empty cycles
unnoticed.

---

## Troubleshooting

### Docker Images

```bash
# Check existing images
docker images | grep genoar

# Rebuild if missing
docker build -t genoar:latest .
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

### Stage 2 Failures

```bash
# Verify the UMLS tables are present
ls all_query_results/umls_*_df.csv

# Check logs
cat test_full_pipeline/cycle_*/logs/stage2.log
```

### Running Unit Tests in Docker

The suite runs on the host -- see [Tests](#tests). To run the Stage 2 tests
inside the analysis image instead, point them at a crawl you have; the
directory below is whatever `make run` wrote, not a fixture that ships.

```bash
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

docker run --rm \
  -v $(pwd)/crawl_output:/app/crawl_output:ro \
  -v $(pwd)/all_query_results:/app/all_query_results:ro \
  -e GENOAR_META_DIR=/app/crawl_output/META \
  -e GENOAR_UMLS_DIR=/app/all_query_results \
  --entrypoint python3 \
  genoar-analysis:latest \
  /app/genoar_analysis/genoar_analysis_tests/test_basic_modules.py
```

### Docker Build Errors (`.dockerignore`)

If Docker build fails with permission errors or takes too long, ensure `.dockerignore` excludes large directories:

```bash
# Required entries in .dockerignore
crawl_output/
first_pass_output/
workflow_output/
.chrome/
```

### Stage 3 Failures

```bash
# Check failed SRR downloads
cat test_full_pipeline/cycle_*/stage3/sra/download_failed.txt

# Verify Cell Ranger and the reference genome — exactly what step 0 checks
ls -l cellranger/cellranger cellranger/bin/cellranger 2>/dev/null
# one of those launchers must exist and be executable
ls ref/reference.json ; ls -d ref/fasta/ ref/genes/ ref/star/
```

A missing or incomplete install exits `2` before step 1, with the problems listed
one per line. It is a fault to fix. It is not an empty result.

A sample whose R1 and R2 come from different lanes cannot be analysed. It is
excluded before Snakemake starts, with its reason in the census. See
[Cell Ranger eligibility](srr_pipeline_package/README.md#cell-ranger-eligibility).

### Stage 3 Finished but Analysed Nothing

Stage 3 exits `3` and prints `CELL RANGER PROCESSED 0 SAMPLES` when the run was
valid and complete but none of the samples it was asked to process reached Cell
Ranger. The census that explains why is written under `results/success/`:

```bash
# Every sample, eligible or not, with the reason (written by step 8)
cat results/success/cellranger_eligibility.tsv

# Samples ruled out earlier, by step 6 and step 7
cat results/success/cellranger_ineligible.tsv
```

The usual reason is that a sample has only one FASTQ file: Cell Ranger needs a
paired R1/R2 read set, so a single-FASTQ run cannot be processed however well the
download went. Step 7 also refuses to rename a sample whose FASTQ layout it
cannot handle safely — it records the refusal rather than producing a mangled
pair — and step 8 skips anything with no R1/R2 pair.

Per-step outcomes are sentinel files in `results/reports/`: `step<N>.ok` when the
step did its work, `step<N>.err` when it failed, and `step<N>.warn` when it
completed without doing its work. A `.warn` is not a `.ok`; anything checking for
`.ok` will correctly not find one.

### What a Stage 3 Run Claims to Have Done

`results/` is persistent, so "a BAM is on disk" says nothing about which run
produced it. Every Stage 3 run therefore fixes the set of samples it was asked to
process before step 1 moves anything, and reports itself against that set alone:

```bash
cat results/runs/LATEST                        # the newest run's id
cat results/runs/<run_id>/expected_samples.tsv # what this run was asked to do
cat results/runs/<run_id>/outcome.json         # what it achieved, per sample
ls  results/runs/<run_id>/cellranger/          # the samples its Cell Ranger ran
```

Each sample this run produced or adopted also carries a receipt at
`results/success/<sample>/.genoar_cellranger.json` tying its BAM to the input it
came from and the run that made it. Output counts as this run's own work only when
the Cell Ranger rule left a completion record for that sample. That record is
`results/runs/<run_id>/cellranger/<sample>.json`. The job that ran Cell Ranger
writes it, naming the file it produced. That record is the only thing that makes a
sample **fresh**. A BAM newer than the run's start says nothing about which input
produced it.

Output that a receipt already vouches for, against the same input and the same
file, is reported as a **cache hit** when that receipt also records verified work.
The receipt carries `"provenance": "fresh"` only when the run that wrote it ran
Cell Ranger itself. That is a genuine resume. It counts as complete and never as
fresh work. A receipt that records an adoption instead is reported as **adopted**
again. A receipt that records neither says nothing this run can act on, and the
sample is **unverified**. Output with no completion record and no receipt is
reported as **unverified** too, and does **not** count. Re-run with
`GENOAR_ADOPT_PRIOR_RESULTS=1` to adopt it deliberately, which is recorded as an
adoption.

**Adoption never completes a run.** Adopted output is taken on your word,
with no evidence tying it to this run's input. The banner prints it on its own line,
`adopted, NOT verified and NOT counted as complete: N`. It is excluded from verified
completion and from the exit-0 calculation, so a run whose only completion is
adopted exits `4`. Samples belonging to other runs are listed, left alone, and never
counted towards this one.

---

## Tests

```bash
pip install -r requirements.txt
pytest                    # everything below
pytest cycle_test         # the pipeline: config, handoff, exit codes, provenance
pytest genoar_crawler     # run isolation, pagination, resume, completion claims
```

`pytest.ini` scopes the root run to the trees `requirements.txt` covers.
`genoar_rag/backend` declares its own dependencies and is run from its own
directory.

These are not mock tests. `cycle_test/tests/conftest.py` puts executable
stand-ins for `ssh`, `scp`, `rsync`, `sbatch`, `squeue`, `sacct`, `qsub` and
`qstat` on `PATH`, and the fake `ssh` runs the command string the wrapper built
-- so a changed command or a changed scheduler output format fails a test. Most
of the suite invokes the shipped shell scripts through `subprocess` rather than
importing around them.

Seven tests need Docker and a Stage 3 image; they skip with the reason when it
is absent. Nothing else needs the network, a cluster, or data you do not have.

## Project Structure

```
genoar/
├── genoar_crawler/              # Stage 1: GEO data collection
│   ├── genoar_crawler.py        # Selenium-based crawler
│   ├── Dockerfile.amd64         # x86_64 Docker image
│   ├── Dockerfile.arm64         # ARM64 Docker image
│   └── README.md
│
├── genoar_analysis/             # Stage 2: UMLS matching analysis
│   ├── core/data.py             # GenoarData class
│   ├── io/                      # META/UMLS file loaders
│   ├── preprocessing/           # Filtering, field consolidation
│   ├── pipelines/               # Analysis workflows
│   ├── docker/Dockerfile
│   └── README.md
│
├── srr_pipeline_package/        # Stage 3: Cell Ranger processing
│   ├── pipeline_next/           # 9-step pipeline scripts (the default)
│   ├── pipeline/                # Earlier tree, kept as a comparison baseline
│   │                            #   (legacy_pipeline: true in the config)
│   ├── docker/Dockerfile        # Multi-stage Docker build
│   ├── configs/example.yaml     # Pipeline configuration
│   └── README.md
│
├── cycle_test/                  # End-to-end test framework
│   ├── run_cycle_test.py
│   └── utils/
│
├── scripts/
│   └── fetch_sra.py             # Stage 2 -> Stage 3 bridge (make fetch-sra)
│
├── all_query_results/           # UMLS tables (Stage 2 input)
├── .env.example                 # Environment variable template
├── docker-compose.yml           # Docker Compose for Stage 1+2
├── Makefile                     # Build and run shortcuts
└── README.md                    # This file
```

---

## Citation

GENOAR is described in an article under review at *Nucleic Acids Research*:

> Paik H, Ko TL, Kim D, Shin D, Sirota M, Oskotsky B, Oskotsky T, Lee T, Lee H, Lee D. GENOAR: Global Engine for Navigating the Omnicell space via Agent-based Research. *Nucleic Acids Research*, submitted (2026).

[`CITATION.cff`](CITATION.cff) carries the same in machine-readable form; GitHub's "Cite this repository" reads it. A tagged release with a Zenodo DOI will follow, and the DOI will be added here and to the file.

---

## License

GENOAR is released under the [MIT License](LICENSE).

---

## Data Sources and Acknowledgements

- **Sample metadata** comes from NCBI GEO and SRA. GENOAR indexes it and links to the archive's own record pages; it does not redistribute the deposited data.
- **Concept annotations** (the `CUI` and matched-term columns Stage 2 adds) are made against the Unified Medical Language System (UMLS) Metathesaurus of the U.S. National Library of Medicine, release 2024AB (see [UMLS Reference Data](#umls-reference-data)). If you regenerate the tables, note the release you used: the UMLS licence asks that NLM be acknowledged as the source together with the release.

  Some material in the UMLS Metathesaurus is from copyrighted sources of the respective copyright holders. Users of the UMLS Metathesaurus are solely responsible for compliance with any copyright, patent or trademark restrictions and are referred to the copyright, patent or trademark notices appearing in the original sources, all of which are hereby incorporated by reference.

  Which source vocabularies an annotation draws on is decided by the tables (the `SAB` column) and the per-field preference order in `genoar_analysis/io/umls_readers.py`. If you serve the annotations publicly, list those vocabularies and any notice their producers require.
- **Cell Ranger** and the 10x Genomics reference are downloaded by the user under 10x Genomics' terms; neither is redistributed here.

---

## Requirements

| Component | Python | Memory | Disk | Additional |
|-----------|--------|--------|------|------------|
| Crawler | 3.9+ | 4GB+ | 10-100MB/GSE | Chrome, Internet |
| Analysis | 3.9+ | 4GB+ | A few MB | UMLS tables (included) |
| SRR Pipeline | 3.11+ | 128GB+ | Tens of GB/sample | Docker, Cell Ranger |

---

## Detailed Documentation

- **[Stage 1: GEO Crawler](genoar_crawler/README.md)**
- **[Stage 2: UMLS Analysis](genoar_analysis/README.md)**
- **[Stage 3: SRR Pipeline](srr_pipeline_package/README.md)**
- **[Test Scenarios Guide](docs/SCENARIO_GUIDE.md)** | **[테스트 시나리오 가이드](docs/SCENARIO_GUIDE.ko.md)**
- **[Cycle Test](cycle_test/README.md)**
- **[RAG Web Service](genoar_rag/README.md)** — search UI over the collected metadata (FastAPI + Next.js)
