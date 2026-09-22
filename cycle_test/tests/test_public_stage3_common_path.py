"""Regression tests for the Stage 3 path shown to a first-time user.

These start at the public Make target and its shipped configuration.  Testing
the dispatcher or a generated configuration in isolation is not enough. The
public configuration runs the maintained tree, while the frozen tree remains
available as an explicit comparison baseline. The host preflight must accept
every Cell Ranger layout the container accepts.
"""

import os
import platform
import subprocess
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[2]
MAKEFILE = REPO / "Makefile"
ENTRYPOINT = REPO / "srr_pipeline_package" / "pipeline_entry.sh"


def _make_value(name: str) -> str:
    """Ask Make for the value its real public target will use."""
    rule = "print-value:\n\t@printf '%s\\n' '$({})'\n".format(name)
    done = subprocess.run(
        ["make", "--no-print-directory", "-f", str(MAKEFILE), "-f", "-",
         "print-value"],
        cwd=REPO,
        input=rule,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _dispatch(config: Path) -> subprocess.CompletedProcess:
    # The image paths do not exist on the test host.  The selection message is
    # printed before that expected exit, which is all this seam test needs.
    return subprocess.run(
        ["bash", str(ENTRYPOINT), "--config", str(config)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def test_make_default_config_runs_the_current_pipeline():
    relative = _make_value("SRR_CONFIG")
    config = REPO / relative

    assert config == REPO / "srr_pipeline_package/configs/example.yaml"
    assert config.is_file()

    done = _dispatch(config)
    assert "running the current pipeline (/pipeline_next)" in done.stdout
    assert "legacy_pipeline: true" not in done.stdout


def test_the_shipped_config_keeps_the_frozen_tree_as_an_explicit_baseline(tmp_path):
    config = tmp_path / "legacy.yaml"
    config.write_text("legacy_pipeline: true\n")

    done = _dispatch(config)
    assert "legacy_pipeline: true" in done.stdout
    assert "running the earlier pipeline (/pipeline)" in done.stdout


@pytest.mark.parametrize("relative", [
    "srr_pipeline_package/configs/example.yaml",
    "srr_pipeline_package/hpc/hpc_config.example.yaml",
])
def test_shipped_configs_run_the_current_tree_by_default(relative):
    config = yaml.safe_load((REPO / relative).read_text())
    assert config["legacy_pipeline"] is False
    assert "pipeline_mode" not in config


def _stage3_fixture(tmp_path: Path, launcher: str) -> tuple[dict, Path]:
    (tmp_path / "sample_sra").mkdir()
    (tmp_path / "sample_sra" / "SRR1.sra").write_bytes(b"NCBI.sra")

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "reference.json").write_text("{}")
    for name in ("fasta", "genes", "star"):
        (ref / name).mkdir()

    binary = tmp_path / "cellranger" / launcher
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = images ]; then\n"
        "  printf '%s\\n' 'test-stage3:latest'\n"
        "  exit 0\n"
        "fi\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_DOCKER_ARGS\"\n"
    )
    docker.chmod(0o755)

    capture = tmp_path / "docker-args.txt"
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["FAKE_DOCKER_ARGS"] = str(capture)
    # What these fixtures exercise - the launcher layouts and the reference
    # manifest - is the same on every architecture, and the stub launcher is a
    # shell script rather than an x86_64 binary. The preflight's architecture
    # gate is not, so take its documented way past; the gate has its own test.
    env["GENOAR_ALLOW_NON_X86"] = "1"
    return env, capture


@pytest.mark.parametrize("launcher", ["cellranger", "bin/cellranger"])
def test_make_stage3_accepts_both_supported_cellranger_layouts(
        tmp_path, launcher):
    env, capture = _stage3_fixture(tmp_path, launcher)
    done = subprocess.run(
        ["make", "--no-print-directory", "-f", str(MAKEFILE), "run-stage3",
         "SRA_DIR=sample_sra",
         "SRR_CONFIG=" + str(REPO / "srr_pipeline_package/configs/example.yaml"),
         "SRR_DOCKER_IMAGE=test-stage3:latest"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    args = capture.read_text().splitlines()
    assert ("-v" in args
            and str(tmp_path / "cellranger") + ":/opt/cellranger" in args)


def test_make_stage3_refuses_a_non_x86_host_before_it_looks_at_the_install(
        tmp_path):
    """Cell Ranger is distributed for x86_64 only, and a complete install of it
    is present and executable on an ARM host all the same. Nothing about the
    files says so, so the preflight has to ask the host, or the run only finds
    out at step 0 inside the container."""
    if platform.machine() in ("x86_64", "amd64"):
        pytest.skip("host is x86_64; there is nothing here for the gate to refuse")

    env, capture = _stage3_fixture(tmp_path, "cellranger")
    env.pop("GENOAR_ALLOW_NON_X86", None)

    done = subprocess.run(
        ["make", "--no-print-directory", "-f", str(MAKEFILE), "run-stage3",
         "SRA_DIR=sample_sra",
         "SRR_CONFIG=" + str(REPO / "srr_pipeline_package/configs/example.yaml"),
         "SRR_DOCKER_IMAGE=test-stage3:latest"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert done.returncode != 0
    assert "x86_64 host" in done.stdout
    assert "GENOAR_ALLOW_NON_X86=1" in done.stdout
    assert not capture.exists()


def test_make_stage3_runs_on_a_non_x86_host_when_told_to(tmp_path):
    """The escape hatch is for a host running the binary under emulation, and
    it has to reach the container, not just past the gate."""
    if platform.machine() in ("x86_64", "amd64"):
        pytest.skip("host is x86_64; the gate is not in the way to begin with")

    env, capture = _stage3_fixture(tmp_path, "cellranger")

    done = subprocess.run(
        ["make", "--no-print-directory", "-f", str(MAKEFILE), "run-stage3",
         "SRA_DIR=sample_sra",
         "SRR_CONFIG=" + str(REPO / "srr_pipeline_package/configs/example.yaml"),
         "SRR_DOCKER_IMAGE=test-stage3:latest"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert capture.exists()


def test_make_stage3_rejects_an_empty_reference_manifest(tmp_path):
    env, capture = _stage3_fixture(tmp_path, "cellranger")
    (tmp_path / "ref" / "reference.json").write_text("")

    done = subprocess.run(
        ["make", "--no-print-directory", "-f", str(MAKEFILE), "run-stage3",
         "SRA_DIR=sample_sra",
         "SRR_CONFIG=" + str(REPO / "srr_pipeline_package/configs/example.yaml"),
         "SRR_DOCKER_IMAGE=test-stage3:latest"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert done.returncode != 0
    assert "reference.json" in done.stdout
    assert not capture.exists()
