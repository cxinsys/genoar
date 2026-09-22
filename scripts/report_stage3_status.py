#!/usr/bin/env python3
"""Best-effort summary of the newest Stage 3 run record.

This is intentionally a reporter, not a second verifier.  Stage 3 writes the
authoritative ``outcome.json``; this command only makes that record visible from
``make status`` and never turns a missing or malformed record into success.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COUNT_KEYS = (
    "fresh",
    "cache_hit",
    "released",
    "adopted",
    "suspect",
    "missing",
    "unverified",
    "stale",
    "ineligible",
)


def _count(counts: Mapping[str, Any], name: str) -> int:
    value = counts.get(name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid {name} count")
    return value


def _status(counts: Mapping[str, Any]) -> str:
    expected = _count(counts, "expected")
    completed = _count(counts, "completed")
    if completed > expected:
        raise ValueError("completed exceeds expected")
    if expected == 0:
        return "no_data"
    if completed == expected:
        return "complete"
    if completed > 0 or _count(counts, "adopted") > 0:
        return "partial"
    if _count(counts, "suspect") > 0:
        return "suspect"
    return "incomplete"


def report(results_dir: Path) -> None:
    print("Stage 3 results:")
    latest_file = results_dir / "runs" / "LATEST"
    try:
        run_id = latest_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        print("  - Latest run: none recorded")
        return
    except OSError as exc:
        print(f"  - Latest run: unreadable ({exc})")
        return

    if not RUN_ID_PATTERN.fullmatch(run_id) or run_id == "LATEST":
        print("  - Latest run: invalid pointer; inspect results/runs/LATEST")
        return

    print(f"  - Latest run: {run_id}")
    outcome_file = results_dir / "runs" / run_id / "outcome.json"
    try:
        outcome = json.loads(outcome_file.read_text(encoding="utf-8"))
        if not isinstance(outcome, dict) or outcome.get("run_id") != run_id:
            raise ValueError("record does not match the LATEST run id")
        counts = outcome.get("counts")
        if not isinstance(counts, dict):
            raise ValueError("counts are missing")
        status = _status(counts)
        expected = _count(counts, "expected")
        completed = _count(counts, "completed")
        details = ", ".join(f"{key}={_count(counts, key)}" for key in COUNT_KEYS)
    except FileNotFoundError:
        print("  - Status: outcome.json not written yet")
        return
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"  - Status: outcome record unreadable ({exc})")
        return

    print(f"  - Status: {status} ({completed}/{expected} verified complete)")
    print(f"  - Counts: {details}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    report(args.results_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
