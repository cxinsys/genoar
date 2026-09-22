#!/usr/bin/env python3
"""
Stage 3 Downloader - Download SRA files from AWS S3 public bucket

Based on download_from_s3.sh but implemented in Python for better integration.
"""

import os
import re
import subprocess
import shutil
import logging
import json
import time
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# NCBI's SRA Open Data mirror on AWS, and the environment variable that points
# this elsewhere. A site behind an HTTP proxy, or one with no AWS egress at
# all, needs a mirror of the same layout - <base>/<accession>/<accession> - and
# had no way to say so short of editing this file.
DEFAULT_SRA_BASE_URL = "https://sra-pub-run-odp.s3.amazonaws.com/sra"
SRA_BASE_URL_ENV = "GENOAR_SRA_BASE_URL"

# The prefix this downloader fetches, and the prefix Stage 3 processes. The
# container discovers its samples by it (`name.startswith("SRR")` in
# Snakefile_hs.smk, `success/SRR*` in run_docker_pipeline.sh), so an ENA (ERR)
# or DDBJ (DRR) run fetched here would be staged and then not seen - a job that
# completes having analysed nothing. They are left out on purpose; what was
# missing is saying how many.
STAGE3_ACCESSION_PREFIX = "SRR"

# The run accessions the three INSDC archives issue, so a dropped ERR/DRR can
# be named as such rather than counted with malformed text.
RUN_ACCESSION_PATTERN = re.compile(r"^[SED]RR[0-9]+$")


def resolve_sra_base_url() -> str:
    """The base URL runs are fetched from, honouring GENOAR_SRA_BASE_URL.

    An empty or blank override is a mistake, not a request for the default:
    it would otherwise send every fetch to `/<accession>/<accession>`.
    """
    raw = os.environ.get(SRA_BASE_URL_ENV)
    if raw is None:
        return DEFAULT_SRA_BASE_URL
    base = raw.strip().rstrip("/")
    if not base:
        raise ValueError(
            f"{SRA_BASE_URL_ENV} is set but empty; unset it to use "
            f"{DEFAULT_SRA_BASE_URL}"
        )
    return base


# Below this a file is not a plausible .sra, only a stub or error page.
MIN_SRA_SIZE = 1_000_000
# NCBI's SRA container starts with this signature. Checking it costs nothing and
# rejects payloads that merely happen to be large enough, such as an HTML error
# page or a file from an unrelated URL.
SRA_MAGIC = b"NCBI.sra"


class UnsafeSRAInputError(Exception):
    """A corrupt .sra could not be moved out of the pipeline's input path.

    Distinct from an ordinary download failure: the pipeline collects inputs with
    a */*.sra glob, so this file would still reach Cell Ranger. The run must stop
    rather than proceed with a corrupt input, so this is not caught as a normal
    per-sample error.
    """


def looks_like_sra(path: Path) -> bool:
    """Whether the file begins with the SRA signature."""
    try:
        with open(path, "rb") as f:
            return f.read(len(SRA_MAGIC)) == SRA_MAGIC
    except OSError:
        return False


def is_complete_sra(path: Path, remote_size: Optional[int] = None) -> bool:
    """Whether a file is a usable, complete .sra.

    One definition shared by promotion and by the next run's reuse check, so a
    file promoted at the end of one run cannot be rejected at the start of the
    next. Size is the floor, the signature rules out wrong payloads, and a known
    remote size must match exactly.
    """
    if not path.exists():
        return False
    size = path.stat().st_size
    if remote_size is not None and size != remote_size:
        return False
    if size < MIN_SRA_SIZE:
        return False
    return looks_like_sra(path)


