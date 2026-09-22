"""Shared test fixtures: in-memory SQLite with representative data."""

import sqlite3

import pytest

from app.db.connection import SQLiteConnectionPool

# Schema matching production sra_hybrid.db
SCHEMA_SQL = """
CREATE TABLE sra_core (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Run TEXT UNIQUE NOT NULL,
    Series TEXT,
    BioSample TEXT,
    Sample_Name TEXT,
    STR_tis TEXT,
    STR_dis TEXT,
    STR_cell TEXT,
    tissue TEXT,
    disease_state_modified TEXT,
    cell_type TEXT,
    treatment TEXT,
    sex TEXT,
    Age TEXT,
    strain TEXT,
    genotype TEXT,
    Instrument TEXT,
    Platform TEXT,
    size REAL,
    CUI_tis TEXT,
    CUI_dis TEXT,
    CUI_cell TEXT,
    source_file TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sra_extended (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Run TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_value TEXT,
    data_type TEXT,
    source_file TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (Run) REFERENCES sra_core (Run),
    UNIQUE(Run, field_name)
);

-- Every study a run belongs to. `sra_core.Series` keeps one of them for the
-- queries that still read it; this is where a run in two studies is both.
CREATE TABLE sra_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Run TEXT NOT NULL,
    Series TEXT NOT NULL,
    UNIQUE(Run, Series),
    FOREIGN KEY (Run) REFERENCES sra_core (Run)
);

CREATE INDEX idx_core_run ON sra_core (Run);
CREATE INDEX idx_core_series ON sra_core (Series);
CREATE INDEX idx_series_run ON sra_series (Run);
CREATE INDEX idx_series_series ON sra_series (Series);
CREATE INDEX idx_core_tissue ON sra_core (STR_tis);
CREATE INDEX idx_extended_run ON sra_extended (Run);
CREATE INDEX idx_extended_field ON sra_extended (field_name);
CREATE INDEX idx_extended_field_value ON sra_extended (field_name, field_value);
"""

# Representative test data (10 samples across various filters)
CORE_DATA = [
    # (Run, Series, BioSample, Sample_Name, STR_tis, STR_dis, STR_cell, tissue, disease_state_modified, cell_type, treatment, sex, Age, strain, genotype, Instrument, Platform, size)
    ("SRR001", "GSE001", "SAMN001", None, "bone marrow", None, None, "Bone Marrow", None, "Lin-CD34+ cells", "Untreated", "male", "30", None, "WT", "Illumina NovaSeq 6000", "ILLUMINA", 1e10),
    ("SRR002", "GSE001", "SAMN002", None, "bone marrow", None, None, "Bone Marrow", None, "HSC", "Drug A", "female", "25", None, "PTPN11", "Illumina NovaSeq 6000", "ILLUMINA", 2e10),
    ("SRR003", "GSE002", "SAMN003", None, "blood", None, None, "Blood", None, "T cells", None, None, None, None, None, "Illumina HiSeq 2500", "ILLUMINA", 5e9),
    ("SRR004", "GSE002", "SAMN004", None, "blood", None, None, "blood", None, None, None, None, None, None, None, "Illumina HiSeq 2500", "ILLUMINA", 6e9),
    ("SRR005", "GSE003", "SAMN005", None, "liver", None, None, "Liver", None, "Hepatocytes", None, "male", "45", None, None, "DNBSEQ-G400", "DNBSEQ", 3e9),
    ("SRR006", "GSE003", "SAMN006", None, "liver", None, None, "Liver", None, None, "TGF-beta", None, None, "C57BL/6", None, "DNBSEQ-G400", "DNBSEQ", 4e9),
    ("SRR007", "GSE004", "SAMN007", None, "brain", None, None, "Brain", None, "Neurons", None, None, None, None, None, "Illumina NovaSeq 6000", "ILLUMINA", 8e9),
    ("SRR008", "GSE004", "SAMN008", None, "brain", None, None, "brain", None, "Astrocytes", None, None, None, None, None, "Illumina NovaSeq 6000", "ILLUMINA", 7e9),
    ("SRR009", "GSE005", "SAMN009", None, "lung", None, None, "Lung", None, None, None, "female", "60", None, None, "NextSeq 500", "ILLUMINA", 9e9),
    ("SRR010", "GSE005", "SAMN010", None, "lung", None, None, "Lung", None, "Epithelial", "bone treatment", None, None, None, None, "NextSeq 500", "ILLUMINA", 1e10),
]

