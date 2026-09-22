#!/usr/bin/env python3
"""Validate the exact fetch-sra selection before Stage 3 mounts it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fetch_sra import validate_selected_sra_view


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--view-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        accessions = validate_selected_sra_view(args.source_dir, args.view_dir)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] Stage 3 selection is not usable: {exc}", file=sys.stderr)
        print(
            "Run 'make fetch-sra' again. Cached source files are not removed.",
            file=sys.stderr,
        )
        return 2
    print(
        f"[OK] Stage 3 selection contains exactly {len(accessions)} accession(s): "
        + ", ".join(accessions)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
