"""Does Compose actually hand the backend its dataset settings?

The failure this guards is the deployment kind: the service can be correct
and every unit test green while `docker compose config` shows the backend
container never receives `DATASET_<NAME>_*`, leaving a secondary dataset
reading the primary's files. These tests render the real compose file the
way a deployment would and assert on what reaches the container.

They need the docker CLI with the compose plugin, not a running daemon,
and skip where there is none.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

COMPOSE_FILE = Path(__file__).resolve().parents[3] / "docker-compose.yml"


def compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "version"], capture_output=True
    )
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(
    not compose_available(), reason="docker compose not available"
)


def render(project_dir: Path, *args: str) -> dict:
    result = subprocess.run(
        [
            "docker", "compose",
            "-f", str(project_dir / "docker-compose.yml"),
            "--project-directory", str(project_dir),
            *args,
            "config", "--format", "json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def project_dir(tmp_path):
    shutil.copy(COMPOSE_FILE, tmp_path / "docker-compose.yml")
    return tmp_path


class TestDatasetSettingsReachTheBackend:
    def test_datasets_env_is_forwarded_verbatim(self, project_dir):
        (project_dir / ".env").write_text(
            "DATASETS=main,atlas\nDASHBOARD_DATASET=atlas\n"
        )
        (project_dir / "datasets.env").write_text(
            "DATASET_ATLAS_DB_NAME=genoar_atlas\n"
            "DATASET_ATLAS_HUMAN_FAISS_INDEX_PATH=/data/genoar/atlas_human.bin\n"
            "DATASET_ATLAS_HUMAN_RUN_MAPPING_PATH=/data/genoar/atlas_human.json\n"
            "DATASET_ATLAS_LABEL=Second dataset\n"
        )

        env = render(project_dir)["services"]["backend"]["environment"]

        assert env["DATASETS"] == "main,atlas"
        assert env["DATASET_ATLAS_DB_NAME"] == "genoar_atlas"
        assert (
            env["DATASET_ATLAS_HUMAN_FAISS_INDEX_PATH"]
            == "/data/genoar/atlas_human.bin"
        )
        assert (
            env["DATASET_ATLAS_HUMAN_RUN_MAPPING_PATH"]
            == "/data/genoar/atlas_human.json"
        )
        assert env["DATASET_ATLAS_LABEL"] == "Second dataset"

    def test_single_dataset_deployment_needs_no_datasets_env(
        self, project_dir
    ):
        env = render(project_dir)["services"]["backend"]["environment"]

        assert env["DATASETS"] == ""
        assert not any(k.startswith("DATASET_") for k in env)

    def test_mariadb_gets_the_extra_schema_init_dir(self, project_dir):
        services = render(project_dir, "--profile", "mysql")["services"]

        targets = [v["target"] for v in services["mariadb"]["volumes"]]
        assert "/docker-entrypoint-initdb.d" in targets


class TestASpeciesCanBeTurnedOffFromEnv:
    """Emptying a species' two path variables must actually reach the backend.

    Compose's ${VAR:-default} substitutes the default for an *empty* value
    too, so the documented "leave the two mouse paths empty to disable
    mouse" silently re-enabled them — and under fail-closed startup, a
    deployment without mouse artifacts then refuses to boot instead of
    serving without mouse.
    """

    def test_empty_paths_pass_through_empty(self, project_dir):
        (project_dir / ".env").write_text(
            "MOUSE_FAISS_INDEX_PATH=\nMOUSE_RUN_MAPPING_PATH=\n"
        )

        env = render(project_dir)["services"]["backend"]["environment"]

        assert env["MOUSE_FAISS_INDEX_PATH"] == ""
        assert env["MOUSE_RUN_MAPPING_PATH"] == ""

    def test_unset_paths_keep_the_default_deployment_working(
        self, project_dir
    ):
        env = render(project_dir)["services"]["backend"]["environment"]

        assert env["MOUSE_FAISS_INDEX_PATH"] == "/data/genoar/faiss_mouse.bin"
        assert (
            env["MOUSE_RUN_MAPPING_PATH"]
            == "/data/genoar/run_mapping_mouse.json"
        )
        assert env["HUMAN_FAISS_INDEX_PATH"] == "/data/genoar/faiss_human.bin"
