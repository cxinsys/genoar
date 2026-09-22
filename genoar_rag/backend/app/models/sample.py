"""Sample-related models."""

from pydantic import BaseModel


class ExtendedField(BaseModel):
    field_name: str
    field_value: str | None = None
    data_type: str | None = None


class SampleSummary(BaseModel):
    run_id: str
    # One of the run's studies — whichever `sra_core.Series` kept.
    series: str | None = None
    # All of them. 870 of the 6,199 runs are in two, so filtering by one study
    # returned cards naming the other and nothing said why.
    all_series: list[str] = []
    biosample: str | None = None
    tissue: str | None = None
    cell_type: str | None = None
    # What the submitter wrote.
    disease: str | None = None
    # What the disease filter files it under — the nomenclaturist's UMLS concept
    # where there is one, the text above where there is not.
    #
    # Both are carried because they differ for 455 of the 584 samples that have a
    # disease at all: a page showing "Idiopathic Parkinson's disease (IPD)" is
    # filed under "Parkinson Disease", and typing what was on screen into the
    # filter found nothing. Neither value alone tells the reader that.
    disease_category: str | None = None
    organism: str | None = None
    assay_type: str | None = None
    library_source: str | None = None
    platform: str | None = None
    instrument: str | None = None
    similarity_score: float | None = None


class SampleDetail(SampleSummary):
    sample_name: str | None = None
    treatment: str | None = None
    sex: str | None = None
    age: str | None = None
    strain: str | None = None
    genotype: str | None = None
    size: float | None = None
    str_tis: str | None = None
    str_dis: str | None = None
    str_cell: str | None = None
    cui_tis: str | None = None
    cui_dis: str | None = None
    cui_cell: str | None = None
    extended_fields: list[ExtendedField] = []
