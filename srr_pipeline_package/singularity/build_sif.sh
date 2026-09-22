#!/usr/bin/env bash
# Build a Singularity/Apptainer image (.sif) from the SRR pipeline Docker image.
#
# The Docker path is unchanged. This only produces a .sif for HPC
# (Singularity/PBS) environments that do not allow Docker.
#
# Usage:
#   build_sif.sh [options]
#
# Options:
#   --from   daemon|archive|registry   Source of the image (default: daemon)
#   --image  <name:tag>                Docker image / registry ref (default: genoar-srr:step9)
#   --tar    <path>                    docker-archive .tar (required for --from archive)
#   --output <file.sif>               Output .sif path (default: genoar-srr_step9.sif)
#   --fakeroot                        Pass --fakeroot to the build (some HPC require it)
#   --sudo                            Run the build under sudo
#   --dry-run                         Print the command without executing
#   -h, --help                        Show this help
#
# Notes:
#   * "daemon"  : convert directly from the local Docker daemon (needs Docker + usually root/fakeroot).
#   * "archive" : `docker save genoar-srr:step9 -o srr.tar` first, then convert the .tar (no Docker daemon needed on the build host).
#   * "registry": pull from a registry, e.g. --image docker.io/<user>/genoar-srr:step9.
#   Building a .sif typically needs root or --fakeroot; if the HPC forbids both,
#   build on another Linux host and copy the .sif over.
set -euo pipefail

FROM="daemon"
IMAGE="genoar-srr:step9"
TAR=""
OUTPUT="genoar-srr_step9.sif"
FAKEROOT=""
SUDO=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from)     FROM="$2"; shift 2 ;;
    --image)    IMAGE="$2"; shift 2 ;;
    --tar)      TAR="$2"; shift 2 ;;
    --output)   OUTPUT="$2"; shift 2 ;;
    --fakeroot) FAKEROOT="--fakeroot"; shift ;;
    --sudo)     SUDO="sudo"; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# Pick the singularity/apptainer binary
if command -v apptainer >/dev/null 2>&1; then
  ENGINE="apptainer"
elif command -v singularity >/dev/null 2>&1; then
  ENGINE="singularity"
else
  ENGINE="singularity"   # assume present on target; dry-run still prints
  [[ "$DRY_RUN" -eq 0 ]] && { echo "ERROR: neither apptainer nor singularity found on PATH." >&2; exit 1; }
fi

# Resolve the source URI for `build`
case "$FROM" in
  daemon)   SRC="docker-daemon://${IMAGE}" ;;
  archive)  [[ -n "$TAR" ]] || { echo "ERROR: --from archive requires --tar <path>" >&2; exit 2; }
            SRC="docker-archive://${TAR}" ;;
  registry) SRC="docker://${IMAGE}" ;;
  *) echo "ERROR: --from must be daemon|archive|registry" >&2; exit 2 ;;
esac

CMD=($SUDO "$ENGINE" build $FAKEROOT "$OUTPUT" "$SRC")

echo "[build_sif] engine=$ENGINE from=$FROM source=$SRC output=$OUTPUT"
printf '[build_sif] command: %s\n' "${CMD[*]}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[build_sif] dry-run: not executing."
  exit 0
fi

"${CMD[@]}"
echo "[build_sif] done -> $OUTPUT"
