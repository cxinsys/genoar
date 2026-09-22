# GENOAR Crawler

This directory contains all crawling and integration related components for the GENOAR project.

## Directory Structure

```
genoar_crawler/
├── README.md                          # This documentation
├── genoar_crawler.py                  # Main crawler script
├── integration.py                     # Data integration analysis
├── Dockerfile.amd64                   # x86_64/AMD64 Docker (Intel/AMD, Cloud VMs)
├── Dockerfile.arm64                   # ARM64 Docker (Apple Silicon, Graviton)
├── requirements.txt                   # Python dependencies
├── run_crawl.sh                       # Simple crawl execution script
├── run_docker_parallel.sh             # Parallel Docker execution
├── run_parallel_crawl.sh              # Parallel crawl script
└── *.log                              # Log files
```

## Components

### Core Scripts

#### 1. `genoar_crawler.py` - Main Crawler
The primary web crawler for collecting GEO metadata.

**Features:**
- Selenium-based web scraping
- Popup handling (QSIWebResponsive)
- Multi-data type collection (META, SMTX, SRR)
- Robust error handling and logging
- Configurable output directories

**Usage:**
```bash
# New named arguments (recommended)
python genoar_crawler.py --start <page> --end <page> [options]

# Legacy positional arguments (backward compatible)
python genoar_crawler.py <start_page> <end_page> [headless]
```

**Examples:**
```bash
# Crawl pages 1 to 10
python genoar_crawler.py --start 1 --end 10

# Crawl ALL available pages (auto-detect total)
python genoar_crawler.py --all

# Resume from last checkpoint after interruption
python genoar_crawler.py --resume

# Crawl all pages with custom output directory
python genoar_crawler.py --all --output /data/crawl_output

# Legacy format (still works)
python genoar_crawler.py 1 10 true

# Clear checkpoint and start fresh
python genoar_crawler.py --all --clear-checkpoint

# Run with visible browser (for debugging)
python genoar_crawler.py --start 1 --end 5 --no-headless

# Set log level to DEBUG for verbose output
python genoar_crawler.py --all --log-level DEBUG
```

**CLI Options:**
| Option | Description | Default |
|--------|-------------|---------|
| `--all` | Crawl all available pages | - |
| `--start` | Starting page number | 1 |
| `--end` | Ending page number (0 = auto-detect) | same as start |
| `--output`, `-o` | Output directory | crawl_output |
| `--resume` | Resume from last checkpoint | False |
| `--clear-checkpoint` | Clear checkpoint before starting | False |
| `--headless` | Run in headless mode | True |
| `--no-headless` | Run with visible browser | - |
| `--log-level` | Logging level (DEBUG/INFO/WARNING/ERROR) | INFO |

**Orchestration options.** Neither of these crawls; both exist for the parallel
scripts, and you can run them by hand against the same output directory.

| Option | Description | Default |
|--------|-------------|---------|
| `--report-pages` | Ask GEO how many results and pages the query currently holds, print one `GENOAR_PAGE_REPORT {...}` line on stdout, and exit | - |
| `--aggregate` | Merge the workers of one run into a single whole-range manifest (no crawling) | - |
| `--run-id` | Run identifier | `$GENOAR_RUN_ID` |
| `--worker-id` | Which worker of the run this process is | `$GENOAR_WORKER_ID` |

**New Features (v2.0):**
- **Full Crawling**: `--all` option to crawl all available pages automatically
- **Checkpoint/Resume**: Progress saved after each GSE, resume with `--resume`
- **Network Retry**: Exponential backoff retry for network errors
- **Driver Recovery**: Automatic Chrome driver restart if crashed
- **Improved Logging**: Logs saved to output directory, configurable log level

**Page Range Handling:**

The crawler reads GEO's real result count before it starts and sizes the range
against it. Asking for more pages than exist is not an error: `--start 1 --end 100`
against a 47-page corpus logs a warning, caps the range at 47, and crawls 1–47.
A range that lies entirely past the end has nothing to crawl at all, which is
reported separately from both success and failure — see the exit codes below.

