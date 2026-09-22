# Troubleshooting

This document lists common failure modes, how to recognize them, and how to resolve them. Update this as you discover new issues during testing.

## How To Debug Efficiently
- Check per‑step logs under `/work/logs/stepN_*.log`.
- Check machine‑readable reports under `/work/results/reports/`:
  - `stepN.ok` (the step did its work), `stepN.err` (it failed), or `stepN.warn`
    (it ran to completion without doing its work). `.warn` is written *instead
    of* `.ok` — step 8 with zero eligible samples is the ordinary case — so
    looking only for `.ok` and `.err` finds neither and reads as "the step never
    ran".
  - `stepN.msg` (the reason, written beside a `.warn` or an `.err`)
  - `stepN_failed.txt` (failed items for some steps)
  - `summary.jsonl` (append‑only status lines)
- Verify mounts and config paths are correct (see User Guide).

## Global/Environment Issues
- Symptom: Container exits immediately, no logs written.
  - Likely cause: Missing mounts for config or data.
  - Fix: Ensure `-v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml` and `-v $(pwd)/sample_sra:/work/data/sra` are provided.

- Symptom: “Permission denied” when writing under `/work/results` or `/work/logs`.
  - Cause: Mounted host directory is read‑only or lacks write permission for container user.
  - Fix: Adjust mount options/permissions on host directories (`chmod -R u+rwX results logs`).

- Symptom: “No space left on device”.
  - Cause: Insufficient disk space for FASTQ/Cell Ranger outputs.
  - Fix: Free space or mount a larger/faster volume to `/work/results`.

- Symptom: Very slow compression.
  - Cause: pigz disabled or `gzip_threads` too low; storage throughput bottleneck.
  - Fix: Increase `gzip_threads` and `cores` (config.yaml); use fast SSD/NVMe storage.

## Step 0 — Configuration Pre-flight
Step 0 runs before any file is touched, only in images that carry the Cell Ranger
stage. Every mount and reference fault surfaces here, and it exits `2` — a
misconfigured run, not an empty result.

- Sentinel: `reports/step0.err`; the reasons are also printed under `[step0]`.
- Symptom: “cellranger executable not found at /opt/cellranger/cellranger”.
  - Cause: missing `-v $(pwd)/cellranger:/opt/cellranger` mount, or only `bin/`
    was mounted.
  - Fix: mount the complete installation root. Cell Ranger reads `.env.json`,
    `external/`, `lib/` and `mro/` beside the launcher; the launcher itself is
    found at either `cellranger` or `bin/cellranger` under that root.
- Symptom: “reference directory not found at /ref”.
  - Cause: missing `-v $(pwd)/ref:/ref` mount, or `ref_path` points below the
    mount target (e.g. `/ref/GRCh38-2024-A`). The host directory's own name is
    gone once it is bind-mounted, so `ref_path` is the mount target.
  - Fix: mount the reference root and set `ref_path: /ref`.
- Symptom: “reference.json missing or empty” or “fasta/ directory missing”.
  - Cause: the reference is still packed, or was extracted one level down into
    `refdata-gex-*/`.
  - Fix: mount the directory that holds `reference.json`, `fasta/`, `genes/` and
    `star/` at its top level.
- Symptom: “could not read a version from …/cellranger”.
  - Cause: the launcher did not answer `--version`. On Apple Silicon this is an
    exec-format failure: Cell Ranger ships Linux x86-64 only.
  - Fix: run Stage 3 on Linux x86-64. Which release produced a result is part of
    the result, so the run stops here rather than spending the allocation and
    reporting a version it never read.

## Step 1 — Organize .sra
- Log: `/work/logs/step1_make_directories.log`
- Sentinel: `reports/step1.err`
- Symptom: “Base directory not found” or “No SRR* files found”.
  - Cause: Wrong `input_dir` or empty data directory.
  - Fix: Confirm `input_dir` in config and the `-v` mount for `/work/data/sra`.

## Step 2 — fastq-dump
- Logs: `/work/logs/step2_fastq_dump.log`, joblog `/work/logs/step2_parallel.joblog`
- Reports: `reports/step2_failed.txt`, `reports/step2.err`
- Symptom: “SRA file not found for <sample>”.
  - Cause: Missing `<sample>.sra` (or no‑ext file) inside `<sample>/`.
  - Fix: Ensure Step 1 completed; verify `SRRxxxxxx/SRRxxxxxx.sra` exists.
