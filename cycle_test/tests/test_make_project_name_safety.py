"""The Compose project name must be safe anywhere Make interpolates it.

The value is also used below ``projects/`` and in the destructive ``clean``
recipe.  Docker Compose validates its own ``-p`` argument, but that happens too
late for Make recipes such as ``clean`` which expand paths first.
"""

import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["*", "x;echo_INJECTED", "Upper", "a.b"])
def test_make_refuses_project_names_outside_the_compose_safe_alphabet(name):
    done = subprocess.run(
        ["make", "--no-print-directory", "-n", "clean",
         f"COMPOSE_PROJECT_NAME={name}"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert done.returncode != 0
    assert "rm -rf" not in done.stdout


@pytest.mark.parametrize("name", ["second", "run_2", "run-3", "4th"])
def test_make_accepts_compose_safe_project_names(name):
    done = subprocess.run(
        ["make", "--no-print-directory", "-n", "status",
         f"COMPOSE_PROJECT_NAME={name}"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stdout + done.stderr
