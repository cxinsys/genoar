#!/usr/bin/env bash
set -euo pipefail

# Usage: check_counts.sh [SUCCESS_DIR]
# - Scans SUCCESS_DIR for SRR*/ directories
# - Counts *.fastq.gz files per sample and writes:
#     fastq_1_files.tsv, fastq_2_files.tsv, fastq_3_files.tsv, fastq_4_files.tsv
#   in SUCCESS_DIR (overwrites each run)
# - Reports which samples can never reach Cell Ranger. Cell Ranger needs a
#   paired R1/R2 read set, so a sample with a single FASTQ is ineligible no
#   matter how well the earlier steps went. Ineligible samples are written to
#   cellranger_ineligible.tsv (sample, fastq_count, reason) so the final
#   status banner can say why nothing was processed.

SUCCESS_DIR="${1:-/work/results/success}"
LOG_DIR="${LOG_DIR:-/work/logs}"

mkdir -p "$SUCCESS_DIR" "$LOG_DIR"

if [[ ! -d "$SUCCESS_DIR" ]]; then
  echo "[step6] Success directory not found: $SUCCESS_DIR" >&2
  exit 1
fi

echo "[step6] Counting fastq.gz files per sample in: $SUCCESS_DIR"

for i in 1 2 3 4; do
  : > "$SUCCESS_DIR/fastq_${i}_files.tsv"
done

INELIGIBLE_TSV="$SUCCESS_DIR/cellranger_ineligible.tsv"
: > "$INELIGIBLE_TSV"
printf '# sample\tfastq_count\treason\n' >> "$INELIGIBLE_TSV"

total=0
# Indexed, not associative: the keys are 1 to 4. `declare -A` is bash 4, and a
# script that will not start on bash 3.2 is one nobody can run outside the
# image -- which is why these steps had no tests at all.
counts=(0 0 0 0 0)

scanned=0
eligible=0
ineligible=0
single_fastq=0
no_fastq=0
over_four=0
single_list=""

REASON_TOO_MANY='more than four FASTQ files; step 7 renames 2 to 4 and would not reach this sample'
REASON_LANE_INCOMPLETE='multi-lane FASTQ names do not pair R1 with R2 within the same lane; Cell Ranger needs both reads of every lane it is given'
REASON_SINGLE='only 1 FASTQ file; Cell Ranger requires paired R1/R2 reads'
REASON_NONE='no fastq.gz files found'

# Step 7 does not rename a sample above four files, but Step 8 intentionally
# accepts an already named multi-lane layout. Count that layout by the same
# lane-pair contract instead of declaring it ineligible on file count alone.
# Returns 0 for complete pairs, 1 when this is not a multi-lane layout, and 2
# when lane names exist but an R1 or R2 partner is missing.
multilane_state() {
  local sample_dir="$1" sample="$2" f lane
  local r1_lanes=" " r2_lanes=" "
  shopt -s nullglob
  for f in "$sample_dir/${sample}"_S*_L*_R1_*.fastq.gz; do
    lane="$(basename "$f")"; lane="${lane##*_L}"; lane="${lane%%_*}"
    case "$r1_lanes" in *" $lane "*) ;; *) r1_lanes="$r1_lanes$lane " ;; esac
  done
  for f in "$sample_dir/${sample}"_S*_L*_R2_*.fastq.gz; do
    lane="$(basename "$f")"; lane="${lane##*_L}"; lane="${lane%%_*}"
    case "$r2_lanes" in *" $lane "*) ;; *) r2_lanes="$r2_lanes$lane " ;; esac
  done
  shopt -u nullglob
  if [[ "$r1_lanes" == " " && "$r2_lanes" == " " ]]; then
    return 1
  fi
  for lane in $r1_lanes; do
    case "$r2_lanes" in *" $lane "*) ;; *) return 2 ;; esac
  done
  for lane in $r2_lanes; do
    case "$r1_lanes" in *" $lane "*) ;; *) return 2 ;; esac
  done
  return 0
}