EXTENDED_DATA = [
    # Organism
    ("SRR001", "Organism", "Homo sapiens", "text"),
    ("SRR002", "Organism", "Homo sapiens", "text"),
    ("SRR003", "Organism", "Homo sapiens", "text"),
    ("SRR004", "Organism", "Homo sapiens", "text"),
    ("SRR005", "Organism", "Homo sapiens", "text"),
    ("SRR006", "Organism", "Mus musculus", "text"),
    ("SRR007", "Organism", "Homo sapiens", "text"),
    ("SRR008", "Organism", "Mus musculus", "text"),
    ("SRR009", "Organism", "Homo sapiens", "text"),
    ("SRR010", "Organism", "Homo sapiens", "text"),
    # Assay Type
    ("SRR001", "Assay Type", "RNA-Seq", "text"),
    ("SRR002", "Assay Type", "RNA-Seq", "text"),
    ("SRR003", "Assay Type", "RNA-Seq", "text"),
    ("SRR004", "Assay Type", "ATAC-seq", "text"),
    ("SRR005", "Assay Type", "RNA-Seq", "text"),
    ("SRR006", "Assay Type", "ChIP-Seq", "text"),
    ("SRR007", "Assay Type", "RNA-Seq", "text"),
    ("SRR008", "Assay Type", "ATAC-seq", "text"),
    ("SRR009", "Assay Type", "RNA-Seq", "text"),
    ("SRR010", "Assay Type", "RNA-Seq", "text"),
    # LibrarySource
    ("SRR001", "LibrarySource", "TRANSCRIPTOMIC", "text"),
    ("SRR002", "LibrarySource", "TRANSCRIPTOMIC", "text"),
    ("SRR003", "LibrarySource", "TRANSCRIPTOMIC SINGLE CELL", "text"),
    ("SRR004", "LibrarySource", "GENOMIC", "text"),
    ("SRR005", "LibrarySource", "TRANSCRIPTOMIC", "text"),
    ("SRR006", "LibrarySource", "GENOMIC", "text"),
    ("SRR007", "LibrarySource", "TRANSCRIPTOMIC SINGLE CELL", "text"),
    ("SRR008", "LibrarySource", "GENOMIC", "text"),
    ("SRR009", "LibrarySource", "TRANSCRIPTOMIC", "text"),
    ("SRR010", "LibrarySource", "TRANSCRIPTOMIC", "text"),
    # Disease fields (various field_names)
    ("SRR001", "disease_state", "leukemia", "text"),
    ("SRR003", "disease", "healthy", "text"),
    ("SRR005", "diagnosis", "hepatitis", "text"),
    ("SRR007", "condition", "alzheimer", "text"),
    ("SRR009", "disease_state", "lung cancer", "text"),
    # The nomenclaturist found a concept for one of them, so the resolved value
    # below has something to prefer over the word the submitter chose.
    ("SRR003", "disease (UMLS) - 1st level", "Control Groups", "text"),
    # What the loader writes down: one value per run, the concept where there is
    # one and the submitter's own words where there is not. The disease filter
    # reads this and nothing else, which is why the fixture carries it — a
    # database that has not been through the loader has no disease filter, the
    # same way it has no vector index.
    ("SRR001", "disease_resolved", "leukemia", "text"),
    ("SRR003", "disease_resolved", "Control Groups", "text"),
    ("SRR005", "disease_resolved", "hepatitis", "text"),
    ("SRR007", "disease_resolved", "alzheimer", "text"),
    ("SRR009", "disease_resolved", "lung cancer", "text"),
    # Extra extended fields for detail tests
    ("SRR001", "FMT", "FASTQ", "text"),
    ("SRR001", "Sample Name", "GSM0001", "text"),
    ("SRR001", "library_layout", "PAIRED", "text"),
    # GEO sample ids, under both names the corpus has used. The curated tables
    # call it `Sample Name` (SRR001, above) and the original crawl called it
    # `GEO_Accession (exp)` (SRR002) — a database restored to a pre-rebuild point
    # has only the second, so both have to resolve. SRR003 carries neither, which
    # is the real corpus's shape too: not every run has a GSM.
    ("SRR002", "GEO_Accession (exp)", "GSM0002", "text"),
    # A `Sample Name` that is the submitter's own label rather than an accession.
    # 15 runs in the curated set look like this, and none of them should produce
    # a GEO link.
    ("SRR004", "Sample Name", "L1-1", "text"),
    # Download hints: SRR001 has the crawler's recorded object URL and a size,
    # SRR002 has neither so the URL has to be derived from the archive layout.
    ("SRR001", "path", "https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR001/SRR001", "text"),
    ("SRR001", "Bytes", "10921218304", "text"),
]


def _seed_db(conn: sqlite3.Connection) -> None:
    """Populate test database with representative data."""
    conn.executescript(SCHEMA_SQL)

    insert_core = """
    INSERT INTO sra_core (Run, Series, BioSample, Sample_Name, STR_tis, STR_dis, STR_cell,
        tissue, disease_state_modified, cell_type, treatment, sex, Age, strain, genotype,
        Instrument, Platform, size)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    conn.executemany(insert_core, CORE_DATA)

    insert_ext = """
    INSERT INTO sra_extended (Run, field_name, field_value, data_type)
    VALUES (?, ?, ?, ?)
    """
    conn.executemany(insert_ext, EXTENDED_DATA)

    # Every run under the study its core row names, plus SRR001 under a second
    # study it shares. That second study exists only here — which is the case the
    # real data has 128 of, and the one that reading sra_core.Series misses.
    conn.executemany(
        "INSERT INTO sra_series (Run, Series) VALUES (?, ?)",
        [(row[0], row[1]) for row in CORE_DATA if row[1]] + [("SRR001", "GSE999999")],
    )
    conn.commit()


@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory) -> str:
    """Create a session-scoped test SQLite database and return its path."""
    db_file = tmp_path_factory.mktemp("db") / "test_sra.db"
    conn = sqlite3.connect(str(db_file))
    _seed_db(conn)
    conn.close()
    return str(db_file)


@pytest.fixture(scope="session")
def db_pool(test_db_path) -> SQLiteConnectionPool:
    """Session-scoped connection pool for integration tests."""
    pool = SQLiteConnectionPool(test_db_path, pool_size=2)
    pool.initialize()
    yield pool
    pool.close_all()


@pytest.fixture
def db_conn(db_pool) -> sqlite3.Connection:
    """Yield a single connection for a test, then return to pool."""
    with db_pool.get_connection() as conn:
        yield conn
