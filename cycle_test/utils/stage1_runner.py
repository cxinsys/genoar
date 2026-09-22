#!/usr/bin/env python3
"""
Stage 1 Runner - Docker wrapper for genoar_crawler
Runs crawler in Docker container (genoar-crawler:amd64)
"""

import subprocess
import logging
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional
import platform

try:
    from cycle_test.utils.container_runtime import build_run_command, image_available, DOCKER, SINGULARITY
except ImportError:  # direct-script execution
    from container_runtime import build_run_command, image_available, DOCKER, SINGULARITY

logger = logging.getLogger(__name__)

# Docker image configuration
DOCKER_IMAGE_AMD64 = "genoar-crawler:amd64"
DOCKER_IMAGE_ARM64 = "genoar-crawler:arm64"

# Paths for local fallback
GENOAR_ROOT = Path(__file__).parent.parent.parent
CRAWLER_PATH = GENOAR_ROOT / "genoar_crawler" / "genoar_crawler.py"
INTEGRATION_PATH = GENOAR_ROOT / "genoar_crawler" / "integration.py"

# Written only when a whole requested page range finished. Two writers produce
# it, and the manifest says which:
#   "source": "worker"      a single crawler finished the range it was given.
#                           In a parallel run that is one slice, and such a
#                           manifest lives under the worker's own directory,
#                           never here.
#   "source": "aggregated"  the run's aggregator checked every worker's slice,
#                           found them contiguous over the requested range, and
#                           wrote this. A partially completed parallel run
#                           produces none at all.
# Older manifests carry no "source"; they are single-crawler manifests and are
# read exactly as before.
COMPLETION_MANIFEST_FILE = "crawl_manifest.json"
# Exit code the crawler uses for a user interrupt (SIGINT convention).
CRAWLER_INTERRUPT_CODE = 130
# Exit code the crawler uses for "the request was valid, but once capped to the
# pages GEO actually holds no page fell in range, so nothing was crawled". It is
# neither a success (no page was processed, no manifest exists) nor a failure
# (nothing malfunctioned), so it gets its own status here too.
CRAWLER_NO_PAGES_IN_RANGE_CODE = 3
# The integration pass is secondary to the crawl, so it has its own budget and
# its own failure handling rather than sharing the crawler's.
INTEGRATION_TIMEOUT_SECONDS = 600


def _integration_result(returncode: Optional[int], timed_out: bool = False) -> dict:
    """One shape for the integration outcome, so Docker and local record the same
    keys. returncode is the process code (None on timeout, where the process was
    killed and its code is not meaningful)."""
    return {
        "success": (not timed_out) and returncode == 0,
        "returncode": None if timed_out else returncode,
        "timed_out": timed_out,
        "timeout_seconds": INTEGRATION_TIMEOUT_SECONDS if timed_out else None,
    }


def classify_stage1_outcome(
    returncode: int,
    output_dir: Path,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
) -> tuple:
    """Map a crawler exit code plus its manifest to (status, error).

    status is one of:
      "success"            - the whole requested range was crawled
      "capped"             - the crawl finished, but the range was capped to the
                             pages GEO actually holds, so it covers fewer pages
                             than were asked for. Real work happened
      "no_pages_in_range"  - exit 3: a valid request that had nothing to crawl
      "interrupted"        - exit 130
      "failed"             - anything else

    Exit 0 alone is not enough: a crawl that stopped early can still exit 0, so
    success also requires a completion manifest, which the crawler writes only on
    a natural finish.

    The manifest must also cover the pages this call asked for. Without that
    check a manifest left by a different run (or by a resume over other pages)
    would pass as proof of a completion that never happened. Pass start_page and
    end_page to enforce it; end_page 0 means "all pages", where the crawler
    resolves the real end and any end covering the start is acceptable.
    """
    if returncode == CRAWLER_INTERRUPT_CODE:
        return "interrupted", "Crawler interrupted (exit 130)"
    if returncode == CRAWLER_NO_PAGES_IN_RANGE_CODE:
        # The crawler ran, sized the corpus, and found that no page fell in the
        # requested range. Nothing was crawled, so there is no manifest to check
        # and calling this "failed" would send an operator hunting for a defect
        # that is not there.
        return "no_pages_in_range", (
            "Crawler exited 3: the request was valid but no page fell in range "
            "once capped to the pages GEO currently holds, so nothing was crawled."
        )
    if returncode != 0:
        return "failed", f"Crawler exited with code {returncode}"

    manifest = Path(output_dir) / COMPLETION_MANIFEST_FILE
    if not manifest.exists():
        return "failed", (
            "Crawler exited 0 but wrote no completion manifest; it stopped before "
            "finishing the requested pages."
        )
    try:
        with open(manifest) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return "failed", f"Crawler completion manifest unreadable: {e}"
    if data.get("status") != "completed":
        return "failed", f"Crawler manifest status is {data.get('status')!r}, not 'completed'"

    if start_page is not None:
        return _check_manifest_covers(data, start_page, end_page)

    return "success", None