**Exit Codes:**

| Code | Meaning |
|------|---------|
| `0` | Pages were processed, and a completion manifest was written |
| `1` | The crawl failed |
| `2` | Invalid arguments, or an invalid page range |
| `3` | Valid request, but no page fell in range once capped to what GEO holds |
| `130` | Interrupted (Ctrl+C); progress is in the checkpoint, resume with `--resume` |

Exit `0` means work happened. Exit `3` means the request was fine and there was
simply nothing to do — the usual cause is a parallel run whose last worker was
assigned a slice past the end of the corpus. Treating that as a failure sends
people hunting for a defect that is not there; treating it as a success hides an
empty run. It gets its own code so callers can do neither.

`--aggregate` reuses `0`, `1`, `2` and `3` with the same meanings one level up:
`0` the run covered its requested range, `3` the run had nothing to crawl, `1` it
did not complete, `2` no run id was given.

#### 2. `integration.py` - Data Integration Analysis
Comprehensive analysis tool for validating crawled data completeness.

**Features:**
- Multi-directory scanning (META, SMTX, SRR)
- Data completeness validation
- Missing file detection
- Statistical analysis and reporting
- CSV report generation

**Usage:**
```bash
python integration.py
```

**Output:**
- `integration_report.csv` - Detailed analysis per GSE
- `statistics_summary.json` - Overall statistics
- `keyword_analysis.csv` - Keyword frequency analysis
- `complete_datasets.txt` - List of complete datasets

### Shell Scripts

All three report a verdict that reflects the workers' real exit codes. A worker
that died is never listed as completed, and a run in which every worker had
nothing to do is not reported as a success.

| Code | This script's meaning |
|------|-----------------------|
| `0` | Work was done, the run aggregated, and files were collected |
| `1` | At least one worker failed, or the run did not cover its requested range, or this run's directory could not be created at all (a read-only output tree, a full disk) |
| `2` | Invalid arguments. A run id that is unusable or reserved. A run id that already has a run record, or that another launcher holds right now. In the two container launchers, containers still on this host. In `run_parallel_crawl.sh`, a resume the run's plan does not support |
| `3` | No worker failed, but no worker had anything to crawl either |
| `4` | Workers finished cleanly yet this run collected no file |

Exit `4` is the one to take seriously in an otherwise quiet log. The workers ran
correctly and this run still produced nothing, which points at something upstream:
blocked downloads, the wrong output directory, or a query matching no records.

"This run" is meant literally. `SMTX/`, `SRR/` and `META/` hold every run ever
made into that directory, and have to: a new run may not erase an earlier one's
results. So each summary prints two labelled counts. The first is **files
collected by this run**, taken from the manifest the aggregator writes. The second
is **files in the output directory (all runs, including earlier ones)**. Only the
first may decide the exit status. Counting the directory answers the second
question while appearing to answer the first, and one leftover file from any
earlier run was enough for a run that downloaded nothing to report itself a
success.

