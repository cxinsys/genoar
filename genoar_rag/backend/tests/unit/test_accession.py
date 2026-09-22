"""Accession resolution: SRA run ids pass through, GEO sample ids are looked up."""

import pytest

from app.exceptions import SampleNotFoundError
from app.services import accession


class TestShapeChecks:
    @pytest.mark.parametrize("value", ["SRR001", "srr001", "ERR123", "DRR9"])
    def test_run_ids_recognized(self, value):
        assert accession.looks_like_run_id(value)

    @pytest.mark.parametrize("value", ["GSM0001", "gsm0001"])
    def test_geo_samples_recognized(self, value):
        assert accession.looks_like_geo_sample(value)
        assert not accession.looks_like_run_id(value)


class TestResolve:
    def test_run_id_passes_through(self, db_conn):
        assert accession.resolve(db_conn, "SRR001") == "SRR001"

    def test_geo_sample_resolves_to_run(self, db_conn):
        assert accession.resolve(db_conn, "GSM0001") == "SRR001"
        assert accession.resolve(db_conn, "GSM0002") == "SRR002"

    def test_surrounding_whitespace_ignored(self, db_conn):
        assert accession.resolve(db_conn, "  GSM0001 ") == "SRR001"

    def test_unknown_geo_sample_raises(self, db_conn):
        with pytest.raises(SampleNotFoundError):
            accession.resolve(db_conn, "GSM999999")

    def test_empty_raises(self, db_conn):
        with pytest.raises(SampleNotFoundError):
            accession.resolve(db_conn, "")

    def test_unknown_run_id_is_left_to_the_caller(self, db_conn):
        # resolve() does not verify run ids; the sample lookup reports absence.
        assert accession.resolve(db_conn, "SRR999") == "SRR999"

    @pytest.mark.parametrize(
        "value",
        [
            "NONEXISTENT",
            "SRR",
            "SRR001x",
            'x"; filename=pwn.exe',
            "SRR001\r\nX-Injected: 1",
            "../../etc/passwd",
            "SRR001 OR 1=1",
        ],
    )
    def test_malformed_accession_raises(self, db_conn, value):
        # The value goes on to name a file in a response header. Only a run id
        # or a GEO sample id — prefix and digits — is allowed through.
        with pytest.raises(SampleNotFoundError):
            accession.resolve(db_conn, value)


class TestShape:
    @pytest.mark.parametrize("value", ["SRR001", "err1", "DRR123456789", "GSM0001", "gsm1"])
    def test_well_formed(self, value):
        assert accession.is_well_formed(value)

    @pytest.mark.parametrize("value", ["", "SRR", "SRR-1", "GSM 1", "SRR001;", "SRR001/../x"])
    def test_malformed(self, value):
        assert not accession.is_well_formed(value)


class TestGeoAccessionForRun:
    def test_returns_the_curated_accession(self, db_conn):
        # `Sample Name` is where the filtered tables put the GSM, and where all
        # 6,199 runs carry one.
        assert accession.geo_accession_for_run(db_conn, "SRR001") == "GSM0001"

    def test_falls_back_to_the_crawl_field(self, db_conn):
        # A database restored to a point before the curated rebuild has the GSM
        # only under the name the crawler used.
        assert accession.geo_accession_for_run(db_conn, "SRR002") == "GSM0002"

    def test_none_when_run_has_no_geo_accession(self, db_conn):
        # Matches the real corpus, where only some runs carry the field.
        assert accession.geo_accession_for_run(db_conn, "SRR003") is None

    def test_none_when_sample_name_is_not_an_accession(self, db_conn):
        # `Sample Name` is the submitter's label as often as it is a GSM; a
        # value that is not one must not become a GEO link.
        assert accession.geo_accession_for_run(db_conn, "SRR004") is None
