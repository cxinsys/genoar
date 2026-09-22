#!/usr/bin/env python3
"""
GENOAR Cycle Test Script

Runs Stage 1 (crawler) → Stage 2 (analysis) → Stage 3 (SRR pipeline) repeatedly for N cycles.
Each cycle processes a sequential page range and saves results to separate directories.

Usage:
    python run_cycle_test.py --cycles 3
    python run_cycle_test.py --cycles 5 --pages-per-cycle 100 --wait-minutes 10
    python run_cycle_test.py --cycles 2 --dry-run
    python run_cycle_test.py --cycles 1 --run-stage3  # Include Stage 3 pipeline
"""

import argparse
import logging
import signal
import subprocess
import sys
import time
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cycle_test.utils.stage1_runner import run_stage1
from cycle_test.utils.stage2_runner import run_stage2
from cycle_test.utils.stage3_preparer import prepare_stage3, extract_srr_from_stage2_tables
from cycle_test.utils.stage3_downloader import download_sra_files
from cycle_test.utils.stage3_runner import (
    run_stage3_pipeline,
    detect_ref_genome_path,
    DEFAULT_CELLRANGER_PATH,
    DEFAULT_REF_GENOME_PATH,
    DEFAULT_SPECIES,
    SPECIES_GENOME_PATTERNS,
)
from cycle_test.utils.report_generator import generate_cycle_summary

# Configuration defaults
DEFAULT_PAGES_PER_CYCLE = 100
DEFAULT_WAIT_MINUTES = 10
DEFAULT_OUTPUT_DIR = "test_cycles"

# Stage 3 HPC runner (Phase 2a: generate bundle; Phase 2b: --execute full-auto with fallback)
HANDOFF_RUNNER = Path(__file__).parent.parent / "srr_pipeline_package" / "hpc" / "run_hpc_stage3.py"

# Which cycle status each Stage 3 outcome earns. stage3_runner grades the run;
# this says what the cycle may then claim.
#
#   success            every expected sample has verified Cell Ranger output, so
#                      the cycle did the work it was asked to do
#   partial_success    some expected samples were analysed and others were not.
#                      Work was done and work is missing, both measurably
#   nothing_processed  a complete, valid run that analysed nothing. No work and
#                      no defect, which is the "correctly did nothing" the other
#                      stages report as no_data
#   config_error       Cell Ranger or its reference is unusable, so the run could
#                      not have analysed anything. A fault to fix
#   failed             the run broke, or its counts contradict each other, or it
#                      came back with no record of itself. A run that left no
#                      record proved nothing, and partial_success would credit it
#                      with work no evidence supports
STAGE3_CYCLE_STATUS = {
    "success": "success",
    "partial_success": "partial_success",
    "nothing_processed": "no_data",
    "config_error": "failed_stage3",
    "failed": "failed_stage3",
}

# The verdict a Stage 3 HPC run earns, keyed by the wrapper's exit code. The
# wrapper exits on the pipeline's contract, so this reads the same scale the
# local pipeline uses.
HANDOFF_EXIT_STATUS = {
    0: "success",
    1: "failed_stage3",
    2: "failed_stage3",
    3: "no_data",
    4: "partial_success",
}

# Worse news wins. A later stage may raise the cycle status to a worse one. It
# may never lower a status an earlier stage already set: "Stage 3 correctly did
# nothing" must not overwrite "Stage 2 broke".
CYCLE_STATUS_SEVERITY = {
    "running": 0,
    "success": 1,
    "no_data": 2,
    "partial_success": 3,
    "failed_stage3": 4,
    "failed_stage1": 5,
    "interrupted": 6,
}

# Which field carries the reason for a status, where the report reads one.
CYCLE_STATUS_REASON_FIELD = {
    "no_data": "no_data_reason",
    "partial_success": "partial_reason",
    "failed_stage3": "failed_reason",
}


