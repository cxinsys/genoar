#!/usr/bin/env bash
set -euo pipefail

# Usage: fastq_dump_parallel.sh [BASE_DIR] [JOBS]
# - Scans BASE_DIR for SRR* directories with an SRR file inside
# - Runs fastq-dump --split-files for each sample in parallel using xargs
# - Skips samples that already have *_1.fastq or *_1.fastq.gz

BASE_DIR="${1:-/work/data/sra}"
JOBS="${2:-4}"
LOG_DIR="${LOG_DIR:-/work/logs}"

mkdir -p "$LOG_DIR"

if [[ ! -d "$BASE_DIR" ]]; then
  echo "[step2] Base directory not found: $BASE_DIR" >&2
  exit 1
fi

echo "[step2] Starting fastq-dump: base=$BASE_DIR, jobs=$JOBS (GNU parallel)"

# Load reporting helpers if available
REPORTS_DIR_DEFAULT="/work/results/reports"
if [[ -f "/pipeline/lib.sh" ]]; then
  # shellcheck source=/dev/null
  source "/pipeline/lib.sh"
fi
ensure_reports_dir 2>/dev/null || true

# Collect SRR directories to process
tmp_list="$(mktemp)"
status_file="$(mktemp)"
trap 'rm -f "$tmp_list"' EXIT

while IFS= read -r -d '' dir; do
  # Skip if fastq already exists
  if ls "$dir"/*_1.fastq* >/dev/null 2>&1; then
    echo "[step2] Skip $(basename "$dir") (fastq exists)" >> "$LOG_DIR/step2_fastq_dump.log"
    continue
  fi
  printf '%s\0' "$dir" >> "$tmp_list"
done < <(find "$BASE_DIR" -mindepth 1 -maxdepth 1 -type d -name 'SRR*' -print0 | sort -z)

if [[ ! -s "$tmp_list" ]]; then
  echo "[step2] Nothing to do (all samples already have fastqs)." | tee -a "$LOG_DIR/step2_fastq_dump.log"
  exit 0
fi

# Run with GNU parallel; record per-sample status
export STATUS_FILE="$status_file"
parallel -0 -j "$JOBS" --joblog "$LOG_DIR/step2_parallel.joblog" \
  'dir={}; sample=$(basename "$dir"); \
    sra_path="$dir/$sample.sra"; [ -f "$sra_path" ] || sra_path="$dir/$sample"; \
    if [ ! -f "$sra_path" ]; then echo -e "FAIL\t$sample\tno_sra" >> "$STATUS_FILE"; echo "[step2] ERROR: SRA file not found for $sample in $dir" >&2; exit 1; fi; \
    echo "[step2] fastq-dump --split-files $sample"; \
    if fastq-dump --split-files "$sra_path" --outdir "$dir"; then \
      echo -e "OK\t$sample" >> "$STATUS_FILE"; \
    else \
      echo -e "FAIL\t$sample\tfastq_dump_error" >> "$STATUS_FILE"; exit 1; \
    fi' \
  :::: "$tmp_list"

if [[ -f "$status_file" ]]; then
  while IFS=$'\t' read -r st smp reason; do
    if [[ "$st" == "FAIL" ]]; then
      if type append_failed_item >/dev/null 2>&1; then
        append_failed_item 2 "$smp" || true
      else
        mkdir -p "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}" && echo "$smp" >> "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}/step2_failed.txt" || true
      fi
    fi
  done < "$status_file"
fi

echo "[step2] fastq-dump completed." | tee -a "$LOG_DIR/step2_fastq_dump.log"