- Symptom: fastq‑dump errors for specific samples.
  - Cause: Corrupted or incomplete `.sra`.
  - Fix: Re‑download the `.sra` and rerun; problematic samples are listed in `step2_failed.txt`.

## Step 3 — Gzip FASTQs
- Log: `/work/logs/step3_fastq_gzip.log`
- Reports: `reports/step3_failed.txt`, `reports/step3.err`
- Symptom: Compression failures (entries in `step3_failed.txt`).
  - Cause: Read errors, permissions, or missing files.
  - Fix: Verify file existence and permissions; check disk space; rerun.

## Step 4 — Success/Failed Lists
- Log: `/work/logs/step4_parse_fastq.log`
- Sentinel: `reports/step4.err`
- Symptom: `fastq_success.txt` empty, all in `fastq_failed.txt`.
  - Cause: Previous steps didn’t create `.fastq.gz` files.
  - Fix: Recheck Steps 2–3 logs; verify `.fastq.gz` presence in each sample.

## Step 5 — Move Success Samples
- Log: `/work/logs/step5_move_success.log`
- Reports: `reports/step5_failed.txt`, `reports/step5.err`
- Symptom: “Success list not found: …/fastq_success.txt”.
  - Cause: Step 4 not executed or wrote to a different `output_dir`.
  - Fix: Ensure consistent `output_dir` across steps; rerun Step 4.
- Symptom: Move fails for specific sample(s).
  - Cause: Permission issues or destination already exists.
  - Fix: Fix permissions; remove conflicting destination; rerun Step 5.

## Step 6 — Count FASTQs
- Log: `/work/logs/step6_check_counts.log`
- Sentinel: `reports/step6.err`
- Symptom: TSVs not created or empty.
  - Cause: No `.fastq.gz` files under `results/success`.
  - Fix: Recheck Steps 3 and 5; ensure files were moved.

## Step 7 — Rename FASTQs
- Log: `/work/logs/step7_rename_fastq.log`
- Reports: `reports/step7_failed.txt`, `reports/step7.err`
- Symptom: “Expected _1/_2 not found” or insufficient files for 3–4 case.
  - Cause: Input files don’t follow expected `_1/_2` naming or counts.
  - Fix: Check sample dir content; confirm Step 2/3 completed correctly.

## Step 8 — Snakemake / Cell Ranger
- Log: `/work/logs/step8_cellranger.log`
- Reports: `reports/step8_failed.txt`, `reports/step8.err`
- Symptom: “cellranger not found at /opt/cellranger/cellranger”.
  - Cause: Missing mount or wrong path.
  - Fix: Mount the complete host installation with
    `-v $(pwd)/cellranger:/opt/cellranger` and ensure either `cellranger` or
    `bin/cellranger` under it is executable.
- Symptom: “reference path not found: /ref”.
  - Cause: Missing or wrong `-v $(pwd)/ref:/ref` mount.
  - Fix: Mount the correct reference root and set `ref_path` in config.
- Symptom: Snakemake exits non‑zero; samples listed in `step8_failed.txt`.
  - Cause: Cell Ranger failure per sample (e.g., memory, corrupt FASTQ).
  - Fix: Inspect sample dir logs under `<sample>/cellranger_output/outs/` and the main log; adjust resources or fix data.

## Step 9 — Retry Failed Samples
- Log: `/work/logs/step9_retry_failed.log`
- Reports: `reports/step9_failed.txt`, sentinel `reports/step9.err`
- Symptom: Retry list empty but failures expected.
  - Cause: Failure criteria not met (no `cellranger_output` dir) or Step 8 didn’t run.
  - Fix: Confirm Step 8 execution and outputs; ensure failure criteria match your case.

## Quick Verification Commands (inside container)
- List SRR dirs: `ls -d /work/data/sra/SRR*`
- Check FASTQs: `find /work/data/sra -name "*.fastq*" | head`
- Check success dir: `ls -d /work/results/success/SRR* 2>/dev/null | wc -l`
- Check renamed files: `ls /work/results/success/*/*_S1_R[12]_001.fastq.gz | head`
- Check Cell Ranger BAM: `ls /work/results/success/*/cellranger_output/outs/possorted_genome_bam.bam 2>/dev/null | wc -l`
