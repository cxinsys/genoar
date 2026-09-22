#!/usr/bin/env bash
set -euo pipefail

# Usage: make_directories.sh [BASE_DIR]
# Moves SRR* files into per-sample directories under BASE_DIR.
# - SRR123456.sra -> SRR123456/SRR123456.sra
# - SRR123456     -> SRR123456/SRR123456

BASE_DIR="${1:-/work/data/sra}"
if [[ ! -d "$BASE_DIR" ]]; then
  echo "[step1] Base directory not found: $BASE_DIR" >&2
  exit 1
fi

shopt -s nullglob

echo "[step1] Scanning for SRR* files in: $BASE_DIR"

# Iterate over SRR* entries that are regular files
found_any=false
for path in "$BASE_DIR"/SRR*; do
  [[ -e "$path" ]] || continue
  if [[ -f "$path" ]]; then
    found_any=true
    filename=$(basename -- "$path")
    # Determine sample ID and destination filename
    if [[ "$filename" == *.sra ]]; then
      sample="${filename%.sra}"
      dest_dir="$BASE_DIR/$sample"
      dest_file="$dest_dir/$filename"
    else
      sample="$filename"
      dest_dir="$BASE_DIR/$sample"
      dest_file="$dest_dir/$sample"
    fi

    mkdir -p "$dest_dir"
    # If already in place, skip
    if [[ -f "$dest_file" ]]; then
      echo "[step1] Exists, skipping: $dest_file"
      continue
    fi

    # Use a temporary move to avoid clobbering
    tmp_name="$BASE_DIR/.tmp_${filename}.$RANDOM"
    mv "$path" "$tmp_name"
    mv "$tmp_name" "$dest_file"
    echo "[step1] Moved: $filename -> $dest_file"
  fi
done

if [[ "$found_any" == false ]]; then
  echo "[step1] No SRR* files found in $BASE_DIR (nothing to do)."
else
  echo "[step1] Completed moving SRR files."
fi

