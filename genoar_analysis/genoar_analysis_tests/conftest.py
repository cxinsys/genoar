"""
Pytest configuration and fixtures for genoar_analysis tests.

This module provides portable path configuration that works across:
- macOS local development
- Linux servers
- Docker containers
- CI/CD pipelines

Path Configuration:
- META_DIR: Configurable via GENOAR_META_DIR env var (default: sample_crawl_output/META)
- UMLS_DIR: Configurable via GENOAR_UMLS_DIR env var (default: all_query_results)
- OUTPUT_DIR: Configurable via GENOAR_OUTPUT_DIR env var (default: test_first_pass_output)

Usage:
    # Run with sample data (default)
    pytest genoar_analysis_tests/

    # Run with custom META directory (e.g., real crawl output)
    export GENOAR_META_DIR=/path/to/crawl_output/META
    pytest genoar_analysis_tests/

    # UMLS data is always from all_query_results/ (not configurable)
"""

import os
import sys
from pathlib import Path
from typing import Optional

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


# A directory is the project root when it *contains* the genoar_analysis
# package and carries one of these packaging/VCS markers.
_ROOT_MARKERS = ('setup.py', 'pyproject.toml', '.git')


def get_project_root() -> Path:
    """
    Detect the project root: the directory that holds the genoar_analysis
    package, not the package itself and not this test directory.

    README.md is not a marker: the walk stops at the first directory that has
    one, which would be this very directory, since
    genoar_analysis_tests/README.md exists. Every relative default would then
    resolve under genoar_analysis_tests/ (.../genoar_analysis_tests/
    sample_crawl_output/META), so fixtures look absent even when they are
    present at the real root, and the tests report failures they have no
    business reporting.
    """
    start = Path(__file__).resolve().parent

    for candidate in [start, *start.parents][:6]:
        if not (candidate / 'genoar_analysis').is_dir():
            continue
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate

    # Fallback: assume 3 levels up from test file
    # genoar_analysis_tests -> genoar_analysis -> genoar
    return Path(__file__).resolve().parent.parent.parent


def get_path_from_env_or_default(env_var: str, default_relative: str) -> Path:
    """
    Get path from environment variable or use default relative path.

    Args:
        env_var: Environment variable name to check
        default_relative: Default path relative to project root

    Returns:
        Path object (may not exist)
    """
    env_value = os.getenv(env_var)
    if env_value:
        return Path(env_value)

    project_root = get_project_root()
    return project_root / default_relative


# Path configuration
PROJECT_ROOT = get_project_root()

# META directory: configurable via env var, default to sample_crawl_output/META for testing
META_DIR = get_path_from_env_or_default('GENOAR_META_DIR', 'sample_crawl_output/META')

# UMLS directory: configurable via env var for Docker, default to project's all_query_results/
UMLS_DIR = get_path_from_env_or_default('GENOAR_UMLS_DIR', 'all_query_results')

# Output directory: configurable via env var
OUTPUT_DIR = get_path_from_env_or_default('GENOAR_OUTPUT_DIR', 'test_first_pass_output')


# Derive the skip signal from pytest's own, so the same test function means
# "skipped" under both runners: `pytest genoar_analysis_tests/` reports a skip,
# and the scripts' own main() below reports SKIPPED. Raising a plain Exception
# would be a pytest failure, which is the opposite of what a missing optional
# fixture deserves.
_SKIP_BASE = getattr(pytest, 'skip', None)
_SKIP_BASE = getattr(_SKIP_BASE, 'Exception', Exception)


class FixtureUnavailable(_SKIP_BASE):
    """
    Raised by a test that cannot run because an optional fixture is absent.

    A skip is neither a pass nor a failure. These scripts are also run directly
    by workflow/run.sh (MODE=test), which reads nothing but the exit status, so
    the three outcomes have to be kept apart there:

        passed  -> exit 0, reported as PASSED
        skipped -> exit 0, reported as SKIPPED, never as PASSED
        failed  -> exit 1

    Printing failure text and then returning 0 makes a broken run
    indistinguishable from a good one.

    Note that pytest's skip exception derives from BaseException, so this one
    does too: catch it by name, never via `except Exception`.
    """


# The crawled META files are named GSE*_meta.txt; this is the pattern
# genoar_analysis.io.readers.read_crawled_meta() globs for, and the pattern the
# scripts below have to agree with. A directory that holds no match is as good
# as absent: the reader raises FileNotFoundError on it.
META_FILE_PATTERN = '*_meta.txt'

# The UMLS export, as UMLSCSVReader.field_mappings names its files. A directory
# with none of them in it is an empty export, not an export.
UMLS_FILENAMES = (
    'umls_celltype_df.csv',
    'umls_tissue_df.csv',
    'umls_disease_df.csv',
)


def require_meta_dir(meta_dir: Path) -> Path:
    """
    Require the META directory itself, for callers that only pass the path on.

    Use this when the code under test is handed the directory but does not read
    it (a constructor, say). When the files are actually read, use
    require_meta_files(): the reader raises on an empty directory, and that
    would be a failure about missing data rather than the skip it should be.

    Raises:
        FixtureUnavailable: if the directory is not there.
    """
    if not meta_dir.exists():
        raise FixtureUnavailable(
            f"META directory not found: {meta_dir} "
            "(pass --meta-dir, or set GENOAR_META_DIR, to point at crawled META files)"
        )

    return meta_dir


