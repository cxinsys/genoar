# Docker-Based SRR Pipeline User Guide

Complete guide for running the SRR (Sequence Read Archive) processing pipeline using Docker.

This is GENOAR Stage 3 only — the `genoar-srr` image and the raw `docker run`
commands that drive it. Stages 1 and 2 (the GEO crawler and the UMLS analysis)
are covered by the [root README](README.md) and
[genoar_crawler/README.md](genoar_crawler/README.md).

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Quick Start](#2-quick-start)
3. [Docker Command Anatomy](#3-docker-command-anatomy)
4. [Volume Mounts Explained](#4-volume-mounts-explained)
5. [Configuration File Setup](#5-configuration-file-setup)
6. [Execution Options](#6-execution-options)
7. [Step-by-Step Execution Guide](#7-step-by-step-execution-guide)
8. [Troubleshooting](#8-troubleshooting)
9. [Advanced Usage](#9-advanced-usage)

---

## 1. Prerequisites

### Required Software

| Software | Minimum Version | Purpose | Installation Check |
|----------|----------------|---------|-------------------|
| **Docker** | 20.10+ | Container runtime | `docker --version` |
| **Architecture** | Linux x86-64 | Cell Ranger is distributed for no other | `uname -sm` |
| **Disk Space** | 50+ GB free | Pipeline outputs | `df -h` |
| **Memory** | 100+ GB RAM | Cell Ranger analysis | `free -h` |
| **CPU Cores** | 8+ cores | Parallel processing | `nproc` |

### Required Data Files

#### 1. SRA Files (Input Data)
```bash
# Directory structure
sample_sra/
└── SRR9134611.sra    # Your SRA files here

# Verification
ls -lh sample_sra/*.sra
```

**How to obtain SRA files**:
```bash
# Option 1: Direct download from NCBI
wget https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR9134611/SRR9134611

# Option 2: Use prefetch (SRA Toolkit)
prefetch SRR9134611

# Move to sample_sra directory
mv SRR9134611/SRR9134611.sra sample_sra/
```

#### 2. Cell Ranger Reference Genome
```bash
# Download from 10x Genomics
# URL: https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz

wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz
tar -xzf refdata-gex-GRCh38-2024-A.tar.gz

# Move to the project root as ref/
mv refdata-gex-GRCh38-2024-A ref/

# Expected structure
ref/
├── fasta/
├── genes/
├── reference.json
└── star/
```

**Supported references**:
- `refdata-gex-GRCh38-2024-A` (Human, recommended)
- `refdata-gex-mm10-2020-A` (Mouse)
- Custom references (see Cell Ranger documentation)

#### 3. Cell Ranger Binary
```bash
# Download Cell Ranger 8.0.1
curl -o cellranger-8.0.1.tar.gz \
  "https://cf.10xgenomics.com/releases/cell-exp/cellranger-8.0.1.tar.gz?Expires=..."

# Extract, then move to the project root as cellranger/
tar -xzf cellranger-8.0.1.tar.gz
mv cellranger-8.0.1 cellranger/

# Verify installation
cellranger/cellranger --version
# Expected: cellranger cellranger-8.0.1
```

**Note**: Cell Ranger requires registration at 10x Genomics website for download link.

#### 4. Configuration File
```bash
# Use provided template
cp srr_pipeline_package/configs/example.yaml \
   srr_pipeline_package/configs/my_config.yaml

# Edit configuration (see Section 5)
nano srr_pipeline_package/configs/my_config.yaml
```

### Directory Preparation

```bash
# Create required directories
mkdir -p sample_sra
mkdir -p logs
mkdir -p results

# Verify structure
tree -L 2 -d
# Expected:
# .
# ├── sample_sra/                        (input SRA files)
# ├── ref/                               (reference genome)
# ├── cellranger/                        (Cell Ranger install root)
# ├── logs/                              (execution logs)
# ├── results/                           (pipeline outputs)
# └── srr_pipeline_package/
#     └── configs/                       (pipeline config)
```

`logs/` and `results/` sit at the project root because that is where the Makefile
puts them. `make status` reads `results/` — it is the `STAGE3_RESULTS` variable,
default `results`, so a run written anywhere else is reported as
`Latest run: none recorded` unless you say `make status STAGE3_RESULTS=<dir>`.

`ref/` and `cellranger/` at the project root are what `make run-stage3` mounts and
what `cycle_test/run_cycle_test.py` auto-detects. Installing them elsewhere works,
but then every run has to name the paths explicitly.

---

## 2. Quick Start

### With Make (recommended)

From the project root, the Make targets wrap everything below and check the
prerequisites before starting:

```bash
make build-srr             # docker build --target step9 -t genoar-srr:step9 .
make fetch-sra             # Stage 2 filtered selection; caches 2 by default
make run-fetched-stage3    # mounts exactly that selection
# Or: make run-stage3      # all user-managed inputs directly in sample_sra/
```

The Stage 3 targets refuse to start when their input is empty, when the config
file is absent, when neither supported Cell Ranger launcher is executable, or
when `ref/` lacks a non-empty `reference.json`, `fasta/`, `genes/` or `star/`. It
names what is missing. It checks that the install is usable. Presence alone does
not satisfy it. The container repeats the same checks as its step 0. `make
fetch-sra` reads the filtered Stage 2 tables and downloads 2 samples by default
(`MAX_SAMPLES=N`, or `MAX_SAMPLES=all`); `make run-fetched-stage3` then excludes
older raw or custom cache entries.
See the [root README](README.md) for the full Make workflow.

`make run-stage3` allocates a TTY only when it has one, so it runs unattended in
CI or cron. `make` reports success as `0` and failure as its own non-zero status.
It does not pass the pipeline's exact exit code through. Run the container
directly, as below, when you need it.

The rest of this guide is the raw Docker equivalent, for anyone driving the image
directly or adapting it to another orchestrator.

### Minimal Working Example

```bash
# 1. Build Docker image
docker build \
  -f srr_pipeline_package/docker/Dockerfile \
  --target step9 \
  -t genoar-srr:step9 \
  .

# 2. Run complete pipeline (steps 1-9)
docker run --rm -it \
  -v $(pwd)/sample_sra:/work/data/sra \
  -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \
  -v $(pwd)/ref:/ref \
  -v $(pwd)/cellranger:/opt/cellranger \
  -v $(pwd)/logs:/work/logs \
  -v $(pwd)/results:/work/results \
  genoar-srr:step9
```

**Expected output**: Pipeline completes steps 1-9, results in `results/`

---

## 3. Docker Command Anatomy

### Command Breakdown

```bash
docker run \              # Run a container from an image
  --rm \                  # Remove container after completion
  -it \                   # Interactive terminal (see logs in real-time)
  -v HOST:CONTAINER \     # Mount volumes (data sharing)
  IMAGE_NAME              # Docker image to run
```

### Flags Explained

| Flag | Purpose | Required? | Notes |
|------|---------|-----------|-------|
| `--rm` | Auto-remove container | Recommended | Prevents container accumulation |
| `-it` | Interactive mode | Recommended | Allows Ctrl+C to stop, view live logs |
| `-v` | Volume mount | **Required** | Maps host directories to container |
| `--name` | Container name | Optional | For identification in `docker ps` |
| `-e VAR=value` | Environment variable | Optional | Override config values |
| `--cpus` | CPU limit | Optional | Limit CPU usage (e.g., `--cpus=8`) |
| `--memory` | Memory limit | Optional | Limit RAM (e.g., `--memory=100g`) |

### Alternative: Detached Mode

```bash
# Run in background
docker run --rm -d \
  --name srr-pipeline-run1 \
  -v $(pwd)/sample_sra:/work/data/sra \
  -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \
  -v $(pwd)/ref:/ref \
  -v $(pwd)/cellranger:/opt/cellranger \
  -v $(pwd)/logs:/work/logs \
  -v $(pwd)/results:/work/results \
  genoar-srr:step9

# Monitor logs
docker logs -f srr-pipeline-run1

# Stop if needed
docker stop srr-pipeline-run1
```

**Use cases**:
- `-it` (interactive): Single sample testing, debugging, immediate feedback
- `-d` (detached): Batch processing, long-running jobs, remote sessions

---

## 4. Volume Mounts Explained

### Required Mounts (6 Total)

#### Mount 1: Input SRA Files
```bash
-v $(pwd)/sample_sra:/work/data/sra
```

| Component | Description |
|-----------|-------------|
| **Host Path** | `$(pwd)/sample_sra` - Directory containing `.sra` files |
| **Container Path** | `/work/data/sra` - Fixed path expected by pipeline |
| **Purpose** | Provide input SRA files for processing |
| **Read/Write** | Read (step 1-3), Write (directory organization) |

**Requirements**:
- Must contain at least one `SRR*.sra` file
- Supports multiple SRA files (processed in parallel)
- Minimum 10 GB free space per SRA file

**Example**:
```bash
sample_sra/
├── SRR9134611.sra
├── SRR9134612.sra
└── SRR9134613.sra
```

---

#### Mount 2: Configuration File
```bash
-v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml
```

| Component | Description |
|-----------|-------------|
| **Host Path** | Path to your YAML configuration file |
| **Container Path** | `/work/config.yaml` - Fixed path read by entrypoint |
| **Purpose** | Pipeline settings (cores, memory, paths) |
| **Read/Write** | Read-only |

**Requirements**:
- Valid YAML syntax
- Must specify `ref_path`, `cellranger_bin`, resource settings
- See [Section 5](#5-configuration-file-setup) for details

**Customization**:
```bash
# Create custom config
cp srr_pipeline_package/configs/example.yaml \
   srr_pipeline_package/configs/production.yaml

# Use custom config
-v $(pwd)/srr_pipeline_package/configs/production.yaml:/work/config.yaml
```

---

#### Mount 3: Reference Genome
```bash
-v $(pwd)/ref:/ref
```

| Component | Description |
|-----------|-------------|
| **Host Path** | **ABSOLUTE PATH** to Cell Ranger reference |
| **Container Path** | `/ref` - Must match `ref_path` in config.yaml |
| **Purpose** | Genome reference for Cell Ranger alignment |
| **Read/Write** | Read-only |

**⚠️ CRITICAL REQUIREMENTS**:
1. **Must use absolute path** (not relative path like `./refdata...`)
2. **Container path must match** `ref_path` value in `config.yaml`
3. **Reference must be uncompressed** (not `.tar.gz`)

**Path Matching Example**:
```yaml
# config.yaml
ref_path: /ref

# Docker command must mount to the same path
-v /home/user/ref:/ref
   └─────────────────────────────────┘  └──────────────────┘
          Host (absolute)                  Container (matches config)
```

**Common Errors**:
```bash
# ❌ WRONG - Relative path
-v ./ref:/ref

# ❌ WRONG - Path mismatch
# config.yaml: ref_path: /ref
-v /path/to/refdata:/ref/my-ref   # Container path doesn't match

# ✅ CORRECT - Absolute path, matches config
-v /home/user/ref:/ref
```

---

#### Mount 4: Cell Ranger Binary
```bash
-v $(pwd)/cellranger:/opt/cellranger
```

| Component | Description |
|-----------|-------------|
| **Host Path** | Directory containing Cell Ranger executable |
| **Container Path** | `/opt/cellranger` - Must match `cellranger_bin` in config |
| **Purpose** | Cell Ranger software (not included in image) |
| **Read/Write** | Read-only |

**Requirements**:
- Must contain `cellranger` executable
- Version 8.0.1 (or compatible)
- Execute permission on host (`chmod +x cellranger`)

**Licensing Note**: Cell Ranger cannot be redistributed in Docker images due to 10x Genomics licensing terms.

**Verification**:
```bash
# Check Cell Ranger availability
ls -lh cellranger/cellranger

# Test execution (on host)
cellranger/cellranger --version
```

**Path Matching**:
```yaml
# config.yaml
cellranger_bin: /opt/cellranger/cellranger

# Docker mount must provide this path
-v $(pwd)/cellranger:/opt/cellranger
```

---

#### Mount 5: Log Directory
```bash
-v $(pwd)/logs:/work/logs
```

| Component | Description |
|-----------|-------------|
| **Host Path** | Directory for step execution logs |
| **Container Path** | `/work/logs` - Default log location |
| **Purpose** | Persist logs after container exits |
| **Read/Write** | Write (creates log files) |

**Requirements**:
- Directory must exist (created automatically if using `$(pwd)`)
- Minimum 100 MB free space
- Write permissions for Docker user

**Generated Files**:
```
logs/
├── step1_make_directories.log
├── step2_fastq_dump.log
├── step3_fastq_gzip.log
├── step4_parse_fastq.log
├── step5_move_success.log
├── step6_check_counts.log
├── step7_rename_fastq.log
├── step8_cellranger.log
└── step9_retry_failed.log
```

**Log Access**:
```bash
# View logs during execution
tail -f logs/step8_cellranger.log

# Check for errors after completion
grep -i error logs/*.log   # Windows PowerShell: Select-String -Pattern error logs/*.log
```

---

#### Mount 6: Results Directory
```bash
-v $(pwd)/results:/work/results
```

| Component | Description |
|-----------|-------------|
| **Host Path** | Directory for pipeline outputs |
| **Container Path** | `/work/results` - Default output location |
| **Purpose** | Persist results after container exits |
| **Read/Write** | Write (creates output files) |

**Requirements**:
- Directory must exist
- Minimum 50 GB free space per sample
- Write permissions for Docker user

**Generated Structure**:
```
results/
├── fastq_success.txt           # List of successful samples
├── fastq_failed.txt            # List of failed samples
├── reports/                    # Per-step sentinels (step*.ok/.warn/.err/.msg)
├── retry_failed/               # Failed samples for retry
├── runs/                       # One directory per run
│   ├── LATEST                  # Newest run id
│   └── <run_id>/
│       ├── expected_samples.tsv  # What this run was asked to process
│       ├── cellranger/           # One record per sample this run's Cell Ranger ran
│       ├── outcome.json          # What it achieved, per sample
│       └── samples/              # The same, one file per sample
└── success/                    # Successful sample outputs
    └── SRR9134611/
        ├── SRR9134611.sra
        ├── SRR9134611_S1_R1_001.fastq.gz
        ├── SRR9134611_S1_R2_001.fastq.gz
        ├── .genoar_cellranger.json   # Receipt: which run made this, from which input
        └── cellranger_output/
```

**Disk Space Estimation**:
- SRA file: ~8 GB
- FASTQ files (compressed): ~12 GB
- Cell Ranger BAM: ~12 GB
- Cell Ranger matrices: ~1 GB
- Total per sample: ~33 GB

---

### Optional Mounts

#### Additional Input Directory (Alternative SRA Location)
```bash
-v /mnt/storage/sra_archive:/work/data/input
```

**Use case**: Read SRA files from network storage or alternative location.

**Configuration**:
```yaml
# config.yaml
input_dir: /work/data/input  # Override default /work/data/sra
```

---

#### Custom Retry Directory
```bash
-v $(pwd)/retry_samples:/work/results/retry_failed
```

**Use case**: Separate failed samples for re-processing.

---

## 5. Configuration File Setup

### Template: example.yaml

```yaml
# Example config for SRR pipeline

# ===== Input/Output Paths =====
input_dir: /work/data/sra          # Where SRR*.sra files are located
output_dir: /work/results          # Root directory for all outputs
ref_path: /ref                     # Cell Ranger reference; the mount target itself

# ===== Resource Settings =====
cores: 16                          # Parallel jobs for steps 2-3
gzip_threads: 2                    # Threads per gzip operation (step 3)
cellranger_threads: 16             # Cell Ranger --localcores (step 8)
cellranger_mem: 100                # Cell Ranger --localmem in GB (step 8)
cellranger_bin: /opt/cellranger/cellranger  # Cell Ranger binary

# ===== Pipeline Options =====
download: false                    # Reserved for future SRA download
log_dir: /work/logs
legacy_pipeline: false             # true selects the frozen earlier tree
```

`srr_pipeline_package/configs/example.yaml` also carries `success_dir`,
`retry_move_dir` and `keep_intermediate`. Nothing reads them: the success and
retry directories are `<output_dir>/success` and `<output_dir>/retry_failed`, and
step 3 replaces the uncompressed FASTQ whatever `keep_intermediate` says.

### Parameter Reference

#### Input/Output Paths

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `input_dir` | Path | `/work/data/sra` | Directory containing `.sra` files |
| `output_dir` | Path | `/work/results` | Root output directory. `success/` and `retry_failed/` are derived from it |
| `ref_path` | Path | **Required** | Cell Ranger reference. The bind-mount target itself, not a directory below it |

**Path Requirements**:
- Must be **container paths** (not host paths)
- Must match Docker volume mounts
- Use absolute paths only

---

#### Resource Settings

| Parameter | Type | Range | Recommended | Description |
|-----------|------|-------|-------------|-------------|
| `cores` | Integer | 1-128 | 16 | Parallel jobs (steps 2-3) |
| `gzip_threads` | Integer | 1-16 | 2 | Threads per compression job |
| `cellranger_threads` | Integer | 4-64 | 16 | Cell Ranger CPU cores |
| `cellranger_mem` | Integer | 64-256 | 100 | Cell Ranger memory (GB) |
| `cellranger_bin` | Path | **Required** | `/opt/cellranger/cellranger` | Cell Ranger executable |

**Tuning Guidelines**:

```yaml
# Low-resource system (32 GB RAM, 8 cores)
cores: 8
gzip_threads: 1
cellranger_threads: 8
cellranger_mem: 64

# Medium-resource system (128 GB RAM, 16 cores)
cores: 16
gzip_threads: 2
cellranger_threads: 16
cellranger_mem: 100

# High-resource system (256 GB RAM, 32 cores)
cores: 32
gzip_threads: 4
cellranger_threads: 32
cellranger_mem: 200
```

**Memory Calculation**:
```
Required RAM = cellranger_mem + (cores × 2 GB) + 10 GB (system overhead)

Example (default config):
  100 GB (Cell Ranger) + (16 × 2 GB) + 10 GB = 142 GB minimum
```

---

#### Pipeline Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `log_dir` | Path | `/work/logs` | Log file directory |
| `legacy_pipeline` | Boolean | `false` | `true` runs the frozen earlier tree (`/pipeline/`) instead of the current one (`/pipeline_next/`) |
| `download` | Boolean | `false` | Reserved for future use |
| `keep_intermediate` | Boolean | — | Not implemented. Step 3 calls `pigz`/`gzip` without `-k`, so the uncompressed FASTQ is always replaced |
| `retry_move_dir` | Path | — | Not implemented. Step 9 writes to `<output_dir>/retry_failed` |
| `success_dir` | Path | — | Not implemented. Steps 5-9 use `<output_dir>/success` |

**Disk space**: step 3 releases the uncompressed FASTQ as it compresses (~25 GB
per sample), so the peak is one sample's uncompressed reads, not the whole batch.

---

### Configuration Examples

#### Example 1: Single Sample Test (Minimal Resources)
```yaml
input_dir: /work/data/sra
output_dir: /work/results
ref_path: /ref

cores: 4
gzip_threads: 1
cellranger_threads: 8
cellranger_mem: 64
cellranger_bin: /opt/cellranger/cellranger

log_dir: /work/logs
```

**Use case**: Testing pipeline with small sample on laptop/workstation.

---

#### Example 2: Batch Processing (Production)
```yaml
input_dir: /work/data/sra
output_dir: /work/results
ref_path: /ref

cores: 32
gzip_threads: 4
cellranger_threads: 24
cellranger_mem: 150
cellranger_bin: /opt/cellranger/cellranger

log_dir: /work/logs
```

**Use case**: High-throughput processing of multiple samples on HPC.

---

#### Example 3: Mouse Genome
```yaml
input_dir: /work/data/sra
output_dir: /work/results
ref_path: /ref/mm10-2020-A         # Mouse reference

cores: 16
gzip_threads: 2
cellranger_threads: 16
cellranger_mem: 100
cellranger_bin: /opt/cellranger/cellranger

log_dir: /work/logs
```

**Docker mount** (must match `ref_path`):
```bash
-v /path/to/refdata-gex-mm10-2020-A:/ref/mm10-2020-A
```

---

## 6. Execution Options

### Option 1: Complete Pipeline (Steps 1-9)

**Command**:
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

**What happens**:
1. Step 1: Organize SRA files → `/work/data/sra/SRR*/`
2. Step 2: Extract FASTQ files → `SRR*_1.fastq`, `SRR*_2.fastq`
3. Step 3: Compress FASTQ → `SRR*_1.fastq.gz`, `SRR*_2.fastq.gz`
4. Step 4: Classify success/failed → `fastq_success.txt`, `fastq_failed.txt`
5. Step 5: Copy success samples → `/work/results/success/`
6. Step 6: Count FASTQ files → `fastq_N_files.tsv`
7. Step 7: Rename to Cell Ranger format → `*_S1_R1_001.fastq.gz`
8. Step 8: Run Cell Ranger → `cellranger_output/`
9. Step 9: Detect failed samples → `retry_failed/`

**Duration**: ~60-90 minutes per sample

**When to use**:
- ✅ First time running pipeline
- ✅ Production processing
- ✅ Complete analysis needed
- ✅ Default for most users

---

### Option 2: Partial Pipeline (Steps 1-7 Only)

**Build step7 image**:
```bash
docker build \
  -f srr_pipeline_package/docker/Dockerfile \
  --target step7 \
  -t genoar-srr:step7 \
  .
```

**Run**:
```bash
docker run --rm -it \
  -v $(pwd)/sample_sra:/work/data/sra \
  -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \
  -v $(pwd)/logs:/work/logs \
  -v $(pwd)/results:/work/results \
  genoar-srr:step7
```

**Notes**:
- ⚠️ No Cell Ranger reference or binary needed
- ⚠️ Stops after FASTQ renaming
- ⚠️ Manual Cell Ranger execution required

**Duration**: ~10-15 minutes per sample

**When to use**:
- ✅ FASTQ extraction only (no analysis)
- ✅ Testing SRA downloads
- ✅ Manual Cell Ranger parameter tuning
- ✅ Debugging FASTQ issues

---

### Option 3: Re-run After a Failure

**Scenario**: Pipeline failed at step 5, you want to carry on.

**Read what happened**:
```bash
ls results/reports/
# step1.ok  step2.ok  step3.ok  step4.ok  step5.err
cat results/reports/step5.msg      # the reason
```

**Fix the cause, then run the same command again**:
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

**Behavior**: every run executes steps 1-9. The sentinels are a report of what
this run did, not a switch that skips anything, and no step consults them.

What is not repeated is the expensive work, and that is decided by evidence
rather than by a filename:

- Steps 1-7 are cheap and idempotent. They find their outputs already in place
  and pass over them.
- Step 8 does not re-run Cell Ranger for a sample whose existing BAM a receipt
  already vouches for against the same input. That sample is reported as a
  **cache hit** — reused, and never counted as new work.
- A sample whose BAM nothing accounts for is `unverified` and is **not**
  counted, even though it exists. That is the case to look at after a partial
  run.

A re-run is a new run with its own id and its own record; earlier run
directories are never touched. See
[What the Run Directory Records](#what-the-run-directory-records) for how a
cache hit is decided.

**When to use**:
- ✅ Disk full error recovery
- ✅ Network interruption recovery
- ✅ Configuration fix after partial run

---

### Option 4: Dry Run (FASTQ Validation Only)

**Build step4 image**:
```bash
docker build \
  -f srr_pipeline_package/docker/Dockerfile \
  --target step4 \
  -t genoar-srr:step4 \
  .
```

**Run**:
```bash
docker run --rm -it \
  -v $(pwd)/sample_sra:/work/data/sra \
  -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \
  -v $(pwd)/logs:/work/logs \
  -v $(pwd)/results:/work/results \
  genoar-srr:step4
```

**Output**:
```
results/
├── fastq_success.txt    # Samples with valid R1/R2 FASTQ
└── fastq_failed.txt     # Samples with missing/corrupt FASTQ
```

**Duration**: ~10-15 minutes per sample

**When to use**:
- ✅ Validate SRA files before expensive Cell Ranger run
- ✅ Quick quality check
- ✅ Identify problematic samples early
- ✅ Batch pre-screening

---

### Option 5: Cell Ranger Only (Steps 8-9)

**Prerequisites**:
- FASTQ files already renamed to Cell Ranger format
- Files located in `/work/results/success/SRR*/`

**Build step8-9 image**:
```bash
docker build \
  -f srr_pipeline_package/docker/Dockerfile \
  --target step9 \
  -t genoar-srr:step8-9 \
  .
```

**Run**:
```bash
docker run --rm -it \
  -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \
  -v $(pwd)/ref:/ref \
  -v $(pwd)/cellranger:/opt/cellranger \
  -v $(pwd)/logs:/work/logs \
  -v $(pwd)/results:/work/results \
  genoar-srr:step8-9
```

Steps 1-7 still run. They find the renamed FASTQs already in place and pass over
them; writing sentinel files by hand skips nothing, because nothing reads them.

**When to use**:
- ✅ Re-run Cell Ranger with different parameters
- ✅ Different reference genome
- ✅ FASTQ files obtained from external source
- ✅ Resume after Cell Ranger failure

---

## 7. Step-by-Step Execution Guide

### Beginner Workflow: Single Sample Test

#### Step 1: Prepare Environment
```bash
# Navigate to project directory
cd /path/to/genoar

# Verify prerequisites
docker --version        # Should be 20.10+
df -h .                 # Should have 50+ GB free
nproc                   # Check CPU cores

# Create directories
mkdir -p sample_sra
mkdir -p logs
mkdir -p results
```

#### Step 2: Download Sample SRA File
```bash
# Option A: Use prefetch (recommended)
prefetch SRR9134611
mv SRR9134611/SRR9134611.sra sample_sra/

# Option B: Direct wget
wget https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR9134611/SRR9134611 \
  -O sample_sra/SRR9134611.sra

# Verify download
ls -lh sample_sra/
# Expected: SRR9134611.sra (7-10 GB)
```

#### Step 3: Obtain Cell Ranger Reference
```bash
# Download reference (if not already present)
wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz

# Extract and move to the project root as ref/
tar -xzf refdata-gex-GRCh38-2024-A.tar.gz
mv refdata-gex-GRCh38-2024-A ref/

# Verify structure
ls -lh ref/
# Expected: fasta/, genes/, star/, reference.json
```

#### Step 4: Install Cell Ranger Binary
```bash
# Download from 10x Genomics (requires login)
# URL: https://www.10xgenomics.com/support/software/cell-ranger/downloads

# Extract and move to the project root as cellranger/
tar -xzf cellranger-8.0.1.tar.gz
mv cellranger-8.0.1 cellranger/

# Verify
cellranger/cellranger --version
```

#### Step 5: Configure Pipeline
```bash
# Copy template
cp srr_pipeline_package/configs/example.yaml \
   srr_pipeline_package/configs/my_config.yaml

# Edit resource settings (optional)
nano srr_pipeline_package/configs/my_config.yaml

# Minimal changes needed:
# - ref_path: /ref (verify matches mount)
# - cellranger_bin: /opt/cellranger/cellranger (verify matches mount)
```

#### Step 6: Build Docker Image
```bash
# Build step9 image (complete pipeline)
docker build \
  -f srr_pipeline_package/docker/Dockerfile \
  --target step9 \
  -t genoar-srr:step9 \
  .

# Expected output:
# Successfully built <image_id>
# Successfully tagged genoar-srr:step9

# Verify image
docker images | grep genoar-srr   # Windows PowerShell: docker images | Select-String genoar-srr
```

#### Step 7: Run Pipeline
```bash
# Execute complete pipeline
docker run --rm -it \
  -v $(pwd)/sample_sra:/work/data/sra \
  -v $(pwd)/srr_pipeline_package/configs/my_config.yaml:/work/config.yaml \
  -v $(pwd)/ref:/ref \
  -v $(pwd)/cellranger:/opt/cellranger \
  -v $(pwd)/logs:/work/logs \
  -v $(pwd)/results:/work/results \
  genoar-srr:step9

# Expected duration: ~60-90 minutes
```

#### Step 8: Monitor Progress
```bash
# In another terminal, watch logs
tail -f logs/step8_cellranger.log

# Check sentinel files
watch -n 10 ls -lh results/reports/
```

#### Step 9: Verify Results
```bash
# Check completion status
cat results/reports/summary.jsonl

# List outputs
tree -L 3 results/success/

# Verify key files
ls -lh results/success/SRR9134611/cellranger_output/outs/
# Expected:
# - possorted_genome_bam.bam (10-15 GB)
# - filtered_feature_bc_matrix.h5 (20-50 MB)
# - web_summary.html (5 MB)
```

#### Step 10: Review QC Report
```bash
# Open web summary in browser
firefox results/success/SRR9134611/cellranger_output/outs/web_summary.html

# Or copy to desktop for viewing
cp results/success/SRR9134611/cellranger_output/outs/web_summary.html \
   ~/Desktop/SRR9134611_QC.html
```

---

### Advanced Workflow: Batch Processing

#### Scenario: Process 10 samples overnight

```bash
# 1. Prepare batch input
sample_sra/
├── SRR9134611.sra
├── SRR9134612.sra
├── SRR9134613.sra
├── ...
└── SRR9134620.sra

# 2. Configure for high-throughput
cat > srr_pipeline_package/configs/batch_config.yaml <<EOF
input_dir: /work/data/sra
output_dir: /work/results
ref_path: /ref

cores: 32                  # Max parallel jobs
gzip_threads: 4
cellranger_threads: 24     # Per-sample Cell Ranger cores
cellranger_mem: 150
cellranger_bin: /opt/cellranger/cellranger

log_dir: /work/logs
EOF

# 3. Run in detached mode with resource limits
docker run --rm -d \
  --name srr-batch-run \
  --cpus=32 \
  --memory=200g \
  -v $(pwd)/sample_sra:/work/data/sra \
  -v $(pwd)/srr_pipeline_package/configs/batch_config.yaml:/work/config.yaml \
  -v $(pwd)/ref:/ref \
  -v $(pwd)/cellranger:/opt/cellranger \
  -v $(pwd)/logs:/work/logs \
  -v $(pwd)/results:/work/results \
  genoar-srr:step9

# 4. Monitor progress
docker logs -f srr-batch-run

# 5. Check status periodically
# Note: process substitution <(...) is bash/zsh only — on Windows use WSL, or run:
#   Windows PowerShell: docker logs srr-batch-run 2>&1 | Select-String "^\[done\]"
grep -E "^\[done\]" <(docker logs srr-batch-run 2>&1)

# 6. Review results after completion
cat results/fastq_success.txt
cat results/fastq_failed.txt
```

---

## 8. Troubleshooting

### Exit Codes

Read the container's exit status before the logs. "Every step exited 0" is not
the same claim as "Cell Ranger analysed data", and the pipeline keeps them apart:

| Exit | Meaning |
|------|---------|
| `0` | Every sample this run was asked to process has verified Cell Ranger output. Also returned when this image has no Cell Ranger stage at all (a steps 1–7 build target), which the final banner states explicitly |
| `1` | A step failed and the banner names it. Also: the run directory could not be created at all (read-only tree, full disk, mount gone) |
| `2` | Misconfigured: no usable `cellranger` executable, `/ref` is not a usable reference, `cellranger --version` answered no version, or a QC floor is not a number. Detected before anything runs |
| `3` | The run was valid and complete, and **zero** of its expected samples were analysed. A run given no input reports this too |
| `4` | Partial: this run cannot show verified output for every expected sample, and some expected sample carries output it accounts for. That output is either verified (some analysed, others not) or adopted |

```bash
docker run --rm ... genoar-srr:step9; echo "exit=$?"
```

Exit `2` is what a missing mount produces. `/ref` or `/opt/cellranger` absent
or unusable is a configuration fault. It is not a valid empty result. The banner
lists every problem it found. A launcher that is present and executable but
answers no version is the same fault: step 8 chooses Cell Ranger's arguments by
release, so a run that cannot name the release stops rather than spending the
allocation. On Apple Silicon this is what an x86-64-only Cell Ranger looks like. An unusable `GENOAR_RUN_ID`, one that already names a
run, or one another run reserved at the same moment is the same kind of fault and
the same exit `2`. Exit `1` is the other answer. It means the run directory could
not be created at all, which is a fact about the volume. It does **not** mean the id
is taken.

Exit `3` means the run was fine and no expected sample qualified. The census is
written to `results/success/cellranger_eligibility.tsv` (every sample, with a
reason) and `results/success/cellranger_ineligible.tsv` (what steps 6 and 7 ruled
out earlier). The most common reason is a sample with a single FASTQ file: Cell
Ranger requires a paired R1/R2 read set.

Exit `4` means this run cannot show verified output for every expected sample, and
some expected sample carries output it accounts for. The per-sample reasons are
printed and recorded in `results/runs/<run_id>/outcome.json`. A run whose only
completion is adopted also exits `4`. Adoption is not a completion, so such a run
verified nothing and is not a success.

A sample whose multi-lane FASTQ names do not pair R1 with R2 within the same lane
is excluded before Snakemake starts, with that reason in the census. Selecting
one would fail `cellranger count`, and that single failure fails the whole
Snakemake step, ending the run before its remaining samples are analysed. See
[Cell Ranger eligibility](srr_pipeline_package/README.md#cell-ranger-eligibility).

### What the Run Directory Records

`/work/results` is persistent, so a BAM on disk proves nothing about which run
made it. Every run fixes its expected sample set before step 1 and records the
outcome against that set alone:

```bash
cat results/runs/LATEST                        # newest run id
cat results/runs/<run_id>/expected_samples.tsv # what the run was asked to do
cat results/runs/<run_id>/outcome.json         # what it achieved, per sample
ls  results/runs/<run_id>/cellranger/          # samples its Cell Ranger ran
```

A completed sample also carries `results/success/<sample>/.genoar_cellranger.json`,
tying its BAM to the input it came from and the run that made it. Output counts as
this run's own work only when the Cell Ranger rule left a completion record for
that sample. That record is `results/runs/<run_id>/cellranger/<sample>.json`. The
job that ran Cell Ranger writes it, naming the file it produced. A BAM newer than
the run's start is not by itself evidence of who made it. Output that a receipt
already vouches for, against the same input and the same file, counts as a
**cache hit**. Output with neither a completion record nor such a receipt is
**unverified** and does **not** count. Set `GENOAR_ADOPT_PRIOR_RESULTS=1` to adopt
such output deliberately. It is then recorded as an adoption, and never as this
run's own work. Receipts are written only for output this run produced or the
operator adopted.

**Adoption never completes a run.** The banner always prints verified and adopted
as two separate counts. They are two different claims:

```text
[result]   verified complete: 2 (fresh 1, cache hit 1)
[result]   adopted, NOT verified and NOT counted as complete: 1
```

Adopted output is excluded from verified completion and from the exit-0
calculation. A run whose only completion is adopted exits `4`.

**How the run tells its own output from another file.** The completion record
stamps the device and inode the BAM occupies, its size, a timestamp floor, and the
SHA-256 of its first and last 64 KiB. Only the ends are hashed, because a Cell
Ranger BAM is several gigabytes. The inode decides the match. Where everything
agrees except the inode number, the run compares the recorded digests. A digest is
computed from the file's bytes, so it does not depend on the filesystem's
bookkeeping.

This matters on Docker Desktop for macOS. Its bind mounts do not keep an inode
number stable for an unchanged file. Without the digests, a healthy run on such a
mount reported its own output as unverified. With them the run completes normally
and exits `0`.

The rule warns at the time if it could not read the file's ends, and that record
carries no digests. On a filesystem that renumbers the file, the sample is then
reported `unverified`. The reason names both states that produce it, a filesystem
that renumbers the file and an output swapped for one of the same size and
timestamp, and gives the action for each. Cell Ranger is Linux-x86-64 only, so a Mac
is a rehearsal environment for Stage 3 in any case.

Set `GENOAR_RUN_ID` (`-e GENOAR_RUN_ID=...`) to name the run yourself. It becomes
a directory name under `results/runs/`, so it must match
`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. Surrounding whitespace is stripped. `LATEST`
is reserved for the newest-run pointer. The comparison is case-sensitive, so
`latest` is fine. An id that fails that rule, or that already names a run
directory, exits `2` before any step runs. A run record is never overwritten.

> **Before upgrading an existing deployment.** The input fingerprint now covers
> every file of a multi-file FASTQ input. Earlier versions covered only the
> largest file. Receipts written by earlier versions for those samples no longer
> match, so they read as `unverified` on the first run after the upgrade and are
> not counted as complete. Re-run them, or adopt the existing output once with
> `GENOAR_ADOPT_PRIOR_RESULTS=1`. Single-file `.sra` inputs are unaffected. Their
> fingerprint is unchanged.

Per-step sentinels in `results/reports/` follow the same distinction:
`stepN.ok` when the step did its work, `stepN.err` when it failed, and
`stepN.warn` — written *instead of* `stepN.ok` — when it completed without doing
its work.

### Common Errors

#### Error 1: "Cannot connect to Docker daemon"
```bash
# Error message
Cannot connect to the Docker daemon at unix:///var/run/docker.sock

# Solution
sudo systemctl start docker
sudo usermod -aG docker $USER
# Log out and log back in
```

---

#### Error 2: "No space left on device"
```bash
# Error during step 3 or step 8
ERROR: No space left on device

# Check disk usage
df -h $(pwd)/results

# Solutions:
# 1. Free up space
docker system prune -a  # Remove unused images
rm -rf results/success/*/SRR*.sra  # Remove SRA after FASTQ

# 2. Use external storage
mkdir /mnt/storage/results
docker run ... -v /mnt/storage/results:/work/results ...
```

---

#### Error 3: "Cell Ranger reference not found"
```bash
# Error during step 8
ERROR: Could not find reference at /ref

# Diagnosis
# 1. Check mount path
docker run --rm -it genoar-srr:step9 ls /ref/
# Should show: fasta/  genes/  reference.json  star/

# 2. Verify host path
ls $(pwd)/ref/
# Should show: fasta/, genes/, star/

# Solution: Fix mount path
# Ensure absolute path:
-v $(pwd)/ref:/ref

# Ensure config.yaml matches:
ref_path: /ref
```

---

#### Error 4: "Permission denied" (logs/results)
```bash
# Error
/work/logs/step1.log: Permission denied

# Cause: Docker writes as root, host user can't access

# Solution 1: Change ownership (after run)
sudo chown -R $USER:$USER logs
sudo chown -R $USER:$USER results

# Solution 2: Use Docker user mapping (advanced)
docker run --user $(id -u):$(id -g) ...
```

---

#### Error 5: "FASTQ files not found" (step 7)
```bash
# Error during step 7
WARNING: Expected at least 2 fastq.gz for SRR9134611; found 0

# Diagnosis
ls results/success/SRR9134611/
# Should show: SRR9134611_1.fastq.gz, SRR9134611_2.fastq.gz

# Common causes:
# 1. Step 5 did not copy the files
ls results/reports/step5.ok results/reports/step5.warn results/reports/step5.err
cat results/reports/step5.msg 2>/dev/null

# 2. Compression failed (check step 3 logs)
cat logs/step3_fastq_gzip.log

# 3. File naming mismatch
# Expected: SRR9134611_1.fastq.gz, SRR9134611_2.fastq.gz
# Not: SRR9134611.fastq.gz or other formats

# Solution: fix the cause and run the same command again. Steps 1-9 all run;
# removing sentinel files changes nothing.
docker run ... genoar-srr:step9
```

---

#### Error 6: "Cell Ranger out of memory"
```bash
# Error during step 8
ERROR: Cell Ranger killed (OOM)

# Diagnosis
dmesg | grep -i kill  # Check kernel OOM killer (Linux/WSL only; not available in Windows host shells)

# Solutions:
# 1. Reduce Cell Ranger memory
cellranger_mem: 64  # Instead of 100

# 2. Increase Docker memory limit
docker run --memory=150g ...

# 3. Reduce parallel samples (if batch processing)
cores: 16  # Instead of 32 (fewer concurrent samples)
```

---

### Debugging Steps

#### 1. Check Sentinel Files
```bash
# View step completion status
ls -lh results/reports/
# step1.ok   - the step did its work
# step5.err  - the step failed
# step8.warn - the step finished without doing its work (e.g. zero samples were
#              eligible for Cell Ranger). Written INSTEAD of step8.ok, so a
#              check for .ok and .err alone finds neither
# step8.msg  - the reason, written beside a .warn or an .err
# (missing)  - the step was not reached
#
# These record what this run did. Nothing reads them back: every run executes
# steps 1-9 regardless of what is in this directory.
```

#### 2. Review Logs
```bash
# Check specific step log
cat logs/step2_fastq_dump.log

# Search for errors across all logs
grep -i error logs/*.log   # Windows PowerShell: Select-String -Pattern error logs/*.log

# View last 50 lines of current step
tail -50 logs/step8_cellranger.log
```

#### 3. Validate Input Data
```bash
# Check SRA files
file sample_sra/*.sra
# Expected: SRR9134611.sra: SQLite 3.x database

# Validate SRA integrity
vdb-validate sample_sra/SRR9134611.sra
```

#### 4. Test Cell Ranger Manually
```bash
# Enter container interactively
docker run --rm -it \
  -v $(pwd)/ref:/ref \
  -v $(pwd)/cellranger:/opt/cellranger \
  genoar-srr:step9 bash

# Inside container
/opt/cellranger/cellranger --version
ls /ref/
```

---

## 9. Advanced Usage

### Custom Step Execution

#### Re-run Cell Ranger With Different Parameters

**Scenario**: run step 8 again on the same sample with `cellranger_threads: 32`

There is no way to start the pipeline at a chosen step: every run executes steps
1-9, and the `stepN.*` sentinels are a report, not a switch. What decides whether
Cell Ranger runs again is the receipt beside the existing BAM. While that receipt
vouches for the BAM against the same input, the sample is a cache hit and the new
`cellranger_threads` is never used. Remove the output to make the work happen:

```bash
# 1. Update config
nano srr_pipeline_package/configs/example.yaml
# Change: cellranger_threads: 32

# 2. Retire the existing output, so step 8 has work to do
rm -rf results/success/SRR9134611/cellranger_output

# 3. Run the pipeline
docker run --rm -it \
  -v $(pwd)/sample_sra:/work/data/sra \
  -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \
  -v $(pwd)/ref:/ref \
  -v $(pwd)/cellranger:/opt/cellranger \
  -v $(pwd)/logs:/work/logs \
  -v $(pwd)/results:/work/results \
  genoar-srr:step9
```

Leaving the BAM in place is what a resume wants, and the run reports it as a
cache hit rather than as work it did.

---

### Selecting a Different Config File

The entrypoint reads `/work/config.yaml` by default. Two ways to point it elsewhere:

```bash
# Environment variable
docker run --rm -it -e CONFIG=/work/configs/my_config.yaml -v ... genoar-srr:step9

# Or as an argument
docker run --rm -it -v ... genoar-srr:step9 --config /work/configs/my_config.yaml
```

**Note**: Individual settings (`cores`, `cellranger_threads`, …) are not readable
from the environment. Change them in the YAML file, or mount a different one.

---

### Resource Monitoring

```bash
# Monitor container resource usage
docker stats

# Expected output:
# CONTAINER ID   CPU %   MEM USAGE / LIMIT   MEM %
# abc123def456   1600%   95GB / 200GB        47.5%

# Explanation:
# - CPU %: 1600% = 16 cores fully utilized
# - MEM USAGE: Cell Ranger currently using 95 GB
```

---

### Parallel Sample Processing

**Automatic**: Pipeline processes all `SRR*.sra` files in `input_dir`

```bash
# Multiple samples in sample_sra/
sample_sra/
├── SRR9134611.sra
├── SRR9134612.sra
└── SRR9134613.sra

# Run pipeline once
docker run --rm -it \
  -v $(pwd)/sample_sra:/work/data/sra \
  ... \
  genoar-srr:step9

# Behavior:
# - Steps 1-7: Process all samples in parallel (up to 'cores' limit)
# - Step 8: Snakemake schedules Cell Ranger jobs sequentially
#   (parallelization controlled by cellranger_threads)
```

**Manual Parallelization** (multiple containers):

```bash
# Split samples into batches
mkdir sample_batch1 sample_batch2
mv sample_sra/SRR913461{1,2,3}.sra sample_batch1/
mv sample_sra/SRR913461{4,5,6}.sra sample_batch2/

# Run separate containers
docker run -d --name batch1 \
  -v $(pwd)/sample_batch1:/work/data/sra \
  -v $(pwd)/results_batch1:/work/results \
  ... genoar-srr:step9

docker run -d --name batch2 \
  -v $(pwd)/sample_batch2:/work/data/sra \
  -v $(pwd)/results_batch2:/work/results \
  ... genoar-srr:step9

# Monitor both
docker logs -f batch1  # Terminal 1
docker logs -f batch2  # Terminal 2
```

---

### Integration with HPC Schedulers

The example below runs this image directly, on a site that permits Docker on its
compute nodes. Most sites do not. For those, `srr_pipeline_package/hpc/`
generates a Singularity handoff bundle and submits it, and
`srr_pipeline_package/singularity/` covers the Docker-free run itself. The
handoff supports Slurm, PBS Pro and Torque, and Slurm is the default. See
[srr_pipeline_package/hpc/README.md](srr_pipeline_package/hpc/README.md).

#### SLURM Example

```bash
#!/bin/bash
#SBATCH --job-name=srr-pipeline
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=200G
#SBATCH --time=24:00:00
#SBATCH --output=srr_pipeline_%j.log

# Load Docker/Singularity
module load docker

# Run pipeline
docker run --rm \
  --cpus=$SLURM_CPUS_PER_TASK \
  --memory=${SLURM_MEM_PER_NODE}M \
  -v /scratch/$USER/sample_sra:/work/data/sra \
  -v /scratch/$USER/configs/batch.yaml:/work/config.yaml \
  -v /data/references/GRCh38-2024-A:/ref \
  -v /data/cellranger:/opt/cellranger \
  -v /scratch/$USER/logs:/work/logs \
  -v /scratch/$USER/results:/work/results \
  genoar-srr:step9
```

---

## Summary

### Quick Reference Card

| Task | Docker Target | Command |
|------|---------------|---------|
| **Full pipeline** | `step9` | All 9 steps, Cell Ranger included |
| **FASTQ only** | `step7` | Stops after FASTQ renaming |
| **Validation** | `step4` | Check SRA → FASTQ success rate |
| **Cell Ranger only** | `step9` over FASTQ already in `results/success/` | Steps 1-7 find their work done |

The Dockerfile also defines `base` and `step1`–`step8`. `pipeline` is an alias for
`step9`. Building any target below `step8`
gives an image without Snakemake, which exits `0` while stating that no
single-cell analysis was performed.

### Essential Mounts (Minimum Required)

```bash
# For full pipeline (step9)
-v $(pwd)/sample_sra:/work/data/sra                                    # Input
-v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml  # Config
-v $(pwd)/ref:/ref                                                     # Reference
-v $(pwd)/cellranger:/opt/cellranger                                   # Install root
-v $(pwd)/logs:/work/logs                                              # Logs
-v $(pwd)/results:/work/results                                        # Outputs
```

### Pre-Flight Checklist

- [ ] Docker installed and running
- [ ] Linux x86-64 (Cell Ranger ships for no other architecture; step 0
      stops the run where its launcher cannot answer `--version`)
- [ ] 50+ GB free disk space
- [ ] 100+ GB RAM available
- [ ] SRA files in `sample_sra/`
- [ ] Cell Ranger reference downloaded and extracted
- [ ] Cell Ranger binary downloaded and extracted
- [ ] Configuration file reviewed
- [ ] All volume mount paths are absolute (for reference/binary)
- [ ] Log and results directories exist

---

**For additional help**:
- Review logs: `logs/stepN_*.log`
- Check documentation: `srr_pipeline_package/docs/`
- Troubleshooting guide: [Section 8](#8-troubleshooting)

---

**Document Version**: 1.0
**Last Updated**: 2026-08-12
**Pipeline Version**: Steps 1-9 (Dockerized)
