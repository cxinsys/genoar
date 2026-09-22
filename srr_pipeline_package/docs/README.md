# SRR Pipeline Package (Dockerized)

Containerized end-to-end SRR processing (Steps 1–9) aligned with `srr_pipeline/README.md`. Runs locally with Docker, without SLURM.

Implemented Steps 1–9:
- Step 1: organize `.sra` into per-SRR directories
- Step 2: `fastq-dump --split-files` in parallel (GNU Parallel)
- Step 3: compress `.fastq` → `.fastq.gz` in parallel (pigz optional)
- Step 4: classify samples (success/failed) by `.fastq.gz` presence
- Step 5: move success samples to `/work/results/success`
- Step 6: emit `fastq_1_files.tsv` … `fastq_4_files.tsv` in `success/`
- Step 7: rename to `<sample>_S1_R1_001.fastq.gz` and `_R2_001.fastq.gz`
- Step 8: run Cell Ranger via Snakemake (local mode)
- Step 9: prepare failed samples (swap R1/R2, move to `retry_failed/`, list)

## Layout

```
srr_pipeline_package/
├─ docker/
│  └─ Dockerfile                  # Multi-stage; final target: step9
├─ pipeline_entry.sh              # Image ENTRYPOINT; picks the tree
├─ pipeline_next/                 # Current tree — what runs by default
│  ├─ run_docker_pipeline.sh      # Orchestrates Steps 1–9
│  ├─ lib.sh                      # Logging/report helpers
│  ├─ make_directories.sh         # Step 1
│  ├─ fastq_dump_parallel.sh      # Step 2
│  ├─ fastq_gzip_parallel.sh      # Step 3
│  ├─ parse_fastq_logs.py         # Step 4
│  ├─ move_success_dirs.sh        # Step 5
│  ├─ check_counts.sh             # Step 6
│  ├─ rename_fastq.sh             # Step 7
│  ├─ Snakefile_hs.smk            # Step 8
│  └─ rename_move_failed_fastqs.py# Step 9
├─ pipeline/                      # Frozen earlier tree (legacy_pipeline: true)
│                                 # Same 11 filenames; kept for reproducing an
│                                 # earlier run, not for new work
├─ requirements.base.txt          # Pinned: PyYAML for config parsing
├─ requirements.snakemake.txt     # Pinned: Snakemake for Step 8
├─ configs/
│  └─ example.yaml                # Example config (paths, cores, etc.)
└─ docs/
   └─ USER_GUIDE.md               # Full usage guide
```

## Which Tree Runs

The image `ENTRYPOINT` is `/pipeline_entry.sh`. It reads the config and hands the
arguments, unchanged, to one of two trees:

- `/pipeline_next/` — the default, and the only one under maintenance.
- `/pipeline/` — frozen at the state a sample was validated in on the cluster.
  Selected only by `legacy_pipeline: true`. Give it its own results directory:
  the two trees answer differently, and that difference is the reason to run it.

`pipeline_mode: verified|corrected` and `safe_mode: true|false` are earlier
spellings of the same choice and are still understood; `legacy_pipeline` wins
where both appear.

## Quick Start

1) Prepare data and config

- Place `.sra` files under a host dir, e.g. `./sample_sra/SRRxxxxxx.sra`.
- Prepare references at `./ref` (Cell Ranger transcriptome).
- Prepare the complete Cell Ranger installation at `./cellranger/`, with an
  executable launcher at either `cellranger` or `bin/cellranger`.
- Adjust `srr_pipeline_package/configs/example.yaml` as needed.

2) Build the image

```bash
docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .
```

3) Run the full pipeline

Required mounts:
- `-v $(pwd)/sample_sra:/work/data/sra`       # input SRA files
- `-v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml`
- `-v $(pwd)/ref:/ref`                        # Cell Ranger reference
- `-v $(pwd)/cellranger:/opt/cellranger`      # Cell Ranger install root

Optional mounts (`./logs` and `./results` are the directories `make status`
reads; override with `make status STAGE3_RESULTS=<dir>`):
- `-v $(pwd)/logs:/work/logs`                 # persist logs
- `-v $(pwd)/results:/work/results`           # persist outputs

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

## Run (Step 1–9)

Mount your data directory under `/work/data` and a config under `/work/config.yaml`.

```bash
docker run --rm -it \
  -v $(pwd)/sample_sra:/work/data/sra \
  -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \
  genoar-srr:step9
```

The entrypoint will:
- Step 1: create one directory per SRR file (e.g., `SRR123456/`), move `SRR123456.sra` → `SRR123456/SRR123456.sra` (or no-ext variant)
- Step 2: run `fastq-dump --split-files` for each SRR directory concurrently (concurrency from `cores` in config)

Logs:
- Step 1: `/work/logs/step1_make_directories.log`
- Step 2: `/work/logs/step2_fastq_dump.log`, `/work/logs/step2_parallel.joblog`
- Step 3: `/work/logs/step3_fastq_gzip.log`
- Step 4: `/work/logs/step4_parse_fastq.log`
- Step 5: `/work/logs/step5_move_success.log`
- Step 6: `/work/logs/step6_check_counts.log`
- Step 7: `/work/logs/step7_rename_fastq.log`
- Step 8: `/work/logs/step8_cellranger.log`
- Step 9: `/work/logs/step9_retry_failed.log`

