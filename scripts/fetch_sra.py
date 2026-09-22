#!/usr/bin/env python3
"""Bridge Stage 2's filtered tables to Stage 3's .sra inputs.

Stage 2 writes quality-filtered, human transcriptomic tables under
``first_pass_output/``.  Stage 3 (``make run-stage3``) wants actual ``.sra``
files in ``sample_sra/``.  This command joins those public pipeline stages.

The default path is deliberately the filtered one::

    first_pass_output/HS_*_1st_pass_meta_table.csv
        -> cycle_test.utils.stage3_preparer.extract_srr_from_stage2_tables
        -> crawl_output/stage3_prep/srr_list.txt
        -> cycle_test.utils.stage3_downloader.download_sra_files  (fetch from S3)
        -> sample_sra/<SRR>/<SRR>.sra

Raw Stage 1 accession lists can still be fetched for diagnostic or custom use,
but only through the explicit ``--source raw-stage1`` opt-in.  Missing Stage 2
output never falls back to the larger, unfiltered Stage 1 corpus.

A full download is enormous, so --max-samples is required to be explicit and
``make fetch-sra`` defaults it to a small number. Pass ``all`` (or 0) to lift it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

# Import the two helpers by their directory rather than as cycle_test.utils.*.
# The package's __init__ pulls in the stage runners as well, and those need
# PyYAML, which this script otherwise does not: fetch-sra runs on the host, not
# inside a container, so it should hold to the standard library.
sys.path.insert(0, str(REPO_ROOT / "cycle_test" / "utils"))

try:
    from stage3_preparer import extract_srr_from_stage2_tables, prepare_stage3
    from stage3_downloader import download_sra_files
except ImportError as e:  # pragma: no cover - a broken checkout, not a run error
    sys.exit(
        f"Could not load the Stage 3 helpers from cycle_test/utils: {e}\n"
        "Run this from a complete GENOAR checkout."
    )

logger = logging.getLogger("fetch_sra")

DEFAULT_SRR_DIR = Path("crawl_output/SRR")
DEFAULT_STAGE2_DIR = Path("first_pass_output")
DEFAULT_OUTPUT_DIR = Path("sample_sra")
# Where the merged list and its GSE->SRR mapping land. Kept beside the crawl
# output it is derived from, not in sample_sra/, which Stage 3 mounts and reads
# with a */*.sra glob.
DEFAULT_WORK_DIR = Path("crawl_output/stage3_prep")
SRR_PATTERN = re.compile(r"^SRR[0-9]+$")
SELECTED_VIEW_NAME = ".genoar_selected"


def _validate_selected_view_location(
    source_dir: Path, view_dir: Path
) -> tuple[Path, Path]:
    """Resolve the generated view without allowing it to name user data."""
    source_dir = Path(source_dir)
    view_dir = Path(view_dir)
    if not source_dir.is_dir():
        raise ValueError(f"SRA cache directory does not exist: {source_dir}")
    source_resolved = source_dir.resolve()
    if view_dir.name != SELECTED_VIEW_NAME:
        raise ValueError(
            f"selected view must be the generated {SELECTED_VIEW_NAME} child of "
            f"the SRA cache, not {view_dir}"
        )
    if view_dir.parent.resolve() != source_resolved:
        raise ValueError(
            f"selected view must be directly inside the SRA cache {source_resolved}"
        )
    if view_dir.is_symlink():
        raise ValueError(f"refusing symlink selected view: {view_dir}")
    if view_dir.exists() and not view_dir.is_dir():
        raise ValueError(f"selected view is not a directory: {view_dir}")
    return source_resolved, view_dir


def invalidate_selected_sra_view(source_dir: Path, view_dir: Path) -> None:
    """Invalidate a generated view without touching the persistent SRA cache."""
    _, view_dir = _validate_selected_view_location(source_dir, view_dir)
    if view_dir.exists():
        shutil.rmtree(view_dir)


def build_selected_sra_view(
    source_dir: Path, view_dir: Path, accessions: list[str]
) -> Path:
    """Build the exact writable Stage 3 input view for one fetch selection.

    The persistent download cache may contain runs fetched earlier through a
    raw or custom path. Stage 3 scans every ``SRR*`` directory it is mounted,
    so mounting that cache would silently add those old runs. The view contains
    hard links to this selection only: no multi-gigabyte copy, and rebuilding
    it never deletes the cached source files.
    """
    source_resolved, view_dir = _validate_selected_view_location(source_dir, view_dir)
    if not accessions:
        raise ValueError("cannot build an empty Stage 3 selection")
    if len(accessions) != len(set(accessions)):
        raise ValueError("Stage 3 selection contains duplicate accessions")

    view_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{view_dir.name}.next-", dir=view_dir.parent)
    )
    try:
        for accession in accessions:
            if not SRR_PATTERN.fullmatch(accession):
                raise ValueError(f"not an NCBI SRA run accession: {accession!r}")
            source = source_resolved / accession / f"{accession}.sra"
            if not source.is_file():
                raise FileNotFoundError(f"selected SRA file is missing: {source}")
            destination_dir = staging / accession
            destination_dir.mkdir()
            # source_dir and its hidden selection view share a filesystem in
            # the public Make path, so this preserves bytes without copying.
            os.link(source, destination_dir / source.name)

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_dir": str(source_resolved),
            "accessions": accessions,
        }
        (staging / "selection.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # A real fetch invalidates the old view before any download begins. If
        # something recreated it meanwhile, do not overwrite another run's
        # state; fail and leave the complete staging tree unpublished.
        if view_dir.exists():
            raise FileExistsError(f"selected view appeared concurrently: {view_dir}")
        os.replace(staging, view_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return view_dir


def validate_selected_sra_view(source_dir: Path, view_dir: Path) -> list[str]:
    """Return the exact selected accessions, or reject a stale/partial view."""
    source_resolved, view_dir = _validate_selected_view_location(source_dir, view_dir)
    if not view_dir.is_dir():
        raise ValueError(f"selected view does not exist: {view_dir}")
    manifest_path = view_dir / "selection.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"selected view manifest is unreadable: {exc}") from exc
    accessions = manifest.get("accessions") if isinstance(manifest, dict) else None
    if (
        not isinstance(accessions, list)
        or not accessions
        or any(
            not isinstance(item, str) or not SRR_PATTERN.fullmatch(item)
            for item in accessions
        )
        or len(accessions) != len(set(accessions))
    ):
        raise ValueError("selected view manifest has no valid unique accession list")
    if Path(str(manifest.get("source_dir", ""))).resolve() != source_resolved:
        raise ValueError("selected view manifest names a different SRA cache")

    actual = sorted(
        entry.name
        for entry in view_dir.iterdir()
        if entry.name.startswith("SRR")
    )
    if actual != sorted(accessions):
        raise ValueError(
            "selected view directories do not match selection.json "
            f"(expected {sorted(accessions)}, found {actual})"
        )
    for accession in accessions:
        selected = view_dir / accession / f"{accession}.sra"
        cached = source_resolved / accession / f"{accession}.sra"
        try:
            same_file = selected.is_file() and cached.is_file() and selected.samefile(cached)
        except OSError:
            same_file = False
        if not same_file:
            raise ValueError(
                f"selected view is incomplete or no longer matches the cache: {accession}"
            )
    return accessions


def parse_max_samples(value: str) -> Optional[int]:
    """Turn the --max-samples argument into a limit, where None means no limit.

    'all' and 0 both mean everything, so the Makefile can pass MAX_SAMPLES
    straight through without having to special-case an empty value.
    """
    text = value.strip().lower()
    if text in {"all", "0", ""}:
        return None
    try:
        limit = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--max-samples takes a positive number or 'all', not {value!r}"
        )
    if limit < 0:
        raise argparse.ArgumentTypeError("--max-samples cannot be negative")
    return limit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_sra.py",
        description=(
            "Download SRA files for the quality-filtered SRR accessions Stage 2 "
            "produced, so Stage 3 has something to run on."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  scripts/fetch_sra.py --max-samples 5\n"
            "  scripts/fetch_sra.py --dry-run\n"
            "  scripts/fetch_sra.py --source raw-stage1 --dry-run\n"
            "  scripts/fetch_sra.py --max-samples all --max-concurrent 8\n"
        ),
    )
    parser.add_argument(
        "--source",
        choices=("stage2", "raw-stage1"),
        default="stage2",
        help=(
            "Accession source: quality-filtered Stage 2 tables (default), or "
            "raw Stage 1 lists as an explicit opt-in"
        ),
    )
    parser.add_argument(
        "--stage2-dir",
        type=Path,
        default=DEFAULT_STAGE2_DIR,
        help=f"Stage 2's filtered tables (default: {DEFAULT_STAGE2_DIR})",
    )
    parser.add_argument(
        "--srr-dir",
        type=Path,
        default=DEFAULT_SRR_DIR,
        help=(
            "Stage 1's raw per-GSE accession lists; used only with "
            f"--source raw-stage1 (default: {DEFAULT_SRR_DIR})"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Where the .sra files go (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help=f"Where the merged accession list is written (default: {DEFAULT_WORK_DIR})",
    )
    parser.add_argument(
        "--selected-dir",
        type=Path,
        default=None,
        help=(
            "Writable Stage 3 view containing only this fetch selection "
            "(default: <output-dir>/.genoar_selected)"
        ),
    )
    parser.add_argument(
        "--max-samples",
        type=parse_max_samples,
        default=2,
        help="How many accessions to download, or 'all' (default: 2)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=4,
        help="Parallel downloads (default: 4)",
    )
    parser.add_argument(
        "--complete-datasets",
        type=Path,
        default=None,
        help=(
            "Optional complete_datasets.txt; only accessions from the GSE ids "
            "it lists are considered"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be downloaded and stop before fetching anything",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the download result as JSON as well as the summary",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    selected_dir = args.selected_dir or (args.output_dir / SELECTED_VIEW_NAME)
    if not args.dry_run:
        # A new real fetch owns the meaning of "latest selection" immediately.
        # Invalidate the previous view before work starts, so a failed or partial
        # download cannot leave yesterday's complete-looking view runnable.
        try:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            invalidate_selected_sra_view(args.output_dir, selected_dir)
        except (OSError, ValueError) as exc:
            logger.error("Could not invalidate the prior Stage 3 selection: %s", exc)
            return 2

    # A failed handoff must not leave the previous run's accession list looking
    # current.  The preparers repeat this invalidation, but source precondition
    # failures happen before either preparer is called.
    stale_list = args.work_dir / "srr_list.txt"
    try:
        stale_list.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("Could not invalidate stale accession list %s: %s", stale_list, exc)
        return 1

    if args.source == "stage2":
        if not args.stage2_dir.is_dir():
            logger.error(
                "No Stage 2 tables at %s. Run 'make run' successfully first. "
                "GENOAR will not fall back to raw Stage 1 accessions; use "
                "--source raw-stage1 only if that is what you intend.",
                args.stage2_dir,
            )
            return 1
        prep = extract_srr_from_stage2_tables(
            stage2_dir=args.stage2_dir,
            output_dir=args.work_dir,
        )
        source_label = f"the quality-filtered Stage 2 tables in {args.stage2_dir}"
    else:
        if not args.srr_dir.is_dir():
            logger.error(
                "No raw Stage 1 accession lists at %s. Run 'make run' first.",
                args.srr_dir,
            )
            return 1
        prep = prepare_stage3(
            srr_dir=args.srr_dir,
            output_dir=args.work_dir,
            complete_datasets_file=args.complete_datasets,
        )
        source_label = f"the raw Stage 1 lists in {args.srr_dir}"

    if not prep.get("success"):
        logger.error("Could not build the accession list: %s", prep.get("error"))
        return 1

    srr_list_file = prep.get("files", {}).get("srr_list")
    unique_ids = prep.get("unique_srr_ids", 0)

    if not srr_list_file or not unique_ids:
        # prepare_stage3 calls "nothing found" a success, because for a Stage 1+2
        # run it is. Here it is the whole point of the command, so it fails.
        logger.error(
            "Found no SRR accessions in %s (%s).",
            source_label,
            prep.get("note", "no accessions in the files that are there"),
        )
        return 1

    srr_list_file = Path(srr_list_file)
    logger.info(
        "Selected %d unique accessions from %d GSE series in %s and wrote %s",
        unique_ids,
        prep.get("gse_count", 0),
        source_label,
        srr_list_file,
    )

    accessions = [
        line.strip()
        for line in srr_list_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    limit = args.max_samples
    if limit is None:
        selected = unique_ids
        logger.warning(
            "No sample limit set: all %d accessions will be downloaded. This is "
            "a lot of data.",
            selected,
        )
    else:
        selected = min(limit, unique_ids)
        if unique_ids > selected:
            logger.info(
                "Downloading the first %d of %d accessions (--max-samples %d). "
                "Raise it with 'make fetch-sra MAX_SAMPLES=N', or MAX_SAMPLES=all.",
                selected,
                unique_ids,
                limit,
            )
    selected_accessions = accessions if limit is None else accessions[:limit]

    if args.dry_run:
        logger.info("Dry run: nothing was downloaded. Would fetch into %s:", args.output_dir)
        for accession in selected_accessions:
            print(f"  {accession}")
        print(f"  ({len(selected_accessions)} of {len(accessions)} accessions)")
        return 0

    result = download_sra_files(
        srr_list_file=srr_list_file,
        output_dir=args.output_dir,
        max_concurrent=args.max_concurrent,
        max_samples=limit,
    )

    if args.json:
        print(json.dumps(result, indent=2))

    if result.get("fatal_input_error"):
        logger.error(
            "A corrupt .sra could not be moved out of Stage 3's input path: %s",
            result.get("error"),
        )
        return 2

    if not result.get("success"):
        logger.error(
            "%d of %d downloads failed%s",
            result.get("failed", 0),
            result.get("total", 0),
            f": {result['error']}" if result.get("error") else ".",
        )
        return 1

    try:
        build_selected_sra_view(
            args.output_dir,
            selected_dir,
            selected_accessions,
        )
    except (OSError, ValueError) as exc:
        logger.error(
            "Downloads are intact, but the exact Stage 3 input view could not "
            "be built at %s: %s. Stage 3 was not started.",
            selected_dir,
            exc,
        )
        return 2

    logger.info(
        "%d downloaded, %d already present. Stage 3 inputs are ready in %s; "
        "run 'make run-fetched-stage3' next (direct 'make run-stage3' is for "
        "user-managed inputs).",
        result.get("downloaded", 0),
        result.get("skipped", 0),
        selected_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
