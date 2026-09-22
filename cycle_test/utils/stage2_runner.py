#!/usr/bin/env python3
"""
Stage 2 Runner - Run UMLS analysis pipeline via Docker

Executes genoar-analysis Docker container to generate first-pass tables.
"""

import subprocess
import logging
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional

try:
    from cycle_test.utils.container_runtime import build_run_command, image_available, DOCKER
except ImportError:  # direct-script execution
    from container_runtime import build_run_command, image_available, DOCKER

logger = logging.getLogger(__name__)

# Docker image name
DOCKER_IMAGE = "genoar-analysis:latest"


def run_stage2(
    meta_dir: Path,
    umls_dir: Path,
    output_dir: Path,
    log_file: Optional[Path] = None,
    timeout_seconds: int = 1800,
    runtime: str = DOCKER,
    sif_dir: str = ".",
) -> Dict[str, Any]:
    """
    Run Stage 2 (analysis pipeline) via Docker or Singularity.

    Args:
        meta_dir: Directory containing META files from Stage 1
        umls_dir: Directory containing UMLS CSV files
        output_dir: Output directory for first-pass tables
        log_file: Optional log file path
        timeout_seconds: Analysis container timeout in seconds (default 1800)
        runtime: 'docker' (default) or 'singularity'
        sif_dir: directory holding the .sif image (singularity mode)

    Returns:
        Dict with success status and statistics
    """
    result = {
        "success": False,
        "meta_dir": str(meta_dir),
        "output_dir": str(output_dir),
        "tables": {},
        "error": None
    }

    # Validate meta_dir
    if not meta_dir.exists():
        result["error"] = f"META directory not found: {meta_dir}"
        logger.error(result["error"])
        return result

    meta_files = list(meta_dir.glob("*_meta.txt"))
    if not meta_files:
        meta_files = list(meta_dir.glob("*.txt"))

    if not meta_files:
        result["error"] = f"No META files found in {meta_dir}"
        logger.warning(result["error"])
        result["success"] = True
        result["tables"] = {"cell_type": {"rows": 0}, "tissue": {"rows": 0}, "disease": {"rows": 0}}
        return result

    logger.info(f"Found {len(meta_files)} META files in {meta_dir}")

    # The tables themselves, by name; anything else in the directory is not
    # the input.
    if not umls_dir.exists() or not list(umls_dir.glob("umls_*_df.csv")):
        result["error"] = f"UMLS data not found at {umls_dir}"
        logger.error(result["error"])
        return result

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON output file for results
    json_output = output_dir / "analysis_result.json"

    # Build container command (docker or singularity)
    docker_cmd = build_run_command(
        DOCKER_IMAGE,
        mounts=[
            (str(meta_dir.resolve()), "/data/meta", True),
            (str(umls_dir.resolve()), "/data/umls", True),
            (str(output_dir.resolve()), "/data/output", False),
        ],
        args=[
            "--meta-dir", "/data/meta",
            "--umls-dir", "/data/umls",
            "--output-dir", "/data/output",
            "--json-output", "/data/output/analysis_result.json",
        ],
        runtime=runtime,
        sif_dir=sif_dir,
    )

    logger.info(f"Running analysis via {runtime}...")
    logger.debug(f"Command: {' '.join(docker_cmd)}")

    try:
        # Run Docker with real-time output streaming
        log_handle = None
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(log_file, 'w')
            log_handle.write(f"=== Stage 2 Docker Execution ===\n")
            log_handle.write(f"Command: {' '.join(docker_cmd)}\n\n")
            log_handle.flush()

        proc = subprocess.Popen(
            docker_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        deadline = time.monotonic() + timeout_seconds

        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                line = line.rstrip("\n")
                if log_handle:
                    log_handle.write(line + "\n")
                    log_handle.flush()
                logger.info(f"[analysis] {line}")
                for handler in logging.getLogger("cycle_test").handlers:
                    handler.flush()
            if time.monotonic() > deadline:
                proc.kill()
                raise subprocess.TimeoutExpired(docker_cmd, timeout_seconds)

        proc.wait()

        if log_handle:
            log_handle.close()

        if proc.returncode != 0:
            result["error"] = f"Docker exited with code {proc.returncode}"
            logger.error(result["error"])
            return result

        # Parse results from JSON output
        if json_output.exists():
            with open(json_output) as f:
                docker_result = json.load(f)

            result["success"] = docker_result.get("success", False)
            result["tables"] = docker_result.get("tables", {})
            result["successful_tables"] = docker_result.get("successful_tables", 0)

            if docker_result.get("error"):
                result["error"] = docker_result["error"]

            # Log results
            for field_name, table_info in result["tables"].items():
                rows = table_info.get("rows", 0)
                series = table_info.get("unique_series", 0)
                logger.info(f"  {field_name}: {rows} samples, {series} series")
        else:
            # Fallback: check output files directly
            result = _parse_output_files(output_dir, result)

        logger.info(f"Stage 2 completed: {result.get('successful_tables', 0)}/3 tables with data")

    except subprocess.TimeoutExpired:
        result["error"] = f"Analysis timed out after {timeout_seconds} seconds"
        logger.error(result["error"])
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Stage 2 failed: {e}", exc_info=True)

    return result


def _parse_output_files(output_dir: Path, result: Dict) -> Dict:
    """Parse output files to determine success."""
    import pandas as pd

    successful_tables = 0
    for field_name in ["cell_type", "tissue", "disease"]:
        output_file = output_dir / f"HS_{field_name}_1st_pass_meta_table.csv"

        if output_file.exists():
            try:
                df = pd.read_csv(output_file)
                result["tables"][field_name] = {
                    "rows": len(df),
                    "file": str(output_file),
                    "unique_series": df['Series'].nunique() if 'Series' in df.columns else 0,
                    "unique_runs": df['Run'].nunique() if 'Run' in df.columns else 0
                }
                if len(df) > 0:
                    successful_tables += 1
            except Exception as e:
                result["tables"][field_name] = {"rows": 0, "error": str(e)}
        else:
            result["tables"][field_name] = {"rows": 0}

    result["success"] = True
    result["successful_tables"] = successful_tables
    return result


def check_docker_image(runtime: str = DOCKER, sif_dir: str = ".") -> bool:
    """Check if the Stage 2 image (docker) or .sif (singularity) is present."""
    return image_available(DOCKER_IMAGE, runtime=runtime, sif_dir=sif_dir)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Run Stage 2 analysis")
    parser.add_argument("meta_dir", type=Path, help="META files directory")
    parser.add_argument("umls_dir", type=Path, help="UMLS data directory")
    parser.add_argument("output_dir", type=Path, help="Output directory")
    parser.add_argument("--check-image", action="store_true", help="Only check if Docker image exists")

    args = parser.parse_args()

    if args.check_image:
        exists = check_docker_image()
        print(f"Docker image {DOCKER_IMAGE}: {'exists' if exists else 'NOT FOUND'}")
    else:
        result = run_stage2(
            meta_dir=args.meta_dir,
            umls_dir=args.umls_dir,
            output_dir=args.output_dir
        )
        print(json.dumps(result, indent=2))
