#!/usr/bin/env bash
set -euo pipefail

# Usage: move_success_dirs.sh [INPUT_DIR] [OUTPUT_DIR] [SUCCESS_LIST]
# - Reads SUCCESS_LIST (one SRR id per line)
# - Moves matching directories from INPUT_DIR to OUTPUT_DIR/success
# - Idempotent: skips if already in success or target exists

INPUT_DIR="${1:-/work/data/sra}"
OUTPUT_DIR="${2:-/work/results}"
SUCCESS_LIST="${3:-}"
LOG_DIR="${LOG_DIR:-/work/logs}"

mkdir -p "$LOG_DIR"

if [[ -z "$SUCCESS_LIST" ]]; then
  SUCCESS_LIST="$OUTPUT_DIR/fastq_success.txt"
fi

SUCCESS_DIR="$OUTPUT_DIR/success"
mkdir -p "$SUCCESS_DIR"

if [[ ! -f "$SUCCESS_LIST" ]]; then
  echo "[step5] Success list not found: $SUCCESS_LIST" >&2
  exit 1
fi

echo "[step5] Copying success samples from $INPUT_DIR to $SUCCESS_DIR"

# Load reporting helpers if available
REPORTS_DIR_DEFAULT="/work/results/reports"
if [[ -f "/pipeline/lib.sh" ]]; then
  # shellcheck source=/dev/null
  source "/pipeline/lib.sh"
fi
ensure_reports_dir 2>/dev/null || true

while IFS= read -r sample; do
  # skip blanks/comments
  [[ -n "$sample" ]] || continue
  [[ "$sample" =~ ^# ]] && continue

  src_dir="$INPUT_DIR/$sample"
  dst_dir="$SUCCESS_DIR/$sample"

  if [[ -d "$dst_dir" ]]; then
    echo "[step5] Already in success: $sample" >> "$LOG_DIR/step5_move_success.log"
    continue
  fi

  if [[ -d "$src_dir" ]]; then
    if cp -r "$src_dir" "$SUCCESS_DIR/"; then
      echo "[step5] Copied $sample -> success/" >> "$LOG_DIR/step5_move_success.log"
    else
      # record failure then exit non-zero
      if type append_failed_item >/dev/null 2>&1; then
        append_failed_item 5 "$sample" || true
      else
        mkdir -p "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}" && echo "$sample" >> "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}/step5_failed.txt" || true
      fi
      exit 1
    fi
  else
    # Maybe already copied or missing
    if [[ -d "$dst_dir" ]]; then
      echo "[step5] Already copied: $sample" >> "$LOG_DIR/step5_move_success.log"
    else
      echo "[step5] WARNING: Sample not found in input_dir: $sample" >> "$LOG_DIR/step5_move_success.log"
      if type append_failed_item >/dev/null 2>&1; then
        append_failed_item 5 "$sample" || true
      else
        mkdir -p "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}" && echo "$sample" >> "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}/step5_failed.txt" || true
      fi
    fi
  fi
done < "$SUCCESS_LIST"

echo "[step5] Completed copying success samples." | tee -a "$LOG_DIR/step5_move_success.log"
