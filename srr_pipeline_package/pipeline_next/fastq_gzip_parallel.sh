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
if [[ -f "/pipeline_next/lib.sh" ]]; then
  # shellcheck source=/dev/null
  source "/pipeline_next/lib.sh"
fi
ensure_reports_dir 2>/dev/null || true

# Discover .fastq files lacking a .fastq.gz counterpart
tmp_list="$(mktemp)"
trap 'rm -f "$tmp_list"' EXIT

while IFS= read -r -d '' fq; do
  # Existing, and whole. A gzip killed partway leaves a file that exists, and
  # skipping on that alone carried a truncated archive through every later
  # step: step 4 counts a matched `*.fastq.gz` glob as success, and the sample
  # reaches Cell Ranger with half its reads. `gzip -t` reads the file, which
  # is the price of knowing; the alternative is finding out from the counts.
  if [[ -f "$fq.gz" ]]; then
    if gzip -t "$fq.gz" 2>/dev/null; then
      echo "[step3] Skip (gz exists and is whole): $fq" >> "$LOG_DIR/step3_fastq_gzip.log"
      continue
    fi
    echo "[step3] WARNING: $fq.gz is truncated or corrupt; compressing again" \
      >> "$LOG_DIR/step3_fastq_gzip.log"
    rm -f "$fq.gz"
  fi
  printf '%s\0' "$fq" >> "$tmp_list"
done < <(find "$BASE_DIR" -type f -name '*.fastq' -print0 | sort -z)

# A `.gz` whose `.fastq` is gone is never reached by the loop above, so a
# corrupt one from an interrupted run was carried straight through: step 4
# counts a matched glob as success and the sample reaches Cell Ranger with
# half its reads. It cannot be rebuilt here -- the source is gone -- so it is
# reported rather than fixed, which is the difference between wasting a job
# and trusting the result of one.
orphan_corrupt=""
while IFS= read -r -d '' gz; do
  [[ -f "${gz%.gz}" ]] && continue
  if ! gzip -t "$gz" 2>/dev/null; then
    orphan_corrupt="$orphan_corrupt $gz"
    echo "[step3] ERROR: $gz is truncated or corrupt and its .fastq is gone" \
      >> "$LOG_DIR/step3_fastq_gzip.log"
    if type append_failed_item >/dev/null 2>&1; then
      append_failed_item 3 "$gz" || true
    else
      echo "$gz" >> "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}/step3_failed.txt" || true
    fi
  fi
done < <(find "$BASE_DIR" -type f -name '*.fastq.gz' -print0 | sort -z)

if [[ -n "$orphan_corrupt" ]]; then
  echo "[step3] corrupt archive(s) with no source to rebuild from:$orphan_corrupt" \
    | tee -a "$LOG_DIR/step3_fastq_gzip.log"
  exit 1
fi

if [[ ! -s "$tmp_list" ]]; then
  echo "[step3] Nothing to gzip." | tee -a "$LOG_DIR/step3_fastq_gzip.log"
  exit 0
fi

compress_one() {
  local fq="$1"
  local threads TOOL_ARGV
  threads=${THREADS}
  if command -v pigz >/dev/null 2>&1 && [[ "$threads" -gt 1 ]]; then
    TOOL_ARGV=(pigz -p "$threads")
  else
    TOOL_ARGV=(gzip)
  fi
  echo "[step3] Compressing: $fq with ${TOOL_ARGV[*]}"
  # An array, not `eval`. `eval "$tool" "\"$fq\""` runs the FILE NAME through
  # the shell: a file called `a$(touch /tmp/PWNED)_1.fastq` executes what is
  # inside the parentheses. The names come from fastq-dump, which takes them
  # from accessions, so this is not the likeliest way to be attacked -- but it
  # is a command substitution on a path, and there is no reason to keep one.
  if ! "${TOOL_ARGV[@]}" "$fq"; then
    if type append_failed_item >/dev/null 2>&1; then
      append_failed_item 3 "$fq" || true
    else
      mkdir -p "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}" && echo "$fq" >> "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}/step3_failed.txt" || true
    fi
    return 1
  fi
}

export THREADS
# The subshell xargs starts is where a failure gets recorded, and it inherits
# only what is exported. Without these the fallback runs `mkdir -p ""` and
# appends to `/step3_failed.txt`, so the record of which file failed is written
# nowhere anybody looks.
export REPORTS_DIR="${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}"
export REPORTS_DIR_DEFAULT LOG_DIR
export -f compress_one
if type append_failed_item >/dev/null 2>&1; then
  export -f append_failed_item
  # It calls this. Unexported, every failure prints
  # "ensure_reports_dir: command not found" into the step log -- noise that
  # reads as breakage while the record itself lands.
  if type ensure_reports_dir >/dev/null 2>&1; then
    export -f ensure_reports_dir
  fi
fi

# Truncated at the start, for the same reason as step 2's.
mkdir -p "$REPORTS_DIR" 2>/dev/null || true
: > "$REPORTS_DIR/step3_failed.txt" 2>/dev/null || true

# `set -e` and xargs: GNU xargs exits 123 when any child failed, which would kill
# the script on that line -- before the summary, and before anything reports
# which file it was. The status is taken instead, the record is already on disk,
# and the failure is reported as a failure.
xargs_status=0
xargs -0 -I{} -P "$JOBS" bash -c 'compress_one "$0"' {} < "$tmp_list" || xargs_status=$?

if [[ "$xargs_status" -ne 0 ]]; then
  echo "[step3] compression failed for at least one file; see $REPORTS_DIR/step3_failed.txt" \
    | tee -a "$LOG_DIR/step3_fastq_gzip.log"
  exit 1
fi

echo "[step3] gzip completed." | tee -a "$LOG_DIR/step3_fastq_gzip.log"
