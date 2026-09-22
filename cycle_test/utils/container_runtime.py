#!/usr/bin/env python3
"""
Container runtime abstraction for the cycle_test stage runners.

Builds either a Docker or a Singularity/Apptainer command from the same call.
Docker output matches the previous hardcoded commands. Singularity maps the
mounts and args onto `singularity run --bind ... <image>.sif`, resolving the
image name to a local .sif and dropping docker-only flags.
"""

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# (host_path, container_path, read_only)
Mount = Tuple[str, str, bool]

DOCKER = "docker"
SINGULARITY = "singularity"
VALID_RUNTIMES = (DOCKER, SINGULARITY)


def sif_name_for(image: str) -> str:
    """Map a Docker image reference to a conventional .sif filename.

    genoar-srr:step9      -> genoar-srr_step9.sif
    genoar-analysis:latest -> genoar-analysis.sif
    genoar-crawler:amd64  -> genoar-crawler_amd64.sif
    """
    name, _, tag = image.partition(":")
    if tag and tag != "latest":
        return f"{name}_{tag}.sif"
    return f"{name}.sif"


def singularity_engine() -> str:
    """Return 'apptainer' if available, else 'singularity'."""
    if shutil.which("apptainer"):
        return "apptainer"
    return "singularity"


def sif_path(image: str, sif_dir: str = ".") -> Path:
    return Path(sif_dir) / sif_name_for(image)


def build_run_command(
    image: str,
    mounts: Sequence[Mount],
    args: Optional[Sequence[str]] = None,
    *,
    runtime: str = DOCKER,
    docker_flags: Optional[Sequence[str]] = None,
    sif_dir: str = ".",
) -> List[str]:
    """Build a container run command for the given runtime.

    Args:
        image: Docker image reference (also used to derive the .sif name).
        mounts: list of (host, container, read_only) bind mounts.
        args: positional args passed to the image entrypoint / runscript.
        runtime: 'docker' or 'singularity'.
        docker_flags: extra flags applied ONLY in docker mode (e.g. --shm-size);
            ignored under singularity, which uses a different security model.
        sif_dir: directory holding the .sif file (singularity mode).

    Returns:
        The command as a list of strings.
    """
    args = list(args or [])

    if runtime == DOCKER:
        cmd: List[str] = ["docker", "run", "--rm", *(docker_flags or [])]
        for host, cont, ro in mounts:
            cmd += ["-v", f"{host}:{cont}" + (":ro" if ro else "")]
        cmd.append(image)
        cmd += args
        return cmd

    if runtime == SINGULARITY:
        cmd = [singularity_engine(), "run"]
        for host, cont, ro in mounts:
            cmd += ["--bind", f"{host}:{cont}" + (":ro" if ro else "")]
        cmd.append(str(sif_path(image, sif_dir)))
        cmd += args
        return cmd

    raise ValueError(f"Unknown runtime: {runtime!r} (expected one of {VALID_RUNTIMES})")


def image_available(image: str, *, runtime: str = DOCKER, sif_dir: str = ".") -> bool:
    """Check whether the image (docker) or .sif (singularity) is present."""
    if runtime == DOCKER:
        try:
            proc = subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True
            )
            return proc.returncode == 0
        except Exception:
            return False
    if runtime == SINGULARITY:
        return sif_path(image, sif_dir).exists()
    raise ValueError(f"Unknown runtime: {runtime!r}")