def _page_range(data: dict, key: str):
    """(start, end) from a manifest range field, or None if it is not well-formed.

    Types are checked rather than assumed: a malformed manifest must come back as
    a failure the caller can report, not raise out of the runner.
    """
    value = data.get(key)
    if not isinstance(value, dict):
        return None
    start, end = value.get("start"), value.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    return start, end


def _check_manifest_covers(data: dict, start_page: int, end_page: Optional[int]) -> tuple:
    """Whether the manifest proves this request's pages finished."""
    # The request itself must be a real range before a manifest can prove it.
    # Pages are 1-based; end 0 means "all pages", otherwise the end cannot precede
    # the start. A reversed or negative request can otherwise be "satisfied" by a
    # completed end that merely is not smaller.
    want_end_req = 0 if end_page is None else end_page
    if start_page < 1 or (want_end_req != 0 and want_end_req < start_page):
        return "failed", (
            f"Requested range {start_page}-{want_end_req} is not a valid page range."
        )

    requested = _page_range(data, "requested_pages")
    completed = _page_range(data, "completed_pages")
    if requested is None or completed is None:
        return "failed", (
            "Crawler manifest has no well-formed requested_pages/completed_pages, "
            f"so it cannot prove pages {start_page}-{end_page} finished."
        )

    # The manifest must be about this request, not one left by another run.
    want_end = 0 if end_page is None else end_page
    if requested != (start_page, want_end):
        return "failed", (
            f"Crawler manifest was written for request {requested[0]}-{requested[1]}, "
            f"not the requested range {start_page}-{want_end}."
        )

    got_start, got_end = completed
    # Pages are 1-based and ranges run forwards. Values outside that are not a
    # smaller completion, they are a manifest that cannot be believed at all.
    if got_start < 1 or got_end < got_start:
        return "failed", (
            f"Crawler manifest reports an implausible completed range "
            f"{got_start}-{got_end}."
        )
    if got_start > start_page:
        return "failed", (
            f"Crawler manifest covers pages {got_start}-{got_end}, which does not "
            f"cover the requested range {start_page}-{want_end}."
        )

    if want_end == 0:
        # "All pages": the crawler resolved the real end, so the manifest has to
        # record it and show the crawl reached exactly it. Without that, a crawl
        # that stopped at page 1 would look complete.
        resolved = data.get("resolved_end_page")
        if not isinstance(resolved, int) or isinstance(resolved, bool):
            return "failed", (
                "Crawler manifest for an all-pages request does not record "
                "resolved_end_page, so its completion cannot be checked."
            )
        if resolved < start_page:
            # A crawl from page N cannot resolve to fewer than N pages.
            return "failed", (
                f"Crawler manifest resolved {resolved} pages, which is before the "
                f"requested start page {start_page}."
            )
        if got_end != resolved:
            return "failed", (
                f"Crawler manifest covers pages {got_start}-{got_end} but the crawl "
                f"resolved {resolved} pages."
            )
    elif got_end < want_end:
        return _classify_short_completion(data, got_start, got_end, start_page, want_end)

    return "success", None


