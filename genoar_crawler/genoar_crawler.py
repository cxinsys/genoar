#!/usr/bin/env python3
"""
GENOAR Crawler - Enhanced version with checkpoint, resume, and full crawling support
Gene Expression Omnibus Automated Retriever
"""

import errno
import os
import re
import sys
import time
import json
import shutil
import subprocess
import logging
import argparse
import uuid
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple, Callable, Any

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    WebDriverException
)

# Pure parsing helpers live in a selenium-free module so they stay unit-testable.
# The crawler runs as a direct script (`python genoar_crawler.py`), so its own
# directory is on sys.path and this flat import resolves at runtime.
from parsing import parse_grep_count

# Global logger - will be configured in main()
logger = logging.getLogger(__name__)


def setup_logging(log_target_dir: Path, log_level: str = 'INFO') -> None:
    """Setup logging with proper path handling for Docker.

    `log_target_dir` is the worker's own log directory (see WorkerScope), so
    two workers of one run never write into a single log file. LOG_DIR still
    overrides it for deployments that collect logs elsewhere.
    """
    log_dir = Path(os.environ.get('LOG_DIR', str(log_target_dir)))

    # Clear existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    handlers = [logging.StreamHandler()]  # Console always enabled
    log_file = None

    # Try to create log directory and file
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f'genoar_crawler_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
        # Test write permission
        log_file.touch()
        handlers.append(logging.FileHandler(log_file))
    except PermissionError:
        print(f"WARNING: Cannot write to {log_dir}, using console-only logging")
        print("TIP: Run with --user $(id -u):$(id -g) to fix permissions")
    except OSError as e:
        print(f"WARNING: Cannot create log file: {e}, using console-only logging")

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

    if log_file and log_file.exists():
        logger.info(f"Log file: {log_file}")
    else:
        logger.warning("File logging disabled - check directory permissions")


# Written only when a crawl finishes its whole requested page range. Its presence
# is how a caller distinguishes a completed crawl from one that stopped early yet
# still let the container exit 0 (an interrupt caught above the process, say).
COMPLETION_MANIFEST_FILE = "crawl_manifest.json"
CHECKPOINT_FILE = "checkpoint.json"

# --- Run/worker directory layout -------------------------------------------
#
# A parallel run must not point every worker at one output directory and one
# working directory. Sharing checkpoint.json, crawl_manifest.json, the browser's
# download area and the Chrome profile costs the run its results and its
# evidence: each worker deletes the manifest at startup and writes only its own
# slice at the end, each overwrites the other's resume state, and each cleanup
# glob can delete a file another worker has just downloaded. What survives
# describes one slice and calls it the whole range.
#
# So everything a worker writes-but-does-not-publish now lives under its own
# directory:
#
#   <output_dir>/
#     SMTX/ SRR/ META/                     the run's actual data - still shared,
#                                          because filenames are GSE accessions
#                                          and page slices are disjoint
#     runs/<run_id>/
#       run.json                           the plan: requested range, workers
#       crawl_manifest.json                the merged, whole-range manifest
#       workers/worker-<id>/
#         assignment.json                  this worker's slice (orchestrator)
#         exit_code                        this worker's real exit status
#         checkpoint.json                  progress through one page request:
#                                          the request itself, the page and GSE
#                                          index reached, the accessions taken and
#                                          failed, and the files collected so far.
#                                          A resume whose checkpoint began at a
#                                          different page is refused
#         crawl_manifest.json              this worker's slice manifest
#         downloads/                       browser download temp area
#         chrome-profile/                  Chrome --user-data-dir
#         genoar_crawler_*.log             this worker's log
#     crawl_manifest.json                  a pointer to the newest run that
#                                          completed, kept for readers that
#                                          predate this layout
#
# Nothing deletes an earlier run: a new run gets a new run ID. The only file a
# new run overwrites is the top-level pointer manifest, and only its
# orchestrator, only after aggregation succeeded.
RUNS_DIR_NAME = "runs"
WORKERS_DIR_NAME = "workers"
RUN_PLAN_FILE = "run.json"
WORKER_ASSIGNMENT_FILE = "assignment.json"
WORKER_EXIT_CODE_FILE = "exit_code"
# Where an unscoped crawler (no orchestrator, no worker id) keeps its browser
# downloads. Under the output directory so a completed download is renamed, not
# copied, into SMTX/SRR/META.
DOWNLOAD_SCRATCH_DIR = ".downloads"
DEFAULT_ITEMS_PER_PAGE = 500

# The three shared result directories, and therefore the buckets a run reports
# having filled. They are shared on purpose: file names are GSE accessions and
# earlier runs' results must survive, which is exactly why the number of files
# in them cannot be read as "what this run collected".
COLLECTED_FILE_BUCKETS = ('SMTX', 'SRR', 'META')

# Run and worker ids become directory names, so they may not be a path.
IDENTITY_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')

# The GEO query this project was built around: human GSE series, expression
# profiling by high-throughput sequencing, carrying a tissue attribute. Every
# mode of one crawl uses one URL, and which URL it is decides which datasets
# the run is about — so it is resolved once, recorded in the completion
# manifest, and never read from more than one place.
DEFAULT_GEO_SEARCH_URL = (
    'https://www.ncbi.nlm.nih.gov/gds/?term=%22Homo+sapiens%22%5Bporgn%5D+'
    'AND+(%22gse%22%5BFilter%5D+AND+%22Expression+profiling+by+high+throughput+'
    'sequencing%22%5BFilter%5D+AND+%22attribute+name+tissue%22%5BFilter%5D)'
)
GEO_SEARCH_URL_ENV = 'GENOAR_GEO_SEARCH_URL'


def resolve_search_url(explicit: Optional[str] = None) -> str:
    """The GEO query this run crawls: --search-url, then env, then the default.

    A blank value is a mistake rather than a request for the default: it would
    otherwise send the crawl to a URL that is not a search and report whatever
    came back as this project's corpus. Anything that is not http(s) is refused
    for the same reason.
    """
    for value, origin in ((explicit, '--search-url'),
                          (os.environ.get(GEO_SEARCH_URL_ENV), GEO_SEARCH_URL_ENV)):
        if value is None:
            continue
        url = value.strip()
        if not url:
            raise ValueError(f"{origin} is set but empty")
        if not url.startswith(('http://', 'https://')):
            raise ValueError(f"{origin} is not an http(s) URL: {url!r}")
        return url
    return DEFAULT_GEO_SEARCH_URL


# Prefix of the one machine-readable line `--report-pages` prints, so a shell
# orchestrator can pick it out of the log stream on stdout.
PAGE_REPORT_PREFIX = "GENOAR_PAGE_REPORT "

# Same idea for `--aggregate`: one flat JSON line saying what the run it just
# merged covered and collected. The launchers read their exit status off this
# rather than off the output directory, whose contents belong to every run that
# ever wrote there.
RUN_REPORT_PREFIX = "GENOAR_RUN_REPORT "


class CrawlIntegrityError(RuntimeError):
    """Raised when the crawler cannot prove that a page completed correctly.

    An integrity error stops the crawl and says so. The alternatives — retry
    forever, or log a warning and carry on — let a crawl that lost its way
    still end with a completion manifest, recording pages as done that nothing
    actually verified.
    """


class SearchContextLostError(CrawlIntegrityError):
    """Raised when the preserved GEO search tab/session is no longer usable."""


class PageValidationError(CrawlIntegrityError):
    """Raised when a GEO results page is not the page the crawl asked for."""


class EmptyPageRangeError(ValueError):
    """Raised when a valid request leaves no page to crawl.

    A parallel run slices the page range across workers from a page count
    that was true when the run was launched. GEO's corpus changes daily, so
    the last slices can land entirely past the end — nothing to do. That is
    neither a completed crawl (nothing was processed, so no manifest) nor a
    malfunction, and main() gives it its own exit code rather than let it
    read as either.
    """


class ChromeSetupError(RuntimeError):
    """Raised when this machine's Chrome or ChromeDriver cannot be located."""


# Where the three GENOAR images put the browser and the driver. They disagree:
# the root Dockerfile installs chromedriver under /usr/local/bin, Dockerfile.amd64
# under /usr/bin with google-chrome-stable, Dockerfile.arm64 under /usr/bin with
# chromium. A single hardcoded default only ever matched one of them, which is
# how an external run met a bare NoSuchDriverException on the documented
# `make run` path. PATH is searched first, then these, and CHROME_BINARY_PATH /
# CHROME_DRIVER_PATH still override everything.
CHROME_BINARY_NAMES = ('google-chrome-stable', 'google-chrome', 'chromium',
                       'chromium-browser')
CHROME_BINARY_CANDIDATES = ('/usr/bin/google-chrome-stable',
                            '/usr/bin/google-chrome',
                            '/usr/bin/chromium',
                            '/usr/bin/chromium-browser',
                            '/usr/local/bin/google-chrome-stable',
                            '/usr/local/bin/chromium')
CHROME_DRIVER_NAMES = ('chromedriver',)
CHROME_DRIVER_CANDIDATES = ('/usr/bin/chromedriver',
                            '/usr/local/bin/chromedriver')

# Root of the Chrome profiles. Each process gets its own directory beneath it;
# see setup_driver for why sharing one was a defect.
CHROME_PROFILE_ROOT = Path('/data/.chrome')


def resolve_executable(env_var: str, names: Tuple[str, ...],
                       candidates: Tuple[str, ...]) -> Optional[str]:
    """Locate an executable: explicit env var, then PATH, then known paths.

    Returns None when nothing exists, so the caller can say what it searched
    instead of handing Selenium a path that is not there.
    """
    explicit = os.environ.get(env_var, '').strip()
    if explicit:
        return explicit

    for name in names:
        found = shutil.which(name)
        if found:
            return found

    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    return None


def _searched_description(env_var: str, names: Tuple[str, ...],
                          candidates: Tuple[str, ...]) -> str:
    """What resolve_executable looked at, for a failure message."""
    return (f"{env_var} (unset or empty), PATH entries for "
            f"{', '.join(names)}, and {', '.join(candidates)}")


def _scraped_text(value: Optional[str]) -> str:
    """Normalise a scraped value to a stripped string.

    `get_attribute` returns None whenever the attribute is simply absent, and
    a cell GEO rendered empty gives "" or whitespace. Callers that go on to
    match or compare need one shape; an actually missing required value is
    then rejected explicitly by the caller rather than by an AttributeError
    on None.
    """
    return (value or '').strip()


def _profile_dir_unusable(err: OSError) -> bool:
    """Whether this error means "use the /tmp profile instead".

    True for the location being unusable (no permission, read-only root,
    path that cannot exist); False for disk-full/IO errors, which the /tmp
    fallback would only hide.
    """
    return err.errno in (errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOENT)