> `4` means something different in Stage 3, where it is a partial run.
> Codes `0`–`3` are the same everywhere. `4` is per-component. The root README's
> [Reading a Run's Outcome](../README.md#reading-a-runs-outcome) has the full
> per-entry-point table.

#### Parallel crawling

Parallel workers must not share one output directory, one `checkpoint.json`, one
`crawl_manifest.json` or one browser download area. Sharing them means workers
delete each other's manifests, overwrite each other's resume state, and can move
or delete a file another worker has just downloaded -- and the manifest left
standing describes one worker's slice while claiming the whole range.

A parallel run is now scoped to a run id, and every worker to a directory of its
own beneath it:

```
crawl_output/
├── SMTX/  SRR/  META/          the crawled data, shared: filenames are GSE
│                               accessions and page slices do not overlap
├── runs/
│   └── <run_id>/
│       ├── run.json            the plan: requested range, pages distributed,
│                               workers, and the page count probed off GEO
│       ├── resumes.jsonl       one line per resume of this run, if any
│       ├── crawl_manifest.json the merged, whole-range manifest (aggregator only)
│       └── workers/
│           └── worker-<n>/
│               ├── assignment.json   the slice this worker was given
│               ├── exit_code         its real exit status
│               ├── checkpoint.json   its resume state: the page request that
│               │                     state is progress through, the page and
│               │                     GSE it reached, and the files collected
│               │                     so far
│               ├── crawl_manifest.json  its own slice's manifest
│               ├── downloads/        its browser download area
│               ├── chrome-profile/   its Chrome --user-data-dir
│               └── worker.log        its log
└── crawl_manifest.json         a copy of the newest run's merged manifest,
                                for readers that predate this layout
```

Everything under `runs/` is created at runtime. Nothing deletes an earlier run. A
new run gets a new id. The only file a new run overwrites is the top-level
`crawl_manifest.json`, written by the aggregator after it succeeds.

Four things follow from that:

- **The whole-range manifest is written once, by the aggregator, after every
  worker has exited.** It checks that each worker stayed inside its assigned
  slice and that the completed slices are contiguous from the requested start. If
  a worker failed, died, or left a hole, **no whole-range manifest is written at
  all** and the script exits `1`. An incomplete run leaves no evidence claiming
  otherwise.
- **Covering less than was asked for is accepted only when GEO holds no more.** A
  run that stopped early and a run that reached the end of a shorter corpus look
  the same from the coverage alone, so the shortfall is measured against a page
  count this run actually observed on GEO. That is the corpus probe recorded in
  `run.json`, or the count a worker read off GEO and capped itself against. The
  smaller wins where they disagree. Reaching that count is a cap: the manifest
  is written with `"coverage": "capped"` and the script exits `0`. Falling short
  of it is a truncated run: no manifest, exit `1`, and a message naming the pages
  that exist and were crawled by nobody. When neither the probe nor any worker
  recorded a page count, the two cases cannot be told apart. The run is then
  refused. It is never assumed to have been capped.
- **A crawler with no assigned worker id is unchanged.** Run `python
  genoar_crawler.py` yourself, or let `make run` do it with `WORKERS=1`, and
  `checkpoint.json` and `crawl_manifest.json` stay directly under the output
  directory exactly as before. `--resume` and every existing artifact path keep
  working. There is no `runs/` directory unless a parallel run creates one.
- **The identity comes from the orchestrator. The process id is never used.**
  Inside a container the crawler is PID 1, so PID-derived paths gave every
  container the same profile and the same checkpoint.

**Page count is probed once.** Before splitting, each script asks GEO for the real
total with `--report-pages` and distributes `min(requested, available)` pages, so
the later workers of a 100-page request against a 47-page corpus are not started
with nothing to do. Over-long requests are still capped with a warning, and each
worker still caps itself and records the count it read, so a failed probe costs
efficiency. Correctness is unaffected. The aggregator falls back on what the
workers observed.

**Environment variables** read by `run_parallel_crawl.sh`, `run_docker_parallel.sh`
and `run_crawl.sh`:

| Variable | Effect |
|----------|--------|
| `GENOAR_RUN_ID` | Name this run. Default: a fresh timestamped id. See [Naming a run](#naming-a-run) for the rule it must satisfy |
| `GENOAR_WORKER_ID` | Set by the orchestrator per worker; the crawler reads it to find its own directory |
| `GENOAR_RESUME=1` | `run_parallel_crawl.sh` only: continue the run `GENOAR_RUN_ID` names, each worker from its own checkpoint. The one way to reuse an id. The run must already exist. A run exists once its `run.json` is written, and a missing or mistyped id exits `2`. Setting the flag without `GENOAR_RUN_ID` exits `2` as well, because a minted id names no run to continue. It must ask for the same worker count and page request the plan records. `run_crawl.sh` and `run_docker_parallel.sh` refuse the flag and exit `2` rather than ignore it |
| `GENOAR_RECLAIM_RUN=1` | `run_crawl.sh` and `run_docker_parallel.sh`: discard the containers an earlier run of this id left behind, when that run left no record. Never removes a record |
| `GENOAR_TOTAL_PAGES=<n>` | Skip the corpus probe and use this page count |
| `GENOAR_SKIP_PAGE_PROBE=1` | Skip the corpus probe and trust the requested page count |

To aggregate a run by hand, first fix whatever stopped it. Then run the crawler
in aggregation mode against the same output directory:

```bash
python genoar_crawler.py --aggregate --run-id <run_id> -o crawl_output
```

#### Naming a run

A run id becomes a directory name, so all three scripts and the crawler itself
hold it to one rule: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. Surrounding whitespace
is stripped, and unset, empty and all-whitespace alike mean "no id was given", so
one is minted. Whitespace inside an id is refused. It is never turned into a
different id than you set. All three scripts additionally reserve `LATEST`,
because Stage 3 keeps `runs/LATEST` as its newest-run pointer and both halves read
this one variable. A rule a user meets in one half has to hold in the other.
`latest` is fine, the comparison is case-sensitive. A violation exits `2` before
the run directory is created. That is the point. The directory is built here,
before any Python sees the id, so `GENOAR_RUN_ID=..` must not get that far.

**An id that already has a `run.json` cannot be reused.** That file is what the
run promised and what the aggregator judges it against, so a second run under the
same id exits `2` and leaves the record untouched.

**The id is reserved atomically.** Each launcher claims `runs/<run_id>/` with a
single `mkdir`. The kernel serialises that call, so of any number of racers exactly
one gets it. Start two runs on one id at the same instant and one proceeds. The
other exits `2`. `runs/` itself is created with `-p`, because it is shared by every
run and is nobody's to claim.

Exit `1` covers the other way the directory fails to appear. A read-only output
tree, a full disk and a missing parent all fail the same call, and none of them
means the id is taken. Each launcher asks whether the directory is there afterwards
and reports the two separately.

`run_parallel_crawl.sh` alone offers a way to reuse an id:

```bash
GENOAR_RUN_ID=pilot-2026-08 GENOAR_RESUME=1 ./run_parallel_crawl.sh 4 40
```

The original `run.json` still stands. A resume may not redefine what the run
promised, or a run that fell short could be made to look complete by resuming it
with a smaller request. It also has to be there. A resume of an id whose `run.json`
was never written is refused with exit `2`, before anything is reserved, so a
mistyped id leaves nothing behind. Each continuation appends a line to
`runs/<id>/resumes.jsonl`.

**A resume has to ask for what the plan records.** It exits `2` when the worker
count or the requested end page differ from the plan, naming both values. Those two
numbers are what the page split is computed from, and the split is what binds a
checkpoint to a page range. Resumed with three workers where the run had two, every
boundary moves: a worker opens the checkpoint it wrote for one slice and continues
it through another, and the run's manifest then claims pages nobody crawled. GEO is
not re-probed either, because a larger corpus today would move the same boundaries.
The plan's own `distributed_end` stands. Each worker's `assignment.json` records the
slice it was actually given, and the recomputed slice has to equal it. A resume that
would move a worker onto pages it never started exits `2` as well.

**Each checkpoint is held to its own request.** A worker's `checkpoint.json` records
the page request its progress is progress through, along with the page and GSE index
it reached, the accessions it downloaded and failed, and the files it has collected
so far. Continuing a checkpoint is what lets the completion manifest claim the pages
before the resume point, because the invocations that wrote the checkpoint crawled
them. That holds only while the checkpoint belongs to the same request. A checkpoint
whose request started at a different page is refused, and so is one that records no
request at all. `--clear-checkpoint` crawls the request from its start instead.

A second launcher entering a run that is already going is refused the same way. The
run directory already exists, so it cannot be the reservation. A lock directory
inside the run is taken instead, by fresh runs and resumes alike, and a launcher
that finds it held exits `2`.

That record has a limit worth knowing. It distinguishes the original run from its
resumes at run level. A resumed worker overwrites its own `crawl_manifest.json`
and `exit_code` under `workers/worker-<n>/`, so which resume produced a given
worker's slice is not recorded. The manifest that stands covers the worker's whole
assignment. Its `completed_pages` is how much of the assignment is finished, and
the pages before the resume point were crawled by the invocations that wrote the
checkpoint this one continued. `crawled_pages` records the pages the last
invocation walked, and completion is never decided from it. On a fresh crawl the
two are equal. `run_crawl.sh` and `run_docker_parallel.sh` cannot resume. There, a
fresh id is the only way forward.

#### The containers of a run

`run_crawl.sh` and `run_docker_parallel.sh` name every container they start
`genoar-worker-<run_id>-<n>` and label it:

| Label | Value |
|-------|-------|
| `genoar.run` | this run's id. Set on every container the run starts |
| `genoar.worker` | the worker number |
| `genoar.role` | `worker`, `probe` or `aggregator` (the Compose service is `pipeline`) |

Select a run's containers by label. Never select them by name:

```bash
docker ps --filter label=genoar.run=<run_id>              # this run's workers
docker logs -f genoar-worker-<run_id>-1                   # one worker's log
docker stop $(docker ps -q --filter label=genoar.run=<run_id>)   # stop this run
```

`--filter name=` is a substring match with no anchored form, so `name=genoar-worker`
selects every run on the host, including somebody else's live crawl. Even a
run id inside the name only narrows it: `r1` still matches `r12`. The label is an
exact key/value match on metadata only these scripts write. Both scripts obey the
same rule about themselves: neither ever stops or removes a container that does not
carry this run's label.

Before starting, each checks whether the id is already in use here. A run of that
id still running is refused. An id whose run record exists is refused, and no flag
overrides that. Containers left by a run of that id that recorded nothing are
refused too, and `GENOAR_RECLAIM_RUN=1` discards those so the id can be used again.

#### 1. `run_crawl.sh` - Interactive Docker Launcher
Prompts for a worker count and a page count, then runs one container per worker.
The image is chosen from the host architecture (`uname -m`), building it first if
it is not present. Both answers must be positive integers. Anything else is
refused with exit `2`. It is never turned into a page range by arithmetic.

```bash
./run_crawl.sh
```

#### 2. `run_parallel_crawl.sh` - Parallel Execution
Runs one background Python worker per page slice, on this machine. Requires the
crawler's Python dependencies to be installed locally.

```bash
./run_parallel_crawl.sh [workers] [total_pages] [output_dir]

# 4 workers over 100 pages into crawl_output/ (the defaults)
./run_parallel_crawl.sh

# 2 workers over 10 pages
./run_parallel_crawl.sh 2 10
```

Pages are split evenly, with the remainder spread over the first workers rather
than dumped on the last. A worker left with no pages is reported as skipped and
never started — it is not handed an end page of `0`, which the crawler reads as
"crawl everything". All three scripts split the same way; see
[Parallel crawling](#parallel-crawling) for the run layout and the environment
variables.

#### 3. `run_docker_parallel.sh` - Docker Parallel Execution
The same split, one container per worker. Each container gets `--shm-size=2g`,
because Chrome's renderers write to `/dev/shm` and Docker's 64MB default kills
them mid-crawl.

```bash
./run_docker_parallel.sh [num_workers] [total_pages] [target]

# 4 workers, 100 pages, image chosen from the host architecture
./run_docker_parallel.sh

# Force the x86_64 image on an ARM host
./run_docker_parallel.sh 4 100 amd64
```

The third argument picks the image. This repository ships two, so the choice is
really about architecture:

| `target` | Image | Notes |
|----------|-------|-------|
| `auto` | Host architecture, from `uname -m` | Default |
| `amd64`, `x86_64` | `Dockerfile.amd64` | Google Chrome |
| `arm64`, `aarch64` | `Dockerfile.arm64` | Chromium |
| `linux`, `windows` | `Dockerfile.amd64` | Accepted for callers that pass an OS name |
| `macos` | Host architecture | A Mac is either architecture, so `uname -m` decides |

### Docker Configuration

#### Dockerfiles (Architecture-based)

| Dockerfile | Architecture | Use Case |
|------------|--------------|----------|
| `Dockerfile.amd64` | x86_64/AMD64 | Intel/AMD CPUs, Linux servers, Windows WSL2, Cloud VMs (AWS EC2, GCP, Azure) |
| `Dockerfile.arm64` | ARM64 | Apple Silicon (M1/M2/M3/M4), AWS Graviton, Raspberry Pi |

**Key Differences:**
- **AMD64**: Uses Google Chrome (official) + ChromeDriver from Chrome for Testing API
- **ARM64**: Uses Chromium (apt package) + chromium-driver (ARM64 native)

#### Docker Usage

**Build Images:**
```bash
# AMD64 (Intel/AMD CPUs, most cloud servers)
docker build -f Dockerfile.amd64 -t genoar-crawler:amd64 .

# ARM64 (Apple Silicon, AWS Graviton)
docker build -f Dockerfile.arm64 -t genoar-crawler:arm64 .
```

**Run Containers:**
```bash
# AMD64
docker run --rm -v "$(pwd)/../crawl_output:/data" genoar-crawler:amd64 1 10 true

# ARM64
docker run --rm -v "$(pwd)/../crawl_output:/data" genoar-crawler:arm64 1 10 true

# Auto-detect architecture (if using buildx multi-platform image)
docker run --rm -v "$(pwd)/../crawl_output:/data" genoar-crawler:latest 1 10 true
```

**Run Integration Analysis:**
```bash
docker run --rm -v "$(pwd)/../crawl_output:/data" --entrypoint python genoar-crawler:amd64 /app/integration.py
```

### Dependencies

#### `requirements.txt`
Crawler-specific dependency list.

**Includes:**
- `selenium` — the crawler drives a real browser rather than fetching pages, so
  it needs no HTTP client or HTML parser
- `pandas` — parsing the crawler's own tabular output

ChromeDriver and the browser come from the Dockerfile, not from pip.

**Usage:**
```bash
# Install crawler dependencies
pip install -r requirements.txt
```

## Setup and Installation

### 1. Virtual Environment Setup

```bash
# Create virtual environment
python -m venv genoar_env

# Activate (macOS/Linux)
source genoar_env/bin/activate

# Activate (Windows)
genoar_env\Scripts\activate

# Install crawler dependencies
pip install -r requirements.txt
```

### 2. Docker Setup

```bash
# Check your architecture
uname -m  # x86_64 = amd64, aarch64 = arm64

# Build appropriate image for your platform
# For Intel/AMD (x86_64)
docker build -f Dockerfile.amd64 -t genoar-crawler:latest .

# For Apple Silicon/ARM (aarch64)
docker build -f Dockerfile.arm64 -t genoar-crawler:latest .

# Test run
docker run --rm -v "$(pwd)/../crawl_output:/data" genoar-crawler:latest 1 2 false
```

## Usage Workflows

### 1. Local Development Workflow

```bash
# 1. Setup environment
source genoar_env/bin/activate
pip install -r requirements.txt

# 2. Run crawler
python genoar_crawler.py 1 10 true

# 3. Analyze results
python integration.py
```

### 2. Docker Production Workflow

```bash
# 1. Build image (choose based on your architecture)
docker build -f Dockerfile.amd64 -t genoar-crawler:prod .  # Intel/AMD
# or
docker build -f Dockerfile.arm64 -t genoar-crawler:prod .  # Apple Silicon/ARM

# 2. Run parallel crawling
./run_docker_parallel.sh

# 3. Integration analysis
docker run --rm -v "$(pwd)/../crawl_output:/data" --entrypoint python genoar-crawler:prod /app/integration.py
```

### 3. Batch Processing Workflow

```bash
# Use parallel execution for large datasets
./run_parallel_crawl.sh

# Or Docker parallel execution
./run_docker_parallel.sh
```

## Output Structure

The crawler generates data in the following structure:

```
crawl_output/
├── META/                    # Metadata files (GSE*_meta.txt)
├── SMTX/                    # Series matrix files (GSE*_series_matrix.txt.gz)
├── SRR/                     # SRR data files (GSE*.txt)
├── checkpoint.json          # Resume state (unscoped runs only)
├── crawl_manifest.json      # Completion evidence for the last finished range
├── runs/                    # One directory per parallel run (see above)
├── integration_report.csv   # Integration analysis results
├── statistics_summary.json  # Summary statistics
├── keyword_analysis.csv     # Keyword analysis
└── complete_datasets.txt    # Complete dataset list
```

Everything but `META/`, `SMTX/` and `SRR/` is created at runtime. `runs/` appears
only after a parallel run; a single crawler keeps its checkpoint and manifest at
the top level, as it always has.

## Configuration

### Environment Variables

- `CHROME_BINARY_PATH`: Chrome/Chromium binary path
- `CHROME_DRIVER_PATH`: ChromeDriver path
- `LOG_DIR`: Where the log file is written (default: the output directory)
- `GENOAR_CHROME_DISABLE_HTTP2`: Set to `1`/`true` to run Chrome with
  HTTP/2 disabled, for networks whose middleboxes drop HTTP/2 streams to
  NCBI (observed on K-BDS: TLS handshakes pass, page transitions then
  stall). Transport-level only — the pages GEO returns are identical.
  `0`/`false`/unset means off; any other value refuses to start, so a typo
  cannot silently run with HTTP/2 still on. Forwarded into containers by
  `run_docker_parallel.sh`; direct `python genoar_crawler.py` runs inherit
  it from the shell.

### Locating Chrome and ChromeDriver

The crawler resolves both executables in three steps, in order:

1. `CHROME_BINARY_PATH` / `CHROME_DRIVER_PATH`, if set and non-empty
2. `PATH`, searched for `google-chrome-stable`, `google-chrome`, `chromium`,
   `chromium-browser` and for `chromedriver`
3. Known install locations: `/usr/bin/` and `/usr/local/bin/`

Every GENOAR image sets both variables, always under `/usr/bin`:
`Dockerfile.amd64` to Google Chrome, `Dockerfile.arm64` to Chromium, and the root
`Dockerfile` (the image behind `make run` and `docker compose up`) to whichever
matches the architecture it was built for. That is Google Chrome on amd64 and
Chromium on arm64. The root `Dockerfile` selects its browser stage from
`TARGETARCH` and therefore needs BuildKit, which Docker 23+ enables by default.
The search below exists for local runs and for images built elsewhere.

When nothing is found, the crawler refuses to start and prints exactly what it
looked at, so the fix is either a missing install or a path to set — not a bare
`NoSuchDriverException`.

## Troubleshooting

### Docker Pre-flight Setup (Required)

Before running the crawler in Docker, complete these setup steps:

```bash
# 1. Create output directories on host
mkdir -p ~/genoar/crawl_output/SMTX
mkdir -p ~/genoar/crawl_output/SRR
mkdir -p ~/genoar/crawl_output/META

# 2. Set directory permissions (required for container access)
chmod -R 777 ~/genoar/crawl_output

# 3. Build Docker image
cd ~/genoar/genoar_crawler
docker build -f Dockerfile.amd64 -t genoar-crawler:amd64 .  # Intel/AMD
# or
docker build -f Dockerfile.arm64 -t genoar-crawler:arm64 .  # Apple Silicon/ARM

# 4. Run crawler
docker run --rm -it \
  -v ~/genoar/crawl_output:/data \
  genoar-crawler:amd64 1 100 true
```

**Directory Structure Required:**
```
~/genoar/crawl_output/     # chmod 777
├── SMTX/                  # Supplementary matrix files
├── SRR/                   # SRR ID files
├── META/                  # Metadata files
├── .chrome/               # Chrome user data (auto-created)
├── .downloads/            # Browser download scratch (auto-created)
├── checkpoint.json        # Progress checkpoint (auto-created)
├── runs/                  # Per-run worker state, parallel runs only (auto-created)
└── *.log                  # Log files (auto-created)
```

`.chrome/`, `.downloads/`, `checkpoint.json` and the log belong to a container run
without an assigned worker id. A worker of a parallel run keeps all four inside
its own directory under `runs/` instead.

### Common Issues

1. **PermissionError: '/data/...'**
   ```bash
   # Cause: Container user cannot write to mounted volume
   # Solution: Set host directory permissions
   chmod -R 777 ~/genoar/crawl_output
   ```

2. **Chrome instance exited (SessionNotCreatedException)**
   ```bash
   # Cause: Chrome cannot access temp directories with --user flag
   # Solution: Do NOT use --user flag, use chmod 777 instead

   # Wrong:
   docker run --user $(id -u):$(id -g) -v ... genoar-crawler:amd64

   # Correct:
   chmod -R 777 ~/genoar/crawl_output
   docker run -v ~/genoar/crawl_output:/data genoar-crawler:amd64
   ```

3. **Output directories not found**
   ```bash
   # Cause: SMTX/SRR/META directories don't exist on host
   # Solution: Create them before running
   mkdir -p ~/genoar/crawl_output/{SMTX,SRR,META}
   ```

4. **ChromeDriver Version Mismatch**
   ```bash
   # Rebuild Docker image to get latest Chrome + ChromeDriver
   docker build --no-cache -f Dockerfile.amd64 -t genoar-crawler:amd64 .
   ```

5. **Docker Platform Issues**
   ```bash
   # Specify platform explicitly
   docker run --platform linux/amd64 ...
   ```

6. **Memory Issues**
   ```bash
   # Monitor Docker memory usage
   docker stats
   ```

7. **Workers vanish mid-crawl in a parallel run**
   ```bash
   # Cause: Docker's default 64MB /dev/shm is too small for several Chrome
   #        renderers; they are killed and the worker looks like it disappeared
   # Solution: give the container 2GB of shared memory
   docker run --shm-size=2g -v ~/genoar/crawl_output:/data genoar-crawler:amd64 1 10 true
   ```
   `run_docker_parallel.sh` already passes `--shm-size=2g`, and the root
   `docker-compose.yml` sets `shm_size: "2gb"`.

8. **Chrome or ChromeDriver not found at startup**
   ```bash
   # The crawler prints every location it searched before exiting.
   # Point it at the real install instead of guessing:
   docker run -e CHROME_BINARY_PATH=/usr/bin/chromium \
              -e CHROME_DRIVER_PATH=/usr/bin/chromedriver ...
   ```

### Log Analysis

Log files are generated with timestamps:
- `genoar_crawler_YYYYMMDD_HHMMSS.log`
- `integration_YYYYMMDD_HHMMSS.log`

Check logs for detailed error information and debugging.

## Performance Optimization

### Parallel Execution
- Use `run_parallel_crawl.sh` for CPU-bound parallelization
- Use `run_docker_parallel.sh` for container-based isolation
- Adjust parallel instance count based on system resources

### Resource Management
- Monitor disk space (crawled data can be large)
- Configure appropriate timeout values
- Use Docker resource limits for memory management

## Contributing

When adding new crawler features:
1. Update the main `genoar_crawler.py`
2. Add corresponding Docker configurations if needed
3. Update shell scripts for new parameters
4. Test with both local and Docker environments
5. Update this documentation

## Integration with Analysis Package

The crawler output is designed to work seamlessly with the `genoar_analysis` package:

```python
import genoar_analysis as ga

# Load crawled data
adata = ga.io.read_crawled_meta('crawl_output/META')

# Run analysis pipeline
from genoar_analysis.pipelines import FirstPassPipeline
pipeline = FirstPassPipeline('crawl_output/META', 'all_query_results')
results = pipeline.create_all_first_pass_tables()
```