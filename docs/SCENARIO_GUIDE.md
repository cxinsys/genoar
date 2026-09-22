# GENOAR Pipeline Test Scenarios

A step-by-step guide for running 5 test scenarios that cover all stage combinations of the GENOAR pipeline.

[Korean / 한국어](SCENARIO_GUIDE.ko.md)

> **This is not the `make` path.** The scenarios below drive
> `cycle_test/run_cycle_test.py`, the harness that runs the stages together and
> checks what came out. The README's `make setup` / `make run` route is the
> supported way to *use* the pipeline; this one is how it is *exercised*, and
> the two do not share their images:
>
> | | `make` | this guide |
> |---|---|---|
> | Stage 1 + 2 | `genoar:latest`, built by `make setup` and run through Compose | `genoar-crawler:amd64` and `genoar-analysis:latest`, built by hand below |
> | Stage 3 | `genoar-srr:step9`, built by `make build-srr` | `genoar-srr:step9` — the same image, same build command |
>
> So `make setup` builds none of the images the crawler and analysis scenarios
> need. Build the three below first. Only Stage 3 is shared, and `make
> build-srr` and the command in step 2 produce the same thing.

---

## Scenario Overview

| Scenario | Stages | Crawl Pages | Purpose | Est. Time |
|----------|--------|-------------|---------|-----------|
| **A** | 1 → 2 | 5 | Smoke test of crawling + analysis | 7-8 hours (see note) |
| **B** | 1 → 2 | 100 | Large-scale data collection + analysis | Days |
| **C** | 3 only | — | SRA download + Cell Ranger on existing results | Hours |
| **D** | 1 → 2 → 3 | 5 | Full pipeline end-to-end (small scale) | Hours |
| **E** | 1 → 2 → 3 | 100 | Full pipeline at production scale | Days |

> **Crawl page size.** The crawler requests 500 entries per page and processes them
> one at a time, so "5 pages" is up to 2,500 GEO entries, not a few dozen. Measured
> throughput is roughly 11 seconds per entry.
>
> Stage 1 has a hardcoded 2-hour timeout (`stage1_runner.py`) that is not exposed on
> the command line, so a 5-page crawl hits that timeout before it finishes. The run
> then stops as a Stage 1 failure, and Stage 2 does not run: a crawl that did not
> finish its range is not a base for analysis, so the pipeline stops rather than
> analysing a partial collection.
>
> Until the page size is configurable, do not expect Scenario A to complete. To
> exercise Stage 2 on data Stage 1 collected before it stopped, run it directly
> against the META directory:
>
> ```bash
> python3 cycle_test/utils/stage2_runner.py \
>   <output-dir>/cycle_001_pages_0001-0005/stage1/META \
>   all_query_results \
>   <output-dir>/cycle_001_pages_0001-0005/stage2
> ```
>
> Writing into the cycle's own `stage2/` keeps Scenario C working, since
> `--stage3-only` reads its input from there. It also keeps the two questions
> apart: whether the crawl completed its range, and whether the analysis works on
> what it collected.

```
Scenario A/B:  [Stage 1: Crawler] → [Stage 2: UMLS Analysis]
Scenario C:                                                    [Stage 3: Cell Ranger]
Scenario D/E:  [Stage 1: Crawler] → [Stage 2: UMLS Analysis] → [Stage 3: Cell Ranger]
```

---

## Prerequisites (One-Time Setup)

### 1. System Requirements

| Item | Minimum | Recommended |
|------|---------|-------------|
| CPU | 8 cores | 16+ cores |
| RAM | 8GB (A/B), 64GB (C/D/E) | 128GB+ |
| Disk | 10GB (A/B), 100GB+ (C/D/E) | 500GB+ |
| OS | Linux (Ubuntu 20.04+) | Ubuntu 22.04 |
| Docker | 20.10+ | Latest |
| Python | 3.9+ | 3.11 |

### 2. Build Docker Images

All scenarios use Docker containers. Build the required images from the project root:

