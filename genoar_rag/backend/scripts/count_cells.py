"""Count the cells in every processed matrix the service offers.

Read-only. Walks the processed-results layouts the way download_service does,
opens each run's filtered_feature_bc_matrix.h5 and reads the barcode count out
of it — that is the number of cells Cell Ranger called for the run. Writes one
JSON, `{run_id: n_cells}`, which the stats endpoint folds into its totals.

Run inside the backend container, where h5py and the results mount are:

    podman exec genoar_rag_backend_1 python scripts/count_cells.py \
        > /tmp/cell_counts.json

Sizes alone are read (`barcodes.shape`), not the matrix, so a file costs a few
milliseconds and the whole corpus a few minutes. Runs whose file is missing or
unreadable are reported on stderr and left out of the JSON rather than written
as zero, so a gap stays visible as a gap.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import h5py

ROOT = Path(os.environ.get("PROCESSED_RESULTS_DIR", "/data/genoar/results"))
LAYOUTS = [
    s for s in os.environ.get("PROCESSED_RESULTS_LAYOUTS", "").split(",") if s.strip()
] or ["{run_id}/cellranger_output/outs"]
FILENAME = "filtered_feature_bc_matrix.h5"


def barcodes(path: Path) -> int:
    with h5py.File(path, "r") as f:
        grp = f["matrix"] if "matrix" in f else f[next(iter(f.keys()))]
        return int(grp["barcodes"].shape[0])


def main() -> int:
    counts: dict[str, int] = {}
    missing: list[str] = []
    for layout in LAYOUTS:
        head, _, tail = layout.partition("{run_id}")
        base = ROOT / head.strip("/") if head.strip("/") else ROOT
        if not base.is_dir():
            print(f"layout base not found: {base}", file=sys.stderr)
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            h5 = entry / tail.strip("/") / FILENAME if tail.strip("/") else entry / FILENAME
            if not h5.is_file():
                missing.append(entry.name)
                continue
            try:
                counts[entry.name] = barcodes(h5)
            except Exception as exc:  # noqa: BLE001 — one bad file must not stop the census
                print(f"{entry.name}: {exc}", file=sys.stderr)
                missing.append(entry.name)
    json.dump(counts, sys.stdout, separators=(",", ":"))
    print(
        f"\n{len(counts)} runs counted, {sum(counts.values()):,} cells, "
        f"{len(missing)} without a readable matrix",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
