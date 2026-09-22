"""Metadata export: curated columns only, streamed across the whole result set."""

from app.models.search import SearchFilters
from app.services import export_service

# The fixtures that build a results directory live with the download tests.
pytest_plugins = ("tests.unit.test_download_service",)

# Fields the paper keeps out of the download section (submitter-written text).
WITHHELD = ("sample_name", "treatment", "sex", "age", "strain", "genotype")


def _filters(**overrides):
    base = {"offset": 0}
    base.update(overrides)
    return SearchFilters(**base)


class TestColumns:
    def test_headers_are_the_allowlist(self):
        assert export_service.HEADERS[0] == "run_id"
        assert "tissue" in export_service.HEADERS
        assert "umls_disease_cui" in export_service.HEADERS

    def test_submitter_written_fields_are_withheld(self):
        for column in WITHHELD:
            assert column not in export_service.HEADERS

    def test_rows_carry_exactly_the_declared_columns(self, db_conn):
        row = next(export_service.iter_rows(db_conn, _filters(), max_rows=1))
        assert tuple(row) == export_service.HEADERS


class TestRows:
    def test_curated_and_enriched_values_are_populated(self, db_conn):
        rows = list(export_service.iter_rows(db_conn, _filters(), max_rows=10))
        first = next(r for r in rows if r["run_id"] == "SRR001")
        assert first["tissue"] == "Bone Marrow"
        assert first["cell_type"] == "Lin-CD34+ cells"
        assert first["organism"] == "Homo sapiens"
        assert first["assay_type"] == "RNA-Seq"

    def test_series_carries_every_study_the_run_is_in(self, db_conn):
        """A run in two studies exports both, semicolon-separated.

        `sra_core.Series` keeps one of them, so a download built from that column
        told the reader a sample belonged to one study when the curated tables
        say two. 870 of the 6,199 runs are in this position.
        """
        rows = list(export_service.iter_rows(db_conn, _filters(), max_rows=10))
        by_run = {r["run_id"]: r for r in rows}
        assert by_run["SRR001"]["series"] == "GSE001;GSE999999"
        assert by_run["SRR002"]["series"] == "GSE001"

    def test_run_ids_decide_the_order(self, db_conn):
        """An AI export arrives ranked, and leaves ranked.

        The rows are read back with SQL `IN`, which returns them by run id — so
        the file held the same twenty samples as the screen in a different
        order, under a button labelled "download these results".
        """
        wanted = ["SRR005", "SRR001", "SRR003"]
        rows = list(
            export_service.iter_rows(db_conn, _filters(), max_rows=10, run_ids=wanted)
        )
        assert [r["run_id"] for r in rows] == wanted

    def test_no_run_ids_leaves_the_query_order_alone(self, db_conn):
        rows = list(export_service.iter_rows(db_conn, _filters(), max_rows=10))
        assert [r["run_id"] for r in rows] == sorted(r["run_id"] for r in rows)

    def test_max_rows_is_honoured(self, db_conn):
        assert len(list(export_service.iter_rows(db_conn, _filters(), max_rows=4))) == 4

    def test_streams_past_the_chunk_size(self, db_conn, monkeypatch):
        """Several chunks over the 10-sample fixture, to exercise the loop.

        `limit` on the filters is deliberately smaller than the fixture: an
        export is not a page, so the model's paging must not reach it. When it
        did, an export of everything returned one page of it."""
        monkeypatch.setattr(export_service, "CHUNK_SIZE", 3)
        rows = list(export_service.iter_rows(db_conn, _filters(limit=3), max_rows=100))
        assert len(rows) == 10
        assert len({r["run_id"] for r in rows}) == 10

    def test_filters_are_applied(self, db_conn):
        rows = list(
            export_service.iter_rows(db_conn, _filters(organism=["Mus musculus"]), max_rows=100)
        )
        assert rows
        assert {r["organism"] for r in rows} == {"Mus musculus"}


class TestCsv:
    def test_header_comes_first(self, db_conn):
        chunks = list(export_service.iter_csv(db_conn, _filters(), max_rows=2))
        assert chunks[0].strip().split(",") == list(export_service.HEADERS)

    def test_one_chunk_per_row_after_the_header(self, db_conn):
        chunks = list(export_service.iter_csv(db_conn, _filters(), max_rows=2))
        assert len(chunks) == 3
        assert "SRR001" in chunks[1]


class TestProcessingColumns:
    """The parameters travel with the metadata, not only with the file.

    Someone who exports a filtered set and then downloads the matrices needs to
    know how each was made, and the sheet is where they are looking.
    """

    def test_columns_are_last(self):
        assert export_service.HEADERS[-3:] == (
            "preprocessing_software",
            "preprocessing_chemistry",
            "preprocessing_reference",
        )

    def test_filled_for_a_run_with_a_matrix(self, db_conn, cellranger_results):
        row = next(
            r
            for r in export_service.iter_rows(db_conn, _filters(), max_rows=100)
            if r["run_id"] == "SRR001"
        )
        assert row["preprocessing_software"] == "cellranger-8.0.1"
        assert row["preprocessing_chemistry"] == "Single Cell 3' v2"
        assert row["preprocessing_reference"] == "GRCh38"

    def test_blank_for_a_run_without_one(self, db_conn, cellranger_results):
        # Only SRR001 has a directory under the results dir.
        row = next(
            r
            for r in export_service.iter_rows(db_conn, _filters(), max_rows=100)
            if r["run_id"] == "SRR002"
        )
        assert row["preprocessing_software"] is None
        assert row["preprocessing_chemistry"] is None

    def test_columns_exist_with_no_results_directory(self, db_conn, no_processed_source):
        """The sheet has the same shape either way. A deployment with nothing
        processed should still produce a CSV a script can read."""
        row = next(export_service.iter_rows(db_conn, _filters(), max_rows=1))
        assert tuple(row) == export_service.HEADERS
        assert row["preprocessing_software"] is None
