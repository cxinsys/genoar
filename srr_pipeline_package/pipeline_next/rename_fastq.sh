#!/usr/bin/env bash
set -euo pipefail

# Usage: rename_fastq.sh [SUCCESS_DIR]
# - Renames FASTQ files for samples listed in fastq_[2-4]_files.tsv
# - Produces: <sample>_S1_R1_001.fastq.gz and <sample>_S1_R2_001.fastq.gz
# - Heuristics per pipeline README. They exist to interpret raw
#   `fastq-dump --split-files` output (<sample>_1.fastq.gz .. <sample>_4.fastq.gz),
#   where an index/barcode read has to be dropped:
#   Which file is which comes from the length of the first read, not from the
#   size of the file. A 10x run has one read of 24-32 bases (cell barcode and
#   UMI), one of 40 or more (cDNA), and index reads of 8-12; those are set by
#   the chemistry rather than by the sequencing depth or the compressor.
#   Anything that does not resolve to exactly one barcode read and one cDNA
#   read is refused and recorded, not guessed at.
#
#   Sizes decided this until it was found that they cannot. Losing the cDNA
#   read -- the largest file, so the first casualty of a truncated copy --
#   shuffled the rest up and renamed an 8-base index read to R1, and the run
#   reported success.
#
# A sample that ALREADY carries the naming Cell Ranger wants is left alone. The
# Snakefile (has_r1r2 / get_sample_fastqs) accepts two forms and so do we:
#   * single lane: <sample>_S1_R[12]_001.fastq.gz
#   * multi lane:  <sample>_S*_L*_R[12]_*.fastq.gz
# "Already named" means the form is COMPLETE: R1 and R2 both present, for the
# same sample, and paired within the same lane. R1 without R2, R2 without R1,
# and an R1 and R2 that live in different lanes are not layouts Cell Ranger can
# use, so they are recorded as ineligible with a reason instead of being waved
# through as correctly named.
# The size heuristics must never be let loose on either. On a 4-file, 2-lane
# sample they pick two files out of four by size -- which silently drops both
# L002 reads AND picks the two R2 files (R2 is the larger read), so the result
# is not even a valid R1/R2 pair. The run then reports success.
#
# Anything else that already carries Illumina read tokens (_R1_/_R2_/_I1_/_I2_)
# but forms no complete R1/R2 set is refused loudly and left untouched, and is
# recorded in cellranger_ineligible.tsv alongside step 6's own census. A partial
# rename is worse than no rename: it destroys the only evidence of the layout.

SUCCESS_DIR="${1:-/work/results/success}"
LOG_DIR="${LOG_DIR:-/work/logs}"

mkdir -p "$SUCCESS_DIR" "$LOG_DIR"

# Load reporting helpers if available
REPORTS_DIR_DEFAULT="/work/results/reports"
if [[ -f "/pipeline_next/lib.sh" ]]; then
  # shellcheck source=/dev/null
  source "/pipeline_next/lib.sh"
fi
ensure_reports_dir 2>/dev/null || true

log() { echo "[step7] $*" | tee -a "$LOG_DIR/step7_rename_fastq.log"; }

if [[ ! -d "$SUCCESS_DIR" ]]; then
  echo "[step7] Success directory not found: $SUCCESS_DIR" >&2
  exit 1
fi

# Written by step 6 (check_counts.sh); step 7 only ever appends rows to it.
INELIGIBLE_TSV="$SUCCESS_DIR/cellranger_ineligible.tsv"

# Canonical, sample-independent reasons. The orchestrator groups the report's
# rows by this exact string, so keep per-sample detail out of them.
REASON_PRENAMED_NO_PAIR='FASTQ names already use Illumina read tokens but form no R1/R2 set; step 7 refused to rename'
REASON_COUNT_CHANGED='FASTQ count no longer matches the step 6 census; step 7 refused to rename'
REASON_LANE_MISMATCH='multi-lane FASTQ names do not pair R1 with R2 within the same lane; step 7 refused to rename'

n_renamed=0
n_already=0
n_refused=0
n_skipped=0

