#!/usr/bin/env bash
# Which Stage 3 runs: the one that was verified, or the one that was fixed.
#
# `/pipeline` holds the pipeline as it stood when a sample went through K-BDS
# and came back complete, plus the two fixes made by hand on the cluster to
# get it there (the `--create-bam` version branch, and finding the Cell
# Ranger launcher under bin/). Nothing else. It is frozen, and a test fails if
# it changes.
#
# `/pipeline_next` is the same pipeline with everything found since: the checks
# that were reporting nothing, the silent failures, retention, the corpus path.
# Better reasoned, and never run on a cluster.
#
# Both travel in the image, and `legacy_pipeline` in the config decides.
# `pipeline_mode: verified|corrected` and `safe_mode: true|false` are earlier
# spellings and still answer.
#
# The default is the current tree. This is a pipeline handed to people who will
# run it on hardware nobody here can see, and the one whose silent failures are
# fixed is the one to hand them. The earlier tree is for reproducing an earlier
# run against the bytes that produced it, which is the only thing it is better
# at.
#
# The two are also an instrument. Run the same input under each and compare the
# outcome records: a sample the two disagree about is a sample where something
# we changed actually changed the answer, which is the only way to find out
# whether the fixes matter on real data rather than on fixtures.
set -uo pipefail

CONFIG="${CONFIG:-/work/config.yaml}"
# The same argument the pipeline takes, read without consuming it: whichever
# tree runs gets the arguments exactly as they arrived.
if [[ "${1:-}" == "--config" && -n "${2:-}" ]]; then
  CONFIG="$2"
fi

TREE_CHOICE="$(python3 - "$CONFIG" <<'PY' 2>/dev/null || echo current
import sys
try:
    import yaml
except Exception:
    print("current"); raise SystemExit
try:
    with open(sys.argv[1]) as fh:
        data = yaml.safe_load(fh) or {}
except Exception:
    print("current"); raise SystemExit

# The same table as site_settings.pipeline_tree_of, which cannot be imported
# here -- this runs inside the container with nothing but the standard
# library. A test puts the same cases through both, because a disagreement
# about this value is a disagreement about which code analyses the data.
YES = ("1", "true", "yes", "on")
NO = ("0", "false", "no", "off")


def word(value):
    return "" if value is None else str(value).strip().lower()


chosen = "current"
named = word(data.get("legacy_pipeline"))
if named:
    chosen = "legacy" if named in YES else "current"
else:
    mode = word(data.get("pipeline_mode"))
    if mode:
        chosen = "legacy" if mode == "verified" else "current"
    else:
        older = word(data.get("safe_mode"))
        if older:
            chosen = "legacy" if older in YES else "current"
print(chosen)
PY
)"

if [[ "$TREE_CHOICE" == "legacy" ]]; then
  TREE="/pipeline"
  echo "[entry] legacy_pipeline: true -- running the earlier pipeline ($TREE)."
  echo "[entry] Kept unchanged so an earlier run can be reproduced. It does not"
  echo "[entry] carry the fixes made since; omit the setting for the current one."
else
  TREE="/pipeline_next"
  echo "[entry] running the current pipeline ($TREE)."
fi

if [[ ! -x "$TREE/run_docker_pipeline.sh" ]]; then
  echo "[entry] FATAL: $TREE/run_docker_pipeline.sh is not in this image." >&2
  echo "[entry] An image built before both trees existed carries only one." >&2
  exit 2
fi
exec "$TREE/run_docker_pipeline.sh" "$@"