def _classify_short_completion(
    data: dict, got_start: int, got_end: int, start_page: int, want_end: int
) -> tuple:
    """A finished crawl covering fewer pages than were asked for: cap or defect?

    The crawler caps a request that runs past the pages GEO currently holds
    (its corpus shrinks between the moment a range is planned and the moment it
    runs) and records the end it settled on in ``resolved_end_page``. So the two
    cases are told apart by whether the crawl reached the end it resolved:

      completed_pages.end == resolved_end_page < requested_pages.end
          the crawler capped and then crawled every page of the capped range —
          it did all the work that exists. Reported as "capped".

      completed_pages.end < resolved_end_page
          the crawl stopped before the end it was itself going to do. That is a
          short run, which is a failure however it exited.

      resolved_end_page missing or implausible
          nothing proves a cap happened, so the short coverage is not excused.

    This stays a check and not an assumption: the crawler only writes the
    manifest after the page loop reaches ``resolved_end_page`` (and deletes any
    earlier run's manifest at startup), so a genuinely short crawl normally
    leaves no manifest at all and is already caught above. The comparison here is
    what keeps a truncated, hand-edited, or future-writer manifest from turning a
    short run into a success.
    """
    resolved = data.get("resolved_end_page")
    if not isinstance(resolved, int) or isinstance(resolved, bool):
        return "failed", (
            f"Crawler manifest covers pages {got_start}-{got_end}, short of the "
            f"requested range {start_page}-{want_end}, and records no "
            f"resolved_end_page, so nothing shows the range was capped rather "
            f"than cut short."
        )
    if resolved < got_start or resolved >= want_end:
        # A cap can only ever shorten the range, and never below the pages the
        # crawl actually covered.
        return "failed", (
            f"Crawler manifest covers pages {got_start}-{got_end} of the "
            f"requested {start_page}-{want_end}, but its resolved_end_page "
            f"{resolved} is not a cap of that request."
        )
    if got_end != resolved:
        return "failed", (
            f"Crawler manifest covers pages {got_start}-{got_end} but the crawl "
            f"resolved {resolved} pages, so it stopped before the end it was "
            f"going to reach."
        )
    return "capped", None


