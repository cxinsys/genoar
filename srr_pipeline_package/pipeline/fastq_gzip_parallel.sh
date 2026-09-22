#!/usr/bin/env bash
set -euo pipefail

# Usage: fastq_gzip_parallel.sh [BASE_DIR] [JOBS] [THREADS]
# - Compresses *.fastq into *.fastq.gz in parallel.
# - Skips files that already have a matching .fastq.gz.
# - Uses pigz if available and THREADS>1; otherwise gzip.

BASE_DIR="${1:-/work/data/sra}"
JOBS="${2:-4}"
THREADS="${3:-1}"
LOG_DIR="${LOG_DIR:-/work/logs}"

mkdir -p "$LOG_DIR"

if [[ ! -d "$BASE_DIR" ]]; then
  echo "[step3] Base directory not found: $BASE_DIR" >&2
  exit 1
fi

echo "[step3] Starting gzip: base=$BASE_DIR, jobs=$JOBS, threads=$THREADS"

# Load reporting helpers if available
REPORTS_DIR_DEFAULT="/work/results/reports"
if [[ -f "/pipeline/lib.sh" ]]; then
  # shellcheck source=/dev/null
  source "/pipeline/lib.sh"
fi
ensure_reports_dir 2>/dev/null || true

# Discover .fastq files lacking a .fastq.gz counterpart
tmp_list="$(mktemp)"
trap 'rm -f "$tmp_list"' EXIT

while IFS= read -r -d '' fq; do
  if [[ -f "$fq.gz" ]]; then
    echo "[step3] Skip (gz exists): $fq" >> "$LOG_DIR/step3_fastq_gzip.log"
    continue
  fi
  printf '%s\0' "$fq" >> "$tmp_list"
done < <(find "$BASE_DIR" -type f -name '*.fastq' -print0 | sort -z)

if [[ ! -s "$tmp_list" ]]; then
  echo "[step3] Nothing to gzip." | tee -a "$LOG_DIR/step3_fastq_gzip.log"
  exit 0
fi

compress_one() {
  local fq="$1"
  local tool threads
  threads=${THREADS}
  if command -v pigz >/dev/null 2>&1 && [[ "$threads" -gt 1 ]]; then
    tool="pigz -p ${threads}"
  else
    tool="gzip"
  fi
  echo "[step3] Compressing: $fq with $tool"
  # Use eval to allow args expansion for pigz
  if ! eval "$tool" "\"$fq\""; then
    if type append_failed_item >/dev/null 2>&1; then
      append_failed_item 3 "$fq" || true
    else
      mkdir -p "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}" && echo "$fq" >> "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}/step3_failed.txt" || true
    fi
    return 1
  fi
}

export THREADS
export -f compress_one

xargs -0 -I{} -P "$JOBS" bash -c 'compress_one "$0"' {} < "$tmp_list"

echo "[step3] gzip completed." | tee -a "$LOG_DIR/step3_fastq_gzip.log"
