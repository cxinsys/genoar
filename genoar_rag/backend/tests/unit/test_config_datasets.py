"""How a deployment's dataset settings become DatasetSpecs.

The failure these tests guard against is silent: a secondary dataset that
omits a setting must not inherit the primary's files and serve the wrong
corpus under its own name. Missing configuration is a startup error, never
a fallback.
"""

import pytest

from app.config import Settings


def make_settings(**overrides) -> Settings:
    """A Settings with deterministic top-level values, env ignored."""
    base = dict(
        datasets="",
        db_backend="mysql",
        db_path="/data/main.db",
        db_name="genoar",
        human_embedding_model_name="sapbert",
        human_faiss_index_path="/data/faiss_human.bin",
        human_run_mapping_path="/data/run_mapping_human.json",
        mouse_embedding_model_name="minilm",
        mouse_faiss_index_path="/data/faiss_mouse.bin",
        mouse_run_mapping_path="/data/run_mapping_mouse.json",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestSecondaryDatasetRequiresItsOwnDatabase:
    def test_mysql_secondary_without_db_name_fails(self, monkeypatch):
        settings = make_settings(datasets="main,atlas")
        with pytest.raises(ValueError, match="atlas"):
            settings.dataset_specs()

    def test_sqlite_secondary_without_db_path_fails(self, monkeypatch):
        settings = make_settings(datasets="main,atlas", db_backend="sqlite")
        with pytest.raises(ValueError, match="atlas"):
            settings.dataset_specs()


class TestSecondarySemanticBackendsAreExplicit:
    def test_species_with_index_and_mapping_is_enabled(self, monkeypatch):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        monkeypatch.setenv(
            "DATASET_ATLAS_HUMAN_FAISS_INDEX_PATH", "/data/atlas_human.bin"
        )
        monkeypatch.setenv(
            "DATASET_ATLAS_HUMAN_RUN_MAPPING_PATH", "/data/atlas_human.json"
        )
        settings = make_settings(datasets="main,atlas")

        atlas = settings.dataset_specs()[1]

        assert atlas.species == {
            "human": {
                "model_name": "sapbert",
                "index_path": "/data/atlas_human.bin",
                "mapping_path": "/data/atlas_human.json",
            }
        }

    def test_index_without_mapping_fails(self, monkeypatch):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        monkeypatch.setenv(
            "DATASET_ATLAS_HUMAN_FAISS_INDEX_PATH", "/data/atlas_human.bin"
        )
        settings = make_settings(datasets="main,atlas")

        with pytest.raises(ValueError, match="HUMAN_RUN_MAPPING_PATH"):
            settings.dataset_specs()

    def test_unconfigured_species_is_off_not_inherited(self, monkeypatch):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        settings = make_settings(datasets="main,atlas")

        atlas = settings.dataset_specs()[1]

        assert atlas.species == {}

    def test_species_the_primary_turned_off_still_works_when_fully_named(
        self, monkeypatch
    ):
        """A dataset's backends are its own, not a subset of the primary's."""
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        monkeypatch.setenv(
            "DATASET_ATLAS_MOUSE_FAISS_INDEX_PATH", "/data/atlas_mouse.bin"
        )
        monkeypatch.setenv(
            "DATASET_ATLAS_MOUSE_RUN_MAPPING_PATH", "/data/atlas_mouse.json"
        )
        settings = make_settings(
            datasets="main,atlas",
            mouse_faiss_index_path="",
            mouse_run_mapping_path="",
        )

        atlas = settings.dataset_specs()[1]

        assert atlas.species == {
            "mouse": {
                "model_name": "minilm",
                "index_path": "/data/atlas_mouse.bin",
                "mapping_path": "/data/atlas_mouse.json",
            }
        }

    def test_half_configured_species_fails_even_when_primary_has_it_off(
        self, monkeypatch
    ):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        monkeypatch.setenv(
            "DATASET_ATLAS_MOUSE_FAISS_INDEX_PATH", "/data/atlas_mouse.bin"
        )
        settings = make_settings(
            datasets="main,atlas",
            mouse_faiss_index_path="",
            mouse_run_mapping_path="",
        )

        with pytest.raises(ValueError, match="MOUSE_RUN_MAPPING_PATH"):
            settings.dataset_specs()


class TestPrimaryOverridesComeInPairs:
    def test_overriding_only_the_index_fails(self, monkeypatch):
        """A new index next to the old mapping maps vectors to wrong runs."""
        monkeypatch.setenv("DATASET_MAIN_DB_NAME", "genoar")
        monkeypatch.setenv(
            "DATASET_MAIN_HUMAN_FAISS_INDEX_PATH", "/data/new_human.bin"
        )
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        settings = make_settings(datasets="main,atlas")

        with pytest.raises(ValueError, match="HUMAN_RUN_MAPPING_PATH"):
            settings.dataset_specs()

    def test_a_full_pair_replaces_the_top_level_one(self, monkeypatch):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        monkeypatch.setenv(
            "DATASET_MAIN_HUMAN_FAISS_INDEX_PATH", "/data/new_human.bin"
        )
        monkeypatch.setenv(
            "DATASET_MAIN_HUMAN_RUN_MAPPING_PATH", "/data/new_human.json"
        )
        settings = make_settings(datasets="main,atlas")

        main = settings.dataset_specs()[0]

        assert main.species["human"] == {
            "model_name": "sapbert",
            "index_path": "/data/new_human.bin",
            "mapping_path": "/data/new_human.json",
        }

    def test_a_full_pair_enables_a_species_the_top_level_has_off(
        self, monkeypatch
    ):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        monkeypatch.setenv(
            "DATASET_MAIN_MOUSE_FAISS_INDEX_PATH", "/data/main_mouse.bin"
        )
        monkeypatch.setenv(
            "DATASET_MAIN_MOUSE_RUN_MAPPING_PATH", "/data/main_mouse.json"
        )
        settings = make_settings(
            datasets="main,atlas",
            mouse_faiss_index_path="",
            mouse_run_mapping_path="",
        )

        main = settings.dataset_specs()[0]

        assert main.species["mouse"] == {
            "model_name": "minilm",
            "index_path": "/data/main_mouse.bin",
            "mapping_path": "/data/main_mouse.json",
        }


class TestDatasetNamesAreSafeEverywhereTheyAppear:
    def test_a_name_unusable_as_an_env_key_or_url_is_refused(
        self, monkeypatch
    ):
        """A name lives in DATASET_<NAME>_* keys and in ?dataset= queries."""
        for bad in ("at-las", "atlas set", "Atlas", "데이터"):
            settings = make_settings(datasets=f"main,{bad}")
            with pytest.raises(ValueError, match="a-z0-9_"):
                settings.dataset_specs()


class TestDatasetNamesAndTargetsAreDistinct:
    def test_duplicate_names_fail(self, monkeypatch):
        monkeypatch.setenv("DATASET_MAIN_DB_NAME", "genoar_other")
        settings = make_settings(datasets="main,main")
        with pytest.raises(ValueError, match="more than once"):
            settings.dataset_specs()

    def test_two_datasets_reaching_the_same_database_fail(self, monkeypatch):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar")
        settings = make_settings(datasets="main,atlas")

        with pytest.raises(ValueError, match="genoar"):
            settings.dataset_specs()


class TestTopLevelSemanticSettingsComeInPairs:
    def test_index_without_mapping_fails_at_startup(self):
        settings = make_settings(mouse_run_mapping_path="")
        with pytest.raises(ValueError, match="MOUSE_RUN_MAPPING_PATH"):
            settings.dataset_specs()

    def test_mapping_without_index_fails_at_startup(self):
        settings = make_settings(human_faiss_index_path="")
        with pytest.raises(ValueError, match="HUMAN_FAISS_INDEX_PATH"):
            settings.dataset_specs()

    def test_neither_means_the_species_is_off(self):
        settings = make_settings(
            mouse_faiss_index_path="", mouse_run_mapping_path=""
        )
        spec = settings.dataset_specs()[0]
        assert set(spec.species) == {"human"}

    def test_an_index_pair_with_no_model_fails_at_startup(self):
        """The same rule the per-dataset overrides already follow.

        Emptying only the model name must not silently drop the species,
        index and mapping notwithstanding — that is configuration ignored
        instead of honored or refused.
        """
        settings = make_settings(mouse_embedding_model_name="")
        with pytest.raises(ValueError, match="MOUSE_EMBEDDING_MODEL_NAME"):
            settings.dataset_specs()


class TestExistingDeploymentsKeepTheirShape:
    def test_no_datasets_setting_yields_one_default_from_top_level(self):
        specs = make_settings().dataset_specs()

        assert len(specs) == 1
        spec = specs[0]
        assert spec.name == "default"
        assert spec.db_path == "/data/main.db"
        assert spec.db_name == "genoar"
        assert set(spec.species) == {"human", "mouse"}

    def test_primary_dataset_reads_top_level_settings(self, monkeypatch):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        settings = make_settings(datasets="main,atlas")

        main = settings.dataset_specs()[0]

        assert main.name == "main"
        assert main.db_path == "/data/main.db"
        assert main.db_name == "genoar"
        assert set(main.species) == {"human", "mouse"}
        assert main.species["human"]["index_path"] == "/data/faiss_human.bin"

    def test_fully_configured_secondary_keeps_its_own_artifacts(
        self, monkeypatch
    ):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        monkeypatch.setenv("DATASET_ATLAS_DB_PATH", "/data/atlas.db")
        monkeypatch.setenv("DATASET_ATLAS_LABEL", "Atlas")
        for species in ("human", "mouse"):
            monkeypatch.setenv(
                f"DATASET_ATLAS_{species.upper()}_FAISS_INDEX_PATH",
                f"/data/atlas_{species}.bin",
            )
            monkeypatch.setenv(
                f"DATASET_ATLAS_{species.upper()}_RUN_MAPPING_PATH",
                f"/data/atlas_{species}.json",
            )
        settings = make_settings(datasets="main,atlas")

        atlas = settings.dataset_specs()[1]

        assert atlas.label == "Atlas"
        assert atlas.db_name == "genoar_atlas"
        assert atlas.db_path == "/data/atlas.db"
        assert atlas.species["mouse"] == {
            "model_name": "minilm",
            "index_path": "/data/atlas_mouse.bin",
            "mapping_path": "/data/atlas_mouse.json",
        }

    def test_secondary_without_db_path_has_no_fallback_file(self, monkeypatch):
        monkeypatch.setenv("DATASET_ATLAS_DB_NAME", "genoar_atlas")
        settings = make_settings(datasets="main,atlas")

        atlas = settings.dataset_specs()[1]

        assert atlas.db_path == ""