```bash
cd /path/to/genoar

# Stage 1: Crawler (required for A, B, D, E)
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/

# Stage 2: Analysis (required for all scenarios)
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

# Stage 3: SRR Pipeline (required for C, D, E)
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

Verify all images are built:

```bash
docker images | grep genoar   # Windows PowerShell: docker images | Select-String genoar
# Expected:
#   genoar-crawler    amd64     ...   ~1.2GB
#   genoar-analysis   latest    ...   ~500MB
#   genoar-srr        step9     ...   ~700MB
```

### 3. Verify UMLS Data (Stage 2 Input)

The UMLS tables are included in the repository and required for all scenarios:

```bash
ls all_query_results/umls_*_df.csv
# Expected: umls_celltype_df.csv  umls_disease_df.csv  umls_tissue_df.csv
```

### 4. Stage 3 External Dependencies (Scenarios C, D, E Only)

Stage 3 requires two large external components that cannot be distributed with the repository:

#### Cell Ranger

Download from [10x Genomics](https://www.10xgenomics.com/support/software/cell-ranger/downloads) (registration required):

```bash
tar -xzvf cellranger-8.0.1.tar.gz
mv cellranger-8.0.1 cellranger/
```

Verify:
```bash
ls cellranger/cellranger
```

#### Reference Genome

```bash
wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz
tar -xzvf refdata-gex-GRCh38-2024-A.tar.gz
mv refdata-gex-GRCh38-2024-A ref/
```

Verify:
```bash
ls ref/fasta/ ref/genes/
```

`cellranger/` and `ref/` at the project root are what `make run-stage3` mounts and
what `run_cycle_test.py` auto-detects. Installing elsewhere means passing
`--cellranger-path` and `--ref-genome-path` on every run.

> **Note**: The reference genome is approximately 20GB compressed, 50GB+ uncompressed. Ensure sufficient disk space before downloading.

---

## Scenario A: Quick Stage 1→2

**Purpose**: Verify that the crawler collects GEO data and the analysis pipeline generates UMLS-annotated tables. Fastest way to confirm Stages 1 and 2 work end-to-end.

**Required Docker images**: `genoar-crawler:amd64`, `genoar-analysis:latest`

**Estimated time**: 7-8 hours for the full 5 pages, which exceeds Stage 1's 2-hour
timeout. See the page-size note in the overview above.

### Dry Run (optional)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --output-dir test_scenario_A \
  --dry-run
```

### Execute

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --output-dir test_scenario_A
```

### Verify Results

With the current page size this run stops at the Stage 1 timeout, so `summary.json`
reports `failed_stage1` and the `stage2/` directory stays empty. That is the
expected outcome, not a separate fault: the pipeline does not analyse a crawl that
did not finish its range.

```bash
# Check cycle summary. Expect status failed_stage1 until the page size is configurable.
cat test_scenario_A/cycle_001_pages_0001-0005/summary.json | python3 -m json.tool

# Check Stage 1 output (crawled metadata files). These exist for whatever the
# crawl collected before it stopped.
ls test_scenario_A/cycle_001_pages_0001-0005/stage1/META/
```

To check the analysis against what was collected, run Stage 2 on its own. Write
it into the cycle's own `stage2/`, which is where Scenario C looks for its input:

```bash
python3 cycle_test/utils/stage2_runner.py \
  test_scenario_A/cycle_001_pages_0001-0005/stage1/META \
  all_query_results \
  test_scenario_A/cycle_001_pages_0001-0005/stage2