def require_meta_files(meta_dir: Path) -> Path:
    """
    Require crawled META files: the directory must exist *and* hold a match.

    Every script in this directory has to ask the same question, or the answer
    drifts. It did: one script checked the directory and the glob, the others
    checked only that the directory existed. `make setup` creates an empty
    crawl_output/META and workflow/run.sh points GENOAR_META_DIR at it, so on a
    fresh checkout the directory-only check waved the tests through into
    read_crawled_meta(), which then raised

        FileNotFoundError: No META files found in ... with pattern *_meta.txt

    - a failed run where nothing was wrong except that nothing had been crawled
    yet.

    Raises:
        FixtureUnavailable: if the directory is missing or holds no META file.
    """
    require_meta_dir(meta_dir)

    if not list(meta_dir.glob(META_FILE_PATTERN)):
        raise FixtureUnavailable(
            f"No {META_FILE_PATTERN} files in {meta_dir} "
            "(pass --meta-dir, or set GENOAR_META_DIR, to point at crawled META files)"
        )

    return meta_dir


def require_umls_files(umls_dir: Path) -> Path:
    """
    Require the UMLS export: the directory must exist *and* hold a CSV from it.

    Same predicate, same reason as require_meta_files(). An empty
    all_query_results/ - a checkout whose export was never downloaded, or a
    container run without that bind mount - otherwise gets as far as
    UMLSCSVReader.load_umls_data(), whose FileNotFoundError reads as a failing
    test rather than an absent fixture.

    Raises:
        FixtureUnavailable: if the directory is missing or holds no UMLS CSV.
    """
    if not umls_dir.exists():
        raise FixtureUnavailable(
            f"UMLS directory not found: {umls_dir} "
            "(the UMLS export belongs in all_query_results/, or set GENOAR_UMLS_DIR)"
        )

    if not any((umls_dir / name).exists() for name in UMLS_FILENAMES):
        raise FixtureUnavailable(
            f"No UMLS export in {umls_dir}: none of {', '.join(UMLS_FILENAMES)} is there "
            "(the UMLS export belongs in all_query_results/, or set GENOAR_UMLS_DIR)"
        )

    return umls_dir


def run_test_suite(title: str, tests) -> int:
    """
    Run a sequence of (name, callable) tests and report what actually happened.

    Args:
        title: Heading for the summary block.
        tests: Iterable of (name, zero-argument callable) pairs.

    Returns:
        0 if every test passed or was skipped, 1 if any test failed.
    """
    import traceback

    outcomes = []
    failed = []

    for name, test in tests:
        try:
            test()
        except FixtureUnavailable as exc:
            print(f"[SKIP] {name}: {exc}\n")
            outcomes.append((name, "SKIPPED", str(exc)))
        except Exception as exc:  # noqa: BLE001 - a failing test must be visible
            print(f"[FAIL] {name}: {exc}")
            traceback.print_exc()
            print("")
            outcomes.append((name, "FAILED", str(exc)))
            failed.append(name)
        else:
            outcomes.append((name, "PASSED", ""))

    width = max((len(name) for name, _, _ in outcomes), default=0)

    print("=" * 70)
    print(f"[INFO] {title}")
    print("=" * 70)
    for name, outcome, detail in outcomes:
        tag = {"PASSED": "[OK]", "SKIPPED": "[SKIP]", "FAILED": "[FAIL]"}[outcome]
        suffix = f" - {detail}" if detail else ""
        print(f"{tag} {name.ljust(width)} : {outcome}{suffix}")

    passed = sum(1 for _, outcome, _ in outcomes if outcome == "PASSED")
    skipped = sum(1 for _, outcome, _ in outcomes if outcome == "SKIPPED")

    print("")
    print(f"{passed} passed, {skipped} skipped, {len(failed)} failed")

    if failed:
        print("[FAIL] Failing tests: " + ", ".join(failed))
        return 1

    if skipped:
        print("[SKIP] Some tests were skipped; their fixtures were not present.")

    return 0


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Fixture: Project root directory."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def meta_dir() -> Path:
    """
    Fixture: META files directory.

    Set GENOAR_META_DIR environment variable for custom path.
    Default: {project_root}/crawl_output/META
    """
    return META_DIR


@pytest.fixture(scope="session")
def umls_dir() -> Path:
    """
    Fixture: UMLS data directory.

    Set GENOAR_UMLS_DIR environment variable for custom path.
    Default: {project_root}/all_query_results/

    Contains: umls_celltype_df.csv, umls_tissue_df.csv, umls_disease_df.csv
    """
    return UMLS_DIR


@pytest.fixture(scope="session")
def output_dir() -> Path:
    """
    Fixture: Test output directory.

    Set GENOAR_OUTPUT_DIR environment variable for custom path.
    Default: {project_root}/test_first_pass_output

    Creates the directory if it doesn't exist.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


# Helper functions for non-pytest usage (backward compatibility)
def get_meta_dir() -> Path:
    """Get META directory path (for use in scripts)."""
    return META_DIR


def get_umls_dir() -> Path:
    """Get UMLS directory path (for use in scripts)."""
    return UMLS_DIR


def get_output_dir() -> Path:
    """Get output directory path (for use in scripts)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def print_path_info():
    """Print current path configuration (for debugging)."""
    print(f"Project root: {PROJECT_ROOT}")
    print(f"META dir: {META_DIR} (exists: {META_DIR.exists()})")
    print(f"UMLS dir: {UMLS_DIR} (exists: {UMLS_DIR.exists()})")
    print(f"Output dir: {OUTPUT_DIR}")


if __name__ == "__main__":
    print_path_info()
