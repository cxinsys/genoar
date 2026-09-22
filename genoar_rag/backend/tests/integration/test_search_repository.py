"""Integration tests for SearchRepository."""

from app.models.search import SearchFilters
from app.repositories.search_repository import SearchRepository


class TestSearchRepository:
    def test_no_filters_returns_all(self, db_conn):
        repo = SearchRepository(db_conn)
        results = repo.search(SearchFilters())
        assert len(results) == 10

    def test_no_filters_count(self, db_conn):
        repo = SearchRepository(db_conn)
        assert repo.search_count(SearchFilters()) == 10

    def test_tissue_filter_case_insensitive(self, db_conn):
        repo = SearchRepository(db_conn)
        # "Bone Marrow" and "bone marrow" variants
        results = repo.search(SearchFilters(tissue=["Bone Marrow"]))
        assert len(results) == 2
        for r in results:
            assert r["Run"] in ("SRR001", "SRR002")

    def test_organism_filter(self, db_conn):
        repo = SearchRepository(db_conn)
        results = repo.search(SearchFilters(organism=["Mus musculus"]))
        assert len(results) == 2
        run_ids = {r["Run"] for r in results}
        assert run_ids == {"SRR006", "SRR008"}

    def test_combined_tissue_and_organism(self, db_conn):
        repo = SearchRepository(db_conn)
        results = repo.search(SearchFilters(
            tissue=["Bone Marrow"],
            organism=["Homo sapiens"],
        ))
        assert len(results) == 2

    def test_assay_type_filter(self, db_conn):
        repo = SearchRepository(db_conn)
        results = repo.search(SearchFilters(assay_type=["ATAC-seq"]))
        assert len(results) == 2
        run_ids = {r["Run"] for r in results}
        assert run_ids == {"SRR004", "SRR008"}

    def test_library_source_filter(self, db_conn):
        repo = SearchRepository(db_conn)
        results = repo.search(SearchFilters(library_source=["GENOMIC"]))
        assert len(results) == 3

    def test_disease_filter(self, db_conn):
        repo = SearchRepository(db_conn)
        results = repo.search(SearchFilters(disease=["leukemia"]))
        assert len(results) == 1
        assert results[0]["Run"] == "SRR001"

    def test_keyword_search(self, db_conn):
        repo = SearchRepository(db_conn)
        # "bone" should match tissue "Bone Marrow" and treatment "bone treatment"
        results = repo.search(SearchFilters(keyword="bone"))
        run_ids = {r["Run"] for r in results}
        assert "SRR001" in run_ids  # tissue: Bone Marrow
        assert "SRR010" in run_ids  # treatment: bone treatment

    def test_series_filter(self, db_conn):
        repo = SearchRepository(db_conn)
        results = repo.search(SearchFilters(series="GSE004"))
        assert len(results) == 2

    def test_platform_filter(self, db_conn):
        repo = SearchRepository(db_conn)
        results = repo.search(SearchFilters(platform=["DNBSEQ"]))
        assert len(results) == 2

    def test_pagination(self, db_conn):
        repo = SearchRepository(db_conn)
        page1 = repo.search(SearchFilters(limit=3, offset=0))
        page2 = repo.search(SearchFilters(limit=3, offset=3))
        assert len(page1) == 3
        assert len(page2) == 3
        ids1 = {r["Run"] for r in page1}
        ids2 = {r["Run"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_count_with_filters(self, db_conn):
        repo = SearchRepository(db_conn)
        count = repo.search_count(SearchFilters(tissue=["Bone Marrow"]))
        assert count == 2
