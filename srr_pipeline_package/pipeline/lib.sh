#!/usr/bin/env bash
# Common helpers for logging and reporting without changing core behavior.

set -o pipefail

REPORTS_DIR_DEFAULT="/work/results/reports"

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

ensure_reports_dir() {
  local dir="${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}"
  mkdir -p "$dir"
}

log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_err()  { echo "[$(timestamp)] [ERROR] $*" >&2; }

# Drop every sentinel for a step before writing a new one. A leftover step8.ok
# (or a step1.msg describing a failure that has since been fixed) from an earlier
# run would otherwise misreport this run.
clear_step_sentinels() {
  local step="$1"
  local dir="${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}"
  rm -f "$dir/step${step}.ok" "$dir/step${step}.err" \
        "$dir/step${step}.warn" "$dir/step${step}.msg"
}

mark_ok() {
  local step="$1"; shift || true
  local dir="${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}"
  ensure_reports_dir
  clear_step_sentinels "$step"
  : > "$dir/step${step}.ok"
  append_summary "$step" ok "$*"
}

# The step ran to completion but did not do the work it exists to do (e.g. step 8
# finished with zero samples eligible for Cell Ranger). Deliberately does NOT
# write step<N>.ok: consumers that look for .ok must not read this as success.
mark_warn() {
  local step="$1"; shift || true
  local dir="${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}"
  ensure_reports_dir
  clear_step_sentinels "$step"
  : > "$dir/step${step}.warn"
  if [[ -n "${1:-}" ]]; then
    echo "$*" > "$dir/step${step}.msg"
  fi
  append_summary "$step" warn "$*"
}

mark_err() {
  local step="$1"; shift || true
  local dir="${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}"
  ensure_reports_dir
  clear_step_sentinels "$step"
  : > "$dir/step${step}.err"
  if [[ -n "${1:-}" ]]; then
    echo "$*" > "$dir/step${step}.msg"
  fi
  append_summary "$step" err "$*"
}

append_failed_item() {
  local step="$1"; local item="$2"; shift 2 || true
  local dir="${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}"
  ensure_reports_dir
  echo "$item" >> "$dir/step${step}_failed.txt"
}

append_summary() {
  local step="$1"; local status="$2"; shift 2 || true
  local dir="${REPORTS_DIR:-$REPORTS_DIR_DEFAULT}"
  ensure_reports_dir
  local msg="$*"
  printf '{"timestamp":"%s","step":%s,"status":"%s","message":%s}\n' \
    "$(timestamp)" "$step" "$status" \
    "$(printf %s "$msg" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read().strip()))')" \
    >> "$dir/summary.jsonl" 2>/dev/null || true
}

require_cmd() {
  local cmd="$1"; if ! command -v "$cmd" >/dev/null 2>&1; then return 1; fi
}