while IFS= read -r -d '' sample_dir; do
  sample="$(basename "$sample_dir")"
  n=$(find "$sample_dir" -maxdepth 1 -type f -name '*.fastq.gz' | wc -l | tr -d ' ')
  scanned=$(( scanned + 1 ))
  if [[ $n -ge 1 && $n -le 4 ]]; then
    echo "$sample" >> "$SUCCESS_DIR/fastq_${n}_files.tsv"
    counts[$n]=$(( counts[$n] + 1 ))
    total=$(( total + 1 ))
  fi

  # Cell Ranger eligibility, judged on FASTQ count alone. Step 8 re-checks for a
  # real R1/R2 pair; this is the earliest point at which "can never work" is known.
  if [[ $n -eq 0 ]]; then
    printf '%s\t%s\t%s\n' "$sample" "$n" "$REASON_NONE" >> "$INELIGIBLE_TSV"
    no_fastq=$(( no_fastq + 1 ))
    ineligible=$(( ineligible + 1 ))
  elif [[ $n -eq 1 ]]; then
    printf '%s\t%s\t%s\n' "$sample" "$n" "$REASON_SINGLE" >> "$INELIGIBLE_TSV"
    single_fastq=$(( single_fastq + 1 ))
    ineligible=$(( ineligible + 1 ))
    single_list="${single_list}${single_list:+ }${sample}"
  elif [[ $n -gt 4 ]]; then
    # Raw fastq-dump output above four files cannot be renamed, but an already
    # named, complete multi-lane sample is exactly what Step 8 accepts.
    if multilane_state "$sample_dir" "$sample"; then
      eligible=$(( eligible + 1 ))
    else
      lane_state=$?
      reason="$REASON_TOO_MANY"
      [[ $lane_state -eq 2 ]] && reason="$REASON_LANE_INCOMPLETE"
      printf '%s\t%s\t%s\n' "$sample" "$n" "$reason" >> "$INELIGIBLE_TSV"
      over_four=$(( over_four + 1 ))
      ineligible=$(( ineligible + 1 ))
    fi
  else
    eligible=$(( eligible + 1 ))
  fi
done < <(find "$SUCCESS_DIR" -mindepth 1 -maxdepth 1 -type d -name 'SRR*' -print0 | sort -z)

{
  echo "[step6] Summary: total=$total  (1=${counts[1]}, 2=${counts[2]}, 3=${counts[3]}, 4=${counts[4]})"
  echo "[step6] Cell Ranger eligibility: $eligible eligible, $ineligible ineligible (of $scanned sample directories)"

  if [[ $single_fastq -gt 0 ]]; then
    echo "[step6] WARNING: $single_fastq sample(s) have only 1 FASTQ file; Cell Ranger requires paired R1/R2 reads."
    for s in $single_list; do
      echo "[step6] WARNING:   - $s"
    done
  fi
  if [[ $no_fastq -gt 0 ]]; then
    echo "[step6] WARNING: $no_fastq sample directory/directories contain no fastq.gz file at all."
  fi
  if [[ $over_four -gt 0 ]]; then
    echo "[step6] NOTE: $over_four sample(s) have more than 4 FASTQ files; step 7 will not rename them."
    echo "[step6] NOTE: they reach Cell Ranger only if they already use the <sample>_S*_L*_R[12]_*.fastq.gz naming."
  fi
  if [[ $ineligible -gt 0 ]]; then
    echo "[step6] Ineligible samples written to: $INELIGIBLE_TSV"
  fi
  if [[ $eligible -eq 0 ]]; then
    echo "[step6] WARNING: no sample is eligible for Cell Ranger; step 8 will have nothing to process."
  fi

  echo "[step6] Wrote files under: $SUCCESS_DIR"
} | tee -a "$LOG_DIR/step6_check_counts.log"