ls test_scenario_A/cycle_001_pages_0001-0005/stage2/HS_*_1st_pass_meta_table.csv
head test_scenario_A/cycle_001_pages_0001-0005/stage2/HS_tissue_1st_pass_meta_table.csv
```

### Expected Output Structure

```
test_scenario_A/
├── master_YYYYMMDD_HHMMSS.log
└── cycle_001_pages_0001-0005/
    ├── stage1/
    │   ├── META/*.txt          # GSE metadata files
    │   ├── SMTX/*.gz           # Series Matrix files
    │   └── SRR/*.txt           # SRR ID lists
    ├── stage2/                   # empty when Stage 1 stops at the timeout
    │   ├── HS_cell_type_1st_pass_meta_table.csv
    │   ├── HS_tissue_1st_pass_meta_table.csv
    │   └── HS_disease_1st_pass_meta_table.csv
    ├── logs/
    │   ├── stage1.log
    │   └── stage2.log            # only written if Stage 2 ran
    └── summary.json
```

---

## Scenario B: Large Stage 1→2

**Purpose**: Collect data at larger scale (100 GEO pages) to verify pipeline stability and generate a richer dataset for downstream analysis.

**Required Docker images**: `genoar-crawler:amd64`, `genoar-analysis:latest`

**Estimated time**: Days. At 500 entries per page and ~11 s each, 100 pages is
far past Stage 1's 2-hour timeout; see the page-size note in the overview.

### Execute

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 100 \
  --output-dir test_scenario_B
```

### Monitor Progress

```bash
# Real-time log monitoring
tail -f test_scenario_B/master_*.log
```

### Verify Results

As with Scenario A, this stops at the Stage 1 timeout, so the cycle reports
`failed_stage1` and `stage2/` is empty.

```bash
# Check cycle summary. Expect failed_stage1 at the current page size.
cat test_scenario_B/cycle_001_pages_0001-0100/summary.json | python3 -m json.tool

# Count collected datasets
ls test_scenario_B/cycle_001_pages_0001-0100/stage1/META/ | wc -l
```

Then run the analysis over what was collected, as in Scenario A:

```bash
python3 cycle_test/utils/stage2_runner.py \
  test_scenario_B/cycle_001_pages_0001-0100/stage1/META \
  all_query_results \
  test_scenario_B/cycle_001_pages_0001-0100/stage2

wc -l test_scenario_B/cycle_001_pages_0001-0100/stage2/HS_*_1st_pass_meta_table.csv
```

---

## Scenario C: Stage 3 Only

**Purpose**: Test SRA download and Cell Ranger processing using SRR IDs extracted from a previous Stage 2 run.

**Required Docker images**: `genoar-analysis:latest`, `genoar-srr:step9`

**Prerequisite**: an existing Stage 2 output with populated tables. Scenario A
and B cannot complete at the current page size, so produce the tables with the
standalone stage2_runner command shown in Scenario A, then point this scenario
at that directory.

**Estimated time**: Minutes to hours (SRA download) + hours per sample (Cell Ranger)

### Important Notes

- `--stage3-only` reuses the existing output directory from a previous run.
- `--pages-per-cycle` and `--start-page` must match the previous run so the correct cycle directory is located.
- `--max-samples` limits the number of SRA files to download (recommended for initial testing).

### Alternative: `make fetch-sra`

There is a second route to `.sra` files, outside cycle_test. `make fetch-sra`
extracts a de-duplicated accession list from the quality-filtered Stage 2 tables
that `make run` wrote under `first_pass_output/`. It caches the downloads in
`sample_sra/` and builds an exact current-selection view for
`make run-fetched-stage3`; older cached runs are not included.

```bash
make fetch-sra DRY_RUN=1          # list what would be downloaded, then stop
make fetch-sra MAX_SAMPLES=3      # download 3 (the default is 2)
make run-fetched-stage3           # process exactly those 3
```

The two routes are not interchangeable. cycle_test reads
`<output-dir>/cycle_XXX/stage2` and writes to `<cycle>/stage3/sra/`, with no
default sample cap. The Make route reads `first_pass_output/` — populated by
`make run`, not by cycle_test — caches downloads in `sample_sra/`, publishes an
exact selection view, and caps at 2 samples unless told otherwise. Use whichever
matches how Stage 1+2 was run.

### Execute (reusing Scenario A output)

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --stage3-only \
  --max-samples 3 \
  --cellranger-cores 8 \
  --cellranger-mem 64 \
  --output-dir test_scenario_A
```

`--cellranger-path` and `--ref-genome-path` are only needed when Cell Ranger and
the reference genome are somewhere other than `cellranger/` and `ref/`.

### Download Only (skip Cell Ranger)

To test just the SRA download step without running Cell Ranger:

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --stage3-only \
  --download-only \
  --max-samples 3 \
  --output-dir test_scenario_A
```

### Verify Results

```bash
# Check extracted SRR IDs
cat test_scenario_A/cycle_001_pages_0001-0005/stage3/srr_list.txt

# Check downloaded SRA files
ls test_scenario_A/cycle_001_pages_0001-0005/stage3/sra/

# Check Cell Ranger results (if not --download-only)
ls test_scenario_A/cycle_001_pages_0001-0005/stage3/results/success/
```

---

## Scenario D: Quick Full Pipeline (1→2→3)

**Purpose**: Run the entire pipeline end-to-end at small scale. Crawl 5 pages, generate analysis tables, download SRA files, and run Cell Ranger.

**Required Docker images**: `genoar-crawler:amd64`, `genoar-analysis:latest`, `genoar-srr:step9`

**Estimated time**: Stage 1+2 does not complete at the current page size (see
Scenario A), so Stage 3 is not reached. Use Scenario C against an existing
Stage 2 output to exercise Stage 3.

### Execute

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 5 \
  --run-stage3 \
  --max-samples 2 \
  --cellranger-cores 8 \
  --cellranger-mem 64 \
  --output-dir test_scenario_D
```

### Monitor Progress

```bash
# Master log (all stages)
tail -f test_scenario_D/master_*.log

# Stage 3 Docker pipeline log
tail -f test_scenario_D/cycle_001_pages_0001-0005/stage3/logs/docker_stdout.log
```

### Verify Results

```bash
# Overall summary
cat test_scenario_D/cycle_001_pages_0001-0005/summary.json | python -m json.tool

# Stage 1 output
ls test_scenario_D/cycle_001_pages_0001-0005/stage1/META/

# Stage 2 output
ls test_scenario_D/cycle_001_pages_0001-0005/stage2/HS_*_1st_pass_meta_table.csv

# Stage 3 output
ls test_scenario_D/cycle_001_pages_0001-0005/stage3/results/success/
```

---

## Scenario E: Large Full Pipeline (1→2→3)

**Purpose**: Production-scale run. Crawl 100 pages, generate comprehensive analysis tables, and process all discovered SRR samples through Cell Ranger.

**Required Docker images**: `genoar-crawler:amd64`, `genoar-analysis:latest`, `genoar-srr:step9`

**Estimated time**: Hours to days (depends on number of SRR samples found)

### Execute

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 \
  --pages-per-cycle 100 \
  --run-stage3 \
  --cellranger-cores 16 \
  --cellranger-mem 100 \
  --output-dir test_scenario_E
```

### Monitor Progress

```bash
# Master log
tail -f test_scenario_E/master_*.log

# Check Stage 3 download progress
cat test_scenario_E/cycle_001_pages_0001-0100/stage3/sra/download_progress.json | python -m json.tool

# Cell Ranger log
tail -f test_scenario_E/cycle_001_pages_0001-0100/stage3/logs/docker_stdout.log
```

### Verify Results

```bash
# Final report
cat test_scenario_E/final_report_*.json | python -m json.tool

# Cell Ranger success
ls test_scenario_E/cycle_001_pages_0001-0100/stage3/results/success/

# Gene expression matrices
ls test_scenario_E/cycle_001_pages_0001-0100/stage3/results/success/*/cellranger_output/outs/filtered_feature_bc_matrix/
```

---

## Reading a Run's Outcome

Every scenario ends in one of three states, not two: it did work, it correctly did
nothing, or it failed. `cat summary.json` alone does not separate them.

### Exit codes

```bash
python3 cycle_test/run_cycle_test.py --cycles 1 --pages-per-cycle 5; echo "exit=$?"
```

| Exit | Meaning |
|------|---------|
| `0` | Every cycle succeeded |
| `130` | The run was interrupted (Ctrl+C), including between cycles |
| `1` | Anything else, including a cycle that correctly had nothing to do |

`no_data` is deliberately a `1`. Automation asked for work that did not happen,
and a scheduled run should not keep producing empty cycles unnoticed. Which of
the two it was is legible from the cycle status, not from the code.

These three codes are `run_cycle_test.py`'s alone. The crawler, the parallel
crawl scripts, the pipeline container and Stage 3 each use a finer set. The
`make` targets use a coarser one. See
[Reading a Run's Outcome](../README.md#reading-a-runs-outcome) in the root README
for the per-entry-point table.

### Cycle statuses

`summary.json` and `final_report_*.json` carry a status per cycle:

| Status | Meaning |
|--------|---------|
| `success` | Every requested stage did its work |
| `partial_success` | A stage completed with some samples or tables missing. For Stage 3, some of the samples the run was asked to process have verified Cell Ranger output and others do not; `partial_reason` says which |
| `no_data` | The run was valid and complete but had nothing to do; `no_data_reason` says what |
| `failed_stage1` | The crawl failed |
| `failed_stage3` | SRA download or Cell Ranger failed, the Cell Ranger install is unusable, or the run left no account of itself; `failed_reason` says which |
| `interrupted` | Stopped by Ctrl+C mid-cycle |

Three fields carry the reason, one per status that has one. `no_data_reason`
distinguishes the ways nothing can happen: no page fell in the requested range
once it was capped to what GEO currently holds, or no sample qualified for Cell
Ranger. `partial_reason` says how much of the expected work is there.
`failed_reason` carries the failure text. All three are in `summary.json`.
`final_report_*.json` repeats `no_data_reason` alone, and carries the full Stage
3 result under `stage3_pipeline` or `stage3_handoff`. The console prints the
reason beside the status.

A missing or incomplete Cell Ranger install is never `no_data`. It is a
configuration fault the run cannot work around, so it becomes `failed_stage3`.

### Stage 3 outcomes and the cycle status

Stage 3 grades its own run and records the grade under
`stage3_pipeline.status`. Each grade earns the cycle status that is true of it:

| Stage 3 outcome | Cycle status | What it means |
|-----------------|--------------|---------------|
| `success` | `success` | Every expected sample has verified Cell Ranger output |
| `partial_success` | `partial_success` | Some expected samples were analysed and others were not |
| `nothing_processed` | `no_data` | A complete, valid run that analysed nothing |
| `config_error` | `failed_stage3` | Cell Ranger or its reference is unusable, so the run could not have analysed anything |
| `failed` | `failed_stage3` | The run broke, or its counts contradict each other, or it came back with no record of itself |

A grade the table does not list is `failed_stage3`. So is a result that names the
`success` grade and denies success in the same breath.

A run that leaves no record proves nothing, so it cannot be graded
`partial_success` -- that would credit it with work no evidence supports. It is
`failed_stage3`, and so is a Stage 3 run that reports no success and claims no
partial work either.

Stage 3's verdict may raise the cycle to a worse status. It never lowers one an
earlier stage already set, so "Stage 3 correctly did nothing" cannot overwrite
"Stage 2 broke". The order, from best to worst:

```
running < success < no_data < partial_success < failed_stage3 < failed_stage1 < interrupted
```

With `--stage3-hpc`, Stage 3 is graded from the exit code of
`run_hpc_stage3.py`. With `--stage3-hpc-execute`, `0` is `success`, `3` is
`no_data`, `4` is `partial_success`, and `1` and `2` are `failed_stage3`. Without
it the cycle asked for a transfer-ready bundle rather than an analysis, so
writing the bundle is `success`. See
[../srr_pipeline_package/hpc/README.md](../srr_pipeline_package/hpc/README.md#what-the-runner-reports).

### Stage 1 statuses

Stage 1 records its own outcome under `stage1.status`:

| Status | Meaning |
|--------|---------|
| `success` | The whole requested range was crawled |
| `capped` | The range was trimmed to the pages GEO actually holds, and all of those were crawled |
| `no_pages_in_range` | Nothing fell in the requested range; the cycle becomes `no_data` |
| `interrupted` | Ctrl+C |
| `failed` | Anything else |

Requesting more pages than GEO has is **not** an error. The crawler detects the
real total, warns, caps the range and crawls what exists. `capped` counts as a
success: it is a complete crawl of every page that exists. `stage1.capped_pages`
records what the run actually covered, and the console prints
`(capped at page X of the Y requested)`.

---

## Quick Reference

### Required Docker Images Per Scenario

| Image | Build Command | A | B | C | D | E |
|-------|--------------|---|---|---|---|---|
| `genoar-crawler:amd64` | `docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/` | O | O | — | O | O |
| `genoar-analysis:latest` | `docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .` | O | O | O | O | O |
| `genoar-srr:step9` | `docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .` | — | — | O | O | O |

### External Dependencies Per Scenario

| Dependency | A | B | C | D | E |
|------------|---|---|---|---|---|
| UMLS tables (`all_query_results/`, included) | O | O | O | O | O |
| Cell Ranger binary | — | — | O | O | O |
| Reference genome | — | — | O | O | O |
| Prior Stage 2 results | — | — | O | — | — |
| Internet (GEO crawling) | O | O | — | O | O |
| Internet (SRA download) | — | — | O | O | O |

### Key CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--cycles N` | Number of cycles (required) | — |
| `--pages-per-cycle N` | GEO pages per cycle | 100 |
| `--output-dir PATH` | Output directory | `test_cycles` |
| `--run-stage3` | Enable Stage 3 (download + Cell Ranger) | off |
| `--stage3-only` | Run only Stage 3 on existing data | off |
| `--download-only` | Download SRA only, skip Cell Ranger | off |
| `--max-samples N` | Limit number of SRA samples | all |
| `--cellranger-cores N` | CPU cores for Cell Ranger | 16 |
| `--cellranger-mem N` | Memory (GB) for Cell Ranger | 100 |
| `--cellranger-path PATH` | Cell Ranger install path | auto-detect |
| `--ref-genome-path PATH` | Reference genome path | auto-detect for `--species` |
| `--species {human,mouse}` | What the run is about; picks the reference and is checked against the genome the installed one names | `human` |
| `--dry-run` | Show plan without executing | off |

