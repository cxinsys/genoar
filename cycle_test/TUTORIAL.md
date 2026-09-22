# GENOAR End-to-End Pipeline Tutorial (Linux Server)

This document guides first-time GENOAR users through running the full **Stage 1 → Stage 2 → Stage 3** pipeline on a **Linux server** via `cycle_test`, with verification at each step.

The tutorial is organized into four stages, each assuming the previous one succeeded. If something fails mid-way, do not proceed — consult [Troubleshooting](#troubleshooting) first.

```
[0] Prerequisites → [1] Dry run → [2] Stage 1+2 only → [3] Small end-to-end
```

- **Total expected duration:** 3–12 hours (including small-scale Stage 3 run)
- **Required access:** a user able to run Docker (member of `docker` group or sudo)
- **Target path:** this document assumes the GENOAR repository lives at `/path/to/genoar`. If yours is elsewhere, substitute accordingly.

---

## 0. Prerequisites

### 0.1 System requirements check

Before starting, confirm the server meets the following conditions.

```bash
# Architecture (Stage 3 requires AMD64)
uname -m            # → must be x86_64

# CPU cores
nproc               # → 16+ recommended

# Memory
free -g | awk 'NR==2{print $2" GB"}'   # → 128GB+ recommended, 64GB minimum

# Free disk space (100GB minimum for a small-scale test)
df -h /path/to/genoar   # check the Available column

# Docker
docker --version    # Docker 20.10 or newer

# Python
python3 --version   # Python 3.9 or newer
```

> **Note.** If `uname -m` shows `aarch64`/`arm64`, Stage 3 (Cell Ranger) cannot run. In that case only `[1]`–`[2]` are feasible.

### 0.2 Install a download tool (optional but recommended)

Stage 3a downloads large SRA files in parallel from AWS S3. If `aria2c` is available, it will be used automatically and significantly boosts throughput.

```bash
sudo apt update && sudo apt install -y aria2
which aria2c        # → /usr/bin/aria2c
```

If you cannot install `aria2c`, the script falls back to `wget` or `curl` automatically.

### 0.3 Build the three Docker images

`cycle_test` depends on three Docker images. Build each one from the repository root.

```bash
cd /path/to/genoar

# Stage 1: GEO Crawler (AMD64)
docker build -t genoar-crawler:amd64 -f genoar_crawler/Dockerfile.amd64 genoar_crawler/

# Stage 2: UMLS Analysis
docker build -t genoar-analysis:latest -f genoar_analysis/docker/Dockerfile .

# Stage 3: SRR Pipeline (AMD64 only)
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

**Expected build time:** Stage 1 ~5–10 min, Stage 2 ~3–5 min, Stage 3 ~10–15 min.

**Verification:**

```bash
docker images | grep -E "genoar-crawler|genoar-analysis|genoar-srr"
```

You should see three lines, for example:
```
genoar-crawler    amd64     ...   1.2GB
genoar-analysis   latest    ...   500MB
genoar-srr        step9     ...   700MB
```

### 0.4 Verify the UMLS tables (no download needed)

The three UMLS CSVs ship with the repository. Confirm the filenames match exactly.

```bash
ls /path/to/genoar/all_query_results/umls_*_df.csv
# Expected:
#   umls_celltype_df.csv
#   umls_disease_df.csv
#   umls_tissue_df.csv
```

If any of the three is missing, Stage 2 will fail.

### 0.5 Install Cell Ranger (Stage 3 only)

This is only required for the `[3]` small-scale end-to-end step. Skip it if you plan to run only `[1]`–`[2]`.

1. Download a Cell Ranger 8.x+ tarball from the [10x Genomics downloads page](https://www.10xgenomics.com/support/software/cell-ranger/downloads) (a free account is required).
2. Extract it under the repository root as `cellranger/`.

```bash
cd /path/to/genoar

# After moving the tarball into the repo root:
tar -xzvf cellranger-8.0.1.tar.gz
mv cellranger-8.0.1 cellranger

# Verification: the binary must exist at this path
ls -l /path/to/genoar/cellranger/cellranger
```

### 0.6 Install the reference genome (Stage 3 only)

Download the 10x Genomics public GRCh38 reference and place it under the repository root as `ref/`.

```bash
cd /path/to/genoar

wget https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCh38-2024-A.tar.gz
tar -xzvf refdata-gex-GRCh38-2024-A.tar.gz
mv refdata-gex-GRCh38-2024-A ref

# Verification: these four entries must be present
ls /path/to/genoar/ref/
# → fasta/  genes/  reference.json  star/
```

> **Disk note.** `refdata-gex-GRCh38-2024-A.tar.gz` expands to roughly 16GB. The downloaded tarball can be deleted afterwards.

### 0.7 Final prerequisite checklist

Before moving on to `[1]`, verify every item below.

- [ ] `docker images | grep genoar` → three lines printed
- [ ] `/path/to/genoar/all_query_results/` → three `umls_*_df.csv` present
- [ ] `/path/to/genoar/cellranger/cellranger` exists (if using Stage 3)
- [ ] `/path/to/genoar/ref/fasta/` exists (if using Stage 3)
- [ ] `which aria2c || which wget || which curl` → at least one prints a path

---

## Step 1: Dry run — inspect the plan (no side effects)

Before any real execution, run `--dry-run` to verify **path auto-detection** and the cycle plan. This step creates no files or directories and is safe to repeat as many times as you like.

```bash
cd /path/to/genoar

python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline \
  --run-stage3 --dry-run
```

### Log lines to confirm

In the output, make sure you see the following block:

```
Stage 3 Configuration:
  ...
  Cell Ranger:     /path/to/genoar/cellranger
  Ref Genome:      /path/to/genoar/ref
```

If you see `(auto-detect failed — pass --cellranger-path)`, the installation paths in `[0.5]` or `[0.6]` are incorrect. Go back to those steps, fix them, and re-run.

The cycle plan is also printed in a form similar to this:

```
[DRY RUN MODE - No actual execution]
  Cycle 1: Pages 1-5 -> cycle_001_pages_0001-0005/
           Stage 1 -> Stage 2 -> Stage 3 (Download + Pipeline)
[DRY RUN COMPLETE]
```

**If you do not plan to use Stage 3,** drop `--run-stage3` from the dry-run:

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline --dry-run
```

---

## Step 2: Run Stage 1+2 only — validate crawler/analysis (~60–90 min)

Run Stage 1 (crawl) and Stage 2 (UMLS matching) first, without Cell Ranger, to confirm the GEO crawler and UMLS analysis pipeline work correctly.

```bash
cd /path/to/genoar

python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline
```

### Monitoring progress

In a separate terminal, tail the master log to stream Stage 1 crawler progress in real time.

```bash
tail -f /path/to/genoar/test_full_pipeline/master_*.log
```

### Expected result

On successful completion, the console prints a summary similar to:

```
CYCLE TEST FINAL REPORT
============================================================
Total Cycles: 1

Success Rates:
  Stage 1: 100.0%
  Stage 2: 100.0%
  Overall: 100.0%

Totals:
  META files:         15–30
  SMTX files:         similar
  SRR files:          similar
  Total SRR IDs:      100–500
  Cell Type samples:  dozens
  ...
```

### Output verification

```bash
cd /path/to/genoar/test_full_pipeline

# 1) Directory structure
ls cycle_001_pages_0001-0005/
# → stage1/  stage2/  stage3/  logs/  summary.json

# 2) Stage 1: were META files actually produced?
ls cycle_001_pages_0001-0005/stage1/META/ | head -5
ls cycle_001_pages_0001-0005/stage1/META/ | wc -l

# 3) Stage 2: were all three CSVs generated?
ls cycle_001_pages_0001-0005/stage2/*.csv
# → HS_cell_type_1st_pass_meta_table.csv
# → HS_disease_1st_pass_meta_table.csv
# → HS_tissue_1st_pass_meta_table.csv

# 4) Row counts (header included)
wc -l cycle_001_pages_0001-0005/stage2/*.csv

# 5) Cycle summary
python -m json.tool cycle_001_pages_0001-0005/summary.json
```

### What you must verify here

- `echo $?` right after the command is `0`
- `status` in `summary.json` is `success`
- At least one of the three Stage 2 CSVs has `rows > 0`

A `WARNING` in `master_*.log` is not a verdict — the crawler warns when it caps a
range it could not fill, which is a normal complete run. Read the status, not the
log level. A `no_data` status means the run was valid and simply had nothing to
crawl; anything starting `failed_` means stop and diagnose. The statuses are
listed in [`README.md`](README.md#cycle-status-and-exit-codes).

**If this step fails,** Stage 3 cannot run anyway. See [Troubleshooting](#troubleshooting).

> **Scaling up pages.** 5 pages is enough for the tutorial, but for production-like volumes set `--pages-per-cycle 100`. Stage 1 crawl time scales linearly with page count, so 100 pages takes about 10–20 hours.

---

## Step 3: Small end-to-end — include Stage 3 (~3–10 hours)

If `[2]` succeeded, now run an end-to-end smoke test that downloads **only 2 SRR IDs** extracted from the Stage 2 tables and pushes them through Cell Ranger.

```bash
cd /path/to/genoar

python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline_e2e \
  --run-stage3 --max-samples 2 \
  --cellranger-cores 8 --cellranger-mem 64
```

### Option reference

| Option | Role |
|--------|------|
| `--run-stage3` | Enable Stage 3 (download + Cell Ranger) |
| `--max-samples 2` | Limit the SRRs extracted from Stage 2 to 2 downloads |
| `--cellranger-cores 8` | CPU cores available to Cell Ranger |
| `--cellranger-mem 64` | Memory (GB) available to Cell Ranger |

> **Resource tuning.** On larger machines you can raise these to `--cellranger-cores 16 --cellranger-mem 100`. If the server is shared with other workloads, dial them down.

### Monitoring during execution (separate terminal)

```bash
# Full master log (includes Cell Ranger progress)
tail -f /path/to/genoar/test_full_pipeline_e2e/master_*.log

# Raw Stage 3 Docker output (Cell Ranger step progress)
tail -f /path/to/genoar/test_full_pipeline_e2e/cycle_*/stage3/logs/docker_stdout.log
```

> **"Looks stuck" can be normal.** A single Cell Ranger run takes 1–4 hours, and stdout updates every few to tens of minutes. Even after 30 minutes of apparent inactivity, check `docker_stdout.log` before assuming it has hung.

### Per-step timeline example

| Stage | Approximate duration | Log prefix |
|-------|----------------------|------------|
| Stage 1 (5-page crawl) | 60–90 min | `[crawler]` |
| Stage 2 (UMLS matching) | 1–2 min | `[analysis]` |
| Stage 3a (2 SRA downloads) | 6–20 min | download progress |
| Stage 3b (Cell Ranger × 2) | 2–8 h | `[cellranger]` |

### Output verification

```bash
cd /path/to/genoar/test_full_pipeline_e2e/cycle_001_pages_0001-0005

# 1) Stage 3 preparation output
python -m json.tool stage3/stage3_prep_summary.json
cat stage3/srr_list.txt

# 2) Downloaded SRA files
ls -lh stage3/sra/*/*.sra

# 3) Cell Ranger success samples
ls stage3/results/success/

# 4) Cell Ranger outputs (core reports)
ls stage3/results/success/*/cellranger_output/outs/
# → web_summary.html  metrics_summary.csv  filtered_feature_bc_matrix/  ...
```

`web_summary.html` can be copied locally and opened in a browser for a visual QC summary.

```bash
# From your local machine:
scp user@server:/path/to/genoar/test_full_pipeline_e2e/cycle_*/stage3/results/success/*/cellranger_output/outs/web_summary.html ./
```

### What you must verify here

- `summary.json` → `stages.stage3_pipeline.success: true`
- At least one sample directory under `stage3/results/success/`
- Each successful sample has its own `cellranger_output/outs/web_summary.html`

**`partial_success` is also acceptable.** It is common for one of the two samples to succeed while the other fails at SRA download or Cell Ranger. A single success is enough to prove the full pipeline works.

**`no_data` is not a failure, but it is not a pass either.** Stage 3 exited `3`: it ran to completion and produced Cell Ranger output for none of the samples it was given. `no_data_reason` says why. See [Stage 3b: the run finished but analysed zero samples](#stage-3b-the-run-finished-but-analysed-zero-samples).

---

## (Optional) Re-run Stage 3 only — reuse existing Stage 2 output

Use this when you want to re-run Stage 3 with a different sample count or parameters **against Stage 2 results that already completed**, without redoing the Stage 1 crawl.

```bash
cd /path/to/genoar

python cycle_test/run_cycle_test.py \
  --cycles 1 --stage3-only \
  --output-dir test_full_pipeline_e2e \
  --max-samples 5 \
  --cellranger-cores 16 --cellranger-mem 100
```

- `--output-dir` must point at a directory **that already contains completed cycle results**.
- `cycle_NNN_pages_SSSS-EEEE/stage2/HS_*_1st_pass_meta_table.csv` must exist for this to work.
- Stage 1 and Stage 2 are skipped; only Stage 3 prep → download → Cell Ranger runs.

**To download only, without running Cell Ranger,** add `--download-only`. Cell Ranger is skipped and the run stops after populating `stage3/sra/`.

```bash
python cycle_test/run_cycle_test.py \
  --cycles 1 --stage3-only --download-only \
  --output-dir test_full_pipeline_e2e \
  --max-samples 5
```

---

## Troubleshooting

### Stage 1: Docker image not found

```
Docker image 'genoar-crawler:amd64' not found. Build it first.
```

→ Redo the image build in `[0.3]`. After building, always confirm with `docker images | grep genoar`.

### Stage 1: Chrome/ChromeDriver permission error

```
[crawler] chromedriver: ... permission denied
```

The host blocks `--cap-add=SYS_ADMIN` / `--security-opt seccomp=unconfined`. Either relax the security policy or run `[2]` on a different host that allows those flags.

### Stage 2: UMLS files not found

```
UMLS data not found at /path/to/genoar/all_query_results
```

→ Check `ls all_query_results/umls_*_df.csv` for all three tables. If any are missing, restore them from the repository (they are committed).

### Stage 2: all tables have 0 rows

Stage 1 produced META files, but none of the samples in that range passed the Homo sapiens + TRANSCRIPTOMIC filter. Try increasing `--pages-per-cycle` to widen the crawl, or change `--start-page` to try a different range.

### Stage 3a: SRA download failure

```bash
cat /path/to/genoar/test_full_pipeline_e2e/cycle_*/stage3/sra/download_failed.txt

# Check S3 reachability itself
curl -I https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR000001/SRR000001
```

Per-SRR failures for expired or relocated IDs are normal. If all downloads fail, it is a network/firewall issue.

### Stage 3b: Cell Ranger path auto-detection fails

If the dry-run log prints `(auto-detect failed — pass --cellranger-path)`:

```bash
# Confirm the install landed in the expected location
ls /path/to/genoar/cellranger/cellranger
ls /path/to/genoar/ref/fasta/

# If installed elsewhere, pass explicit paths
python cycle_test/run_cycle_test.py \
  --cycles 1 --pages-per-cycle 5 \
  --output-dir test_full_pipeline_e2e \
  --run-stage3 --max-samples 2 \
  --cellranger-path /custom/path/to/cellranger \
  --ref-genome-path /custom/path/to/ref
```

### Stage 3b: the run finished but analysed zero samples

Stage 3 exits `3` and the cycle becomes `no_data`. The census that explains it sits beside the results:

```bash
cat test_full_pipeline_e2e/cycle_*/stage3/results/success/cellranger_eligibility.tsv
cat test_full_pipeline_e2e/cycle_*/stage3/results/success/cellranger_ineligible.tsv
```

Two causes, in order of likelihood:

1. **A sample has only one FASTQ file.** Cell Ranger needs a paired R1/R2 read set; the sample can never be processed. Try a different SRR.
2. **The image was built to the wrong target.** `summary.json` shows `step8: "not_run"` when the image has no Snakemake in it. Rebuild with `--target step9`.

A missing or unusable `cellranger/` or `ref/` is a different outcome. The pipeline exits `2` at step 0, before any step runs, and the cycle is `failed_stage3`. The cycle is never `no_data` in this case. It is a fault to fix. It is not an empty result.

### Stage 3b: only some samples were analysed

Stage 3 exits `4` and the cycle becomes `partial_success`. Some of the samples this run was given have verified Cell Ranger output and the rest do not. The per-sample reasons are in the run record:

```bash
cat test_full_pipeline_e2e/cycle_*/stage3/results/runs/<run_id>/outcome.json
```

Only samples this run was asked to process count. Output left by an earlier run appears under `other_samples_with_output`. It is preserved, and not counted here. Output adopted with `GENOAR_ADOPT_PRIOR_RESULTS=1` is reported on its own line and counts towards no verdict.

### Stage 3b: Cell Ranger OOM / appears stalled for a long time

```bash
cat /path/to/genoar/test_full_pipeline_e2e/cycle_*/stage3/logs/docker_stdout.log | tail -50
```

Lower `--cellranger-mem` or reduce concurrent samples. A safe value is below 80% of system memory.

### Resuming after an interruption

`Ctrl+C` requests shutdown and no further cycle starts. It does not let the running cycle finish: the interrupt reaches the crawler too, so Stage 1 ends at `interrupted`, the later stages are skipped, and the process exits `130`. Resume with `--skip-stage1` to reuse existing Stage 1 output, or `--stage3-only` to rerun Stage 3 alone.

---

## Next steps

Once `[1]`–`[3]` all succeed, you are ready to move on to production-scale runs.

- **Large-scale crawls:** use `--pages-per-cycle 100 --cycles 5 --wait-minutes 10` to cover the broader NCBI range incrementally across cycles. Asking for more pages than GEO holds is safe — the range is capped with a warning — but a `--start-page` past the end leaves nothing to crawl and the cycle ends `no_data`.
- **Full-sample Cell Ranger:** drop `--max-samples` to process every SRR extracted by Stage 2. Verify disk capacity and time budget first.
- **For operational flags,** see the `§4 CLI options` table in [`README.md`](README.md).
