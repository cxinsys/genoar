"""Public Stage 1 -> 2 -> 3 handoff tests.

These tests start at the commands a new user runs.  They deliberately put a
different accession in the raw Stage 1 directory so a silent fallback is
observable rather than hidden by an agreeable fixture.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
FETCH_SRA = REPO_ROOT / "scripts" / "fetch_sra.py"


def _write_stage2_table(directory: Path, accession) -> None:
    directory.mkdir(parents=True)
    accessions = [accession] if isinstance(accession, str) else list(accession)
    with (directory / "HS_tissue_1st_pass_meta_table.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=["Run", "Series"])
        writer.writeheader()
        for index, run in enumerate(accessions, start=1):
            writer.writerow({"Run": run, "Series": f"GSE{200000 + index}"})


def _write_raw_stage1(directory: Path, accession: str) -> None:
    directory.mkdir(parents=True)
    (directory / "GSE100001.txt").write_text(f"{accession}\n", encoding="utf-8")


def test_fetch_sra_defaults_to_stage2_filtered_tables_without_raw_fallback(tmp_path):
    stage2 = tmp_path / "first_pass_output"
    raw = tmp_path / "crawl_output" / "SRR"
    work = tmp_path / "stage3_prep"
    _write_stage2_table(stage2, "SRR200001")
    _write_raw_stage1(raw, "SRR100001")

    completed = subprocess.run(
        [
            sys.executable,
            str(FETCH_SRA),
            "--stage2-dir",
            str(stage2),
            "--srr-dir",
            str(raw),
            "--work-dir",
            str(work),
            "--output-dir",
            str(tmp_path / "sample_sra"),
            "--dry-run",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "SRR200001" in completed.stdout
    assert "SRR100001" not in completed.stdout
    assert (work / "srr_list.txt").read_text(encoding="utf-8") == "SRR200001\n"


def test_missing_stage2_output_fails_instead_of_using_raw_stage1(tmp_path):
    raw = tmp_path / "crawl_output" / "SRR"
    work = tmp_path / "stage3_prep"
    _write_raw_stage1(raw, "SRR100001")
    work.mkdir(parents=True)
    (work / "srr_list.txt").write_text("SRR999999\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(FETCH_SRA),
            "--stage2-dir",
            str(tmp_path / "missing_stage2"),
            "--srr-dir",
            str(raw),
            "--work-dir",
            str(work),
            "--dry-run",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    assert "will not fall back" in completed.stderr
    assert not (work / "srr_list.txt").exists()
    assert "SRR100001" not in completed.stdout


def test_raw_stage1_accessions_require_explicit_source_opt_in(tmp_path):
    raw = tmp_path / "crawl_output" / "SRR"
    work = tmp_path / "stage3_prep"
    _write_raw_stage1(raw, "SRR100001")

    completed = subprocess.run(
        [
            sys.executable,
            str(FETCH_SRA),
            "--source",
            "raw-stage1",
            "--srr-dir",
            str(raw),
            "--work-dir",
            str(work),
            "--output-dir",
            str(tmp_path / "sample_sra"),
            "--dry-run",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "SRR100001" in completed.stdout
    assert (work / "srr_list.txt").read_text(encoding="utf-8") == "SRR100001\n"


def test_make_fetch_sra_passes_the_stage2_handoff_to_the_cli(tmp_path):
    stage2 = tmp_path / "first_pass_output"
    raw = tmp_path / "crawl_output" / "SRR"
    _write_stage2_table(stage2, "SRR200001")
    _write_raw_stage1(raw, "SRR100001")

    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "fetch-sra",
            "DRY_RUN=1",
            f"FIRST_PASS_OUTPUT={stage2}",
            f"CRAWL_OUTPUT={tmp_path / 'crawl_output'}",
            f"SRA_DIR={tmp_path / 'sample_sra'}",
            "MAX_SAMPLES=2",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "SRR200001" in completed.stdout
    assert "SRR100001" not in completed.stdout
    assert (
        tmp_path / "crawl_output" / "stage3_prep" / "srr_list.txt"
    ).read_text(encoding="utf-8") == "SRR200001\n"


def test_run_full_includes_the_explicit_stage2_to_stage3_handoff():
    completed = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", "run-full"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    commands = completed.stdout
    run_pos = commands.index("make --no-print-directory run")
    fetch_pos = commands.index("make --no-print-directory fetch-sra")
    stage3_pos = commands.index("make --no-print-directory run-fetched-stage3")
    assert run_pos < fetch_pos < stage3_pos


def test_run_full_rejects_dry_run_before_reusing_a_previous_stage3_selection(
    tmp_path,
):
    selected = tmp_path / "sample_sra" / ".genoar_selected"
    selected.mkdir(parents=True)
    stale = selected / "selection.json"
    stale.write_text('{"accessions": ["SRR999999"]}\n', encoding="utf-8")

    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "run-full",
            "DRY_RUN=1",
            f"SRA_DIR={tmp_path / 'sample_sra'}",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "run-full" in output
    assert "DRY_RUN" in output
    assert "before Stage 1" in output
    assert "Starting the whole pipeline" not in output
    assert stale.read_text(encoding="utf-8") == (
        '{"accessions": ["SRR999999"]}\n'
    )


def test_selected_sra_view_excludes_files_left_by_an_earlier_fetch(tmp_path):
    from scripts.fetch_sra import build_selected_sra_view

    source = tmp_path / "sample_sra"
    for accession in ("SRR100001", "SRR200001", "SRR300001"):
        sample_dir = source / accession
        sample_dir.mkdir(parents=True)
        (sample_dir / f"{accession}.sra").write_bytes(b"NCBI.sra fixture")

    view = source / ".genoar_selected"
    build_selected_sra_view(source, view, ["SRR100001", "SRR200001"])

    assert sorted(path.name for path in view.iterdir()) == [
        "SRR100001",
        "SRR200001",
        "selection.json",
    ]
    assert not (view / "SRR300001").exists()
    assert (view / "SRR100001" / "SRR100001.sra").samefile(
        source / "SRR100001" / "SRR100001.sra"
    )


def test_make_fetch_sra_publishes_only_current_selection_not_stale_cache(tmp_path):
    from cycle_test.utils.stage3_downloader import MIN_SRA_SIZE, SRA_MAGIC

    stage2 = tmp_path / "first_pass_output"
    source = tmp_path / "sample_sra"
    _write_stage2_table(stage2, ["SRR100001", "SRR200001"])
    payload = SRA_MAGIC + (b"\0" * MIN_SRA_SIZE)
    for accession in ("SRR100001", "SRR200001", "SRR300001"):
        sample_dir = source / accession
        sample_dir.mkdir(parents=True)
        (sample_dir / f"{accession}.sra").write_bytes(payload)

    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "fetch-sra",
            f"FIRST_PASS_OUTPUT={stage2}",
            f"CRAWL_OUTPUT={tmp_path / 'crawl'}",
            f"SRA_DIR={source}",
            "MAX_SAMPLES=2",
            "MAX_CONCURRENT=1",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    view = source / ".genoar_selected"
    assert sorted(path.name for path in view.glob("SRR*")) == [
        "SRR100001",
        "SRR200001",
    ]
    assert not (view / "SRR300001").exists()
    assert (source / "SRR300001" / "SRR300001.sra").exists()


def test_selected_view_refuses_cache_or_ancestor_as_deletion_target(tmp_path):
    from scripts.fetch_sra import build_selected_sra_view

    source = tmp_path / "sample_sra"
    sample = source / "SRR100001"
    sample.mkdir(parents=True)
    cached = sample / "SRR100001.sra"
    cached.write_bytes(b"NCBI.sra fixture")

    with pytest.raises(ValueError):
        build_selected_sra_view(source, source, ["SRR100001"])
    assert cached.exists()

    ancestor = tmp_path / ".genoar_selected"
    nested_source = ancestor / "cache"
    nested_sample = nested_source / "SRR200001"
    nested_sample.mkdir(parents=True)
    nested_cached = nested_sample / "SRR200001.sra"
    nested_cached.write_bytes(b"NCBI.sra fixture")
    with pytest.raises(ValueError):
        build_selected_sra_view(nested_source, ancestor, ["SRR200001"])
    assert nested_cached.exists()


def test_selected_view_refuses_a_symlink_target(tmp_path):
    from scripts.fetch_sra import build_selected_sra_view

    source = tmp_path / "sample_sra"
    sample = source / "SRR100001"
    sample.mkdir(parents=True)
    (sample / "SRR100001.sra").write_bytes(b"NCBI.sra fixture")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    sentinel = elsewhere / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (source / ".genoar_selected").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        build_selected_sra_view(
            source, source / ".genoar_selected", ["SRR100001"]
        )
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_partial_selected_view_is_rejected_before_reuse(tmp_path):
    from scripts.fetch_sra import (
        build_selected_sra_view,
        validate_selected_sra_view,
    )

    source = tmp_path / "sample_sra"
    for accession in ("SRR100001", "SRR200001"):
        sample = source / accession
        sample.mkdir(parents=True)
        (sample / f"{accession}.sra").write_bytes(b"NCBI.sra fixture")
    view = source / ".genoar_selected"
    build_selected_sra_view(source, view, ["SRR100001", "SRR200001"])
    (view / "SRR200001" / "SRR200001.sra").unlink()

    with pytest.raises(ValueError, match="incomplete"):
        validate_selected_sra_view(source, view)


def test_failed_new_fetch_invalidates_the_previous_selection(tmp_path, monkeypatch):
    import scripts.fetch_sra as fetch_sra

    stage2 = tmp_path / "first_pass_output"
    source = tmp_path / "sample_sra"
    sample = source / "SRR100001"
    sample.mkdir(parents=True)
    cached = sample / "SRR100001.sra"
    cached.write_bytes(b"NCBI.sra fixture")
    _write_stage2_table(stage2, "SRR100001")
    view = source / ".genoar_selected"
    fetch_sra.build_selected_sra_view(source, view, ["SRR100001"])
    monkeypatch.setattr(
        fetch_sra,
        "download_sra_files",
        lambda **kwargs: {"success": False, "failed": 1, "total": 1},
    )

    rc = fetch_sra.main(
        [
            "--stage2-dir",
            str(stage2),
            "--output-dir",
            str(source),
            "--work-dir",
            str(tmp_path / "work"),
        ]
    )

    assert rc == 1
    assert not view.exists()
    assert cached.exists()


def test_run_fetched_stage3_uses_only_the_generated_selection_view():
    completed = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", "run-fetched-stage3"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert (
        "make --no-print-directory run-stage3 "
        "SRA_DIR=sample_sra/.genoar_selected"
    ) in completed.stdout


def test_make_status_reports_the_latest_stage3_outcome(tmp_path):
    results = tmp_path / "results"
    run_dir = results / "runs" / "pilot-1"
    run_dir.mkdir(parents=True)
    (results / "runs" / "LATEST").write_text("pilot-1\n", encoding="utf-8")
    (run_dir / "outcome.json").write_text(
        json.dumps(
            {
                "run_id": "pilot-1",
                "counts": {
                    "expected": 2,
                    "completed": 1,
                    "fresh": 1,
                    "cache_hit": 0,
                    "released": 0,
                    "adopted": 0,
                    "suspect": 1,
                    "missing": 0,
                    "unverified": 0,
                    "stale": 0,
                    "ineligible": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "status",
            f"CRAWL_OUTPUT={tmp_path / 'crawl'}",
            f"FIRST_PASS_OUTPUT={tmp_path / 'stage2'}",
            f"LOGS_DIR={tmp_path / 'logs'}",
            f"STAGE3_RESULTS={results}",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Stage 3 results:" in completed.stdout
    assert "Latest run: pilot-1" in completed.stdout
    assert "Status: partial (1/2 verified complete)" in completed.stdout
    assert "fresh=1" in completed.stdout
    assert "suspect=1" in completed.stdout