### Make Variables

The `make` entry point takes its settings from `.env` or the command line, with
the command line winning. Every line of `.env` must be a plain unquoted
`KEY=value`: Make parses the file too, and a line it cannot read breaks every
`make` invocation.

| Variable | Description | Default |
|----------|-------------|---------|
| `PAGES` | GEO pages to crawl (`make run`) | 100 |
| `WORKERS` | Parallel crawl workers | 1 |
| `MAX_SAMPLES` | SRA files `make fetch-sra` downloads, or `all` | 2 |
| `MAX_CONCURRENT` | Parallel downloads for `make fetch-sra` | 4 |
| `SRA_SOURCE` | Accession source: filtered `stage2`, or explicit unfiltered `raw-stage1` | stage2 |
| `DRY_RUN` | `make fetch-sra` lists and stops | off |
| `STAGE3_RESULTS` | The results directory `make status` reads for the Stage 3 run. A container started by hand and pointed somewhere else is reported as `Latest run: none recorded` until this names it | results |
| `COMPOSE_PROJECT_NAME` | The Compose project `make run` uses. Give a second, concurrent run its own. It also decides where results land: the default project writes to the checkout root, any other name to `projects/<name>/`. Pass the same name to `make status`, `make logs` and `make clean` | genoar |

`WORKERS=1` is the default because a single crawler needs none of the run-scoping
machinery a parallel crawl does. Raising it is supported. Each worker gets its
own directory under `crawl_output/runs/<run_id>/`. Raise it deliberately.

