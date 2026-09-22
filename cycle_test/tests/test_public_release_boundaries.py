"""Release boundaries and commands a public user is asked to copy."""

import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]


def _ignore_entries(name):
    return {
        line.strip().rstrip("/")
        for line in (REPO / name).read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_root_docker_context_excludes_internal_and_web_trees():
    ignored = _ignore_entries(".dockerignore")

    assert "docs/internal" in ignored
    assert "genoar_rag" in ignored
    assert "wiki" in ignored


@pytest.mark.parametrize("tree", ["docs/internal", "wiki"])
def test_working_notes_stay_out_of_both_contexts(tree):
    # Git and Docker read different ignore files, so a tree that is only named
    # in one of them crosses the other boundary the first time someone runs
    # `git add -A` or builds the image.
    assert tree in _ignore_entries(".gitignore")
    assert tree in _ignore_entries(".dockerignore")


@pytest.mark.parametrize("tree", ["docs/internal", "wiki"])
def test_nothing_under_the_working_notes_is_tracked(tree):
    # Naming a tree in .gitignore does not keep a file out of it: an explicit
    # `git add` or `git mv` overrides the rule silently, and the file then ships
    # from the very directory the rule exists to withhold.
    tracked = subprocess.run(
        ["git", "ls-files", "--", tree],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()

    assert tracked == [], tracked


@pytest.mark.parametrize("readme", ["README.md", "README.ko.md"])
def test_every_documented_cycle_test_command_names_a_cycle_count(readme):
    text = (REPO / readme).read_text(encoding="utf-8")
    # Join shell continuations, then inspect only actual command lines. The CLI
    # requires --cycles; omitting it makes a copied HPC/Singularity example die
    # in argparse before it can inspect the environment.
    commands = []
    current = ""
    for line in text.splitlines():
        if not current and not line.startswith(
                "python cycle_test/run_cycle_test.py"):
            continue
        current += " " + line.strip().removesuffix("\\").strip()
        if not line.rstrip().endswith("\\"):
            commands.append(current.strip())
            current = ""

    assert commands
    assert all("--cycles" in command for command in commands), commands


@pytest.mark.parametrize("readme", ["README.md", "README.ko.md"])
def test_public_singularity_example_uses_the_bounded_fetch_selection(readme):
    text = (REPO / readme).read_text(encoding="utf-8")

    assert "--sra ./sample_sra/.genoar_selected" in text