def remote_content_length(url: str, timeout: int = 30) -> Optional[int]:
    """Content-Length from a HEAD request, or None if it cannot be determined."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length is not None else None
    except Exception as e:
        logger.warning(f"Could not get remote size for {url}: {e}")
        return None


def verify_and_promote(part_path: Path, final_path: Path, remote_size: Optional[int]) -> bool:
    """Promote a finished .part to its final name only if it is actually complete.

    Complete means the part exists and either matches the known remote size
    exactly, or, when the remote size is unknown, both clears the minimum
    plausible size and starts with the SRA signature. Otherwise the .part is left
    in place so the next run can resume it, and the file never takes the name
    that marks a download done.
    """
    if not part_path.exists():
        return False

    if not is_complete_sra(part_path, remote_size):
        size = part_path.stat().st_size
        if remote_size is not None and size != remote_size:
            reason = f"is {size} bytes, expected {remote_size}"
        elif size < MIN_SRA_SIZE:
            reason = f"is only {size} bytes"
        else:
            reason = "does not start with the SRA signature"
        logger.error(
            f"Not promoting {final_path.name}: it {reason}; keeping .part for resume"
        )
        return False

    if remote_size is None:
        logger.warning(
            f"Remote size unknown for {final_path.name}; verified by signature and "
            "size only, not an exact length"
        )
    part_path.replace(final_path)  # atomic within the same directory
    return True


@dataclass
class DownloadProgress:
    """Track download progress."""
    total: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    in_progress: str = ""
    failed_srr_ids: List[str] = field(default_factory=list)
    bytes_downloaded: int = 0
    started_at: str = ""


class S3SRADownloader:
    """Download SRA runs from the AWS SRA Open Data mirror, or a mirror of it."""

    def __init__(
        self,
        srr_list_file: Path,
        output_dir: Path,
        max_concurrent: int = 4,
        timeout_per_file: int = 3600,  # 1 hour
        log_file: Optional[Path] = None,
        base_url: Optional[str] = None
    ):
        """
        Initialize downloader.

        Args:
            srr_list_file: Path to srr_list.txt with SRR IDs
            output_dir: Output directory for downloaded SRA files
            max_concurrent: Maximum concurrent downloads
            timeout_per_file: Timeout per file in seconds
            log_file: Optional log file path
            base_url: Where runs are fetched from. When None, resolved from
                GENOAR_SRA_BASE_URL and otherwise the AWS mirror.
        """
        self.base_url = (base_url.strip().rstrip("/") if base_url
                         else resolve_sra_base_url())
        if self.base_url != DEFAULT_SRA_BASE_URL:
            logger.info(f"Fetching SRA runs from {self.base_url}")
        self.srr_list_file = Path(srr_list_file)
        self.output_dir = Path(output_dir)
        self.max_concurrent = max_concurrent
        self.timeout_per_file = timeout_per_file
        self.log_file = log_file
        self.downloader = self._select_downloader()
        self.progress = DownloadProgress()

    def _select_downloader(self) -> str:
        """Select best available download tool."""
        if shutil.which("aria2c"):
            logger.info("Using aria2c (fast multi-threaded downloader)")
            return "aria2c"
        elif shutil.which("wget"):
            logger.info("Using wget")
            return "wget"
        elif shutil.which("curl"):
            logger.info("Using curl")
            return "curl"
        else:
            raise RuntimeError("No download tool found (need aria2c, wget, or curl)")

    def _load_srr_list(self) -> List[str]:
        """Load SRR IDs from file."""
        if not self.srr_list_file.exists():
            raise FileNotFoundError(f"SRR list file not found: {self.srr_list_file}")

        accepted: List[str] = []
        skipped: Dict[str, int] = {}
        with open(self.srr_list_file) as f:
            for line in f:
                accession = line.strip()
                if not accession:
                    continue
                if accession.startswith(STAGE3_ACCESSION_PREFIX):
                    accepted.append(accession)
                else:
                    key = (accession[:3].upper()
                           if RUN_ACCESSION_PATTERN.match(accession) else "unrecognised")
                    skipped[key] = skipped.get(key, 0) + 1
        if skipped:
            logger.warning(
                f"{self.srr_list_file}: {sum(skipped.values())} accession(s) are "
                f"not {STAGE3_ACCESSION_PREFIX}* and will not be fetched "
                f"({', '.join(f'{k}={v}' for k, v in sorted(skipped.items()))}). "
                f"Stage 3 discovers its samples by that prefix, so fetching them "
                f"here would produce inputs it never looks at."
            )
        return accepted

    def _get_output_path(self, srr_id: str) -> Path:
        """Get output path for SRR file."""
        return self.output_dir / srr_id / f"{srr_id}.sra"

    def _get_s3_url(self, srr_id: str) -> str:
        """Get the URL for one run, from whichever base this run resolved."""
        return f"{self.base_url}/{srr_id}/{srr_id}"

    def _file_exists_and_valid(self, output_path: Path) -> bool:
        """Whether a *completed, usable* download is already present.

        Only the final .sra counts as done; a leftover .part means an earlier run
        did not finish and must be resumed, not skipped. The signature is checked
        as well as the size (via is_complete_sra, the same definition promotion
        uses), so a corrupt file from an older run is re-fetched rather than
        carried forward into Cell Ranger.
        """
        if not output_path.exists():
            return False
        if is_complete_sra(output_path):
            return True

        # Present but unusable. Move it aside rather than just declining to reuse
        # it: Stage 3 collects its inputs with a */*.sra glob, so a corrupt file
        # left in place would still reach Cell Ranger if the re-download failed.
        quarantine = output_path.with_name(output_path.name + ".corrupt")
        logger.warning(
            f"{output_path.name} exists but is not a usable SRA file; moving it to "
            f"{quarantine.name} and downloading again"
        )
        # If it cannot be moved, it is still in Stage 3's */*.sra glob. That is a
        # safety-invariant failure, not a "re-download" case, so it raises a
        # dedicated error that the run treats as fatal rather than swallowing it.
        try:
            output_path.replace(quarantine)
        except OSError as e:
            raise UnsafeSRAInputError(
                f"Could not quarantine corrupt {output_path}: {e}. It is still in "
                "the Stage 3 input path, so the run cannot continue safely."
            ) from e
        return False

    def _get_part_path(self, srr_id: str) -> Path:
        """Where an in-flight download is written before it is verified."""
        final = self._get_output_path(srr_id)
        return final.with_name(final.name + ".part")

    def download_single(self, srr_id: str) -> Dict[str, Any]:
        """
        Download single SRA file.

        Args:
            srr_id: SRR ID to download

        Returns:
            Dict with download result
        """
        result = {
            "srr_id": srr_id,
            "success": False,
            "skipped": False,
            "size": 0,
            "duration": 0,
            "error": None
        }

        output_path = self._get_output_path(srr_id)
        part_path = self._get_part_path(srr_id)
        url = self._get_s3_url(srr_id)

        # Skip only a completed download. A .part left from a previous run is not
        # complete, so we fall through and let the tool resume it.
        if self._file_exists_and_valid(output_path):
            result["skipped"] = True
            result["success"] = True
            result["size"] = output_path.stat().st_size
            logger.info(f"  {srr_id}: Already exists ({self._format_size(result['size'])})")
            return result

        # Create directory
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Download
        start_time = time.time()

        try:
            # Download into a .part so an interrupted transfer never occupies the
            # final name, then verify size and promote atomically.
            if self.downloader == "aria2c":
                success = self._download_with_aria2c(url, part_path)
            elif self.downloader == "wget":
                success = self._download_with_wget(url, part_path)
            else:
                success = self._download_with_curl(url, part_path)

            result["duration"] = time.time() - start_time

            promoted = success and verify_and_promote(
                part_path, output_path, remote_content_length(url)
            )
            if promoted:
                result["success"] = True
                result["size"] = output_path.stat().st_size
                speed = result["size"] / result["duration"] if result["duration"] > 0 else 0
                logger.info(
                    f"  {srr_id}: Downloaded {self._format_size(result['size'])} "
                    f"in {result['duration']:.1f}s ({self._format_size(speed)}/s)"
                )
            else:
                result["error"] = "Download failed or incomplete"
                logger.error(f"  {srr_id}: Download failed or incomplete")

        except subprocess.TimeoutExpired:
            result["error"] = f"Timeout after {self.timeout_per_file}s"
            logger.error(f"  {srr_id}: Timeout")
        except UnsafeSRAInputError:
            # Not a per-sample failure to tally: it must stop the whole run, so it
            # propagates past download_all() rather than being caught here.
            raise
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"  {srr_id}: Error - {e}")

        return result

    # One retry policy across the three backends: five attempts per file, then
    # give up. Only the spelling differs, because the defaults do -- aria2c's
    # --max-tries is already 5, wget's --tries defaults to 20 and is cut down,
    # and curl retries nothing unless asked. Nothing above these retries, so a
    # file that exhausts its attempts is a failed sample for this run; what
    # makes that survivable is resume, since every backend continues into the
    # same .part and the next run pays only for the bytes not already there.
    # timeout_per_file bounds the whole attempt either way.

    def _download_with_aria2c(self, url: str, output: Path) -> bool:
        """Download with aria2c (multi-threaded)."""
        cmd = [
            "aria2c",
            "--continue=true",
            # Eight connections over eight splits, no piece under 10M: past
            # this the bucket starts rate-limiting rather than going faster.
            "--max-connection-per-server=8",
            "--split=8",
            "--min-split-size=10M",
            f"--dir={output.parent}",
            f"--out={output.name}",
            "--file-allocation=none",
            "--summary-interval=10",
            "--console-log-level=warn",
            url
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_per_file
        )
        return proc.returncode == 0

    def _download_with_wget(self, url: str, output: Path) -> bool:
        """Download with wget."""
        cmd = [
            "wget",
            "--continue",
            "--timeout=30",
            "--tries=5",
            f"--output-document={output}",
            url
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_per_file
        )
        return proc.returncode == 0

    def _download_with_curl(self, url: str, output: Path) -> bool:
        """Download with curl."""
        cmd = [
            "curl",
            "--continue-at", "-",
            "--location",
            "--retry", "5",
            # Pinned flat: curl's own default between retries is an
            # exponential backoff, and this keeps the five attempts bounded.
            "--retry-delay", "3",
            "--output", str(output),
            url
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_per_file
        )
        return proc.returncode == 0

    def _format_size(self, size: float) -> str:
        """Format size in human-readable form."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"

    def _save_progress(self) -> None:
        """Save progress, replacing the file atomically.

        This is rewritten after every download, so writing in place would leave a
        truncated file if the run stopped mid-write, and progress is exactly what
        a reader consults after an interrupted run.
        """
        progress_file = self.output_dir / "download_progress.json"
        tmp_file = progress_file.with_suffix(".json.tmp")
        payload = {
            "started_at": self.progress.started_at,
            "total": self.progress.total,
            "completed": self.progress.completed,
            "skipped": self.progress.skipped,
            "failed": self.progress.failed,
            "in_progress": self.progress.in_progress,
            "failed_srr_ids": self.progress.failed_srr_ids,
            "bytes_downloaded": self.progress.bytes_downloaded,
        }
        try:
            with open(tmp_file, "w") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp_file.replace(progress_file)
        finally:
            # A failed write must not leave the temp file behind either.
            if tmp_file.exists():
                tmp_file.unlink()

    def download_all(self, max_samples: Optional[int] = None) -> Dict[str, Any]:
        """
        Download all SRA files from srr_list.txt.

        Args:
            max_samples: Optional limit on number of samples to download

        Returns:
            Dict with download statistics
        """
        result = {
            "success": False,
            "total": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "failed_srr_ids": [],
            "total_bytes": 0,
            "duration_seconds": 0,
            "error": None,
            # Set when a corrupt input could not be quarantined. The caller must
            # stop the run rather than treat it as an ordinary download failure.
            "fatal_input_error": False,
        }

        try:
            # Load SRR list
            srr_ids = self._load_srr_list()

            if max_samples:
                srr_ids = srr_ids[:max_samples]
                logger.info(f"Limited to {max_samples} samples")

            if not srr_ids:
                # Nothing was fetched, so this is not a completed download step.
                # Reporting success here let an empty or stale list finish the
                # cycle as though Stage 3 had run.
                logger.error("No SRR IDs to download")
                result["success"] = False
                result["error"] = "No SRR IDs to download"
                return result

            # Initialize progress
            self.progress.total = len(srr_ids)
            self.progress.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            result["total"] = len(srr_ids)

            logger.info(f"Starting download of {len(srr_ids)} SRA files")
            logger.info(f"Output directory: {self.output_dir}")
            logger.info(f"Downloader: {self.downloader}")
            logger.info(f"Max concurrent: {self.max_concurrent}")

            # Create output directory
            self.output_dir.mkdir(parents=True, exist_ok=True)

            start_time = time.time()

            # Download with ThreadPoolExecutor
            if self.max_concurrent > 1:
                with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
                    future_to_srr = {
                        executor.submit(self.download_single, srr_id): srr_id
                        for srr_id in srr_ids
                    }

                    for future in as_completed(future_to_srr):
                        srr_id = future_to_srr[future]
                        try:
                            download_result = future.result()
                            self._process_result(download_result, result)
                        except UnsafeSRAInputError as e:
                            # A corrupt input still in the glob is fatal to the run,
                            # not a per-sample failure to count and move past.
                            logger.error(f"  {srr_id}: unsafe input - {e}")
                            result["fatal_input_error"] = True
                            result["error"] = str(e)
                        except Exception as e:
                            logger.error(f"  {srr_id}: Exception - {e}")
                            result["failed"] += 1
                            result["failed_srr_ids"].append(srr_id)

                        self._save_progress()
            else:
                # Sequential download
                for srr_id in srr_ids:
                    self.progress.in_progress = srr_id
                    try:
                        download_result = self.download_single(srr_id)
                    except UnsafeSRAInputError as e:
                        logger.error(f"  {srr_id}: unsafe input - {e}")
                        result["fatal_input_error"] = True
                        result["error"] = str(e)
                        self._save_progress()
                        break
                    self._process_result(download_result, result)
                    self._save_progress()

            result["duration_seconds"] = time.time() - start_time
            result["success"] = result["failed"] == 0 and not result["fatal_input_error"]

            # Write failed list
            if result["failed_srr_ids"]:
                failed_file = self.output_dir / "download_failed.txt"
                with open(failed_file, 'w') as f:
                    for srr_id in result["failed_srr_ids"]:
                        f.write(f"{srr_id}\n")

            logger.info("=" * 50)
            logger.info("Download Summary")
            logger.info("=" * 50)
            logger.info(f"  Total:      {result['total']}")
            logger.info(f"  Downloaded: {result['downloaded']}")
            logger.info(f"  Skipped:    {result['skipped']}")
            logger.info(f"  Failed:     {result['failed']}")
            logger.info(f"  Total size: {self._format_size(result['total_bytes'])}")
            logger.info(f"  Duration:   {result['duration_seconds']:.1f}s")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Download failed: {e}", exc_info=True)

        return result

    def _process_result(self, download_result: Dict, result: Dict) -> None:
        """Process individual download result."""
        if download_result["skipped"]:
            result["skipped"] += 1
            self.progress.skipped += 1
        elif download_result["success"]:
            result["downloaded"] += 1
            self.progress.completed += 1
        else:
            result["failed"] += 1
            result["failed_srr_ids"].append(download_result["srr_id"])
            self.progress.failed += 1
            self.progress.failed_srr_ids.append(download_result["srr_id"])

        result["total_bytes"] += download_result.get("size", 0)
        self.progress.bytes_downloaded += download_result.get("size", 0)