def _stage3_cycle_status(pipeline_result: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Grade one Stage 3 pipeline result as a cycle status, with its reason.

    The flags stage3_runner sets are read first, then the outcome it names in
    `status`. An outcome neither names is a failure. An unrecognised grade is not
    evidence of work.

    The last case is the one that matters. A Stage 3 run that reports no success
    and claims no partial work either did not run, broke, or left no account of
    itself. None of those analysed a sample that can be pointed at, so none of
    them earns "partial success".
    """
    if pipeline_result.get("nothing_processed"):
        return "no_data", (pipeline_result.get("nothing_processed_reason")
                           or "no sample was analysed")

    if pipeline_result.get("configuration_error"):
        return "failed_stage3", (pipeline_result.get("error")
                                 or "Stage 3 is misconfigured and could not run")

    if pipeline_result.get("partial"):
        return "partial_success", (pipeline_result.get("partial_reason")
                                   or "some expected samples were not analysed")

    if not pipeline_result.get("success"):
        status = STAGE3_CYCLE_STATUS.get(pipeline_result.get("status") or "failed",
                                         "failed_stage3")
        if status == "success":
            # The result names the success grade and denies success in the same
            # breath. The two cannot both hold, so neither is trusted.
            status = "failed_stage3"
        return status, (pipeline_result.get("error")
                        or "Stage 3 reported no successful run")

    # From here Stage 3 reports success. FASTQ conversion failures are still
    # counted, because samples that never became FASTQ produced no output.
    failed_samples = pipeline_result.get("failed_samples", 0)
    if failed_samples > 0:
        succeeded = pipeline_result.get("successful_samples", 0)
        reason = f"{failed_samples} sample(s) failed, {succeeded} succeeded"
        return ("partial_success" if succeeded else "failed_stage3"), reason

    return "success", None


def _apply_cycle_status(cycle_result: Dict[str, Any], status: str,
                        reason: Optional[str], logger: logging.Logger) -> None:
    """Record a stage's verdict on the cycle, without softening an earlier one."""
    current = cycle_result.get("status", "running")
    if (CYCLE_STATUS_SEVERITY.get(status, 0)
            < CYCLE_STATUS_SEVERITY.get(current, 0)):
        logger.warning(
            f"Cycle stays {current}: an earlier stage already reported a problem"
        )
        return
    cycle_result["status"] = status
    field = CYCLE_STATUS_REASON_FIELD.get(status)
    if field and reason:
        cycle_result[field] = reason


def _generate_hpc_handoff(sra_dir: Path, hpc_config: Path, out_dir: Path,
                          results_dir: Path, logger: logging.Logger,
                          execute: bool = False) -> Dict[str, Any]:
    """Hand Stage 3b off to a remote HPC instead of running Cell Ranger locally.

    Delegates to srr_pipeline_package/hpc/run_hpc_stage3.py, which always writes
    the transfer-ready handoff bundle and, with execute=True, also runs it
    end-to-end over SSH (upload -> submit -> poll -> retrieve) with graceful
    fallback to the manual bundle if SSH or the scheduler is unavailable.

    The scheduler comes from hpc_config.yaml and defaults to slurm. Under Slurm
    the wrapper submits with sbatch and reads the finished job with sacct. Under
    pbspro and torque it submits with qsub and reads the finished job with
    qstat -x -f. This function reads the wrapper's verdict either way.

    The wrapper's own result is read back from `--json` and returned as it
    stands, so `success` here means what it means there: the analysis succeeded.
    `exit_code` carries the wrapper's verdict on the pipeline's contract, and the
    cycle status is decided from that.
    """
    result: Dict[str, Any] = {
        "success": False,
        "bundle_dir": str(out_dir),
        "error": None,
        "submitted": False,
        "analysis_status": "not_attempted",
        "analysis_message": "the HPC handoff did not report an outcome",
        "exit_code": None,
    }
    summary_path = out_dir / "hpc_result.json"
    cmd = [
        sys.executable, str(HANDOFF_RUNNER),
        "--sra-dir", str(sra_dir),
        "--hpc-config", str(hpc_config),
        "--out", str(out_dir),
        "--results-dir", str(results_dir),
        "--json", str(summary_path),
    ]
    if execute:
        cmd.append("--execute")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        for line in (proc.stdout or "").splitlines():
            logger.info(f"[handoff] {line}")
        result["exit_code"] = proc.returncode
        if summary_path.exists():
            try:
                result.update(json.loads(summary_path.read_text()))
                result["exit_code"] = proc.returncode
            except (OSError, ValueError) as e:
                logger.warning(f"HPC handoff summary could not be read: {e}")
        if proc.returncode != 0 and not result.get("error"):
            result["error"] = (proc.stderr or "handoff runner failed").strip()
        if proc.returncode == 0 and not execute:
            logger.info(f"HPC handoff ready: {out_dir}/  (run transfer_and_submit.sh next)")
        logger.info(f"HPC handoff analysis: {result['analysis_status']}. "
                    f"{result['analysis_message']}.")
    except Exception as e:
        result["error"] = str(e)
        result["analysis_message"] = f"the HPC handoff runner could not be started: {e}"
        logger.error(f"HPC handoff generation error: {e}", exc_info=True)
    return result


def _handoff_cycle_status(handoff_result: Dict[str, Any],
                          execute: bool) -> Tuple[str, Optional[str]]:
    """Grade an HPC handoff as a cycle status, with its reason.

    Without execute the cycle was asked for a transfer-ready bundle, and writing
    the bundle is the whole job. The analysis then happens on the cluster, later,
    outside this cycle. The reason field still records that no sample was
    analysed here.

    With execute the cycle was asked for an analysis, so the wrapper's verdict
    decides. A handoff that cannot say what the run achieved reports exit 1, and
    a cycle that cannot evidence Stage 3 work is a Stage 3 failure.
    """
    reason = handoff_result.get("analysis_message")
    code = handoff_result.get("exit_code")
    if not execute:
        if code == 0:
            return "success", f"Stage 3 HPC handoff: {reason}" if reason else None
        return "failed_stage3", (
            f"Stage 3 HPC handoff: {handoff_result.get('error') or reason or 'the bundle was not written'}"
        )
    status = HANDOFF_EXIT_STATUS.get(code, "failed_stage3")
    return status, f"Stage 3 HPC: {reason}" if reason else None

# Global for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle interrupt signals gracefully."""
    global shutdown_requested
    print("\n\nShutdown requested. Completing current cycle...")
    shutdown_requested = True


def _positive_int(value: str) -> int:
    """argparse type that rejects zero and negative page/cycle counts up front,
    so an invalid range never reaches the run rather than failing partway."""
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return ivalue


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="GENOAR Cycle Test - Run crawler + analysis in cycles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run 3 cycles with default settings (100 pages each, 10 min wait)
  python run_cycle_test.py --cycles 3

  # Custom configuration
  python run_cycle_test.py --cycles 5 --pages-per-cycle 100 --start-page 1 --wait-minutes 10

  # Dry run to see what would happen
  python run_cycle_test.py --cycles 3 --dry-run

  # Resume from a specific cycle
  python run_cycle_test.py --cycles 5 --resume-from-cycle 3
        """
    )

    parser.add_argument(
        "--cycles", "-n", type=_positive_int, required=True,
        help="Number of cycles to run"
    )
    parser.add_argument(
        "--pages-per-cycle", type=_positive_int, default=DEFAULT_PAGES_PER_CYCLE,
        help=f"Pages per cycle (default: {DEFAULT_PAGES_PER_CYCLE})"
    )
    parser.add_argument(
        "--start-page", type=_positive_int, default=1,
        help="Starting page number (default: 1)"
    )
    parser.add_argument(
        "--wait-minutes", type=int, default=DEFAULT_WAIT_MINUTES,
        help=f"Wait time between cycles in minutes (default: {DEFAULT_WAIT_MINUTES})"
    )
    parser.add_argument(
        "--output-dir", type=str, default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without executing"
    )
    parser.add_argument(
        "--runtime", choices=["docker", "singularity"], default="docker",
        help="Container runtime for the stages (default: docker). "
             "'singularity' expects .sif images in --sif-dir; the crawler "
             "(Stage 1) needs Chrome privileges and is Docker-oriented."
    )
    parser.add_argument(
        "--sif-dir", type=str, default=".",
        help="Directory holding .sif images when --runtime singularity (default: .)"
    )
    parser.add_argument(
        "--stage3-hpc", type=Path, default=None, metavar="HPC_CONFIG",
        help="Run Stage 3 on a remote HPC: after downloading .sra, generate an "
             "HPC handoff bundle (transfer + submit + retrieve) from this hpc_config.yaml "
             "instead of running Cell Ranger locally. The 'scheduler' key selects "
             "slurm (default), pbspro or torque. See srr_pipeline_package/hpc/."
    )
    parser.add_argument(
        "--stage3-hpc-execute", action="store_true",
        help="With --stage3-hpc, also run the handoff end-to-end over SSH "
             "(upload -> submit -> poll -> retrieve). Falls back to the manual bundle "
             "if SSH or the scheduler is unavailable. Requires working SSH (key/agent) "
             "to the HPC."
    )
    parser.add_argument(
        "--resume-from-cycle", type=int, default=None,
        help="Resume from a specific cycle number"
    )
    parser.add_argument(
        "--skip-stage1", action="store_true",
        help="Skip Stage 1 (crawler), only run Stage 2 on existing data"
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)"
    )
    parser.add_argument(
        "--stage2-timeout", type=int, default=1800,
        help="Stage 2 analysis container timeout in seconds (default: 1800)"
    )

    # Stage 3 options
    stage3_group = parser.add_argument_group("Stage 3 Options")
    stage3_group.add_argument(
        "--run-stage3", action="store_true",
        help="Run Stage 3 (download SRA + run Docker pipeline)"
    )
    stage3_group.add_argument(
        "--stage3-only", action="store_true",
        help="Only run Stage 3 on existing Stage 2 results"
    )
    stage3_group.add_argument(
        "--download-only", action="store_true",
        help="Only download SRA files, skip Docker pipeline"
    )
    stage3_group.add_argument(
        "--max-downloads", type=int, default=4,
        help="Maximum concurrent downloads (default: 4)"
    )
    stage3_group.add_argument(
        "--max-samples", type=int, default=None,
        help="Limit number of samples to download (default: all)"
    )
    stage3_group.add_argument(
        "--cellranger-path", type=Path, default=DEFAULT_CELLRANGER_PATH,
        help=(
            "Cell Ranger installation path "
            "(auto-detected from project root; override if installed elsewhere). "
            f"Auto-detected: {DEFAULT_CELLRANGER_PATH or 'not found'}"
        )
    )
    # Left unset so --species decides what is detected. DEFAULT_REF_GENOME_PATH
    # is what that detection returns for the default species.
    stage3_group.add_argument(
        "--ref-genome-path", type=Path, default=None,
        help=(
            "Reference genome path "
            "(auto-detected from project root for --species; override if "
            "installed elsewhere). "
            f"Auto-detected for {DEFAULT_SPECIES}: {DEFAULT_REF_GENOME_PATH or 'not found'}"
        )
    )
    stage3_group.add_argument(
        "--species", default=DEFAULT_SPECIES,
        choices=sorted(SPECIES_GENOME_PATTERNS),
        help=(
            "What this run is about. Decides which reference is the right one, "
            "and is checked against the genome the installed reference names, "
            f"so a flattened ref/ is still checked (default: {DEFAULT_SPECIES})"
        )
    )
    stage3_group.add_argument(
        "--cellranger-cores", type=int, default=16,
        help="Number of cores for Cell Ranger (default: 16)"
    )
    stage3_group.add_argument(
        "--cellranger-mem", type=int, default=100,
        help="Memory (GB) for Cell Ranger (default: 100)"
    )

    return parser.parse_args()


def setup_logging(output_dir: Path, log_level: str, dry_run: bool = False) -> logging.Logger:
    """Setup logging configuration.

    When ``dry_run`` is True, no output directory or master log file is created;
    only the console handler is attached so inspecting an execution plan has no
    filesystem side effects.
    """
    logger = logging.getLogger("cycle_test")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Console handler (always attached)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    if dry_run:
        logger.info("Dry-run mode: skipping output directory and master log file creation")
        return logger

    # File handler (master log) — real runs only
    output_dir.mkdir(parents=True, exist_ok=True)
    master_log = output_dir / f"master_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = logging.FileHandler(master_log)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    logger.info(f"Logging to {master_log}")

    return logger


def setup_cycle_directory(
    base_dir: Path,
    cycle_num: int,
    start_page: int,
    end_page: int,
    include_stage3: bool = False
) -> Dict[str, Path]:
    """Create directory structure for a single cycle."""
    cycle_name = f"cycle_{cycle_num:03d}_pages_{start_page:04d}-{end_page:04d}"
    cycle_dir = base_dir / cycle_name

    dirs = {
        "root": cycle_dir,
        "stage1": cycle_dir / "stage1",
        "stage2": cycle_dir / "stage2",
        "stage3": cycle_dir / "stage3",
        "logs": cycle_dir / "logs",
    }

    # Stage 3 subdirectories
    dirs["stage3_sra"] = dirs["stage3"] / "sra"
    dirs["stage3_results"] = dirs["stage3"] / "results"
    dirs["stage3_logs"] = dirs["stage3"] / "logs"

    # Create Stage 1 subdirectories
    for subdir in ["META", "SMTX", "SRR"]:
        (dirs["stage1"] / subdir).mkdir(parents=True, exist_ok=True)

    # Create other directories
    for key in ["stage2", "stage3", "logs"]:
        dirs[key].mkdir(parents=True, exist_ok=True)

    # Create Stage 3 subdirectories if needed
    if include_stage3:
        for key in ["stage3_sra", "stage3_results", "stage3_logs"]:
            dirs[key].mkdir(parents=True, exist_ok=True)

    return dirs


def run_single_cycle(
    cycle_num: int,
    start_page: int,
    end_page: int,
    dirs: Dict[str, Path],
    umls_dir: Path,
    logger: logging.Logger,
    skip_stage1: bool = False,
    run_stage3: bool = False,
    stage3_only: bool = False,
    download_only: bool = False,
    max_downloads: int = 4,
    max_samples: Optional[int] = None,
    cellranger_path: Optional[Path] = None,
    ref_genome_path: Optional[Path] = None,
    species: str = DEFAULT_SPECIES,
    cellranger_cores: int = 16,
    cellranger_mem: int = 100,
    stage2_timeout: int = 1800,
    runtime: str = "docker",
    sif_dir: str = ".",
    stage3_hpc: Optional[Path] = None,
    stage3_hpc_execute: bool = False,
) -> Dict[str, Any]:
    """Execute a single cycle of Stage 1 + Stage 2 + Stage 3 (optional)."""
    cycle_result = {
        "cycle_num": cycle_num,
        "start_page": start_page,
        "end_page": end_page,
        "start_time": datetime.now().isoformat(),
        "stages": {},
        "status": "running",
        # Prep always runs, so the report needs to know whether Stage 3 was asked
        # for to tell a Stage-1+2 run apart from a Stage 3 that produced nothing.
        "stage3_requested": bool(run_stage3 or stage3_only or stage3_hpc),
    }

    # Skip Stage 1 and 2 if stage3_only
    if not stage3_only:
        # Stage 1: Crawler
        if not skip_stage1:
            logger.info(f"=== STAGE 1: Crawling pages {start_page}-{end_page} ===")
            stage1_start = time.time()
            stage1_result = run_stage1(
                start_page=start_page,
                end_page=end_page,
                output_dir=dirs["stage1"],
                log_file=dirs["logs"] / "stage1.log",
                runtime=runtime,
                sif_dir=sif_dir
            )
            stage1_elapsed = time.time() - stage1_start
            cycle_result["stages"]["stage1"] = stage1_result

            if not stage1_result["success"]:
                # An interrupt is not a completed range, so Stage 2/3 must not run
                # on the partial data. Distinguish it from a genuine failure, and
                # both from an empty range, so the cycle status (and the process
                # exit code) reflect what happened.
                s1_status = stage1_result.get("status", "failed")
                if s1_status == "interrupted":
                    logger.warning(f"Stage 1 INTERRUPTED after {stage1_elapsed:.1f}s; not running later stages")
                    cycle_result["status"] = "interrupted"
                elif s1_status == "no_pages_in_range":
                    # Crawler exit 3. The request was valid and the crawler did
                    # its job; the pages simply are not there. Reported as the
                    # empty result it is rather than as a failure, and never as
                    # a success — nothing was crawled, so nothing follows.
                    logger.warning(
                        f"=== STAGE 1: NOTHING TO CRAWL after {stage1_elapsed:.1f}s — "
                        f"pages {start_page}-{end_page} are past what GEO holds; "
                        "no later stage will run ==="
                    )
                    cycle_result["status"] = "no_data"
                    cycle_result["no_data_reason"] = (
                        f"Stage 1 found no page in the requested range {start_page}-{end_page}"
                    )
                else:
                    logger.error(f"Stage 1 FAILED after {stage1_elapsed:.1f}s: {stage1_result.get('error')}")
                    cycle_result["status"] = "failed_stage1"
                cycle_result["end_time"] = datetime.now().isoformat()
                return cycle_result

            capped = ""
            if stage1_result.get("capped"):
                pages = stage1_result.get("capped_pages") or {}
                capped = (
                    f" (range capped at page {pages.get('completed_end', '?')}; "
                    f"page {pages.get('requested_end', '?')} was requested)"
                )
            logger.info(
                f"=== STAGE 1 COMPLETED in {stage1_elapsed:.1f}s: "
                f"{stage1_result['statistics'].get('meta_files', 0)} META files{capped} ==="
            )
        else:
            logger.info("=== STAGE 1: Skipped (--skip-stage1) ===")
            cycle_result["stages"]["stage1"] = {"success": True, "skipped": True, "statistics": {}}

            # Guard: --skip-stage1 requires existing META files in the cycle dir.
            # Without them Stage 2 silently produces empty tables.
            meta_dir = dirs["stage1"] / "META"
            has_meta = meta_dir.exists() and any(meta_dir.glob("*.txt"))
            if not has_meta:
                error_msg = (
                    f"--skip-stage1 requires existing META files in {meta_dir} "
                    "(reuse an --output-dir that already holds a completed Stage 1)."
                )
                logger.error(error_msg)
                cycle_result["stages"]["stage1"] = {
                    "success": False,
                    "skipped": True,
                    "error": error_msg,
                    "statistics": {}
                }
                cycle_result["status"] = "failed_stage1"
                cycle_result["end_time"] = datetime.now().isoformat()
                return cycle_result

        logger.info("=== STAGE 2: Running analysis pipeline ===")
        stage2_start = time.time()
        stage2_result = run_stage2(
            meta_dir=dirs["stage1"] / "META",
            umls_dir=umls_dir,
            output_dir=dirs["stage2"],
            log_file=dirs["logs"] / "stage2.log",
            timeout_seconds=stage2_timeout,
            runtime=runtime,
            sif_dir=sif_dir,
        )
        stage2_elapsed = time.time() - stage2_start
        cycle_result["stages"]["stage2"] = stage2_result

        if not stage2_result["success"]:
            logger.warning(f"Stage 2 completed with issues after {stage2_elapsed:.1f}s: {stage2_result.get('error')}")
            cycle_result["status"] = "partial_success"
        else:
            tables_with_data = sum(
                1 for t in stage2_result.get("tables", {}).values()
                if t.get("rows", 0) > 0
            )
            logger.info(f"=== STAGE 2 COMPLETED in {stage2_elapsed:.1f}s: {tables_with_data}/3 tables with data ===")
    else:
        logger.info("=== STAGE 1 & 2: Skipped (--stage3-only) ===")
        cycle_result["stages"]["stage1"] = {"success": True, "skipped": True}
        cycle_result["stages"]["stage2"] = {"success": True, "skipped": True}

    logger.info("=== STAGE 3 PREP: Extracting SRR IDs from Stage 2 tables ===")
    stage3_prep = extract_srr_from_stage2_tables(
        stage2_dir=dirs["stage2"],
        output_dir=dirs["stage3"]
    )
    cycle_result["stages"]["stage3_prep"] = stage3_prep

    # Set once when cycle_result was built; reuse it so the request flag and the
    # value the report reads cannot drift apart.
    stage3_requested = cycle_result["stage3_requested"]

    if not stage3_prep.get("success"):
        # A prep that errored is not the same as one that legitimately found no
        # SRR ids. Both end with zero ids, but only the second is a clean result;
        # treating them alike let a failed prep finish the cycle as success.
        logger.error(f"Stage 3 prep failed: {stage3_prep.get('error')}")
        cycle_result["end_time"] = datetime.now().isoformat()
        cycle_result["status"] = "failed_stage3" if stage3_requested else "partial_success"
        return cycle_result
    elif stage3_prep.get("unique_srr_ids", 0) == 0:
        # Prep worked but the data yielded no SRR ids. That is a clean result for
        # a Stage 1+2 run, but if Stage 3 was asked for it did not happen, so the
        # two cases get different statuses rather than a blanket success.
        logger.info("No SRR IDs to process, skipping Stage 3")
        cycle_result["end_time"] = datetime.now().isoformat()
        if stage3_requested:
            logger.warning("Stage 3 was requested but there are no SRR IDs to process")
            cycle_result["status"] = "no_data"
            cycle_result["no_data_reason"] = (
                "Stage 3 prep found no SRR IDs in the Stage 2 tables"
            )
        elif cycle_result["status"] == "running":
            cycle_result["status"] = "success"
        return cycle_result
    else:
        logger.info(
            f"Stage 3 prep completed: {stage3_prep.get('unique_srr_ids', 0)} SRR IDs "
            f"from {stage3_prep.get('gse_count', 0)} GSE"
        )

    # Stage 3: Download + Pipeline (if --run-stage3 / --stage3-only / --stage3-hpc)
    if run_stage3 or stage3_only or stage3_hpc:
        srr_list_file = dirs["stage3"] / "srr_list.txt"

        if srr_list_file.exists():
            logger.info("=== STAGE 3a: Downloading SRA files ===")
            download_result = download_sra_files(
                srr_list_file=srr_list_file,
                output_dir=dirs["stage3_sra"],
                max_concurrent=max_downloads,
                max_samples=max_samples
            )
            cycle_result["stages"]["stage3_download"] = download_result

            if download_result.get("fatal_input_error"):
                # A corrupt input could not be moved out of the pipeline's glob,
                # so running Stage 3b would feed it to Cell Ranger. Stop here.
                logger.error(
                    "Stage 3a hit an unsafe input that could not be quarantined; "
                    "not running Stage 3b. "
                    f"{download_result.get('error')}"
                )
                cycle_result["status"] = "failed_stage3"
                cycle_result["end_time"] = datetime.now().isoformat()
                return cycle_result

            if not download_result.get("success") or download_result.get("failed", 0) > 0:
                # Downloads that failed leave the sample set incomplete, so the
                # cycle is not a success even if the remaining stages run on what
                # did arrive.
                logger.warning(
                    f"Stage 3a completed with {download_result.get('failed', 0)} failed downloads"
                )
                cycle_result["status"] = "partial_success"

            logger.info(
                f"Stage 3a completed: {download_result.get('downloaded', 0)} downloaded, "
                f"{download_result.get('skipped', 0)} skipped, "
                f"{download_result.get('failed', 0)} failed"
            )

            # Stage 3b: run locally, OR generate an HPC handoff for remote execution
            if stage3_hpc:
                mode = "auto+fallback" if stage3_hpc_execute else "bundle only"
                logger.info(f"=== STAGE 3b: HPC handoff ({mode}) ===")
                handoff_result = _generate_hpc_handoff(
                    sra_dir=dirs["stage3_sra"],
                    hpc_config=stage3_hpc,
                    out_dir=dirs["stage3"] / "hpc_handoff",
                    results_dir=dirs["stage3_results"],
                    logger=logger,
                    execute=stage3_hpc_execute,
                )
                cycle_result["stages"]["stage3_handoff"] = handoff_result
                status, reason = _handoff_cycle_status(handoff_result, stage3_hpc_execute)
                _apply_cycle_status(cycle_result, status, reason, logger)
            elif not download_only:
                logger.info("=== STAGE 3b: Running SRR pipeline ===")
                pipeline_result = run_stage3_pipeline(
                    sra_dir=dirs["stage3_sra"],
                    results_dir=dirs["stage3_results"],
                    logs_dir=dirs["stage3_logs"],
                    cellranger_path=cellranger_path or DEFAULT_CELLRANGER_PATH,
                    # Left as passed, including None: the runner detects a
                    # reference for `species`, which a default resolved here
                    # would override.
                    ref_genome_path=ref_genome_path,
                    species=species,
                    cores=cellranger_cores,
                    cellranger_threads=cellranger_cores,
                    cellranger_mem=cellranger_mem,
                    runtime=runtime,
                    sif_dir=sif_dir
                )
                cycle_result["stages"]["stage3_pipeline"] = pipeline_result

                succeeded = pipeline_result.get("successful_samples", 0)
                failed_samples = pipeline_result.get("failed_samples", 0)

                status, reason = _stage3_cycle_status(pipeline_result)
                if status == "no_data":
                    logger.warning(f"=== STAGE 3b: NO SAMPLE WAS ANALYSED: {reason} ===")
                elif status == "partial_success":
                    logger.warning(f"=== STAGE 3b: PARTIAL: {reason} ===")
                elif status == "failed_stage3":
                    # Includes the run that came back with no record of itself.
                    # It analysed nothing this cycle can point at, so it is a
                    # Stage 3 failure and never a partial success.
                    logger.error(f"=== STAGE 3b: FAILED: {reason} ===")
                else:
                    logger.info(
                        f"Stage 3b completed: {succeeded} FASTQ sample(s) converted, "
                        f"{failed_samples} failed, "
                        f"{pipeline_result.get('cellranger_completed', 0)} with Cell Ranger output"
                    )
                _apply_cycle_status(
                    cycle_result, status,
                    f"Stage 3 Cell Ranger: {reason}" if reason else None, logger,
                )
            else:
                logger.info("=== STAGE 3b: Skipped (--download-only) ===")
                cycle_result["stages"]["stage3_pipeline"] = {"success": True, "skipped": True}
        else:
            # Stage 3 was asked for but there is nothing to download, so it did
            # not do what the run requested.
            logger.error("No SRR list file found, skipping Stage 3 download/pipeline")
            cycle_result["stages"]["stage3_download"] = {"success": False, "error": "No SRR list file"}
            cycle_result["status"] = "failed_stage3"

    # Finalize
    if cycle_result["status"] == "running":
        cycle_result["status"] = "success"

    cycle_result["end_time"] = datetime.now().isoformat()

    # Calculate total elapsed time
    try:
        start_dt = datetime.fromisoformat(cycle_result["start_time"])
        end_dt = datetime.fromisoformat(cycle_result["end_time"])
        total_elapsed = (end_dt - start_dt).total_seconds()
        logger.info("=" * 60)
        logger.info(
            f"CYCLE {cycle_num} FINISHED — status: {cycle_result['status']} — "
            f"elapsed: {total_elapsed:.1f}s"
        )
        logger.info("=" * 60)
    except Exception:
        logger.info(f"CYCLE {cycle_num} FINISHED — status: {cycle_result['status']}")

    return cycle_result


def wait_with_countdown(minutes: int, logger: logging.Logger) -> bool:
    """Wait for specified minutes with countdown display. Returns False if interrupted."""
    global shutdown_requested

    if minutes <= 0:
        return True

    total_seconds = minutes * 60
    logger.info(f"Waiting {minutes} minutes before next cycle...")

    for remaining in range(total_seconds, 0, -1):
        if shutdown_requested:
            return False

        if remaining % 60 == 0:
            mins_left = remaining // 60
            print(f"\r  Waiting... {mins_left} minute(s) remaining    ", end="", flush=True)

        time.sleep(1)

    print("\r" + " " * 50 + "\r", end="")  # Clear line
    return True


def main():
    global shutdown_requested

    # Parse arguments
    args = parse_args()

    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Setup paths
    base_dir = Path(args.output_dir).resolve()
    genoar_root = Path(__file__).parent.parent
    umls_dir = genoar_root / "all_query_results"

    # Setup logging
    logger = setup_logging(base_dir, args.log_level, dry_run=args.dry_run)

    # Determine if Stage 3 is enabled
    stage3_enabled = args.run_stage3 or args.stage3_only or bool(args.stage3_hpc)

    # Log configuration
    logger.info("=" * 60)
    logger.info("GENOAR Cycle Test")
    logger.info("=" * 60)
    logger.info(f"Cycles:          {args.cycles}")
    logger.info(f"Pages per cycle: {args.pages_per_cycle}")
    logger.info(f"Start page:      {args.start_page}")
    logger.info(f"Wait minutes:    {args.wait_minutes}")
    logger.info(f"Output dir:      {base_dir}")
    logger.info(f"UMLS dir:        {umls_dir}")
    logger.info(f"Runtime:         {args.runtime}" + (f" (sif-dir: {args.sif_dir})" if args.runtime == "singularity" else ""))
    logger.info(f"Dry run:         {args.dry_run}")

    # Stage 3 configuration
    if stage3_enabled:
        logger.info("-" * 40)
        logger.info("Stage 3 Configuration:")
        logger.info(f"  Run Stage 3:     {args.run_stage3}")
        logger.info(f"  Stage 3 Only:    {args.stage3_only}")
        logger.info(f"  Download Only:   {args.download_only}")
        logger.info(f"  Max Downloads:   {args.max_downloads}")
        logger.info(f"  Max Samples:     {args.max_samples or 'all'}")
        cellranger_display = args.cellranger_path or "(auto-detect failed — pass --cellranger-path)"
        detected_ref = args.ref_genome_path or detect_ref_genome_path(args.species)
        ref_genome_display = detected_ref or "(auto-detect failed — pass --ref-genome-path)"
        logger.info(f"  Species:         {args.species}")
        logger.info(f"  Cell Ranger:     {cellranger_display}")
        logger.info(f"  Ref Genome:      {ref_genome_display}")
        logger.info(f"  CR Cores:        {args.cellranger_cores}")
        logger.info(f"  CR Memory:       {args.cellranger_mem}GB")

    # Calculate total page range
    total_pages = args.cycles * args.pages_per_cycle
    end_page_total = args.start_page + total_pages - 1
    logger.info(f"Total page range: {args.start_page} to {end_page_total}")
    logger.info("=" * 60)

    # Dry run mode
    if args.dry_run:
        logger.info("\n[DRY RUN MODE - No actual execution]")
        for cycle_num in range(1, args.cycles + 1):
            start_page = args.start_page + (cycle_num - 1) * args.pages_per_cycle
            end_page = start_page + args.pages_per_cycle - 1
            cycle_dir = f"cycle_{cycle_num:03d}_pages_{start_page:04d}-{end_page:04d}"
            stages = "Stage 1 -> Stage 2"
            if stage3_enabled:
                stages += " -> Stage 3 (Download"
                if not args.download_only:
                    stages += " + Pipeline"
                stages += ")"
            logger.info(f"  Cycle {cycle_num}: Pages {start_page}-{end_page} -> {cycle_dir}/")
            logger.info(f"           {stages}")
        logger.info("\n[DRY RUN COMPLETE]")
        return

    # Main execution loop
    all_results: List[Dict[str, Any]] = []
    run_failed = False
    start_cycle = args.resume_from_cycle if args.resume_from_cycle else 1

    try:
        for cycle_num in range(start_cycle, args.cycles + 1):
            if shutdown_requested:
                logger.info("Shutdown requested, stopping...")
                break

            # Calculate page range for this cycle
            start_page = args.start_page + (cycle_num - 1) * args.pages_per_cycle
            end_page = start_page + args.pages_per_cycle - 1

            logger.info("")
            logger.info("=" * 60)
            logger.info(f"CYCLE {cycle_num}/{args.cycles}: Pages {start_page}-{end_page}")
            logger.info("=" * 60)

            # Setup directories
            dirs = setup_cycle_directory(
                base_dir, cycle_num, start_page, end_page,
                include_stage3=stage3_enabled
            )
            logger.info(f"Output: {dirs['root']}")

            # Run cycle
            cycle_result = run_single_cycle(
                cycle_num=cycle_num,
                start_page=start_page,
                end_page=end_page,
                dirs=dirs,
                umls_dir=umls_dir,
                logger=logger,
                skip_stage1=args.skip_stage1,
                run_stage3=args.run_stage3,
                stage3_only=args.stage3_only,
                download_only=args.download_only,
                max_downloads=args.max_downloads,
                max_samples=args.max_samples,
                cellranger_path=args.cellranger_path,
                ref_genome_path=args.ref_genome_path,
                species=args.species,
                cellranger_cores=args.cellranger_cores,
                cellranger_mem=args.cellranger_mem,
                stage2_timeout=args.stage2_timeout,
                runtime=args.runtime,
                sif_dir=args.sif_dir,
                stage3_hpc=args.stage3_hpc,
                stage3_hpc_execute=args.stage3_hpc_execute,
            )

            all_results.append(cycle_result)

            # Save cycle summary
            summary_path = dirs["root"] / "summary.json"
            with open(summary_path, 'w') as f:
                json.dump(cycle_result, f, indent=2)
            logger.info(f"Cycle summary saved: {summary_path}")

            # Wait between cycles (except after last cycle)
            if cycle_num < args.cycles and not shutdown_requested:
                if not wait_with_countdown(args.wait_minutes, logger):
                    logger.info("Wait interrupted, stopping...")
                    break

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        shutdown_requested = True
    except Exception as e:
        # The cycles that did finish do not make a crashed run successful.
        logger.error(f"Unexpected error: {e}", exc_info=True)
        run_failed = True
    finally:
        # Generate final report
        if all_results:
            logger.info("\nGenerating final report...")
            try:
                report_path = generate_cycle_summary(all_results, base_dir)
                logger.info(f"Final report: {report_path}")
            except Exception as e:
                # Without the report there is no record of what happened, so the
                # run cannot be called successful either.
                logger.error(f"Failed to generate report: {e}")
                run_failed = True

        logger.info("\nCycle test completed!")
        logger.info(f"Completed cycles: {len(all_results)}/{args.cycles}")

    # Reflect the outcome in the process exit code so automation does not read a
    # crashed, interrupted, or partial run as success. 0 only when every cycle
    # fully succeeded; 130 if the run was interrupted at any point; 1 otherwise.
    return _exit_code_for(
        all_results, interrupted=shutdown_requested, run_failed=run_failed
    )


def _exit_code_for(
    all_results: List[Dict[str, Any]],
    interrupted: bool = False,
    run_failed: bool = False,
) -> int:
    """0 all-success, 130 interrupted, 1 any other non-success or no cycles.

    "no_data" — a valid run that correctly did nothing, because no page fell in
    the requested range or no sample qualified for Cell Ranger — is deliberately
    a 1 and not a 0: automation asked for work that did not happen, and a 0 would
    let a schedule keep producing empty cycles unnoticed. Which of the two it was
    is legible from the cycle status and `no_data_reason`, not from the code.

    `interrupted` covers a SIGINT that arrived outside a cycle, while waiting
    between them or before the first one started. Those leave no "interrupted"
    cycle behind, so without the flag a run cut short after a successful cycle
    would report a clean 0.

    `run_failed` covers an unexpected exception or a failure to write the final
    report. Those leave the finished cycles looking clean, but the run as a whole
    did not do what was asked.
    """
    if run_failed:
        return 1
    if interrupted:
        return 130
    if not all_results:
        return 1
    statuses = [r.get("status") for r in all_results]
    if any(s == "interrupted" for s in statuses):
        return 130
    if all(s == "success" for s in statuses):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