def _identity_token() -> str:
    """A token unique to this process anywhere, including inside a container.

    `os.getpid()` alone is not one: every container's entrypoint process is
    PID 1, so four containers sharing a bind-mounted /data all derive the same
    "worker-1" profile path. The random half is what
    makes this true across PID namespaces; the PID is kept only because it is
    what an operator sees in `ps`.
    """
    return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class WorkerScope:
    """Where one crawler process may write its private state.

    `state_dir` holds checkpoint.json and this process's completion manifest,
    `download_dir` is the browser's download target and the only directory the
    cleanup globs touch, `profile_dir` is Chrome's --user-data-dir, `log_dir`
    receives the log file.

    A worker of an orchestrated run (`assigned` is True) gets all of them under
    `<output>/runs/<run_id>/workers/worker-<worker_id>/`. A crawler run by hand
    keeps checkpoint.json and crawl_manifest.json exactly where they have always
    been - directly under the output directory, so `--resume` and every existing
    artifact still work - and takes a process-unique download and profile
    directory, which are scratch either way.
    """

    output_dir: Path
    identity: str
    run_id: Optional[str]
    worker_id: Optional[str]
    run_dir: Optional[Path]
    worker_dir: Optional[Path]
    state_dir: Path
    download_dir: Path
    profile_dir: Path
    log_dir: Path

    @property
    def assigned(self) -> bool:
        """True when an orchestrator named this worker."""
        return self.worker_id is not None

    def manifest_path(self) -> Path:
        return self.state_dir / COMPLETION_MANIFEST_FILE

    def checkpoint_path(self) -> Path:
        return self.state_dir / CHECKPOINT_FILE


def _validate_identity(env_var: str, value: str) -> str:
    # fullmatch, not match: a pattern anchored only at the start accepts a
    # trailing newline, so 'good\n' would pass here and then name a directory.
    if not IDENTITY_PATTERN.fullmatch(value):
        raise ValueError(
            f"{env_var}={value!r} is not usable as a directory name; use "
            "letters, digits, '.', '_' or '-' (max 64 characters)"
        )
    return value


def run_dir_for(output_dir: Path, run_id: str) -> Path:
    """Where one run's plan, worker directories and merged manifest live."""
    return Path(output_dir) / RUNS_DIR_NAME / _validate_identity('run id', run_id)


def resolve_worker_scope(output_dir, run_id: Optional[str] = None,
                         worker_id: Optional[str] = None,
                         env: Optional[dict] = None) -> WorkerScope:
    """Decide where this process may write, from the identity it was given.

    The identity comes from the orchestrator through GENOAR_RUN_ID and
    GENOAR_WORKER_ID (or the matching arguments), never from the PID. When
    GENOAR_WORKER_ID is set, GENOAR_RUN_ID must be too: a worker without a run
    has nowhere to be aggregated from, and quietly inventing a run id would
    hide the miswiring until the merged manifest came out short.
    """
    env = os.environ if env is None else env
    output_dir = Path(output_dir)

    if run_id is None:
        run_id = (env.get('GENOAR_RUN_ID') or '').strip() or None
    if worker_id is None:
        worker_id = (env.get('GENOAR_WORKER_ID') or '').strip() or None

    if worker_id is None and run_id is None:
        token = _identity_token()
        return WorkerScope(
            output_dir=output_dir,
            identity=token,
            run_id=None,
            worker_id=None,
            run_dir=None,
            worker_dir=None,
            state_dir=output_dir,
            download_dir=output_dir / DOWNLOAD_SCRATCH_DIR / token,
            profile_dir=CHROME_PROFILE_ROOT / f'worker-{token}',
            log_dir=output_dir,
        )

    if worker_id is None:
        raise ValueError(
            "GENOAR_RUN_ID is set but GENOAR_WORKER_ID is not, so this process "
            "does not know which worker of the run it is."
        )
    if run_id is None:
        raise ValueError(
            "GENOAR_WORKER_ID is set but GENOAR_RUN_ID is not, so this worker's "
            "results could never be aggregated into a run."
        )

    _validate_identity('GENOAR_RUN_ID', run_id)
    _validate_identity('GENOAR_WORKER_ID', worker_id)

    resolved_run_dir = output_dir / RUNS_DIR_NAME / run_id
    worker_dir = resolved_run_dir / WORKERS_DIR_NAME / f'worker-{worker_id}'
    return WorkerScope(
        output_dir=output_dir,
        identity=f'{run_id}-{worker_id}',
        run_id=run_id,
        worker_id=worker_id,
        run_dir=resolved_run_dir,
        worker_dir=worker_dir,
        state_dir=worker_dir,
        download_dir=worker_dir / 'downloads',
        profile_dir=worker_dir / 'chrome-profile',
        log_dir=worker_dir,
    )


