#!/usr/bin/env bash
# Run the SRR pipeline (Stage 3 / Cell Ranger) under Singularity/Apptainer.
#
# This mirrors the Docker invocation in the project Makefile (`make run-stage3`):
#
#   docker run --rm -it \
#     -v $(pwd)/sample_sra:/work/data/sra \
#     -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \
#     -v $(pwd)/ref:/ref \
#     -v $(pwd)/cellranger:/opt/cellranger \
#     -v $(pwd)/logs:/work/logs \
#     -v $(pwd)/results:/work/results \
#     genoar-srr:step9
#
# Singularity differs from Docker in a few ways that matter here:
#   * the .sif is READ-ONLY, so all writable paths (/work/*) are bind-mounted to the host;
#   * it runs as the invoking user (no root), so outputs are owned by you;
#   * bind SOURCE dirs must already exist on the host.
#
# Usage:
#   run_singularity_pipeline.sh [options]
#
# Options:
#   --sif        <file.sif>   Singularity image (default: genoar-srr_step9.sif)
#   --sra        <dir>        SRA input dir            -> /work/data/sra   (default: ./sample_sra)
#   --config     <file>       Pipeline config .yaml    -> /work/config.yaml (default: srr_pipeline_package/configs/example.yaml)
#   --ref        <dir>        Reference genome dir     -> /ref             (default: ./ref)
#   --cellranger <dir>        Cell Ranger install dir  -> /opt/cellranger  (default: ./cellranger)
#   --logs       <dir>        Log output dir           -> /work/logs       (default: ./logs)
#   --results    <dir>        Results output dir       -> /work/results    (default: ./results)
#   --dry-run                 Print the singularity command without executing
#   -h, --help                Show this help
#
# NOTE on paths: the container's config.yaml (ref_path / cellranger_bin) must match
# the bind TARGETS above (/ref, /opt/cellranger/cellranger). If they don't match,
# the Cell Ranger step is skipped. See srr_pipeline_package/singularity/README.md.
set -euo pipefail

SIF="genoar-srr_step9.sif"
SRA_DIR="./sample_sra"
CONFIG="srr_pipeline_package/configs/example.yaml"
REF_DIR="./ref"
CELLRANGER_DIR="./cellranger"
LOG_DIR="./logs"
RESULTS_DIR="./results"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sif)        SIF="$2"; shift 2 ;;
    --sra)        SRA_DIR="$2"; shift 2 ;;
    --config)     CONFIG="$2"; shift 2 ;;
    --ref)        REF_DIR="$2"; shift 2 ;;
    --cellranger) CELLRANGER_DIR="$2"; shift 2 ;;
    --logs)       LOG_DIR="$2"; shift 2 ;;
    --results)    RESULTS_DIR="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# Pick engine
if command -v apptainer >/dev/null 2>&1; then
  ENGINE="apptainer"
elif command -v singularity >/dev/null 2>&1; then
  ENGINE="singularity"
else
  ENGINE="singularity"
  [[ "$DRY_RUN" -eq 0 ]] && { echo "ERROR: neither apptainer nor singularity found on PATH." >&2; exit 1; }
fi

# Writable output dirs must exist before binding
mkdir -p "$LOG_DIR" "$RESULTS_DIR"

# Validate inputs (skip hard-fail in dry-run so the command can still be shown)
if [[ "$DRY_RUN" -eq 0 ]]; then
  [[ -f "$SIF" ]]            || { echo "ERROR: sif not found: $SIF (build with build_sif.sh)" >&2; exit 1; }
  [[ -d "$SRA_DIR" ]]       || { echo "ERROR: SRA dir not found: $SRA_DIR" >&2; exit 1; }
  [[ -f "$CONFIG" ]]        || { echo "ERROR: config not found: $CONFIG" >&2; exit 1; }
  [[ -d "$REF_DIR" ]]       || { echo "ERROR: reference dir not found: $REF_DIR" >&2; exit 2; }
  [[ -d "$CELLRANGER_DIR" ]]|| { echo "ERROR: cellranger dir not found: $CELLRANGER_DIR" >&2; exit 2; }
fi

# Bind sources must exist for Singularity/Apptainer (unlike Docker, which
# auto-creates them). A real run requires the reference and Cell Ranger binds;
# dry-run may omit them so operators can still inspect the rendered command.
CMD=("$ENGINE" exec
  --bind "${SRA_DIR}:/work/data/sra"
  --bind "${CONFIG}:/work/config.yaml"
  --bind "${LOG_DIR}:/work/logs"
  --bind "${RESULTS_DIR}:/work/results")
[[ -d "$REF_DIR" ]]        && CMD+=(--bind "${REF_DIR}:/ref")
[[ -d "$CELLRANGER_DIR" ]] && CMD+=(--bind "${CELLRANGER_DIR}:/opt/cellranger")
# Through the dispatcher, which reads `legacy_pipeline` and hands the run to the
# verified tree or the corrected one. Calling /pipeline/run_docker_pipeline.sh
# directly -- which this did -- meant the setting did nothing at all on the one
# path that matters, and every cluster run got the frozen pipeline whatever the
# config said.
#
# The `sh -c` falls back for an image built before both trees existed, so an
# older .sif still runs instead of failing on a missing entrypoint.
CMD+=("$SIF" sh -c
  'if [ -x /pipeline_entry.sh ]; then exec /pipeline_entry.sh "$@"; fi
   # No dispatcher: an image built before both trees existed. Falling back is
   # right for a run that wanted the verified tree, because that is the only
   # tree this image has. It is wrong for one that asked for the corrected
   # tree: the run would go ahead under code the config did not ask for, and
   # nothing in the output would say so.
   # An image without the dispatcher carries only the earlier tree. Any config
   # that is not asking for that tree is asking for something this image does
   # not have, and running it anyway is the silent wrong answer.
   if ! grep -Eiq "^[[:space:]]*legacy_pipeline:[[:space:]]*.?(true|yes|on|1)([[:space:]]|$)" /work/config.yaml 2>/dev/null \
      && ! grep -Eq "^[[:space:]]*pipeline_mode:[[:space:]]*.?verified" /work/config.yaml 2>/dev/null \
      && ! grep -Eiq "^[[:space:]]*safe_mode:[[:space:]]*.?(true|yes|on|1)([[:space:]]|$)" /work/config.yaml 2>/dev/null; then
     echo "[run_singularity] FATAL: this config asks for the current pipeline and" >&2
     echo "[run_singularity] this image was built before it existed -- it carries only" >&2
     echo "[run_singularity] the verified tree and no dispatcher." >&2
     echo "[run_singularity] Rebuild the image, or set legacy_pipeline: true." >&2
     exit 2
   fi
   exec /pipeline/run_docker_pipeline.sh "$@"' --)

echo "[run_singularity] engine=$ENGINE sif=$SIF"
printf '[run_singularity] command:\n  %s\n' "${CMD[*]}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[run_singularity] dry-run: not executing."
  exit 0
fi

"${CMD[@]}"
echo "[run_singularity] done. results -> $RESULTS_DIR"