To watch a `make run` in progress, use `make logs`. It runs
`docker compose -p <project> logs -f genoar`. The container has no fixed name to
pass to `docker logs`. Compose names it `<project>-genoar-<n>`, one per project,
so two runs cannot recreate each other.

The `make` targets report success as `0` and failure as a non-zero code of Make's
own choosing. They do not pass the container's exact code through. Call the
scripts or the container directly when you need it.

---

## Troubleshooting

### Docker image not found

```bash
# Check existing images
docker images | grep genoar   # Windows PowerShell: docker images | Select-String genoar

# Rebuild missing images (from project root)
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

### Stage 1 fails (crawler)

```bash
# Check crawler log
cat <output-dir>/cycle_*/logs/stage1.log

# Common causes:
# - Docker image not built
# - Network issues (GEO website unreachable)
# - Insufficient shared memory (Docker --shm-size)
```

### Cycle reports `no_data`

Not a failure. The run was valid and complete and had nothing to do. Read
`no_data_reason` to find out which kind:

```bash
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('status'), '-', d.get('no_data_reason'))" \
  <output-dir>/cycle_*/summary.json
```

- *No page fell in the requested range* — `--start-page` is past the end of GEO's
  results for this query. Lower it, or lower the cycle count.
- *No SRR IDs* — Stage 2's tables held no series with an SRA Run Selector list.
- *Cell Ranger analysed nothing* — see below.

### Stage 2 fails (analysis)

```bash
# Check analysis log
cat <output-dir>/cycle_*/logs/stage2.log