def manifest_provenance(output_dir: Path) -> Dict[str, Any]:
    """Which crawl this run's completion manifest came from.

    Best effort and purely informational: the classification above is what
    decides the outcome. It is recorded because a report that says "Stage 1
    succeeded" is worth much less than one that says which run id proved it -
    that is the whole reason the parallel work exists.
    """
    try:
        with open(Path(output_dir) / COMPLETION_MANIFEST_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    provenance = {
        "source": data.get("source", "worker"),
        "run_id": data.get("run_id"),
    }
    workers = data.get("workers")
    if isinstance(workers, list):
        provenance["workers"] = [
            {"worker_id": w.get("worker_id"),
             "completed_pages": w.get("completed_pages"),
             "exit_code": w.get("exit_code")}
            for w in workers if isinstance(w, dict)
        ]
    return provenance


def _capped_note(output_dir: Path) -> Dict[str, Any]:
    """What a capped run covered, for the log and the report.

    Best effort: the classification already passed, so an unreadable manifest
    here only costs detail, never the verdict.
    """
    try:
        with open(Path(output_dir) / COMPLETION_MANIFEST_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    requested = _page_range(data, "requested_pages")
    completed = _page_range(data, "completed_pages")
    if requested is None or completed is None:
        return {}
    return {
        "requested_end": requested[1],
        "completed_end": completed[1],
        "available_pages": data.get("resolved_end_page"),
    }


def _record_crawl_outcome(
    result: Dict[str, Any], status: str, error: Optional[str], output_dir: Path
) -> bool:
    """Fold the crawl classification into `result`; True if later steps may run.

    "capped" is a completed crawl over every page that exists, so the run goes
    on; the cap is recorded rather than silently smoothed over, because a caller
    comparing page counts across cycles needs to know the range moved.
    """
    if status == "capped":
        note = _capped_note(output_dir)
        result["capped"] = True
        result["capped_pages"] = note
        logger.warning(
            "Stage 1 range was capped to the pages GEO currently holds: "
            f"requested up to page {note.get('requested_end', '?')}, crawled "
            f"through page {note.get('completed_end', '?')}. Every page that "
            "exists was crawled."
        )
        return True
    if status == "success":
        return True

    result["status"] = status
    result["error"] = error
    if status == "no_pages_in_range":
        # Not a malfunction, so it is logged as the empty result it is. The
        # caller still sees success=False, because the requested work did not
        # happen.
        logger.warning(f"Stage 1 had nothing to crawl: {error}")
    else:
        logger.error(f"Stage 1 {status}: {error}")
    return False


def get_docker_image() -> str:
    """Get appropriate Docker image based on system architecture."""
    arch = platform.machine()
    if arch in ("arm64", "aarch64"):
        return DOCKER_IMAGE_ARM64
    return DOCKER_IMAGE_AMD64


def check_docker_image(image: str) -> bool:
    """Check if Docker image exists."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def run_stage1(
    start_page: int,
    end_page: int,
    output_dir: Path,
    log_file: Optional[Path] = None,
    timeout_seconds: int = 7200,  # 2 hours
    use_docker: bool = True,
    runtime: str = DOCKER,
    sif_dir: str = "."
) -> Dict[str, Any]:
    """
    Run Stage 1 (crawler) via a container.

    Args:
        start_page: Starting page number
        end_page: Ending page number
        output_dir: Output directory for crawler data (stage1 directory)
        log_file: Optional log file path
        timeout_seconds: Timeout for crawler in seconds
        use_docker: Whether to use a container (default: True)
        runtime: 'docker' (default) or 'singularity'. NOTE: the crawler needs
            Chrome with relaxed privileges (SYS_ADMIN/seccomp), which map to
            Docker; Singularity support is experimental and typically requires
            a permissive host. The crawler is not intended for HPC.
        sif_dir: directory holding the .sif image (singularity mode)

    Returns:
        Dict with success status and statistics
    """
    result = {
        "success": False,
        # "success" | "capped" | "no_pages_in_range" | "failed" | "interrupted";
        # set explicitly so a caller can tell a user interrupt, a range that had
        # nothing in it, and a genuine failure apart. "capped" carries
        # success=True — every page that exists was crawled.
        "status": "failed",
        "start_page": start_page,
        "end_page": end_page,
        "output_dir": str(output_dir),
        "statistics": {},
        "error": None,
        "execution_mode": "docker" if use_docker else "local"
    }

    # Ensure output directories exist
    for subdir in ["META", "SMTX", "SRR"]:
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)

    if use_docker:
        result["execution_mode"] = runtime
        return _run_stage1_docker(
            start_page, end_page, output_dir, log_file, timeout_seconds, result,
            runtime=runtime, sif_dir=sif_dir
        )
    else:
        return _run_stage1_local(
            start_page, end_page, output_dir, log_file, timeout_seconds, result
        )


def _run_stage1_docker(
    start_page: int,
    end_page: int,
    output_dir: Path,
    log_file: Optional[Path],
    timeout_seconds: int,
    result: Dict[str, Any],
    runtime: str = DOCKER,
    sif_dir: str = "."
) -> Dict[str, Any]:
    """Run Stage 1 (crawler) in a container (docker or singularity)."""
    docker_image = get_docker_image()

    if runtime == SINGULARITY:
        logger.warning(
            "Stage 1 (crawler) under Singularity is experimental: Chrome needs "
            "SYS_ADMIN/seccomp privileges that map to Docker. Prefer Docker for "
            "the crawler, or run it on a permissive host (not HPC)."
        )

    # Check if the image (docker) / .sif (singularity) exists
    if not image_available(docker_image, runtime=runtime, sif_dir=sif_dir):
        result["error"] = f"{runtime} image for '{docker_image}' not found. Build it first."
        logger.error(result["error"])
        return result

    try:
        # Make output_dir absolute
        output_dir = Path(output_dir).resolve()

        # Step 1: Run crawler in Docker
        logger.info(f"Running crawler in Docker: pages {start_page}-{end_page}")

        # docker: docker run --rm --shm-size=2g --cap-add=SYS_ADMIN ... -v out:/data IMG start end true
        # singularity: the docker-only Chrome flags are dropped (different security model).
        docker_cmd = build_run_command(
            docker_image,
            mounts=[(str(output_dir), "/data", False)],
            args=[str(start_page), str(end_page), "true"],  # true = headless
            runtime=runtime,
            docker_flags=[
                "--shm-size=2g",              # Required for Chrome in Docker
                "--cap-add=SYS_ADMIN",       # Required for Chrome sandbox
                "--security-opt", "seccomp=unconfined",  # Chrome needs this in Docker
                "--user", "root",            # Run as root to avoid permission issues
            ],
            sif_dir=sif_dir,
        )

        logger.info(f"Container command: {' '.join(docker_cmd)}")

        # Open log file if specified
        log_handle = None
        if log_file:
            log_handle = open(log_file, 'w')
            log_handle.write(f"=== Stage 1 Docker Execution ===\n")
            log_handle.write(f"Pages: {start_page}-{end_page}\n")
            log_handle.write(f"Docker image: {docker_image}\n")
            log_handle.write(f"Output dir: {output_dir}\n")
            log_handle.write(f"Command: {' '.join(docker_cmd)}\n\n")
            log_handle.flush()

        try:
            proc = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            deadline = time.monotonic() + timeout_seconds

            # Use readline() instead of iterator to avoid read-ahead buffering
            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if line:
                    line = line.rstrip("\n")
                    # Write to log file
                    if log_handle:
                        log_handle.write(line + "\n")
                        log_handle.flush()
                    # Forward to master log so WebSocket picks it up
                    logger.info(f"[crawler] {line}")
                    # Flush all log handlers immediately
                    for handler in logging.getLogger("cycle_test").handlers:
                        handler.flush()

                if time.monotonic() > deadline:
                    proc.kill()
                    raise subprocess.TimeoutExpired(docker_cmd, timeout_seconds)

            proc.wait()

            crawl_status, error = classify_stage1_outcome(
                proc.returncode, output_dir, start_page, end_page
            )
            if not _record_crawl_outcome(result, crawl_status, error, output_dir):
                return result

        finally:
            if log_handle:
                log_handle.close()

        logger.info("Docker crawler completed successfully")

        # Step 2: Run integration.py in Docker (same image)
        logger.info("Running integration analysis in Docker...")

        integration_cmd = [
            "docker", "run", "--rm",
            "-v", f"{output_dir}:/data",
            "--entrypoint", "python",
            docker_image,
            "/app/integration.py",
            "/data"
        ]

        logger.info(f"Integration command: {' '.join(integration_cmd)}")

        int_proc = subprocess.Popen(
            integration_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        integration_timed_out = False
        int_deadline = time.monotonic() + INTEGRATION_TIMEOUT_SECONDS
        while True:
            line = int_proc.stdout.readline()
            if not line and int_proc.poll() is not None:
                break
            if line:
                line = line.rstrip("\n")
                logger.info(f"[integration] {line}")
                for handler in logging.getLogger("cycle_test").handlers:
                    handler.flush()
            if time.monotonic() > int_deadline:
                int_proc.kill()
                integration_timed_out = True
                logger.error(
                    f"Integration timed out after {INTEGRATION_TIMEOUT_SECONDS}s; crawl "
                    "data is intact but its reports and complete_datasets list are missing"
                )
                break

        int_proc.wait()

        # The integration pass produces reports and the complete_datasets list,
        # which Stage 3 prep may filter on. Stage 2 reads the META files directly,
        # so a failure here does not invalidate the crawl and is not a Stage 1
        # failure. Record it so the outcome is visible rather than buried in a
        # warning: a missing complete_datasets list changes what Stage 3 sees.
        result["integration"] = _integration_result(
            int_proc.returncode, timed_out=integration_timed_out
        )
        if integration_timed_out:
            pass  # already logged at the kill site
        elif int_proc.returncode != 0:
            logger.error(
                f"Integration failed (exit code {int_proc.returncode}); crawl data is "
                "intact but its reports and complete_datasets list may be missing"
            )
        else:
            logger.info("Integration analysis completed")

        # Step 3: Collect statistics
        result["statistics"] = collect_stage1_statistics(output_dir)
        result["crawl_manifest"] = manifest_provenance(output_dir)
        result["success"] = True
        result["status"] = crawl_status

        logger.info(f"Stage 1 stats: {result['statistics']}")

    except subprocess.TimeoutExpired:
        result["error"] = f"Stage 1 timed out after {timeout_seconds} seconds"
        logger.error(result["error"])
    except FileNotFoundError as e:
        result["error"] = f"Docker not found: {e}"
        logger.error(result["error"])
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Stage 1 failed: {e}")

    return result


def _run_stage1_local(
    start_page: int,
    end_page: int,
    output_dir: Path,
    log_file: Optional[Path],
    timeout_seconds: int,
    result: Dict[str, Any]
) -> Dict[str, Any]:
    """Run Stage 1 locally (fallback without Docker)."""
    try:
        # Step 1: Run crawler locally
        logger.info(f"Running crawler locally: pages {start_page}-{end_page}")

        cmd = [
            "python", str(CRAWLER_PATH),
            "--start", str(start_page),
            "--end", str(end_page),
            "--output", str(output_dir),
            "--headless"
        ]

        logger.info(f"Command: {' '.join(cmd)}")

        log_handle = open(log_file, 'w') if log_file else subprocess.DEVNULL

        try:
            proc = subprocess.run(
                cmd,
                stdout=log_handle if log_file else subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                cwd=str(GENOAR_ROOT)
            )

            crawl_status, error = classify_stage1_outcome(
                proc.returncode, output_dir, start_page, end_page
            )
            if not _record_crawl_outcome(result, crawl_status, error, output_dir):
                return result

        finally:
            if log_file and log_handle != subprocess.DEVNULL:
                log_handle.close()

        logger.info("Crawler completed successfully")

        # Step 2: Run integration.py
        logger.info("Running integration analysis...")

        integration_cmd = [
            "python", str(INTEGRATION_PATH),
            str(output_dir)
        ]

        # Caught here rather than by the outer handler, which is the crawler's:
        # letting an integration timeout escape would fail all of Stage 1 and
        # report the crawler's timeout value, even though the crawl itself
        # finished. Integration is a secondary step either way.
        try:
            proc = subprocess.run(
                integration_cmd,
                capture_output=True,
                text=True,
                timeout=INTEGRATION_TIMEOUT_SECONDS,
                cwd=str(INTEGRATION_PATH.parent)
            )
            result["integration"] = _integration_result(proc.returncode)
            if proc.returncode != 0:
                logger.error(
                    f"Integration failed (exit code {proc.returncode}); crawl data is "
                    f"intact but its reports and complete_datasets list may be missing: "
                    f"{proc.stderr}"
                )
            else:
                logger.info("Integration analysis completed")
        except subprocess.TimeoutExpired:
            result["integration"] = _integration_result(None, timed_out=True)
            logger.error(
                f"Integration timed out after {INTEGRATION_TIMEOUT_SECONDS}s; crawl "
                "data is intact but its reports and complete_datasets list are missing"
            )

        # Step 3: Collect statistics
        result["statistics"] = collect_stage1_statistics(output_dir)
        result["crawl_manifest"] = manifest_provenance(output_dir)
        result["success"] = True
        result["status"] = crawl_status

        logger.info(f"Stage 1 stats: {result['statistics']}")

    except subprocess.TimeoutExpired:
        result["error"] = f"Stage 1 timed out after {timeout_seconds} seconds"
        logger.error(result["error"])
    except FileNotFoundError as e:
        result["error"] = f"Script not found: {e}"
        logger.error(result["error"])
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Stage 1 failed: {e}")

    return result


def collect_stage1_statistics(output_dir: Path) -> Dict[str, Any]:
    """Collect statistics from Stage 1 output."""
    stats = {
        "meta_files": 0,
        "smtx_files": 0,
        "srr_files": 0,
        "total_srr_ids": 0,
        "complete_datasets": 0
    }

    # Count files
    meta_dir = output_dir / "META"
    smtx_dir = output_dir / "SMTX"
    srr_dir = output_dir / "SRR"

    if meta_dir.exists():
        stats["meta_files"] = len(list(meta_dir.glob("*_meta.txt")))

    if smtx_dir.exists():
        stats["smtx_files"] = len(list(smtx_dir.glob("*.gz")))

    if srr_dir.exists():
        srr_files = list(srr_dir.glob("*.txt"))
        stats["srr_files"] = len(srr_files)

        # Count total SRR IDs
        total_srr = 0
        for srr_file in srr_files:
            try:
                with open(srr_file) as f:
                    total_srr += sum(1 for line in f if line.strip().startswith("SRR"))
            except Exception:
                pass
        stats["total_srr_ids"] = total_srr

    # Read complete_datasets.txt if exists
    complete_file = output_dir / "complete_datasets.txt"
    if complete_file.exists():
        try:
            with open(complete_file) as f:
                stats["complete_datasets"] = sum(1 for line in f if line.strip())
        except Exception:
            pass

    # Read statistics_summary.json if exists
    summary_file = output_dir / "statistics_summary.json"
    if summary_file.exists():
        try:
            with open(summary_file) as f:
                stats["integration_summary"] = json.load(f)
        except Exception:
            pass

    return stats


if __name__ == "__main__":
    # Test run
    import sys
    logging.basicConfig(level=logging.INFO)

    test_dir = Path("/tmp/stage1_test")
    test_dir.mkdir(parents=True, exist_ok=True)

    use_docker = "--local" not in sys.argv

    result = run_stage1(
        start_page=1,
        end_page=5,
        output_dir=test_dir,
        log_file=test_dir / "test.log",
        use_docker=use_docker
    )

    print(json.dumps(result, indent=2))
