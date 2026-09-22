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
if [[ -f "/pipeline_next/lib.sh" ]]; then
  # shellcheck source=/dev/null
  source "/pipeline_next/lib.sh"
fi
ensure_reports_dir 2>/dev/null || true

listing_of() {
  # (size, path) for every regular file, NUL-separated, into a file.
  #
  # Not `find -exec ls -ld | awk '{print $5, $NF}'`: `$NF` is the last
  # whitespace-separated field, so `sample one reads.fastq` and
  # `sample TWO reads.fastq` both reduce to `reads.fastq` and two different
  # files compare equal. Written to a file and compared with `cmp` because
  # command substitution drops NUL bytes, which is the only separator a
  # filename cannot contain.
  local dir="$1" out="$2"
  : > "$out"
  ( cd "$dir" && find . -type f -print0 2>/dev/null | sort -z ) \
    | while IFS= read -r -d '' f; do
        printf '%s\t%s\0' "$(wc -c < "$dir/$f" 2>/dev/null | tr -d ' ')" "$f" >> "$out"
      done
}

same_contents() {
  # Whether two directories hold the same files at the same sizes.
  #
  # `cp -r` is checked for its exit status and nothing else, which says the
  # command returned zero, not that the bytes arrived. A copy interrupted by a
  # full disk or a dying node returns non-zero -- but the half-written
  # destination it leaves is what the next run finds, and the next run used to
  # accept it on sight.
  #
  # Names and sizes, not checksums: this runs over every sample of every batch,
  # and reading a hundred gigabytes twice to prove a copy costs more than the
  # copy did. A truncated file is the failure that happens; a file of exactly
  # the right length with different bytes is not. Empty directories and
  # symlinks are outside what this compares.
  local left="$1" right="$2"
  [[ -d "$left" && -d "$right" ]] || return 1
  local a b rc
  a="$(mktemp)"; b="$(mktemp)"
  listing_of "$left" "$a"
  listing_of "$right" "$b"
  cmp -s "$a" "$b" && rc=0 || rc=1
  rm -f "$a" "$b"
  return "$rc"
}

# A sample name is a directory name, not a path. `../reports` in the success
# list made `dst_dir` point at the step 6 census directory, and the recopy
# path then `rm -rf`'d it.
usable_sample_name() {
  local name="$1"
  [[ -n "$name" ]] || return 1
  case "$name" in
    */*|.|..|.*) return 1 ;;
  esac
  return 0
}

while IFS= read -r sample; do
  [[ -n "$sample" ]] || continue
  [[ "$sample" =~ ^# ]] && continue

  if ! usable_sample_name "$sample"; then
    echo "[step5] REFUSING: $sample is not a usable directory name" \
      >> "$LOG_DIR/step5_move_success.log"
    if type append_failed_item >/dev/null 2>&1; then
      append_failed_item 5 "$sample" || true
    fi
    continue
  fi
  src_dir="$INPUT_DIR/$sample"
  dst_dir="$SUCCESS_DIR/$sample"

  # A destination that already exists does not end the matter. It is the state
  # a `cp` that died halfway leaves behind, and adopting it byte for byte --
  # three files of four, one of them truncated -- reports success with the
  # complete source sitting untouched next door. So it is compared against the
  # source rather than merely noticed.
  if [[ -d "$dst_dir" ]]; then
    if [[ ! -d "$src_dir" ]]; then
      echo "[step5] Already in success (source gone, nothing to compare): $sample" \
        >> "$LOG_DIR/step5_move_success.log"
      continue
    fi
    if same_contents "$src_dir" "$dst_dir"; then
      echo "[step5] Already in success: $sample" >> "$LOG_DIR/step5_move_success.log"
      continue
    fi
    echo "[step5] WARNING: $sample in success/ does not match the input; recopying" \
      >> "$LOG_DIR/step5_move_success.log"
    rm -rf "$dst_dir"
  fi

  if [[ -d "$src_dir" ]]; then
    if cp -r "$src_dir" "$SUCCESS_DIR/" && same_contents "$src_dir" "$dst_dir"; then
      echo "[step5] Copied $sample -> success/" >> "$LOG_DIR/step5_move_success.log"
    else
      if type append_failed_item >/dev/null 2>&1; then
        append_failed_item 5 "$sample" || true
      else
        mkdir -p "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}" && echo "$sample" >> "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}/step5_failed.txt" || true
      fi
      exit 1
    fi
  else
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