## Reproducibility

- Python dependencies pinned:
  - `pyyaml==6.0.2` (requirements.base.txt)
  - `snakemake==7.32.4` (requirements.snakemake.txt)
- System tools installed via apt: `sra-toolkit`, `parallel`, `gzip`, `pigz`.
- For deterministic runs, keep Docker image tags fixed and avoid changing base image without validation.
 - Base image pinned: `python:3.11.9-slim` (Dockerfile)

## Outputs and Diagnostics

- Primary outputs under `/work/results`:
  - `success/SRRxxxxxx/` with renamed FASTQs and `cellranger_output/outs/...` if successful
  - `retry_failed/SRRxxxxxx/` for samples prepared for retry (after Step 9)
  - `fastq_success.txt`, `fastq_failed.txt`
  - `success/fastq_[1-4]_files.tsv`
- Logs under `/work/logs`: `stepN_*.log` per step
- Machine‑readable reports under `/work/results/reports`:
  - `stepN.ok` (the step did its work), `stepN.err` (it failed) and `stepN.warn`
    (it completed without doing its work — step 8 with zero eligible samples).
    `.warn` is written instead of `.ok`, so a check for `.ok`/`.err` alone finds
    neither
  - `stepN.msg` (the reason, beside a `.warn` or `.err`)
  - `stepN_failed.txt` (per-step failed items if any)
  - `summary.jsonl` (append-only step status lines)

See also: `docs/TROUBLESHOOTING.md` for common failure modes and resolutions.

## Design Rationale and Changes from Original Pipeline

- Background and goals
  - The SRR pipeline was standardized into a portable, reproducible Docker image with a single entrypoint and `config.yaml`, removing SLURM/HPC assumptions while keeping the same logical 9 steps from `srr_pipeline/README.md`.
  - Objectives: environment independence, one-command UX, reproducibility, and clear logs/artifacts.

- Key changes (AS-IS → TO-BE)
  - Orchestration: manual per-step scripts and SLURM arrays → one orchestrator (`run_docker_pipeline.sh`) that runs Steps 1–9 sequentially with clear logs and status files.
  - Parallelization: SLURM job arrays → GNU Parallel for Steps 2–3; Snakemake in local mode for Step 8 with `--cores` cap.
  - Logging: SLURM logs → container logs under `/work/logs` plus machine-readable reports under `/work/results/reports`.
  - Success detection: parsed SLURM logs → direct presence checks of generated artifacts (e.g., `.fastq.gz` in Step 4), more robust to environment differences.
  - Configuration: ad‑hoc shell variables → explicit `config.yaml` (paths, cores, threads, reference path, retry dir, etc.).
  - Packaging: bare host environment → multi‑stage Dockerfile; base image pinned; Python deps pinned for reproducibility.
  - Cell Ranger: bundled/clustered execution → local Snakemake with host‑mounted `/opt/cellranger` (license compliance) and `/ref`.

- Why these decisions
  - Portability: users can run locally or on any Docker host without SLURM.
  - Reproducibility: same image across environments; fixed package versions; deterministic directory layout.
  - Operability: consistent log files, structured error reporting, and idempotent steps simplify debugging and resuming.

## Technical Implementation Overview

- Dockerfile (multi‑stage)
  - `base`: pinned `python:3.11.9-slim`, core utils.
  - `step1…step9` targets: progressively add tools and scripts; final `pipeline` target equals `step9`. Both trees and `pipeline_entry.sh` travel in every image that has them.
  - Tools: `sra-toolkit`, `parallel`, `gzip/pigz`, pinned Python deps (`pyyaml`, `snakemake`).

- Entrypoint orchestrator (`pipeline_next/run_docker_pipeline.sh`, reached
  through `pipeline_entry.sh`)
  - Loads `config.yaml` via PyYAML; sets `INPUT_DIR`, `OUTPUT_DIR`, `LOG_DIR`, etc.
  - Runs Steps 1–9 sequentially; each step writes a log (stdout/stderr tee) and status sentinels.
  - Error handling: wraps each step, captures exit codes, writes `reports/stepN.err` and exits with the same code; on success, writes `reports/stepN.ok`.