def download_sra_files(
    srr_list_file: Path,
    output_dir: Path,
    max_concurrent: int = 4,
    max_samples: Optional[int] = None,
    log_file: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Convenience function to download SRA files.

    Args:
        srr_list_file: Path to srr_list.txt
        output_dir: Output directory
        max_concurrent: Maximum concurrent downloads
        max_samples: Optional limit on samples
        log_file: Optional log file

    Returns:
        Dict with download statistics
    """
    downloader = S3SRADownloader(
        srr_list_file=srr_list_file,
        output_dir=output_dir,
        max_concurrent=max_concurrent,
        log_file=log_file
    )
    return downloader.download_all(max_samples=max_samples)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download SRA files from S3")
    parser.add_argument("srr_list", type=Path, help="Path to srr_list.txt")
    parser.add_argument("output_dir", type=Path, help="Output directory")
    parser.add_argument("--max-concurrent", type=int, default=4, help="Max concurrent downloads")
    parser.add_argument("--max-samples", type=int, help="Limit number of samples")
    parser.add_argument("--test", action="store_true", help="Test mode (download 2 samples)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    max_samples = 2 if args.test else args.max_samples

    result = download_sra_files(
        srr_list_file=args.srr_list,
        output_dir=args.output_dir,
        max_concurrent=args.max_concurrent,
        max_samples=max_samples
    )

    print(json.dumps(result, indent=2))
