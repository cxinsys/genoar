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
if [[ -f "/pipeline_next/lib.sh" ]]; then
  # shellcheck source=/dev/null
  source "/pipeline_next/lib.sh"
fi
ensure_reports_dir 2>/dev/null || true

# Collect SRR directories to process
tmp_list="$(mktemp)"
status_file="$(mktemp)"
trap 'rm -f "$tmp_list" "$status_file"' EXIT

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
  :::: "$tmp_list" || parallel_status=$?
parallel_status="${parallel_status:-0}"

# `parallel` exits with the number of jobs that failed, and this script runs
# under `set -e`. Left to itself, a single unreadable .sra ends the script HERE
# -- before the loop below, which is the only thing that turns the per-sample
# status file into step2_failed.txt. The batch dies, its remaining samples never
# run, and the one record naming the offender stays in a temp file nobody reads.
#
# The failures are recorded first and reported after, which is the order that
# makes them visible at all.
# Truncated, not appended to. This file is what an operator is pointed at, and
# appending means a clean re-run still names the samples that failed last time
# -- and three failing runs name the same sample three times.
failed_report="${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}/step2_failed.txt"
mkdir -p "$(dirname "$failed_report")" 2>/dev/null || true
: > "$failed_report" 2>/dev/null || true

failed_count=0
if [[ -f "$status_file" ]]; then
  # `|| [[ -n "$line" ]]` because a worker killed mid-`echo` leaves a last line
  # with no newline, and `read` returns non-zero there -- so the one record of
  # the sample that died was the one record dropped.
  #
  # Read whole and split by hand: `IFS=$'\t' read -r st smp reason` truncates a
  # sample name at a tab, and a CR from a status file written on another
  # platform ends up inside the name.
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -n "$line" ]] || continue
    st="${line%%$'\t'*}"
    [[ "$st" == "FAIL" ]] || continue
    smp="${line#*$'\t'}"
    smp="${smp%$'\t'*}"
    failed_count=$(( failed_count + 1 ))
    if type append_failed_item >/dev/null 2>&1; then
      append_failed_item 2 "$smp" || true
    else
      echo "$smp" >> "$failed_report" || true
    fi
  done < "$status_file"
fi

if [[ "$parallel_status" -ne 0 ]]; then
  # Still an error, and still reported as one -- but with the record written.
  # The count is parallel's own: how many samples it could not do.
  # Counted from the records, not from `parallel`'s exit status. That status is
  # a wait status: `parallel` killed by SIGTERM reports 143, an absent
  # `parallel` reports 127, and 150 real failures report 101 because that is
  # where parallel caps. Any of those printed as "N sample(s) failed" tells an
  # operator a number that is not the number of samples.
  if [[ "$failed_count" -gt 0 ]]; then
    echo "[step2] $failed_count sample(s) failed; see $failed_report" \
      | tee -a "$LOG_DIR/step2_fastq_dump.log"
  else
    echo "[step2] fastq-dump exited $parallel_status and recorded no per-sample failure; the step itself failed" \
      | tee -a "$LOG_DIR/step2_fastq_dump.log"
  fi
  exit 1
fi

echo "[step2] fastq-dump completed." | tee -a "$LOG_DIR/step2_fastq_dump.log"