- Per‑step scripts
  - Step 2: `fastq_dump_parallel.sh` uses GNU Parallel and records per‑sample outcomes to `reports/step2_failed.txt` when errors occur.
  - Step 3: `fastq_gzip_parallel.sh` uses `pigz` (if threaded) or `gzip`, logging failed files to `reports/step3_failed.txt` upon error.
  - Step 4: `parse_fastq_logs.py` replaces SLURM log parsing with artifact presence checks.
  - Step 5: `move_success_dirs.sh` moves success samples; on `mv` failure or missing samples, appends to `reports/step5_failed.txt`.
  - Step 6: `check_counts.sh` writes `fastq_[1-4]_files.tsv` in `success/`.
  - Step 7: `rename_fastq.sh` applies size‑based heuristic for 3–4 fastq cases; logs anomalies to `reports/step7_failed.txt` and continues.
  - Step 8: Snakemake (`Snakefile_hs.smk`) runs Cell Ranger in each sample directory; on failure, enumerates samples without BAM into `reports/step8_failed.txt`.
  - Step 9: `rename_move_failed_fastqs.py` swaps R1/R2, cleans `cellranger_output`, moves to `retry_failed/`, and writes `retry_failed_samples.txt`; samples missing R1/R2 get listed in `reports/step9_failed.txt`.

- Reporting helpers (`pipeline_next/lib.sh`)
  - Timestamped logging helpers and reporting utilities for creating `stepN.ok/.warn/.err` and `stepN.msg`, per‑step `_failed.txt`, and appending to `summary.jsonl`.

## Ideas, Trade‑offs, and Improvements

- Robust success detection: direct artifact checks are resilient vs log formats and schedulers.
- Heuristic renaming: size‑based selection for R1/R2 in 3–4 file cases matches prior logic while staying deterministic.
- Performance: pigz and controlled cores improve wall‑time on single hosts; Snakemake keeps parallelism bounded.
- Reproducibility: pinned base image and Python deps; Dockerized tools; consistent directory schema.
- Trade‑offs: local mode may be slower than SLURM at scale; Cell Ranger not baked into the image (license) but mounted.

## Migration Guide (from `srr_pipeline`)

- Inputs: place `.sra` files into the mounted `/work/data/sra` (host `./sample_sra`).
- Config: move any ad‑hoc parameters into `config.yaml` (cores, ref, threads, paths).
- Scripts mapping: SLURM submit scripts replaced by GNU Parallel wrappers; Snakemake runs locally instead of cluster mode.
- Outputs: check under `/work/results` (success, retry_failed, reports); logs under `/work/logs`.
- Compatibility: renamed FASTQs and Cell Ranger outputs match expected downstream conventions; per‑step TSVs preserved.

**Migration Cheat Sheet**
- `make_directories.sh` → `/pipeline_next/make_directories.sh`
  - Same behavior; Step 1 operates on `input_dir` (`/work/data/sra`).
- `slurm_fastq_submit.sh` → `fastq_dump_job.sh` → `/pipeline_next/fastq_dump_parallel.sh`
  - SLURM arrays replaced with GNU Parallel; concurrency from `cores` in `config.yaml`.
  - Logs: `/work/logs/step2_fastq_dump.log`, joblog: `/work/logs/step2_parallel.joblog`.
- `slurm_gzip_submit.sh` → `fastq_gz_job.sh` → `/pipeline_next/fastq_gzip_parallel.sh`
  - Parallel gzip; uses `pigz` if `gzip_threads>1`, else `gzip`.
  - Logs: `/work/logs/step3_fastq_gzip.log`.
- `parse_fastq_logs.py` → `/pipeline_next/parse_fastq_logs.py`
  - No SLURM log parsing; detects success by `.fastq.gz` presence; writes `fastq_success.txt`, `fastq_failed.txt` under `/work/results`.
- `move_success_dirs.sh` → `/pipeline_next/move_success_dirs.sh`
  - Moves samples listed in `fastq_success.txt` to `/work/results/success`.
- `check_counts.sh` → `/pipeline_next/check_counts.sh`
  - Counts `.fastq.gz` per sample in `/work/results/success`; writes `fastq_[1-4]_files.tsv` there.
- `rename_fastq.sh` → `/pipeline_next/rename_fastq.sh`
  - Same naming scheme `<sample>_S1_R1_001.fastq.gz`, `_R2_001.fastq.gz`; size heuristic for 3–4 files.
  - Logs: `/work/logs/step7_rename_fastq.log`.
- `run_cellranger_hs.sh` / `Snakefile_hs.smk` → Snakemake local `/pipeline_next/Snakefile_hs.smk`
  - Local mode via `snakemake --cores $CORES`; requires mounts `/opt/cellranger` and `/ref`.
  - Config keys: `ref_path`, `cellranger_bin` (default `/opt/cellranger/cellranger`).
  - Output layout unchanged under `<sample>/cellranger_output/outs/`.
- `rename_move_failed_fastqs.py` → `/pipeline_next/rename_move_failed_fastqs.py`
  - Swaps R1/R2 for failed samples, removes `cellranger_output/`, moves to `/work/results/retry_failed/`, writes `retry_failed_samples.txt`.

## Known Limitations and Next Steps

- Large‑scale performance: consider wiring Snakemake to host SLURM as an optional mode for massive cohorts.
- Preflight checks: disk space and dependency preflights can be added as flags.
- Partial run flags: add `--from-step/--to-step` for targeted re‑execution.

## Next steps

- Add flags to run a subset of steps (`--from-step/--to-step`)
- Optional dry-run and preflight checks (disk space, dependencies)