def atomic_write_json(path: Path, data: dict) -> Path:
    """Write JSON so a reader never sees a half-written file.

    The manifest is completion evidence. A crash midway through `json.dump`
    leaves a truncated file that a reader reports as "unreadable manifest" -
    or, worse, one that parses but is short. Written to a sibling temp file and
    renamed, a reader sees either the previous content or all of the new
    content.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f'.{path.name}.tmp.{os.getpid()}'
    try:
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def validate_page_request(start_page: int, end_page: int,
                          items_per_page: int = 500) -> None:
    """Reject page ranges that could only produce an empty 'success'."""
    if start_page < 1:
        raise ValueError("start_page must be at least 1")
    if end_page < 0:
        raise ValueError("end_page must be 0 (--all) or a positive page")
    if end_page > 0 and end_page < start_page:
        raise ValueError("end_page cannot be before start_page")
    if items_per_page < 1:
        raise ValueError("items_per_page must be at least 1")


def normalise_collected_files(counts: Optional[dict]) -> Optional[dict]:
    """A per-bucket tally of files with a total, or None if nothing recorded one.

    None and a tally of zeros are different statements - "no record of what was
    collected" against "collected nothing" - and only the second is evidence.
    Both are read as "cannot prove a file was collected", which is the safe way
    round: a run that cannot show its output does not get to claim it.
    """
    if counts is None:
        return None
    tally = {name: int(counts.get(name, 0) or 0)
             for name in COLLECTED_FILE_BUCKETS}
    tally["total"] = sum(tally.values())
    return tally


def write_completion_manifest(
    output_dir: Path,
    requested_start: int,
    requested_end: int,
    completed_start: int,
    completed_end: int,
    items_per_page: int,
    processed: int,
    failed: int,
    run_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    corpus_pages: Optional[int] = None,
    collected_files: Optional[dict] = None,
    crawled_start: Optional[int] = None,
    crawled_end: Optional[int] = None,
    search_url: Optional[str] = None,
) -> Path:
    """Record that the requested range completed. Called only on natural finish.

    `requested_end` of 0 means "all pages"; the resolved end is in completed_end.

    A worker of a parallel run writes this into its own directory and it proves
    only that worker's slice; `aggregate_run` is the only writer of a manifest
    that claims a whole run. `source` says which of the two a reader is holding,
    so a slice manifest can never be mistaken for the run's completion.

    `completed_pages` is how much of the *request* is finished, which is not the
    same statement as how much of it this process walked. A worker resumed from
    its checkpoint crawls only the tail, and the pages before the resume point
    were crawled by the invocations that wrote that checkpoint - under this same
    request, which is what `CheckpointManager` records and `crawl` verifies
    before it will resume at all. Both readers of this file want the first
    statement: `aggregate_run` fits slices together by request start, and
    `stage1_runner._check_manifest_covers` asks whether the request it made is
    covered. Writing the second here is what made a legitimate mid-range resume
    unaggregatable - the manifest said 2-2 of an assignment of 1-2.

    `crawled_pages` is the second statement, kept because it is genuinely
    different information: it is what this invocation did, and on a fresh crawl
    it equals `completed_pages`. Nothing decides completion from it.

    `corpus_pages` is how many pages GEO held when this crawl read the result
    count - the number `completed_end` was capped against. It is what lets the
    aggregator tell a slice that stopped early from one that ran out of corpus,
    which are otherwise the same short manifest.

    `search_url` is the GEO query these pages came from. Page numbers only mean
    something against one query, so a manifest that does not name it cannot be
    compared with another run's.

    `collected_files` is what this crawl wrote into SMTX/SRR/META. Those
    directories accumulate across runs, so this is the only record of what a
    single run produced. Like `processed` and `failed` it spans every invocation
    that contributed to `completed_pages`, because those are the pages it is
    reported beside.
    """
    if crawled_start is None:
        crawled_start = completed_start
    if crawled_end is None:
        crawled_end = completed_end
    manifest = {
        "status": "completed",
        "requested_pages": {"start": requested_start, "end": requested_end},
        "completed_pages": {"start": completed_start, "end": completed_end},
        # What this invocation walked. Equal to completed_pages unless a resume
        # picked the request up part way through.
        "crawled_pages": {"start": crawled_start, "end": crawled_end},
        # For an all-pages request (requested_end 0) this is the last page the
        # crawl resolved. Without it a reader cannot tell a finished all-pages
        # crawl from one that stopped after page 1.
        "resolved_end_page": completed_end,
        "items_per_page": items_per_page,
        "processed": processed,
        "failed": failed,
        "written_at": datetime.now().isoformat(),
        # Additive provenance. Readers that predate these keys ignore them.
        "source": "worker",
        "run_id": run_id,
        "worker_id": worker_id,
        "corpus_pages": corpus_pages,
        "collected_files": normalise_collected_files(collected_files),
        "search_url": search_url,
    }
    path = atomic_write_json(Path(output_dir) / COMPLETION_MANIFEST_FILE, manifest)
    logger.info(f"Wrote completion manifest: {path}")
    return path


class RunAggregationError(RuntimeError):
    """Raised when a parallel run's workers do not add up to a completed range.

    Every path out of this is a refusal to write a whole-range manifest. A run
    whose workers left a hole, died, or crawled outside their slice did not
    complete the range, and the only honest record of that is no record.
    """


class RunHadNothingToCrawlError(RunAggregationError):
    """Every worker of the run reported "no page in range" and none failed.

    Same meaning as the crawler's own exit 3, one level up: the request was
    valid, GEO simply holds nothing in it. Kept apart from a failure so the
    orchestrator's exit code stays truthful.
    """


def write_run_plan(output_dir, run_id: str, requested_start: int,
                   requested_end: int, items_per_page: int = DEFAULT_ITEMS_PER_PAGE,
                   distributed_end: Optional[int] = None,
                   available_pages: Optional[int] = None,
                   workers: Optional[int] = None) -> Path:
    """Record what a run set out to do, before any worker starts.

    `requested_*` is what the user asked for. `distributed_end` is what was
    actually handed to workers, which is smaller when the corpus probe found
    GEO holds fewer pages than were requested. Keeping both is what lets the
    merged manifest say "capped" instead of "short".

    `workers` is the divisor the split was made with. The aggregator does not
    read it - it reads the assignments the workers actually recorded - but
    run_parallel_crawl.sh does, to check that a resume asks for the same split
    the run was made with, and all three shell launchers write it. This writer
    omitted it, so a run planned from Python could never be resumed: the
    launcher would refuse it as a plan that does not say how the run was split.
    That refusal is the safe direction, and this is what makes the two writers
    produce the same document.
    """
    plan = {
        "run_id": run_id,
        "requested_pages": {"start": requested_start, "end": requested_end},
        "distributed_end": (distributed_end if distributed_end is not None
                            else requested_end),
        "available_pages": available_pages,
        "items_per_page": items_per_page,
        "workers": workers,
        "created_at": datetime.now().isoformat(),
    }
    return atomic_write_json(
        run_dir_for(output_dir, run_id) / RUN_PLAN_FILE, plan
    )


def write_worker_assignment(output_dir, run_id: str, worker_id: str,
                            start_page: int, end_page: int) -> Path:
    """Record the slice one worker was given, before it starts."""
    scope = resolve_worker_scope(output_dir, run_id=run_id, worker_id=str(worker_id))
    return atomic_write_json(
        scope.worker_dir / WORKER_ASSIGNMENT_FILE,
        {
            "worker_id": str(worker_id),
            "start_page": start_page,
            "end_page": end_page,
        },
    )


def _read_json(path: Path) -> dict:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RunAggregationError(f"{path} does not contain a JSON object")
    return data


def _worker_records(run_dir: Path) -> List[dict]:
    """Every worker directory of a run, in page order, with its real outcome."""
    workers_root = run_dir / WORKERS_DIR_NAME
    if not workers_root.is_dir():
        raise RunAggregationError(f"run {run_dir} has no {WORKERS_DIR_NAME}/ directory")

    records = []
    for worker_dir in sorted(p for p in workers_root.iterdir() if p.is_dir()):
        assignment_path = worker_dir / WORKER_ASSIGNMENT_FILE
        if not assignment_path.exists():
            raise RunAggregationError(
                f"{worker_dir.name} has no {WORKER_ASSIGNMENT_FILE}; the "
                "orchestrator never recorded what it was asked to crawl"
            )
        assignment = _read_json(assignment_path)

        exit_path = worker_dir / WORKER_EXIT_CODE_FILE
        if not exit_path.exists():
            raise RunAggregationError(
                f"{worker_dir.name} left no {WORKER_EXIT_CODE_FILE}; it never "
                "finished, so this run cannot be declared complete"
            )
        try:
            exit_code = int(exit_path.read_text().strip())
        except ValueError as e:
            raise RunAggregationError(
                f"{worker_dir.name} recorded an unreadable exit code: {e}"
            ) from e

        manifest = None
        manifest_path = worker_dir / COMPLETION_MANIFEST_FILE
        if manifest_path.exists():
            try:
                manifest = _read_json(manifest_path)
            except (json.JSONDecodeError, OSError) as e:
                raise RunAggregationError(
                    f"{worker_dir.name} left an unreadable manifest: {e}"
                ) from e

        records.append({
            "worker_id": str(assignment.get("worker_id", worker_dir.name)),
            "assigned_start": int(assignment["start_page"]),
            "assigned_end": int(assignment["end_page"]),
            "exit_code": exit_code,
            "manifest": manifest,
            "dir": worker_dir,
        })

    records.sort(key=lambda r: r["assigned_start"])
    return records


def _verified_coverage(run_id: str, requested_start: int, requested_end: int,
                       covered_end: int, corpus_pages: Optional[int]) -> str:
    """Say whether a finished run is complete or capped - or refuse to say.

    A run that covered less than it was asked for is one of two things, and
    they are not distinguishable from the coverage alone:

      the corpus ended     GEO holds fewer pages than were requested and the
                           run reached the last of them. All the work that
                           exists was done. This is a cap.

      the run stopped      pages the request covers, and GEO has, were never
                           crawled. This is a truncated run, and writing a
                           whole-range manifest for it is exactly the false
                           success the aggregator exists to prevent.

    So the shortfall has to be measured against a page count this run actually
    observed on GEO - the orchestrator's probe or a worker's own cap. Where
    those disagree the smaller wins: the corpus changes daily, a worker reads
    it later than the probe does, and the run can only ever have covered the
    smaller of the two.

    When nothing observed the corpus - the probe failed and no worker recorded
    what it capped against - the two cases stay indistinguishable and the run
    is refused. Silence is not evidence of a cap, and a run that quietly
    inherited the benefit of the doubt is how this defect worked.
    """
    # A bounded request that reached its end needs no excuse. An all-pages
    # request (end 0) has no end of its own: only GEO's corpus ends it.
    if requested_end > 0 and covered_end >= requested_end:
        return "complete"

    if corpus_pages is None:
        raise RunAggregationError(
            f"run {run_id} covered pages {requested_start}-{covered_end} of the "
            f"{requested_start}-{requested_end or 'all'} it requested, and "
            "nothing recorded how many pages GEO holds: the run plan has no "
            "available_pages (the corpus probe was skipped or failed) and no "
            "worker manifest records the corpus_pages it capped against. A "
            "capped run and a truncated one cannot be told apart from that, so "
            "no whole-range manifest was written"
        )

    if covered_end < corpus_pages:
        missing_end = min(corpus_pages, requested_end) if requested_end > 0 \
            else corpus_pages
        raise RunAggregationError(
            f"run {run_id} covered pages {requested_start}-{covered_end} but "
            f"GEO holds {corpus_pages} page(s) for this query, so pages "
            f"{covered_end + 1}-{missing_end} of the request exist and were "
            "crawled by nobody. That is a truncated run, not a capped one, so "
            "no whole-range manifest was written"
        )

    return "complete" if requested_end <= 0 else "capped"


def aggregate_run(output_dir, run_id: str) -> dict:
    """Merge one run's workers into a single whole-range completion manifest.

    Runs after every worker has exited, in one place, exactly once. It asserts,
    and refuses to write anything unless all of it holds:

      * every worker recorded a real exit code, so none is still running or
        vanished
      * every worker that exited 0 left a manifest marked completed
      * each such manifest starts exactly where the worker was assigned to
        start and never runs past its assigned end - no worker crawled into
        another's slice. ``completed_pages`` is how much of the assignment is
        finished, which for a worker resumed part way through is still the
        whole assignment: the pages before the resume point were crawled by
        the earlier invocations of that same worker, under that same request,
        and the checkpoint it continued is what says so. The tail an invocation
        walked is its ``crawled_pages`` and is not what completion is judged on
      * the completed slices, in page order, are contiguous from the requested
        start - a worker that found nothing in range (exit 3) may only sit at
        the tail, where GEO's corpus ends, never in the middle
      * no worker exited anything else

    The result covers ``requested_start .. covered_end``. Coverage short of the
    requested end is accepted only when something proves GEO holds no more:
    the page count the orchestrator probed before distributing (the run plan's
    ``available_pages``) or the count a worker read off GEO and capped itself
    against (its manifest's ``corpus_pages``). Reaching that count is a cap and
    completes; falling short of it is a truncated run and is refused; and when
    no count is known at all the two cannot be told apart, which is also a
    refusal - an unverifiable cap must not be recorded as a verified one.

    Excusing the shortfall outright would write a run that covered pages 1-2
    of the 1-4 it planned as a completed run of the whole request with
    ``resolved_end_page`` 2, indistinguishable from a run that reached the end
    of GEO's corpus.

    Raises RunAggregationError, and writes no manifest, in every other case.
    """
    output_dir = Path(output_dir)
    run_dir = run_dir_for(output_dir, run_id)
    plan_path = run_dir / RUN_PLAN_FILE
    if not plan_path.exists():
        raise RunAggregationError(f"run {run_id} has no {RUN_PLAN_FILE}")
    plan = _read_json(plan_path)

    requested = plan.get("requested_pages") or {}
    requested_start = int(requested.get("start", 1))
    requested_end = int(requested.get("end", 0))
    items_per_page = int(plan.get("items_per_page", DEFAULT_ITEMS_PER_PAGE))

    records = _worker_records(run_dir)
    if not records:
        raise RunAggregationError(f"run {run_id} started no worker at all")

    covered_next = requested_start
    processed = 0
    failed = 0
    summaries = []
    failures = []
    # Every page count this run actually observed on GEO: the orchestrator's
    # probe, plus what each completed worker capped itself against.
    corpus_observations = []
    if isinstance(plan.get("available_pages"), int) \
            and not isinstance(plan.get("available_pages"), bool):
        corpus_observations.append(int(plan["available_pages"]))
    collected = {name: 0 for name in COLLECTED_FILE_BUCKETS}
    # The distinct GEO queries this run's workers report having crawled.
    search_urls = set()

    for record in records:
        summary = {
            "worker_id": record["worker_id"],
            "assigned_pages": {"start": record["assigned_start"],
                               "end": record["assigned_end"]},
            "exit_code": record["exit_code"],
        }

        if record["exit_code"] == 3:
            # "Valid request, nothing in range." Legitimate only past the end of
            # what GEO holds; a hole in the middle is caught by the contiguity
            # check on the next completed worker.
            summary["status"] = "no_pages_in_range"
            summary["completed_pages"] = None
            summaries.append(summary)
            continue

        if record["exit_code"] != 0:
            summary["status"] = "failed"
            summaries.append(summary)
            failures.append(
                f"worker {record['worker_id']} (pages {record['assigned_start']}-"
                f"{record['assigned_end']}) exited {record['exit_code']}"
            )
            continue

        manifest = record["manifest"]
        if manifest is None or manifest.get("status") != "completed":
            summary["status"] = "failed"
            summaries.append(summary)
            failures.append(
                f"worker {record['worker_id']} exited 0 but left no completed "
                "manifest, so it stopped before finishing its pages"
            )
            continue

        completed = manifest.get("completed_pages") or {}
        got_start, got_end = completed.get("start"), completed.get("end")
        if not isinstance(got_start, int) or not isinstance(got_end, int) \
                or isinstance(got_start, bool) or isinstance(got_end, bool):
            summary["status"] = "failed"
            summaries.append(summary)
            failures.append(
                f"worker {record['worker_id']} left a manifest with no "
                "well-formed completed_pages"
            )
            continue

        if got_start != record["assigned_start"] or got_end > record["assigned_end"]:
            summary["status"] = "failed"
            summaries.append(summary)
            failures.append(
                f"worker {record['worker_id']} was assigned pages "
                f"{record['assigned_start']}-{record['assigned_end']} but its "
                f"manifest covers {got_start}-{got_end}. completed_pages is how "
                "much of the assignment is finished, not the tail one "
                "invocation happened to walk - a resumed worker records that in "
                "crawled_pages"
            )
            continue

        if got_start != covered_next:
            summary["status"] = "failed"
            summaries.append(summary)
            failures.append(
                f"pages {covered_next}-{got_start - 1} of the requested range "
                f"were crawled by nobody; worker {record['worker_id']} picks up "
                f"at {got_start}"
            )
            continue

        covered_next = got_end + 1
        processed += int(manifest.get("processed", 0) or 0)
        failed += int(manifest.get("failed", 0) or 0)

        corpus_pages = manifest.get("corpus_pages")
        if isinstance(corpus_pages, int) and not isinstance(corpus_pages, bool) \
                and corpus_pages > 0:
            corpus_observations.append(corpus_pages)

        worker_url = manifest.get("search_url")
        if worker_url:
            search_urls.add(worker_url)

        worker_files = normalise_collected_files(manifest.get("collected_files"))
        for name in COLLECTED_FILE_BUCKETS:
            collected[name] += worker_files[name] if worker_files else 0

        summary["status"] = "completed"
        summary["completed_pages"] = {"start": got_start, "end": got_end}
        summary["processed"] = manifest.get("processed", 0)
        summary["failed"] = manifest.get("failed", 0)
        summary["collected_files"] = worker_files
        summaries.append(summary)

    if failures:
        raise RunAggregationError(
            "this run did not complete its requested range: " + "; ".join(failures)
        )

    covered_end = covered_next - 1
    if covered_end < requested_start:
        raise RunHadNothingToCrawlError(
            f"no page of the requested range {requested_start}-{requested_end} "
            f"exists on GEO right now, so no worker had anything to crawl"
        )

    # Page numbers only mean something against one query, so workers that
    # crawled different queries are not slices of one range however neatly
    # their page numbers fit together. Manifests written before this key
    # existed carry no URL and are not counted as a disagreement.
    if len(search_urls) > 1:
        raise RunAggregationError(
            "this run's workers crawled different GEO queries, so their pages "
            "are not slices of one range: " + "; ".join(sorted(search_urls))
        )

    corpus_pages = min(corpus_observations) if corpus_observations else None
    coverage = _verified_coverage(run_id, requested_start, requested_end,
                                  covered_end, corpus_pages)

    manifest = {
        "status": "completed",
        "requested_pages": {"start": requested_start, "end": requested_end},
        "completed_pages": {"start": requested_start, "end": covered_end},
        # When the corpus turned out smaller than the request, this is the end
        # the run actually reached, and the shortfall is a cap - the reader in
        # cycle_test/utils/stage1_runner.py tells the two apart on exactly this.
        # _verified_coverage above is what earns the right to write it: a run
        # that stopped short of the corpus never reaches this line.
        "resolved_end_page": covered_end,
        "items_per_page": items_per_page,
        "processed": processed,
        "failed": failed,
        "written_at": datetime.now().isoformat(),
        "source": "aggregated",
        "run_id": run_id,
        "worker_id": None,
        # "complete" - the whole request was covered; "capped" - GEO holds
        # fewer pages than were asked for and the run reached the end of them.
        "coverage": coverage,
        "corpus_pages": corpus_pages,
        # The query these pages came from, carried up from the workers.
        "search_url": next(iter(search_urls)) if search_urls else None,
        # What this run produced, summed from its workers. Not the contents of
        # SMTX/SRR/META, which belong to every run that ever wrote there.
        "collected_files": normalise_collected_files(collected),
        "workers": summaries,
    }

    atomic_write_json(run_dir / COMPLETION_MANIFEST_FILE, manifest)
    # The top-level copy is the one every existing reader looks for. It points
    # at the newest run that completed; the per-run manifest above is what
    # preserves each earlier run's own evidence.
    atomic_write_json(output_dir / COMPLETION_MANIFEST_FILE, manifest)
    return manifest


class CheckpointManager:
    """Manages checkpoint save/load for resume functionality.

    A checkpoint says where one crawl got to. It only says that about the crawl
    request it was written for, so it records that request as well: continuing
    it is what lets the completion manifest claim the pages *before* the resume
    point, and a checkpoint that belongs to a different request is no evidence
    about those pages at all. `requested_pages` is bound once by `crawl` through
    `bind_request` and written into every save.
    """

    def __init__(self, output_dir: Path):
        self.checkpoint_file = output_dir / CHECKPOINT_FILE
        # The request this checkpoint's progress is progress through. None until
        # crawl binds it, which it does before any page is processed.
        self.requested_pages: Optional[dict] = None
        self.data = {
            'last_page': 0,
            'last_gse_index': -1,
            'downloaded_gse': [],
            'failed_gse': [],
            'total_pages': 0,
            'items_per_page': 500,
            'requested_pages': None,
            'collected_files': None,
            'timestamp': None
        }

    def bind_request(self, start_page: int, end_page: int) -> None:
        """Say which crawl request the saves from here on belong to."""
        self.requested_pages = {'start': start_page, 'end': end_page}

    def load(self) -> bool:
        """Load checkpoint from file. Returns True if loaded successfully."""
        if not self.checkpoint_file.exists():
            return False

        try:
            with open(self.checkpoint_file, 'r') as f:
                self.data = json.load(f)
            logger.info(f"Checkpoint loaded: page {self.data['last_page']}, "
                       f"GSE index {self.data['last_gse_index']}, "
                       f"downloaded {len(self.data['downloaded_gse'])}")
            return True
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}")
            return False

    def save(self, page: int, gse_index: int, downloaded: List[str],
             failed: List[str], total_pages: int = 0,
             collected: Optional[dict] = None) -> None:
        """Save checkpoint to file.

        `collected` is the running per-bucket tally of files published so far.
        It is carried here for the same reason `downloaded` is: a resume that
        did not restore it would report the files of its own invocation beside a
        manifest that claims every page of the request, and a run whose first
        invocation collected everything would then read as having collected
        nothing.
        """
        self.data = {
            'last_page': page,
            'last_gse_index': gse_index,
            'downloaded_gse': downloaded,
            'failed_gse': failed,
            'total_pages': total_pages,
            'items_per_page': self.data.get('items_per_page', 500),
            'requested_pages': self.requested_pages,
            'collected_files': dict(collected) if collected else None,
            'timestamp': datetime.now().isoformat()
        }

        try:
            # Atomic: this file is rewritten after every GSE, and a reader that
            # caught it mid-write got a truncated resume state.
            atomic_write_json(self.checkpoint_file, self.data)
        except Exception as e:
            logger.warning(f"Failed to save checkpoint: {e}")

    def clear(self) -> None:
        """Remove checkpoint file"""
        if self.checkpoint_file.exists():
            self.checkpoint_file.unlink()
            logger.info("Checkpoint cleared")


class SimpleCrawler:
    # How long a page transition may take before the old results DOM must
    # have been replaced. Tests shrink this; production keeps it generous.
    NAVIGATION_TIMEOUT = 20

    def __init__(self, output_dir: str = 'crawl_output', headless: bool = True,
                 scope: Optional[WorkerScope] = None,
                 search_url: Optional[str] = None):
        self.output_dir = Path(output_dir)
        self.headless = headless
        # One URL per crawl, resolved once here. Logged and written into the
        # completion manifest, so what a run collected can be traced to the
        # query that selected it.
        self.search_url = search_url or resolve_search_url()
        if self.search_url != DEFAULT_GEO_SEARCH_URL:
            logger.info(f"GEO query for this crawl: {self.search_url}")
        self.scope = scope if scope is not None else resolve_worker_scope(self.output_dir)
        self.driver = None
        # The tab holding the GEO search results. GSE pages open in their own
        # tabs so this one never navigates away; if it did, page 2+ would look
        # for pagination controls on a detail page.
        self.search_window_handle = None
        self.setup_directories()
        # Not the process's current working directory: run_parallel_crawl.sh
        # gives the same one to every worker of a run, and the cleanup glob
        # below deletes by pattern, so one worker there could delete a file
        # another had just downloaded. This directory belongs to this process
        # alone.
        self.download_dir = self._prepare_scratch_dir(
            self.scope.download_dir, f'/tmp/genoar-downloads-{self.scope.identity}',
            'download'
        )
        self.setup_driver()

        self.checkpoint = CheckpointManager(self.scope.state_dir)

        # Tracking
        self.downloaded_gse: List[str] = []
        self.failed_gse: List[str] = []
        # What this process wrote into the shared SMTX/SRR/META directories.
        # Those directories hold every run ever made into this output
        # directory - deliberately, earlier results survive a new run - so
        # counting the files in them says nothing about this run. This does.
        self.collected_files = {name: 0 for name in COLLECTED_FILE_BUCKETS}

        # Keywords for single-cell detection
        self.keywords = ['chromium', '10x', 'cell ranger', 'single-cell rna',
                        '.h5', 'barcodes.tsv.gz', 'features.tsv.gz', 'matrix.mtx.gz']
        self.target_rows = ["!Series_title", "!Series_summary", "!Series_overall_design",
                          "!Series_supplementary_file", "!Sample_extract_protocol_ch1",
                          "!Sample_data_processing"]

    def _record_collected(self, target: Path) -> None:
        """Count one file this crawl published into the output directory.

        Called at the moment a download becomes a result - after the move into
        SMTX/SRR/META, never before - so the number is what this process
        actually produced rather than what it attempted.
        """
        bucket = Path(target).parent.name
        if bucket in self.collected_files:
            self.collected_files[bucket] += 1
        else:
            logger.debug(f"Collected file outside the known buckets: {target}")

    def setup_directories(self) -> None:
        """Create output directories with permission error handling"""
        for subdir in COLLECTED_FILE_BUCKETS:
            subdir_path = self.output_dir / subdir
            try:
                subdir_path.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                logger.error(f"Cannot create directory: {subdir_path}")
                logger.error("Check volume mount permissions or run with --user $(id -u):$(id -g)")
                raise
        try:
            self.scope.state_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            logger.error(f"Cannot create worker state directory: {self.scope.state_dir}")
            raise
        logger.info(f"Output directories created at {self.output_dir}")
        if self.scope.assigned:
            logger.info(
                f"Worker {self.scope.worker_id} of run {self.scope.run_id}; "
                f"state directory {self.scope.state_dir}"
            )

    def _prepare_scratch_dir(self, preferred: Path, fallback: str,
                             kind: str) -> Path:
        """Create a private scratch directory, or fall back to /tmp.

        Same rule as the Chrome profile: only "this location cannot be used"
        errors (no permission, read-only root, nonexistent parent) take the
        fallback. A full or failing disk is a real problem and must surface.
        """
        try:
            preferred.mkdir(parents=True, exist_ok=True)
            return preferred
        except OSError as e:
            if not _profile_dir_unusable(e):
                raise
            logger.warning(
                f"Chrome {kind} dir {preferred} unusable ({e}); using {fallback}"
            )
            fallback_path = Path(fallback)
            fallback_path.mkdir(parents=True, exist_ok=True)
            return fallback_path

    def setup_driver(self) -> None:
        """Setup Chrome driver with proper error handling"""
        options = Options()
        if self.headless:
            options.add_argument("--headless")
        options.add_argument("--window-size=1920,1080")
        options.add_argument('--no-sandbox')
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        # Docker container optimization
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-gpu')
        # Port 0 lets Chrome pick a free one. A fixed 9222 was fine for a
        # single crawler and a collision for every worker after the first.
        options.add_argument('--remote-debugging-port=0')
        options.add_argument('--disable-background-timer-throttling')
        options.add_argument('--disable-renderer-backgrounding')
        options.add_argument('--disable-backgrounding-occluded-windows')

        # User data directory - always this process's own, and derived from the
        # identity the orchestrator assigned, never from the PID. Keying it on
        # os.getpid() looked per-worker and was not: every container's
        # entrypoint process is PID 1, so four containers bind-mounting the same
        # /data all took /data/.chrome/worker-1 and every Chrome after the first
        # died on the profile's SingletonLock.
        # The /tmp fallback stays: only "this location cannot be used" errors
        # take it (permissions, read-only, nonexistent root — macOS' sealed /
        # included); a full or failing disk is a real problem and must surface.
        chrome_data_dir = self.scope.profile_dir
        try:
            chrome_data_dir.mkdir(parents=True, exist_ok=True)
            options.add_argument(f'--user-data-dir={chrome_data_dir}')
        except OSError as e:
            if not _profile_dir_unusable(e):
                raise
            logger.warning(f"Chrome profile dir {chrome_data_dir} unusable ({e}); using /tmp")
            # The fallback carries the same identity, so two containers that both
            # fall back still land in different directories.
            chrome_data_dir = Path(f'/tmp/chrome-{self.scope.identity}')
            options.add_argument(f'--user-data-dir={chrome_data_dir}')
            options.add_argument('--crash-dumps-dir=/tmp')
        # Remembered so cleanup removes the profile that was actually used, not
        # the one that could not be created.
        self.chrome_profile_dir = chrome_data_dir
        logger.info(f"Chrome profile: {chrome_data_dir}")

        # For networks that drop HTTP/2 streams mid-session: TLS handshakes
        # pass, then pages stall part way through. Falling back to HTTP/1.1 for
        # the crawler run gets those pages; GEO returns the same content either
        # way. Off unless explicitly requested; a value that is neither on nor
        # off refuses to start, because whoever sets this is running a
        # comparison and a typo that silently kept HTTP/2 on would corrupt it.
        raw_http2 = os.environ.get('GENOAR_CHROME_DISABLE_HTTP2', '')
        http2_value = raw_http2.strip().lower()
        if http2_value in ('1', 'true'):
            options.add_argument('--disable-http2')
            logger.info("HTTP/2 disabled for this crawl (GENOAR_CHROME_DISABLE_HTTP2)")
        elif http2_value not in ('', '0', 'false'):
            raise ValueError(
                f"GENOAR_CHROME_DISABLE_HTTP2 must be 1/true or 0/false, "
                f"got {raw_http2!r}"
            )

        # Additional options for running as non-root user
        options.add_argument('--disable-software-rasterizer')
        # --single-process is not used: it crashes newer Chrome versions.

        # Performance optimization
        options.page_load_strategy = 'eager'
        prefs = {
            'profile.default_content_setting_values': {
                'images': 2,
                'plugins': 2,
                'javascript': 1
            },
            "download.default_directory": str(self.download_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True
        }
        options.add_experimental_option("prefs", prefs)

        # Chrome binary path. Left unset when nothing is found so Selenium can
        # still try its own lookup. A missing browser then surfaces through
        # the driver check below, and the warning here says where this looked.
        chrome_binary = resolve_executable(
            'CHROME_BINARY_PATH', CHROME_BINARY_NAMES, CHROME_BINARY_CANDIDATES
        )
        if chrome_binary:
            options.binary_location = chrome_binary
            logger.info(f"Chrome binary: {chrome_binary}")
        else:
            logger.warning(
                "No Chrome binary found; searched "
                f"{_searched_description('CHROME_BINARY_PATH', CHROME_BINARY_NAMES, CHROME_BINARY_CANDIDATES)}. "
                "Letting Selenium look for one."
            )

        chrome_driver = resolve_executable(
            'CHROME_DRIVER_PATH', CHROME_DRIVER_NAMES, CHROME_DRIVER_CANDIDATES
        )
        if not chrome_driver:
            raise ChromeSetupError(
                "No chromedriver found; searched "
                f"{_searched_description('CHROME_DRIVER_PATH', CHROME_DRIVER_NAMES, CHROME_DRIVER_CANDIDATES)}. "
                "Set CHROME_DRIVER_PATH, or build the image from a Dockerfile "
                "in this repository, which installs one."
            )
        logger.info(f"ChromeDriver: {chrome_driver}")

        from selenium.webdriver.chrome.service import Service
        service = Service(chrome_driver)

        try:
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(self.download_dir)
            })
            logger.info("Chrome driver initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Chrome driver: {e}")
            raise

    def ensure_driver(self) -> None:
        """Ensure driver is alive, restart if crashed.

        Only before the search context exists. A restarted Chrome has no GEO
        search tab, so restarting mid-crawl and carrying on would process
        whatever the fresh browser shows — the silent wrong-page path this
        module refuses.
        """
        try:
            _ = self.driver.current_url
        except WebDriverException as exc:
            if self.search_window_handle is not None:
                raise SearchContextLostError(
                    "Chrome session died after the GEO search was opened"
                ) from exc
            logger.warning("Driver crashed, restarting...")
            try:
                self.driver.quit()
            except Exception:
                pass
            self.setup_driver()

    def retry_with_backoff(self, func: Callable, max_retries: int = 3,
                          base_delay: float = 2.0, description: str = "operation") -> Any:
        """Execute function with exponential backoff retry"""
        last_exception = None

        for attempt in range(max_retries):
            try:
                return func()
            except SearchContextLostError:
                # The search tab is gone; every retry would run against the
                # wrong browser state. Fail now rather than plausibly.
                raise
            except Exception as e:
                last_exception = e
                if attempt == max_retries - 1:
                    logger.error(f"{description} failed after {max_retries} attempts: {e}")
                    raise

                delay = base_delay * (2 ** attempt)
                logger.warning(f"{description} failed (attempt {attempt + 1}/{max_retries}), "
                              f"retrying in {delay}s: {e}")
                time.sleep(delay)

                # Check and recover driver if needed
                self.ensure_driver()

        raise last_exception

    def handle_popups(self) -> bool:
        """Handle various popups including QSIWebResponsive"""
        try:
            # Method 1: Try to close QSI survey
            try:
                close_buttons = self.driver.find_elements(By.XPATH,
                    "//div[contains(@class, 'QSIWebResponsive')]//button[contains(@class, 'close') or contains(@aria-label, 'close')]")
                if close_buttons:
                    close_buttons[0].click()
                    logger.info("Closed QSI survey popup")
                    # Let the overlay unmount before the caller clicks again.
                    time.sleep(1)
                    return True
            except Exception as e:
                logger.debug(f"QSI close button not found: {e}")

            # Method 2: Use JavaScript to remove overlay
            try:
                self.driver.execute_script("""
                    var elements = document.querySelectorAll('.QSIWebResponsive-creative-container-fade');
                    elements.forEach(function(element) {
                        element.remove();
                    });
                """)
                logger.debug("Removed QSI overlay via JavaScript")
                return True
            except Exception as e:
                logger.debug(f"JavaScript overlay removal failed: {e}")

            # Method 3: Try ESC key
            try:
                webdriver.ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
                # Give the overlay a chance to react to ESC.
                time.sleep(0.5)
            except Exception as e:
                logger.debug(f"ESC key press failed: {e}")

            # Method 4: Handle "No Thanks" button
            try:
                no_thanks = self.driver.find_elements(By.XPATH,
                    "//button[contains(text(), 'No Thanks') or contains(text(), 'No thanks')]")
                if no_thanks:
                    no_thanks[0].click()
                    logger.info("Clicked 'No Thanks' button")
                    # Let the overlay unmount before the caller clicks again.
                    time.sleep(1)
                    return True
            except Exception as e:
                logger.debug(f"No Thanks button not found: {e}")

        except Exception as e:
            logger.debug(f"Error handling popups: {e}")

        return False

    def wait_for_download(self, filename_pattern: str, timeout: int = 20) -> Optional[Path]:
        """Wait for file to be downloaded - handles both .txt and .csv"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            files = list(self.download_dir.glob(filename_pattern))
            if 'SraRunTable' in filename_pattern:
                files.extend(list(self.download_dir.glob('SraRunTable*.csv')))

            complete_files = [f for f in files
                            if not str(f).endswith('.crdownload')
                            and not str(f).endswith('.tmp')]
            if complete_files:
                return complete_files[0]
            time.sleep(0.5)
        return None

    def safe_click(self, element, description: str = "element") -> bool:
        """Safely click an element with popup handling"""
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                element.click()
                return True
            except ElementClickInterceptedException:
                logger.warning(f"Click intercepted for {description}, attempt {attempt + 1}")
                self.handle_popups()
                # Let the popup dismissal above take effect before retrying.
                time.sleep(1)
                if attempt == max_attempts - 1:
                    try:
                        self.driver.execute_script("arguments[0].click();", element)
                        logger.info(f"Used JavaScript click for {description}")
                        return True
                    except Exception as e:
                        logger.error(f"Failed to click {description} even with JavaScript: {e}")
                        return False
            except Exception as e:
                logger.warning(f"Click failed for {description}: {e}")
                if attempt == max_attempts - 1:
                    return False
        return False

    def _require_checkpoint_belongs_here(self, start_page: int,
                                         end_page: int) -> None:
        """Refuse a checkpoint that is not progress through *this* request.

        Resuming makes the completion manifest claim the whole request, not just
        the tail this invocation crawls, because the pages before the resume
        point were crawled by whoever wrote the checkpoint. That is only true
        while the checkpoint was written under the same request. A checkpoint
        left by `1 9` continued as `5 9` would otherwise let the manifest claim
        pages 1-4 that this configuration never asked anybody to crawl, and one
        left by `5 9` continued as `1 9` would let it claim pages 1-4 nobody
        crawled at all - which is the false whole-range completion evidence the
        aggregator exists to prevent, arriving one level lower down.

        A checkpoint that does not say which request it belongs to cannot be
        told apart from either, so it is refused as well rather than given the
        benefit of the doubt. `--clear-checkpoint` starts the request over.

        The start page is what is compared, because the start page is what the
        claim rests on: the pages taken on trust are the ones between the
        request's start and the resume point, and only the start decides which
        those are. The end is recorded too and is checked elsewhere - a
        checkpoint that resumes past this request's end is refused below, and
        run_parallel_crawl.sh holds a parallel resume to the whole page request
        its plan records, because there the end also decides the split. Making
        this one compare the end as well would refuse the documented
            genoar_crawler.py 1 5     # crashes
            genoar_crawler.py --resume
        for no reason: page 1 was crawled either way.
        """
        recorded = self.checkpoint.data.get('requested_pages')
        if isinstance(recorded, dict) and recorded.get('start') == start_page:
            return
        if recorded is None:
            raise ValueError(
                f"{self.checkpoint.checkpoint_file} does not record which crawl "
                "request it is progress through, so it cannot show that the "
                f"pages before it were crawled for the request {start_page}-"
                f"{end_page or 'all'} being resumed. Re-run without --resume, "
                "or with --clear-checkpoint, to crawl this request from its start"
            )
        was = recorded.get('start') if isinstance(recorded, dict) else recorded
        raise ValueError(
            f"{self.checkpoint.checkpoint_file} is progress through a crawl that "
            f"started at page {was}, not at page {start_page} where this request "
            "starts - it says nothing about the pages this request would take "
            "from it on trust"
        )

    def crawl(self, start_page: int = 1, end_page: int = 0,
              items_per_page: int = 500, resume: bool = False) -> None:
        """
        Main crawling function

        Args:
            start_page: Starting page number (1-indexed)
            end_page: Ending page number (0 = auto-detect all pages)
            items_per_page: Number of items per page
            resume: Whether to resume from checkpoint
        """
        validate_page_request(start_page, end_page, items_per_page)

        # Every checkpoint written from here on says it belongs to this request.
        self.checkpoint.bind_request(start_page, end_page)

        # Load checkpoint if resuming
        resume_page = start_page
        resume_gse_index = -1

        if resume and self.checkpoint.load():
            self._require_checkpoint_belongs_here(start_page, end_page)
            resume_page = self.checkpoint.data['last_page']
            resume_gse_index = self.checkpoint.data['last_gse_index']
            self.downloaded_gse = self.checkpoint.data.get('downloaded_gse', [])
            self.failed_gse = self.checkpoint.data.get('failed_gse', [])
            # The files the earlier invocations of this request published. The
            # manifest reports these beside pages they were collected from.
            restored = self.checkpoint.data.get('collected_files') or {}
            for name in COLLECTED_FILE_BUCKETS:
                self.collected_files[name] = int(restored.get(name, 0) or 0)
            logger.info(f"Resuming from page {resume_page}, GSE index {resume_gse_index}")

        url = self.search_url

        try:
            self.retry_with_backoff(
                lambda: self.driver.get(url),
                description="Initial page load"
            )
            # The eager page load strategy returns before GEO renders the
            # result count header that get_total_count reads below.
            time.sleep(3)
            self._capture_search_context()

            total_count = self.get_total_count()
            if total_count <= 0:
                raise PageValidationError(
                    "GEO reported no results for the fixed query — not a "
                    "corpus this crawl can proceed on"
                )
            total_pages = (total_count + items_per_page - 1) // items_per_page

            logger.info(f"Total results: {total_count}, Total pages: {total_pages}")

            actual_end_page = end_page if end_page > 0 else total_pages
            actual_start_page = resume_page if resume else start_page

            # An end page past the results is a request written against a page
            # count GEO no longer has — its corpus changes daily, and a
            # parallel run slices a range that was true when it launched. Cap
            # it to what exists and say so loudly.
            if actual_end_page > total_pages:
                logger.warning(
                    f"Requested end page {actual_end_page} exceeds the "
                    f"{total_pages} pages GEO currently holds for this query; "
                    f"capping the range to page {total_pages}"
                )
                actual_end_page = total_pages

            # A start page past the results leaves nothing to crawl. Not a
            # completed crawl (no page is processed, so no manifest) and not a
            # malfunction either — the caller learns which it was from the exit
            # code, never from an empty success.
            if actual_start_page > total_pages:
                raise EmptyPageRangeError(
                    f"start page {actual_start_page} is past the {total_pages} "
                    f"pages GEO holds for this query — no page falls in the "
                    f"requested range, so nothing was crawled"
                )

            # A checkpoint that resumes outside the requested range is a
            # different matter: before the start it would crawl pages nobody
            # asked for, after the end the page loop would run zero times and
            # still reach the completion manifest.
            if actual_start_page < start_page:
                raise ValueError(
                    f"checkpoint resumes at page {actual_start_page}, before "
                    f"requested start {start_page} — it belongs to a different "
                    "crawl request"
                )
            if actual_start_page > actual_end_page:
                raise ValueError(
                    f"checkpoint resumes at page {actual_start_page}, after "
                    f"requested end {actual_end_page} — nothing would be "
                    "crawled, which is not a completed request"
                )

            logger.info(f"Crawling pages {actual_start_page} to {actual_end_page}")

            for page in range(actual_start_page, actual_end_page + 1):
                logger.info(f"Processing page {page}/{actual_end_page}")

                # Determine starting GSE index for this page
                start_gse_index = 0
                if resume and page == resume_page and resume_gse_index >= 0:
                    start_gse_index = resume_gse_index + 1
                    resume = False  # Only apply resume offset for first page

                # What GEO itself said this page holds, from the count read
                # at the start of the crawl.
                expected_count = min(
                    items_per_page,
                    total_count - (page - 1) * items_per_page,
                )
                self.process_page(page, items_per_page, start_gse_index,
                                  actual_end_page, expected_count)

            # Only reached when the whole requested range finished without an
            # interrupt or error (both re-raise below). This manifest is the
            # signal downstream uses to tell a completed crawl from one that
            # stopped early but still exited a container with code 0.
            # Into this worker's own state directory. A worker of a parallel run
            # writing to the shared output directory is exactly how the run's
            # completion evidence came to describe one worker's slice.
            #
            # completed_pages starts where the *request* starts, not where this
            # invocation did. On a resume those differ, and the difference is
            # not a smaller completion: the pages in between were crawled by the
            # invocations that wrote the checkpoint this one continued, under
            # this same request - `_require_checkpoint_belongs_here` above will
            # not resume any other. Reporting the tail as the completion is what
            # made a worker assigned 1-2 and resumed at page 2 write a manifest
            # covering 2-2, which its own aggregator then rightly refused.
            # What this invocation walked is recorded too, as crawled_pages.
            write_completion_manifest(
                self.scope.state_dir,
                requested_start=start_page,
                requested_end=end_page,
                completed_start=start_page,
                completed_end=actual_end_page,
                crawled_start=actual_start_page,
                crawled_end=actual_end_page,
                items_per_page=items_per_page,
                processed=len(self.downloaded_gse),
                failed=len(self.failed_gse),
                run_id=self.scope.run_id,
                worker_id=self.scope.worker_id,
                # The count this crawl read off GEO and capped itself against,
                # and the files it actually published. Together they are what
                # the aggregator needs to tell a capped slice from a truncated
                # one, and this run's output from the directory's contents.
                corpus_pages=total_pages,
                collected_files=self.collected_files,
                search_url=self.search_url,
            )

        except KeyboardInterrupt:
            logger.info("Crawling interrupted by user")
            raise
        except EmptyPageRangeError:
            # Nothing to do is not a crawl that failed; main() says so with
            # its own exit code. Logging it as a failure here would teach an
            # operator to hunt for a defect that is not there.
            raise
        except Exception as e:
            logger.error(f"Crawling failed: {e}")
            raise

    def probe_corpus_size(self, items_per_page: int = DEFAULT_ITEMS_PER_PAGE) -> dict:
        """How many results and pages this crawl's GEO query holds right now.

        An orchestrator asks this once, before it slices a range across workers.
        Without it each worker divided the *requested* page count and then
        capped itself independently, so asking for 100 pages of a 47-page corpus
        started four workers of which two had nothing to do. The query, the
        filters and the sort are the same ones `crawl` uses - this reads the
        result header and stops.
        """
        self.retry_with_backoff(
            lambda: self.driver.get(self.search_url),
            description="Initial page load"
        )
        # The eager page load strategy returns before GEO renders the
        # result count header that get_total_count reads below.
        time.sleep(3)
        self._capture_search_context()

        total_count = self.get_total_count()
        if total_count <= 0:
            raise PageValidationError(
                f"GEO reported no results for {self.search_url} — not a corpus "
                "this crawl can be planned against"
            )
        total_pages = (total_count + items_per_page - 1) // items_per_page
        return {
            "total_results": total_count,
            "items_per_page": items_per_page,
            "total_pages": total_pages,
        }

    def get_total_count(self) -> int:
        """Get total number of results.

        Returning 0 on failure would quietly become "one page" and, with a
        broken results view, an empty completed crawl. A count this crawl
        cannot read is a crawl it cannot size, so it stops.
        """
        try:
            count_element = self.driver.find_element(By.XPATH, "//h3[@class='result_count left']")
            count_text = count_element.text
            return int(count_text.split('of ')[1].replace(',', ''))
        except Exception as e:
            raise PageValidationError(f"Could not read the GEO result count: {e}") from e

    def process_page(self, page_num: int, items_per_page: int,
                    start_gse_index: int = 0, total_pages: int = 0,
                    expected_count: Optional[int] = None) -> None:
        """Process a single page"""
        self.ensure_driver()
        self.navigate_to_page(page_num, items_per_page)

        gse_entries = self.get_gse_entries(expected_count)
        logger.info(f"Found {len(gse_entries)} GSE entries on page {page_num}")

        for i, entry in enumerate(gse_entries):
            # Skip entries before resume point
            if i < start_gse_index:
                continue

            try:
                gse_id = entry['gse_id']

                if gse_id in self.downloaded_gse:
                    logger.info(f"[{i+1}/{len(gse_entries)}] {gse_id} already downloaded, skipping")
                    continue

                logger.info(f"[{i+1}/{len(gse_entries)}] Processing {gse_id}")

                success = self._process_gse_in_separate_tab(gse_id)

                if success:
                    self.downloaded_gse.append(gse_id)
                else:
                    self.failed_gse.append(gse_id)

                # Save checkpoint after each GSE
                self.checkpoint.save(
                    page=page_num,
                    gse_index=i,
                    downloaded=self.downloaded_gse,
                    failed=self.failed_gse,
                    total_pages=total_pages,
                    collected=self.collected_files
                )

                time.sleep(1)

            except SearchContextLostError:
                # One GSE may fail; the crawl's ability to know where it is
                # may not. Without the search tab every later page would be
                # an empty success.
                raise
            except Exception as e:
                logger.error(f"Error processing entry {i} ({entry.get('gse_id', 'unknown')}): {e}")
                self.failed_gse.append(entry.get('gse_id', f'unknown_{i}'))
                continue

    def _capture_search_context(self) -> None:
        """Remember the tab holding the GEO search results."""
        try:
            self.search_window_handle = self.driver.current_window_handle
        except Exception as e:
            raise SearchContextLostError(
                f"Could not capture the GEO search tab: {e}"
            ) from e

    def _switch_to_search_context(self) -> None:
        """Return to the preserved results tab, or refuse to continue."""
        if self.search_window_handle is None:
            raise SearchContextLostError("GEO search context was never captured")
        try:
            if self.search_window_handle not in self.driver.window_handles:
                raise SearchContextLostError("The GEO search results tab was closed")
            self.driver.switch_to.window(self.search_window_handle)
        except SearchContextLostError:
            raise
        except Exception as e:
            raise SearchContextLostError(
                f"Could not restore the GEO search results tab: {e}"
            ) from e

    def _process_gse_in_separate_tab(self, gse_id: str) -> bool:
        """Process one GSE without navigating the results tab away.

        The GSE detail flow calls `driver.get` several times (GEO page, FTP
        listing, SRA Run Selector). Done in the results tab — as it used to
        be — the last of those is where the browser stays, and the next
        page's navigation runs against it. A separate tab keeps the search
        where the search is.
        """
        self._switch_to_search_context()
        original_handles = set(self.driver.window_handles)

        try:
            self.driver.switch_to.new_window('tab')
            # Chrome scopes download behavior per target in some versions, so
            # the new tab is told where downloads go, same as the profile.
            self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(self.download_dir)
            })
            return self.process_gse_direct(gse_id)
        finally:
            try:
                for handle in list(self.driver.window_handles):
                    if handle not in original_handles:
                        self.driver.switch_to.window(handle)
                        self.driver.close()
                self._switch_to_search_context()
            except Exception as e:
                raise SearchContextLostError(
                    f"Could not restore the search tab after {gse_id}: {e}"
                ) from e

    def navigate_to_page(self, page_num: int, items_per_page: int) -> None:
        """Navigate the preserved search tab to a specific results page.

        It starts from the preserved search tab rather than whatever tab the
        last GSE left the browser on, and lets its final failure be a failure
        rather than a warning: either silence would let a crawl that lost the
        results page record every later page as an empty success. `crawl()`
        only writes its completion manifest when no page raised.
        """
        def _navigate():
            self._switch_to_search_context()
            display_elements = self.driver.find_elements(By.NAME,
                'EntrezSystem2.PEntrez.Gds.Gds_ResultsPanel.Gds_DisplayBar.Display')
            if len(display_elements) > 1:
                display_elements[1].click()
                # Wait for the dropdown to render its page-size options.
                time.sleep(1)
                ps_element = self.driver.find_element(By.ID, f'ps{items_per_page}')
                ps_element.click()
                # Changing the page size re-renders the results list.
                time.sleep(2)

            if page_num > 1:
                # An input keeps whatever was typed into it whether or not the
                # page turned, so reading it back proves nothing. Proof is the
                # old results DOM going stale — GEO replaced the page — and
                # only then is the freshly rendered input's value the server's
                # own answer to "which page is this".
                previous_first = self._first_result_title()
                page_input = self.driver.find_element(By.ID, 'pageno')
                page_input.clear()
                page_input.send_keys(str(page_num))
                page_input.send_keys(Keys.RETURN)
                try:
                    WebDriverWait(self.driver, self.NAVIGATION_TIMEOUT).until(
                        EC.staleness_of(previous_first)
                    )
                except TimeoutException as e:
                    raise PageValidationError(
                        f"GEO's results did not change after asking for page {page_num}"
                    ) from e
                # `get_attribute` gives None when GEO renders the input with no
                # value at all, which is a page this crawl cannot identify —
                # the same answer as a wrong number, not an AttributeError.
                shown = _scraped_text(
                    self.driver.find_element(By.ID, 'pageno').get_attribute('value')
                )
                if shown != str(page_num):
                    raise PageValidationError(
                        f"GEO shows page {shown!r}, not requested page {page_num}"
                    )

        self.retry_with_backoff(_navigate, description=f"Navigate to page {page_num}")

    def _first_result_title(self):
        """The first result row, used as the marker of the current DOM."""
        titles = self.driver.find_elements(By.XPATH, "//p[@class='title']/a[@href]")
        if not titles:
            raise PageValidationError(
                "No GSE results on the current page — not a results view?"
            )
        return titles[0]

    def get_gse_entries(self, expected_count: Optional[int] = None) -> List[dict]:
        """Get all GSE entries from the current results page.

        A page inside the requested range always has entries, so an empty,
        partial, or partly unreadable listing is a page this crawl did not
        actually see. Logging those and returning what was there is how a
        half-rendered page counts as processed, so all of them stop the crawl
        instead.
        """
        entries = []

        geo_titles = self.driver.find_elements(By.XPATH, "//p[@class='title']/a[@href]")
        if not geo_titles:
            raise PageValidationError(
                "No GSE results on the current page — not a results view?"
            )
        if expected_count is not None and len(geo_titles) != expected_count:
            raise PageValidationError(
                f"GEO says this page holds {expected_count} results but the "
                f"page shows {len(geo_titles)} — partial load?"
            )

        seen_gse_ids = set()
        for i, title in enumerate(geo_titles):
            try:
                gse_element = title.find_element(By.XPATH,
                    "./ancestor::div[1]//dl[@class='rprtid']/dd")
                # The accession cell is required: a row without one cannot be
                # crawled, and treating it as absent would drop a dataset the
                # page says exists. An empty cell says so in its own words
                # rather than through the regex.
                gse_id = _scraped_text(gse_element.text)
                if not gse_id:
                    raise ValueError("GSE accession cell is empty")
                if not re.fullmatch(r'GSE\d+', gse_id):
                    raise ValueError(f"not a GSE accession: {gse_id!r}")
                if gse_id in seen_gse_ids:
                    raise ValueError(f"listed twice on one page: {gse_id}")
                seen_gse_ids.add(gse_id)

                entries.append({
                    'gse_id': gse_id,
                    'title': title.text,
                    'href': title.get_attribute('href'),
                    'index': i
                })
            except Exception as e:
                raise PageValidationError(
                    f"Could not extract GSE info for item {i}: {e}"
                ) from e

        return entries

    def process_gse_direct(self, gse_id: str) -> bool:
        """Process GSE by navigating directly to its page"""
        try:
            gse_url = f'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse_id}'

            self.retry_with_backoff(
                lambda: self.driver.get(gse_url),
                description=f"Load GSE page {gse_id}"
            )
            # The eager page load strategy returns before the GSE page body,
            # and with it the SRA Run Selector link looked for below, renders.
            time.sleep(2)

            sra_links = self.driver.find_elements(By.LINK_TEXT, "SRA Run Selector")
            if not sra_links:
                logger.info(f"{gse_id}: No SRA data available")
                return False

            logger.info(f"{gse_id}: Downloading SMTX file")
            smtx_success = self.download_smtx(gse_id)

            if not smtx_success:
                logger.warning(f"{gse_id}: Failed to download SMTX")
                return False

            keywords_found = self.check_keywords(gse_id)

            if keywords_found:
                logger.info(f"{gse_id}: Found keywords: {keywords_found}")
                self.download_sra_data(gse_id)
            else:
                logger.info(f"{gse_id}: No single-cell keywords found")

            return True

        except Exception as e:
            logger.error(f"Error processing {gse_id}: {e}")
            return False

    def download_smtx(self, gse_id: str) -> bool:
        """Download Series Matrix file with retry"""
        series_nnn = gse_id[:-3] + "nnn"
        url = f'https://ftp.ncbi.nlm.nih.gov/geo/series/{series_nnn}/{gse_id}/matrix'

        def _download():
            self.driver.get(url)
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "a"))
            )

            download_links = [
                link for link in self.driver.find_elements(By.TAG_NAME, "a")
                if link.text.endswith('_series_matrix.txt.gz')
            ]

            if not download_links:
                raise ValueError(f"{gse_id}: No matrix files found")

            downloaded_files = []
            for link in download_links:
                link.click()
                logger.info(f"{gse_id}: Downloading {link.text}")

                downloaded_file = self.wait_for_download(link.text, timeout=30)
                if downloaded_file:
                    downloaded_files.append(downloaded_file)
                else:
                    logger.warning(f"{gse_id}: Download timeout for {link.text}")

            if not downloaded_files:
                raise ValueError(f"{gse_id}: No files downloaded")

            return downloaded_files

        try:
            downloaded_files = self.retry_with_backoff(
                _download,
                description=f"Download SMTX for {gse_id}"
            )

            target_file = self.output_dir / 'SMTX' / f"{gse_id}_series_matrix.txt.gz"

            if len(downloaded_files) == 1:
                shutil.move(str(downloaded_files[0]), str(target_file))
            else:
                with open(target_file, 'wb') as merged:
                    for file_path in downloaded_files:
                        with open(file_path, 'rb') as f:
                            shutil.copyfileobj(f, merged, 1024 * 1024 * 10)
                        file_path.unlink()

            self._record_collected(target_file)
            logger.info(f"{gse_id}: SMTX saved to {target_file}")
            return True

        except Exception as e:
            logger.error(f"{gse_id}: Error downloading SMTX: {e}")
            return False

    def check_keywords(self, gse_id: str) -> List[Tuple[str, int]]:
        """Check for single-cell keywords"""
        filepath = self.output_dir / 'SMTX' / f"{gse_id}_series_matrix.txt.gz"

        if not filepath.exists():
            return []

        found_keywords = []
        pattern = '|'.join(self.target_rows)

        for keyword in self.keywords:
            quoted_keyword = f'"{keyword}"'
            command = f"zgrep -E '^({pattern})' {filepath} | grep -c -i {quoted_keyword}"

            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=30
                )

                count = parse_grep_count(result.stdout)
                if count > 0:
                    found_keywords.append((keyword, count))
                    logger.info(f"{gse_id}: Found '{keyword}' {count} times")

            except subprocess.TimeoutExpired:
                logger.warning(f"{gse_id}: Timeout checking keyword {keyword}")
            except Exception as e:
                logger.warning(f"{gse_id}: Error checking keyword {keyword}: {e}")

        return found_keywords

    def download_sra_data(self, gse_id: str) -> None:
        """Download SRA data with improved popup handling"""
        try:
            if f'acc={gse_id}' not in self.driver.current_url:
                gse_url = f'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse_id}'
                self.driver.get(gse_url)
                # The eager page load strategy returns before the GSE page body,
                # and with it the SRA Run Selector link looked for below, renders.
                time.sleep(2)

            sra_links = self.driver.find_elements(By.LINK_TEXT, "SRA Run Selector")
            if not sra_links:
                logger.warning(f"{gse_id}: No SRA Run Selector found")
                return

            sra_links[0].click()
            logger.info(f"{gse_id}: Navigating to SRA Run Selector")
            # The Run Selector is a separate app that fetches its own table;
            # handle_popups below needs that page present to act on.
            time.sleep(5)

            self.handle_popups()

            # Clean up existing files
            for pattern in ['SRR_Acc_List*.txt', 'SraRunTable*.txt', 'SraRunTable*.csv']:
                for f in self.download_dir.glob(pattern):
                    try:
                        f.unlink()
                    except Exception as e:
                        logger.debug(f"Failed to clean up {f}: {e}")

            srr_downloaded = False
            meta_downloaded = False

            # Download SRR accession list
            try:
                srr_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@id='t-acclist-all']"))
                )
                logger.info(f"{gse_id}: Found SRR button")

                if self.safe_click(srr_button, "SRR button"):
                    time.sleep(3)

                    srr_file = self.wait_for_download('SRR_Acc_List*.txt', timeout=10)
                    if srr_file:
                        target = self.output_dir / 'SRR' / f"{gse_id}.txt"
                        shutil.move(str(srr_file), str(target))
                        self._record_collected(target)
                        logger.info(f"{gse_id}: SRR data saved to {target}")
                        srr_downloaded = True
                    else:
                        logger.warning(f"{gse_id}: SRR file not downloaded")

            except TimeoutException:
                logger.info(f"{gse_id}: No SRR button found")
            except Exception as e:
                logger.warning(f"{gse_id}: Error downloading SRR: {e}")

            # Download META data
            try:
                self.handle_popups()

                meta_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@id='t-rit-all']"))
                )
                logger.info(f"{gse_id}: Found META button")

                if self.safe_click(meta_button, "META button"):
                    time.sleep(5)

                    meta_file = self.wait_for_download('SraRunTable*', timeout=20)
                    if meta_file:
                        target = self.output_dir / 'META' / f"{gse_id}_meta.txt"
                        if meta_file.suffix == '.csv':
                            logger.info(f"{gse_id}: Converting CSV to TXT format")
                            try:
                                import pandas as pd
                                df = pd.read_csv(meta_file)
                                df.to_csv(target, sep='\t', index=False)
                                meta_file.unlink()
                            except ImportError:
                                shutil.move(str(meta_file), str(target))
                            except Exception as e:
                                logger.warning(f"{gse_id}: CSV conversion failed: {e}")
                                shutil.move(str(meta_file), str(target))
                        else:
                            shutil.move(str(meta_file), str(target))
                        self._record_collected(target)
                        logger.info(f"{gse_id}: META data saved to {target}")
                        meta_downloaded = True
                    else:
                        logger.warning(f"{gse_id}: META file not downloaded")

            except TimeoutException:
                logger.info(f"{gse_id}: No META button found")
            except Exception as e:
                logger.warning(f"{gse_id}: Error downloading META: {e}")

            # Summary
            if srr_downloaded and meta_downloaded:
                logger.info(f"{gse_id}: Both SRR and META downloaded successfully")
            elif srr_downloaded:
                logger.info(f"{gse_id}: Only SRR downloaded")
            elif meta_downloaded:
                logger.info(f"{gse_id}: Only META downloaded")
            else:
                logger.warning(f"{gse_id}: No SRA data downloaded")

        except Exception as e:
            logger.error(f"{gse_id}: Error downloading SRA data: {e}")

    def cleanup(self) -> None:
        """Clean up resources"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Driver closed")
            except Exception as e:
                logger.debug(f"Error closing driver: {e}")
            self.driver = None

        # Clean up leftover files. The glob is why download_dir must be private:
        # run against a directory shared with another worker it deletes that
        # worker's downloads.
        for pattern in ['SRR_Acc_List*.txt', 'SraRunTable*.txt', 'SraRunTable*.csv', '*_series_matrix.txt.gz']:
            for file in self.download_dir.glob(pattern):
                try:
                    file.unlink()
                    logger.info(f"Cleaned up: {file}")
                except Exception as e:
                    logger.debug(f"Failed to clean up {file}: {e}")

        # Browser scratch. Nothing resumes from a Chrome profile, and one is
        # created per process now, so leaving them behind is unbounded growth in
        # the mounted volume.
        for scratch in (getattr(self, 'chrome_profile_dir', None), self.download_dir):
            if scratch is None:
                continue
            try:
                if scratch.is_dir():
                    shutil.rmtree(scratch)
                    logger.debug(f"Removed scratch directory: {scratch}")
            except OSError as e:
                logger.debug(f"Failed to remove {scratch}: {e}")

        logger.info(f"\nCrawling Summary:")
        logger.info(f"Successfully processed: {len(self.downloaded_gse)} GSE entries")
        logger.info(f"Failed: {len(self.failed_gse)} GSE entries")

        if self.failed_gse:
            logger.info(f"Failed GSE IDs: {', '.join(self.failed_gse[:10])}"
                       + (f"... and {len(self.failed_gse) - 10} more" if len(self.failed_gse) > 10 else ""))


def parse_args():
    """Parse command line arguments with hybrid support"""
    parser = argparse.ArgumentParser(
        description='GENOAR Crawler - NCBI GEO single-cell dataset crawler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Legacy positional arguments (backward compatible)
  python genoar_crawler.py 1 10 true

  # New named arguments
  python genoar_crawler.py --start 1 --end 10
  python genoar_crawler.py --all                    # Crawl all pages
  python genoar_crawler.py --resume                 # Resume from checkpoint
  python genoar_crawler.py --all --output /data     # Custom output directory
        '''
    )

    # New named arguments
    parser.add_argument('--all', action='store_true',
                       help='Crawl all available pages')
    parser.add_argument('--start', type=int, default=None,
                       help='Starting page number (default: 1)')
    parser.add_argument('--end', type=int, default=None,
                       help='Ending page number (0 or omit for auto-detect)')
    parser.add_argument('--headless', action='store_true', default=None,
                       help='Run in headless mode (default: True)')
    parser.add_argument('--no-headless', action='store_true',
                       help='Run with browser visible')
    parser.add_argument('--output', '-o', type=str, default=None,
                       help='Output directory (default: crawl_output)')
    parser.add_argument('--resume', action='store_true',
                       help='Resume from last checkpoint')
    parser.add_argument('--clear-checkpoint', action='store_true',
                       help='Clear existing checkpoint before starting')
    parser.add_argument('--log-level', type=str, default='INFO',
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='Logging level (default: INFO)')
    # Which datasets a run is about. Open so another site can crawl its own
    # organism or query without editing this file; the default is unchanged.
    parser.add_argument('--search-url', type=str, default=None,
                       help=f'GEO search URL to crawl (default: '
                            f'${GEO_SEARCH_URL_ENV}, else the project query)')

    # Orchestration modes. Neither crawls: --report-pages sizes the corpus so a
    # range can be split against what GEO actually holds, --aggregate merges a
    # finished run's workers into one whole-range manifest.
    parser.add_argument('--report-pages', action='store_true',
                       help='Print how many results and pages the GEO query '
                            'currently holds, then exit')
    parser.add_argument('--aggregate', action='store_true',
                       help='Merge the workers of --run-id into one completion '
                            'manifest (no crawling)')
    parser.add_argument('--run-id', type=str, default=None,
                       help='Run identifier (default: $GENOAR_RUN_ID)')
    parser.add_argument('--worker-id', type=str, default=None,
                       help='This worker within the run (default: '
                            '$GENOAR_WORKER_ID)')

    # Legacy positional arguments (for backward compatibility)
    parser.add_argument('start_page', nargs='?', type=int, default=None,
                       help='(Legacy) Starting page number')
    parser.add_argument('end_page', nargs='?', type=int, default=None,
                       help='(Legacy) Ending page number')
    parser.add_argument('headless_flag', nargs='?', type=str, default=None,
                       help='(Legacy) Headless mode flag (true/false)')

    args = parser.parse_args()

    # Resolve hybrid arguments
    # Priority: named args > positional args > defaults

    if args.start is not None:
        start_page = args.start
    elif args.start_page is not None:
        start_page = args.start_page
    else:
        start_page = 1

    # End page
    if args.all:
        end_page = 0  # 0 means auto-detect all
    elif args.end is not None:
        end_page = args.end
    elif args.end_page is not None:
        end_page = args.end_page
    else:
        end_page = start_page  # Default: single page

    if args.no_headless:
        headless = False
    elif args.headless is not None:
        headless = args.headless
    elif args.headless_flag is not None:
        headless = args.headless_flag.lower() != 'false'
    else:
        headless = True

    # Output directory - use /data in Docker, crawl_output locally
    if args.output:
        output_dir = args.output
    elif os.path.exists('/data') and os.path.isdir('/data'):
        output_dir = '/data'  # Docker environment
    else:
        output_dir = 'crawl_output'  # Local environment

    return {
        'start_page': start_page,
        'end_page': end_page,
        'headless': headless,
        'output_dir': output_dir,
        'resume': args.resume,
        'clear_checkpoint': args.clear_checkpoint,
        'log_level': args.log_level,
        'report_pages': args.report_pages,
        'aggregate': args.aggregate,
        'run_id': args.run_id,
        'worker_id': args.worker_id,
        'search_url': resolve_search_url(args.search_url),
    }


def _run_aggregation(output_dir: Path, run_id: Optional[str]) -> int:
    """`--aggregate`: merge a finished run, or say why it did not complete."""
    if not run_id:
        run_id = (os.environ.get('GENOAR_RUN_ID') or '').strip() or None
    if not run_id:
        logger.error("--aggregate needs --run-id (or GENOAR_RUN_ID)")
        return 2

    try:
        manifest = aggregate_run(output_dir, run_id)
    except RunHadNothingToCrawlError as e:
        logger.warning(f"Run {run_id} had nothing to crawl: {e}")
        return 3
    except (RunAggregationError, ValueError) as e:
        logger.error(f"Run {run_id} is not complete: {e}")
        logger.error(
            "No whole-range completion manifest was written. Inspect the "
            "worker directories under "
            f"{run_dir_for(output_dir, run_id) / WORKERS_DIR_NAME}."
        )
        return 1
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Could not read run {run_id}: {e}")
        return 1

    completed = manifest["completed_pages"]
    requested = manifest["requested_pages"]
    collected = manifest.get("collected_files") or {}
    logger.info(
        f"Run {run_id} completed pages {completed['start']}-{completed['end']} "
        f"of the requested {requested['start']}-{requested['end']} "
        f"({manifest['processed']} processed, {manifest['failed']} failed, "
        f"{collected.get('total', 0)} file(s) collected by this run)"
    )
    # One flat line on stdout for the orchestrating shells: what this run
    # covered and what it collected. Before it existed they counted the files
    # in the output directory, which is every run's results, and so a run that
    # collected nothing reported the previous run's files as its own.
    print(RUN_REPORT_PREFIX + json.dumps({
        "run_id": run_id,
        "coverage": manifest.get("coverage"),
        "completed_end": completed["end"],
        "corpus_pages": manifest.get("corpus_pages"),
        "collected_total": int(collected.get("total", 0)),
        "collected_smtx": int(collected.get("SMTX", 0)),
        "collected_srr": int(collected.get("SRR", 0)),
        "collected_meta": int(collected.get("META", 0)),
    }), flush=True)
    return 0


def _run_page_report(output_dir: Path, headless: bool, scope: WorkerScope,
                     search_url: str) -> int:
    """`--report-pages`: size the corpus once so a range can be split by it."""
    crawler = SimpleCrawler(output_dir=str(output_dir), headless=headless,
                            scope=scope, search_url=search_url)
    try:
        report = crawler.probe_corpus_size()
    except Exception as e:
        logger.error(f"Could not read the GEO result count: {e}")
        return 1
    finally:
        crawler.cleanup()

    logger.info(
        f"GEO currently holds {report['total_results']} results "
        f"({report['total_pages']} pages of {report['items_per_page']})"
    )
    print(PAGE_REPORT_PREFIX + json.dumps(report), flush=True)
    return 0


def main():
    """Main entry point.

    Exit codes, which run_parallel_crawl.sh and the cycle-test runner read:
      0   the requested range completed and pages were processed
      1   failure
      2   invalid arguments / invalid page range
      3   valid request, but no page fell in range once capped to what GEO has
      130 interrupted (SIGINT convention)

    `--aggregate` reuses 0/1/2/3 with the same meanings one level up: 0 the run
    completed its range, 3 the run had nothing to crawl, 1 it did not complete.
    """
    config = parse_args()

    # Setup output directory first (for logging)
    output_dir = Path(config['output_dir'])
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"ERROR: Cannot create output directory: {output_dir}")
        print("Solutions:")
        print("  1. Run with --user $(id -u):$(id -g) flag")
        print("  2. Use -o /data to write to mounted volume")
        print("  3. Fix host directory permissions: chmod 777 <host_dir>")
        sys.exit(1)

    if config['aggregate']:
        setup_logging(output_dir, config['log_level'])
        sys.exit(_run_aggregation(output_dir, config['run_id']))

    # Where this process may write its private state. Derived from the identity
    # an orchestrator assigned, so two workers of one run never share a
    # checkpoint, a manifest, a download area or a Chrome profile.
    try:
        scope = resolve_worker_scope(
            output_dir, run_id=config['run_id'], worker_id=config['worker_id']
        )
        scope.state_dir.mkdir(parents=True, exist_ok=True)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(2)
    except PermissionError as e:
        print(f"ERROR: Cannot create worker state directory: {e}")
        sys.exit(1)

    setup_logging(scope.log_dir, config['log_level'])

    if config['report_pages']:
        sys.exit(_run_page_report(output_dir, config['headless'], scope,
                                  config['search_url']))

    # Log configuration
    if config['end_page'] == 0:
        logger.info(f"Starting GENOAR crawler: ALL pages from page {config['start_page']}")
    else:
        logger.info(f"Starting GENOAR crawler: pages {config['start_page']}-{config['end_page']}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Headless mode: {config['headless']}")
    logger.info(f"Resume mode: {config['resume']}")

    # Drop any manifest this process wrote before, so a crawl that dies during
    # driver startup cannot leave a stale manifest reading as this run's
    # completion. crawl() rewrites it on natural completion.
    #
    # Only this worker's own manifest: deleting the shared one at startup was
    # half of how a parallel run lost its completion evidence, since the last
    # worker to write left its slice standing for the whole range. Only the
    # orchestrator touches the shared manifest now.
    stale_manifest = scope.manifest_path()
    if stale_manifest.exists():
        stale_manifest.unlink()

    # Same reasoning for an explicit request to forget the checkpoint.
    if config['clear_checkpoint']:
        stale_checkpoint = scope.checkpoint_path()
        if stale_checkpoint.exists():
            stale_checkpoint.unlink()

    # A nonsensical page range should not cost a Chrome launch to discover.
    try:
        validate_page_request(config['start_page'], config['end_page'])
    except ValueError as e:
        logger.error(f"Invalid crawl range: {e}")
        sys.exit(2)

    crawler = SimpleCrawler(
        output_dir=str(output_dir),
        headless=config['headless'],
        scope=scope,
        search_url=config['search_url'],
    )

    try:
        crawler.crawl(
            start_page=config['start_page'],
            end_page=config['end_page'],
            resume=config['resume']
        )
    except EmptyPageRangeError as e:
        # Nothing was crawled, so no completion manifest exists and exit 0
        # would be a false success. Exit 1 would be a false failure: the
        # request was fine, the pages simply are not there.
        logger.warning(f"Nothing to crawl: {e}")
        logger.warning(
            "Exiting 3 (no pages in range). Lower the requested start page or "
            "the worker count."
        )
        sys.exit(3)
    except KeyboardInterrupt:
        logger.info("Crawling interrupted by user")
        logger.info("Progress saved to checkpoint. Use --resume to continue.")
        # Signal interruption distinctly so the orchestrator does not treat a
        # stopped crawl as a completed one. 130 is the conventional SIGINT code.
        # cleanup() still runs via the finally below.
        sys.exit(130)
    except Exception as e:
        logger.error(f"Crawling failed: {e}")
        logger.info("Progress saved to checkpoint. Use --resume to continue.")
        sys.exit(1)
    finally:
        crawler.cleanup()


if __name__ == '__main__':
    main()
