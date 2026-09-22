"""Integration tests for ExtendedRepository."""

from app.repositories.extended_repository import ExtendedRepository


class TestExtendedRepository:
    def test_get_fields_for_run(self, db_conn):
        repo = ExtendedRepository(db_conn)
        fields = repo.get_fields_for_run("SRR001")
        assert len(fields) >= 3  # Organism, Assay Type, LibrarySource, ...
        names = {f["field_name"] for f in fields}
        assert "Organism" in names
        assert "Assay Type" in names

    def test_get_fields_for_run_empty(self, db_conn):
        repo = ExtendedRepository(db_conn)
        fields = repo.get_fields_for_run("NONEXISTENT")
        assert fields == []

    def test_get_fields_for_runs_batch(self, db_conn):
        repo = ExtendedRepository(db_conn)
        results = repo.get_fields_for_runs(["SRR001", "SRR002"], "Organism")
        assert len(results) == 2
        for r in results:
            assert r["field_value"] == "Homo sapiens"

    def test_get_fields_for_runs_empty_list(self, db_conn):
        repo = ExtendedRepository(db_conn)
        results = repo.get_fields_for_runs([], "Organism")
        assert results == []

    def test_get_distinct_values(self, db_conn):
        repo = ExtendedRepository(db_conn)
        values = repo.get_distinct_values("Organism")
        assert len(values) == 2  # Homo sapiens, Mus musculus
        labels = {v["value"] for v in values}
        assert "Homo sapiens" in labels

    def test_get_disease_values(self, db_conn):
        repo = ExtendedRepository(db_conn)
        values = repo.get_disease_values(("disease_state", "disease", "diagnosis", "condition"))
        assert len(values) >= 4  # leukemia, healthy, hepatitis, alzheimer, lung cancer