record_failed() {
  local sample="$1"
  if type append_failed_item >/dev/null 2>&1; then
    append_failed_item 7 "$sample" || true
  else
    mkdir -p "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}" && echo "$sample" >> "${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}/step7_failed.txt" || true
  fi
}

# Add one row to step 6's ineligibility census, at most once per sample so that
# re-running step 7 cannot pile up duplicates.
mark_ineligible() {
  local sample="$1" n="$2" reason="$3"
  if [[ ! -f "$INELIGIBLE_TSV" ]]; then
    printf '# sample\tfastq_count\treason\n' > "$INELIGIBLE_TSV" || return 0
  elif awk -F'\t' -v s="$sample" '$1 == s { found = 1 } END { exit !found }' "$INELIGIBLE_TSV"; then
    return 0
  fi
  printf '%s\t%s\t%s\n' "$sample" "$n" "$reason" >> "$INELIGIBLE_TSV" || true
}

fastq_names() {
  local sample_dir="$1"
  local f names=""
  while IFS= read -r -d '' f; do
    names="${names}${names:+, }$(basename "$f")"
  done < <(find "$sample_dir" -maxdepth 1 -type f -name '*.fastq.gz' -print0 | sort -z)
  echo "$names"
}

# Refuse to touch a sample whose layout this script cannot rename safely. Never
# fails the step: one unrenameable sample must not abort the whole run.
refuse_sample() {
  local sample_dir="$1" sample="$2" n="$3" reason="$4"
  log "REFUSING to rename $sample: $reason"
  log "REFUSING:   files: $(fastq_names "$sample_dir")"
  log "REFUSING:   left untouched; step 7 renames raw fastq-dump output only"
  log "REFUSING:   (<sample>_1.fastq.gz .. <sample>_4.fastq.gz)"
  log "REFUSING:   excluded from Cell Ranger; see $INELIGIBLE_TSV"
  record_failed "$sample"
  mark_ineligible "$sample" "$n" "$reason"
  n_refused=$(( n_refused + 1 ))
}

# Already named the single-lane way Cell Ranger wants: BOTH reads, exact names.
# The old check accepted any *_S1_R1_001.fastq.gz on its own, so an R1 with no
# R2 -- and an R1 belonging to a different sample -- passed as "already renamed"
# and was never recorded as ineligible.
is_single_lane_pair() {
  local sample_dir="$1" sample="$2"
  [[ -f "$sample_dir/${sample}_S1_R1_001.fastq.gz" ]] || return 1
  [[ -f "$sample_dir/${sample}_S1_R2_001.fastq.gz" ]] || return 1
  return 0
}

# Already named the multi-lane way the Snakefile globs for. Same patterns as
# has_r1r2()/get_sample_fastqs() in Snakefile_hs.smk -- keep them in step --
# but a lane whose R1 has no R2 (or the reverse) is not a complete layout:
# Cell Ranger would be handed reads it cannot pair. Answers 0 for a coherent
# multi-lane set, 2 for an incoherent one, 1 for "not multi-lane at all".
# 10x chemistry, not sequencing depth: the cell barcode and UMI read is 26 bases
# on v2 and 28 on v3, the index reads are 8 to 12, and the cDNA read is 90 or
# more. The bounds are wide enough for the variants and narrow enough that an
# index read cannot be mistaken for a barcode read.
BARCODE_MIN="${GENOAR_BARCODE_MIN:-24}"
BARCODE_MAX="${GENOAR_BARCODE_MAX:-32}"
CDNA_MIN="${GENOAR_CDNA_MIN:-40}"

first_read_length() {
  # The sequence line of the first record, in bases. 0 when it cannot be read.
  #
  # `awk ... exit` rather than `sed -n '2{p;q}'`: BSD sed refuses that form
  # ("extra characters at the end of q command"), and which sed a cluster has
  # is not something to find out from a failed run.
  #
  # Either way it stops after the line it wants, which closes the pipe and
  # hands gzip a SIGPIPE. Under `pipefail` that is a non-zero status for a
  # command that did exactly what was asked of it -- the same misreading that
  # once reported a healthy matrix as INCOMPLETE. So pipefail is off for the
  # one line that needs it off.
  local f="$1" line
  set +o pipefail
  line="$(gzip -cd < "$f" 2>/dev/null | awk 'NR==2{print; exit}' | tr -d '\r\n')"
  set -o pipefail
  printf '%s' "${#line}"
}