# Common causes:
# - UMLS tables missing from all_query_results/
# - No META files from Stage 1 (Stage 1 may have found 0 datasets)
```

### Stage 3 SRA download fails

```bash
# Check failed downloads
cat <output-dir>/cycle_*/stage3/sra/download_failed.txt

# Test network connectivity to SRA
curl -I https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR000001/SRR000001

# Common causes:
# - Network timeout
# - SRR ID no longer available
# - Insufficient disk space
```

### Stage 3 Cell Ranger fails

```bash
# Check Docker pipeline log
cat <output-dir>/cycle_*/stage3/logs/docker_stderr.log

# Common causes:
# - Cell Ranger binary not found at specified path
# - Reference genome not found at specified path
# - Insufficient memory (Cell Ranger requires 64GB+ for most samples)
# - FASTQ files too small or corrupted
```

### Stage 3 ran but analysed nothing

Stage 3 exits `3` and the cycle becomes `no_data` when the pipeline completed
without producing Cell Ranger output for a single one of the samples it was asked
to process. The census that says why is written beside the results:

```bash
# Every sample, eligible or not, with the reason (step 8)
cat <output-dir>/cycle_*/stage3/results/success/cellranger_eligibility.tsv

# Samples ruled out earlier, by steps 6 and 7
cat <output-dir>/cycle_*/stage3/results/success/cellranger_ineligible.tsv
```

Common causes:

- A sample has only one FASTQ file. Cell Ranger needs a paired R1/R2 read set, so
  it can never be processed.
- The image was built to a steps 1–7 target, which contains no Cell Ranger stage
  at all. Build with `--target step9`.

A missing `cellranger/` or `ref/` is a different outcome. The pipeline exits `2`
at step 0 and the cycle is `failed_stage3`. Only some samples verified is a third
outcome. It exits `4` with cycle `partial_success`, and the per-sample reasons are
in `<output-dir>/cycle_*/stage3/results/runs/<run_id>/outcome.json`.
Samples adopted with `GENOAR_ADOPT_PRIOR_RESULTS=1` are reported separately and
count towards none of these verdicts.

A run that comes back without a usable `outcome.json` is a fourth outcome and the
cycle is `failed_stage3`. The run made no statement about what it processed, and
the results volume is persistent, so whatever sits there is output from earlier
runs unless this run's record says otherwise. Two things produce a run with no
record. The Stage 3 image predates run records, in which case rebuild it with
`make build-srr` and run again. Or the run ended before it could write one, in
which case `docker_stdout.log` under this cycle's Stage 3 logs directory says how
far it got.
