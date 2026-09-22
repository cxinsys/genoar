# User Guide

This guide explains how to prepare data and environment, configure parameters, build the image, run the integrated 9‑step pipeline, and verify results or diagnose failures.

## Prerequisites

- Docker installed
- Data directory with `.sra` files, e.g., `./sample_sra/SRRxxxxxx.sra`
- Reference directory, e.g., `./ref` (Cell Ranger transcriptome)
- Cell Ranger binary directory, e.g., `./cellranger` containing `cellranger` executable
- Optional: directories for logs (`./logs`) and outputs (`./results`)

Recommended resources: 16+ cores, 128GB+ RAM, fast SSD/NVMe storage.

## Build

```bash
docker build -f srr_pipeline_package/docker/Dockerfile \
  --target step9 \
  -t genoar-srr:step9 .
```

## Run

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

The image entrypoint is `/pipeline_entry.sh`. It reads `legacy_pipeline` from the
config and runs the current tree (`/pipeline_next/`) unless that key is `true`,
in which case it runs the frozen earlier tree (`/pipeline/`). Either way the
chosen tree runs Steps 1–9 sequentially.

`logs/` and `results/` are the same host directories the Makefile uses, so
`make status` reads the run this command produced. Point them elsewhere with
`make status STAGE3_RESULTS=<dir>`.

## Configuration (config.yaml)

Edit and mount `srr_pipeline_package/configs/example.yaml`:

- `input_dir`: `/work/data/sra` (mounted from host `./sample_sra`)
- `output_dir`: `/work/results` (mounted to persist results)
- `ref_path`: `/ref` — the mount target, not a directory under it. The host
  reference root is bind-mounted at `/ref`, so its own name is gone by the time
  the pipeline sees it
- `cores`: e.g., `16` (used by Steps 2, 3, 8)
- `gzip_threads`: e.g., `2` (threads per file for pigz)
- `log_dir`: `/work/logs`
- `legacy_pipeline`: `false` (which tree runs; see above)

`success_dir`, `retry_move_dir` and `keep_intermediate` appear in the example
config but nothing reads them: the success and retry directories are derived
from `output_dir`, and Step 3 always replaces the uncompressed FASTQ.

## Behavior

- Step 1: For each `SRR*` file in `/work/data/sra`, create a directory and move it inside (`SRRxxxxxx/SRRxxxxxx(.sra)`).
- Step 2: For each `SRRxxxxxx/`, run `fastq-dump --split-files` producing `_1.fastq`, `_2.fastq` (up to 4 files depending on sample).
- Step 3: Compress all `.fastq` files to `.fastq.gz` in parallel. If `pigz` is present and `gzip_threads>1`, uses multithreaded compression; otherwise `gzip`.
- Step 4: Determine success/failed samples by checking for presence of `.fastq.gz` files and write lists to `/work/results/fastq_success.txt` and `/work/results/fastq_failed.txt`.
- Step 5: Move success samples listed in `fastq_success.txt` from `input_dir` to `/work/results/success/SRRxxxxxx/`.
- Step 6: Count `.fastq.gz` files per sample and write `fastq_1_files.tsv` … `fastq_4_files.tsv` to `/work/results/success`.
- Step 7: Rename FASTQs for each sample to `<sample>_S1_R1_001.fastq.gz` and `<sample>_S1_R2_001.fastq.gz` based on file counts and size heuristic.
- Step 8: Run Cell Ranger via Snakemake locally: requires mounting the reference
  at `/ref` and the complete Cell Ranger installation at `/opt/cellranger`.
  The launcher may be `cellranger` or `bin/cellranger` under that root.
- Step 9: Detect failed samples (missing `outs/possorted_genome_bam.bam` but `cellranger_output` exists), swap R1/R2, remove `cellranger_output`, move to `/work/results/retry_failed`, and write `/work/results/retry_failed_samples.txt`.
- Concurrency is read from `cores` in the config (default 8). 
- Logs:
  - `/work/logs/step1_make_directories.log`
  - `/work/logs/step2_fastq_dump.log`
  - `/work/logs/step2_parallel.joblog`
  - `/work/logs/step3_fastq_gzip.log`
  - `/work/logs/step4_parse_fastq.log`
  - `/work/logs/step5_move_success.log`
  - `/work/logs/step6_check_counts.log`
  - `/work/logs/step7_rename_fastq.log`
  - `/work/logs/step8_cellranger.log`
  - `/work/logs/step9_retry_failed.log`

For detailed failure scenarios and resolutions, refer to `docs/TROUBLESHOOTING.md`.

## Validating Success

- Check `results/success/SRRxxxxxx/cellranger_output/outs/possorted_genome_bam.bam` for each processed sample.
- Ensure renamed FASTQs exist: `results/success/SRRxxxxxx/<sample>_S1_R[12]_001.fastq.gz`.
- Review `results/fastq_success.txt` vs `results/fastq_failed.txt` counts.
- Verify `results/success/fastq_[1-4]_files.tsv` lists match actual files.

## Diagnosing Failures

- Logs: see `/work/logs/stepN_*.log`. The most relevant for failures:
  - Step 2: fastq-dump errors and GNU Parallel joblog
  - Step 8: Snakemake + Cell Ranger shell commands
- Machine‑readable reports: `/work/results/reports/`
  - Sentinels: `stepN.ok` (the step did its work), `stepN.err` (it failed), or
    `stepN.warn` (it finished without doing its work — step 8 with zero eligible
    samples is the usual case). `.warn` is written *instead of* `.ok`, so a
    check that looks only for `.ok` and `.err` finds neither and concludes the
    step never ran
  - Reason: `stepN.msg`, beside a `.warn` or `.err`
  - Failed items: `step2_failed.txt` (samples), `step3_failed.txt` (files), `step5_failed.txt` (samples), `step7_failed.txt` (samples), `step8_failed.txt` (samples), `step9_failed.txt` (samples)
  - Summary: `summary.jsonl` (step status lines with timestamps)
- Retry set: `results/retry_failed/` and `results/retry_failed_samples.txt`

## Reproducibility

- Python packages are pinned for consistent environments:
  - `pyyaml==6.0.2`
  - `snakemake==7.32.4`
- System packages installed via apt are defined in the Dockerfile.
- For Cell Ranger, mount a consistent binary version at `/opt/cellranger` and reference at `/ref`.
 - Base image pinned: `python:3.11.9-slim` for deterministic runtime.

## Advanced

- To run a subset of steps (e.g., only 1–3), build and run the corresponding target image (e.g., `--target step3`).
- Persist results/logs by mounting host paths to `/work/results` and `/work/logs`.