multilane_state() {
  # Space-delimited lane lists rather than associative arrays: same question,
  # asked in a shell every cluster has.
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

# Someone already assigned reads to R1/R2/I1/I2. Whatever the layout is, it is
# not raw fastq-dump output and picking files by size would guess over it.
has_illumina_read_token() {
  # Anchored to the sample. Unanchored, a neighbour's already-named file made
  # this sample "already carries read tokens but forms no R1/R2 set" -- a
  # refusal whose reason was about somebody else's data.
  local sample_dir="$1" sample="$2" pat
  # One pattern per `ls`: ls exits non-zero if ANY operand is missing, so
  # passing all four at once would only ever answer "all four present".
  for pat in '_R1_' '_R2_' '_I1_' '_I2_'; do
    if ls "$sample_dir/${sample}"*"${pat}"*.fastq.gz >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

rename_two() {
  local sample_dir="$1" sample="$2"
  # Anchored to this sample. `*_1.fastq.gz` also matches a stray
  # SOMEONE_ELSE_1.fastq.gz sitting in the directory, and renaming that gives
  # this sample another sample's reads under its own name.
  local f1="$sample_dir/${sample}_1.fastq.gz"
  local f2="$sample_dir/${sample}_2.fastq.gz"
  if [[ ! -f "$f1" || ! -f "$f2" ]]; then
    log "WARN: Expected ${sample}_1/_2 not found for $sample; skipping"
    record_failed "$sample"
    n_skipped=$(( n_skipped + 1 ))
    return 0
  fi
  # `--split-files` writes the barcode read first, and usually does. Length
  # says whether it did this time: two files carry no index read, so one
  # barcode-length read and one cDNA-length read settle it outright. Where the
  # lengths do not say -- data that is not 10x, or reads of unusual length --
  # the documented order stands, and the log says which rule was used.
  local len1 len2 chose="the order --split-files wrote them in"
  len1="$(first_read_length "$f1")"
  len2="$(first_read_length "$f2")"
  if [[ "$len1" -ge "$BARCODE_MIN" && "$len1" -le "$BARCODE_MAX" \
        && "$len2" -ge "$CDNA_MIN" ]]; then
    chose="read length"
  elif [[ "$len2" -ge "$BARCODE_MIN" && "$len2" -le "$BARCODE_MAX" \
          && "$len1" -ge "$CDNA_MIN" ]]; then
    local swap="$f1"; f1="$f2"; f2="$swap"
    chose="read length (the files are the other way round on disk)"
  fi
  if [[ "$chose" == "the order --split-files wrote them in" ]]; then
    # The same evidence the n=3/4 path refuses on. Two files whose lengths
    # identify neither a barcode read nor a cDNA read are not a pair this can
    # name, and renaming them anyway handed Cell Ranger an 8-base R2 with
    # nothing but a log line to say so.
    if ! [[ ( "$len1" -ge "$BARCODE_MIN" && "$len1" -le "$BARCODE_MAX" ) \
            || ( "$len2" -ge "$BARCODE_MIN" && "$len2" -le "$BARCODE_MAX" ) ]] \
       || ! [[ "$len1" -ge "$CDNA_MIN" || "$len2" -ge "$CDNA_MIN" ]]; then
      refuse_sample "$sample_dir" "$sample" 2 \
        "read lengths (${len1}b, ${len2}b) identify neither a barcode read nor a cDNA read"
      return 0
    fi
  fi
  log "R1/R2 chosen by $chose: ${len1}b and ${len2}b"
  local r1="$sample_dir/${sample}_S1_R1_001.fastq.gz"
  local r2="$sample_dir/${sample}_S1_R2_001.fastq.gz"
  if [[ -f "$r1" && -f "$r2" ]]; then
    log "Already renamed: $sample"
    n_already=$(( n_already + 1 ))
    return 0
  fi
  mv "$f1" "$r1"
  mv "$f2" "$r2"
  n_renamed=$(( n_renamed + 1 ))
  log "Renamed (2 files, by $chose): $sample -> $(basename "$r1"), $(basename "$r2")"
}

rename_multi_pick() {
  local sample_dir="$1" sample="$2" n="$3"
  # Gather files robustly (handle spaces/newlines). `while read -d ''` rather
  # than `mapfile -d`, which is bash 4.4 and later.
  # Anchored to this sample. `*.fastq.gz` collects a neighbour's reads sitting
  # in the same directory, and renaming one of those gives this sample another
  # sample's data under its own name -- which rename_two already guards
  # against and this path did not.
  local files=() one
  while IFS= read -r -d '' one; do
    files+=("$one")
  done < <(find "$sample_dir" -maxdepth 1 -type f -name "${sample}_*.fastq.gz" -print0)
  if [[ ${#files[@]} -lt $n ]]; then
    log "WARN: Expected at least $n fastq.gz for $sample; found ${#files[@]}"
    record_failed "$sample"
    n_skipped=$(( n_skipped + 1 ))
    return 0
  fi
  # More files on disk than the census recorded means the directory changed
  # under us. The picks below are positional, so they would silently grab the
  # wrong two files. Refuse instead of guessing.
  if [[ ${#files[@]} -gt $n ]]; then
    refuse_sample "$sample_dir" "$sample" "${#files[@]}" "$REASON_COUNT_CHANGED"
    return 0
  fi
  # Which file is which, from the reads themselves.
  #
  # Not by sorting on compressed size and picking by position -- for three
  # files the median and the largest, for four the two largest. Size is
  # a proxy for read length and it is a bad one -- it moves with compression,
  # with how many reads a file has, and with nothing at all when a file is
  # missing. Delete the cDNA read (the largest file, and so the one a
  # truncated copy loses first) and the remaining three shuffle up: the index
  # read becomes "R1" and the barcode read becomes "R2". Both are renamed,
  # Cell Ranger runs, a BAM appears, and the run reports success. There is no
  # artefact anywhere saying it happened.
  #
  # Read length says it outright. A 10x run has one read of 24-32 bases (the
  # cell barcode and UMI), one much longer read (the cDNA), and index reads of
  # 8-12. Those are set by the chemistry, not by the sequencing depth or the
  # compressor.
  local r1_src="" r2_src="" longest=0 barcodes=0 cdnas=0 unreadable=""
  local f len
  for f in "${files[@]}"; do
    len="$(first_read_length "$f")"
    if [[ "$len" -eq 0 ]]; then
      unreadable="$unreadable $(basename "$f")"
      continue
    fi
    if [[ "$len" -ge "$BARCODE_MIN" && "$len" -le "$BARCODE_MAX" ]]; then
      barcodes=$(( barcodes + 1 )); r1_src="$f"
    elif [[ "$len" -ge "$CDNA_MIN" ]]; then
      # Counted whatever its length. Incrementing only when a longer one turned
      # up meant a second cDNA-length read was invisible, so four files at
      # 8/28/98/90 renamed silently and 8/28/90/90 picked whichever the
      # filesystem listed first -- the positional guess this replaced.
      cdnas=$(( cdnas + 1 ))
      if [[ "$len" -gt "$longest" ]]; then longest="$len"; r2_src="$f"; fi
    fi
  done
  if [[ -n "$unreadable" ]]; then
    refuse_sample "$sample_dir" "$sample" "$n" \
      "could not read the first record of:$unreadable"
    return 0
  fi
  # Exactly one of each, or nothing is renamed. Two candidate barcode reads is
  # a layout this cannot resolve, and picking one of them is the guess this
  # replaced.
  if [[ "$barcodes" -ne 1 || "$cdnas" -ne 1 || -z "$r2_src" ]]; then
    refuse_sample "$sample_dir" "$sample" "$n" \
      "read lengths do not identify one barcode read and one cDNA read (found $barcodes barcode-length and $cdnas cDNA-length of $n files)"
    return 0
  fi
  log "Read lengths chose: R1=$(basename "$r1_src") R2=$(basename "$r2_src")"
  local r1="$sample_dir/${sample}_S1_R1_001.fastq.gz"
  local r2="$sample_dir/${sample}_S1_R2_001.fastq.gz"
  if [[ -f "$r1" && -f "$r2" ]]; then
    log "Already renamed: $sample"
    n_already=$(( n_already + 1 ))
    return 0
  fi
  mv "$r1_src" "$r1"
  mv "$r2_src" "$r2"
  n_renamed=$(( n_renamed + 1 ))
  log "Renamed ($n files, by read length): $sample -> $(basename "$r1"), $(basename "$r2")"
}

process_sample() {
  local sample="$1"
  local n="$2"
  local ml_state
  local sample_dir="$SUCCESS_DIR/$sample"
  if [[ ! -d "$sample_dir" ]]; then
    log "WARN: Sample dir not found: $sample (skip)"
    n_skipped=$(( n_skipped + 1 ))
    return 0
  fi
  # Leave a sample alone only when what is on disk is a COMPLETE, coherent
  # layout: R1 and R2 for the same sample, in one of the two forms the Snakefile
  # reads. "An R1 exists" is not that, and treating it as such let a sample with
  # no R2 through as correctly named, unrecorded and unanalysable.
  if is_single_lane_pair "$sample_dir" "$sample"; then
    log "Already correctly named (single lane R1+R2): $sample"
    n_already=$(( n_already + 1 ))
    return 0
  fi
  # Already in the multi-lane form the Snakefile reads. Renaming would mean
  # collapsing several lanes into one filename, i.e. losing lanes.
  multilane_state "$sample_dir" "$sample" && ml_state=0 || ml_state=$?
  if [[ $ml_state -eq 0 ]]; then
    log "Already correctly named (multi-lane, $n files): $sample; leaving untouched"
    n_already=$(( n_already + 1 ))
    return 0
  fi
  if [[ $ml_state -eq 2 ]]; then
    refuse_sample "$sample_dir" "$sample" "$n" "$REASON_LANE_MISMATCH"
    return 0
  fi
  # Named by read, but not in a form we recognise: do not guess over it.
  if has_illumina_read_token "$sample_dir" "$sample"; then
    refuse_sample "$sample_dir" "$sample" "$n" "$REASON_PRENAMED_NO_PAIR"
    return 0
  fi
  case "$n" in
    2) rename_two "$sample_dir" "$sample" ;;
    3) rename_multi_pick "$sample_dir" "$sample" 3 ;;
    4) rename_multi_pick "$sample_dir" "$sample" 4 ;;
    *) log "INFO: Unsupported count=$n for $sample; skipping"; \
       n_skipped=$(( n_skipped + 1 )); \
       record_failed "$sample" ;;
  esac
}

run_from_tsv() {
  # Properly handle unset variables with set -u
  local n
  n="${1:-}"

  if [[ -z "$n" ]]; then
    log "ERROR: run_from_tsv() called without argument"
    return 1
  fi

  local tsv="$SUCCESS_DIR/fastq_${n}_files.tsv"
  if [[ ! -f "$tsv" ]]; then
    log "No list for ${n} files: $tsv (skip)"
    return 0
  fi

  log "Processing TSV: $tsv"

  while IFS= read -r sample; do
    [[ -n "$sample" ]] || continue
    [[ "$sample" =~ ^# ]] && continue
    process_sample "$sample" "$n"
  done < "$tsv"
}

log "Starting rename in: $SUCCESS_DIR"
run_from_tsv 2
run_from_tsv 3
run_from_tsv 4
log "Summary: renamed=$n_renamed, already correctly named=$n_already, refused=$n_refused, skipped=$n_skipped"
if [[ $n_refused -gt 0 ]]; then
  log "WARNING: $n_refused sample(s) were left untouched because their layout"
  log "WARNING: cannot be renamed safely. They will not reach Cell Ranger."
  log "WARNING: Reasons are recorded in: $INELIGIBLE_TSV"
fi
log "Completed renaming."
