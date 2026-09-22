#!/usr/bin/env python3
"""
Copy the SRA hybrid database wholesale from SQLite into MySQL/MariaDB.

The pipeline builds `sra_hybrid.db` as a SQLite file; deployment runs the RAG
backend against MariaDB. This script creates the equivalent schema on the
MySQL/MariaDB server and copies every row of every table over, ids included.

Modes:
  --drop    Full re-import: DROP and recreate the tables, then copy every row
            (ids included). **Use this for a refresh, not only the first load.**
  --upsert  Incremental: INSERT ... ON DUPLICATE KEY UPDATE keyed on the unique
            columns (Run / Run+field_name). Adds new rows and updates changed
            ones without dropping the tables, so the DB stays queryable.
            It never deletes. Rows removed from the source stay live in the
            target, and the row-count check passes because it only requires
            dst >= src.

`--upsert` is for a source that only ever grows. This one can shrink: cutting
the 197,757-run GEO crawl down to the 6,199 curated runs is a change an upsert
cannot express, and it would leave 191,558 dropped rows still being served
under a migration that reports success. Reach for `--drop` unless you can say
why the source cannot have lost a row.

Usage:
    # first load, and every refresh
    python scripts/migrate_sqlite_to_mysql.py \
        --sqlite ../data/sra_hybrid.db \
        --host 127.0.0.1 --user genoar --password *** --db genoar --drop

    # only when the source is known to be a superset of the target
    python scripts/migrate_sqlite_to_mysql.py \
        --sqlite ../data/sra_hybrid.db \
        --host 127.0.0.1 --user genoar --password *** --db genoar --upsert

Connection settings fall back to the same DB_* environment variables the
backend reads (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME), so in a
configured environment you can run it with just --sqlite ... --drop.
"""

import argparse
import os
import sqlite3
import sys

# Tables to copy, in FK-safe order (sra_core before sra_extended).
#
# `sra_series` is not optional. The service reads it for the study count, the
# series filter, a sample's `all_series`, the sibling panel and the export's
# series column — so a MariaDB built without it connects successfully and then
# fails those five requests with table-not-found, which is worse than not
# connecting at all.
TABLES = ["sra_core", "sra_extended", "sra_vectors", "sra_series"]

# One dataset per run. A deployment serving two bodies of data migrates each
# into its own schema — `--sqlite <that dataset's file> --db <that schema>` —
# because that is what keeping them apart means here.

# MariaDB/MySQL DDL mirroring the SQLite schema. Every text column is TEXT (the
# source data is free-form and some values are long, e.g. Age), and indexes on
# text columns use a prefix length so no value can ever be "too long" for the
# column while the index still works. utf8mb4 throughout. The SQLite FK on
# sra_extended.Run is dropped here: SQLite does not enforce it either
# (foreign_keys pragma is never set), and omitting it keeps the load
# order-independent.
SCHEMA = {
    "sra_core": """
        CREATE TABLE IF NOT EXISTS sra_core (
            id                     BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            Run                    TEXT NOT NULL,
            Series                 TEXT,
            BioSample              TEXT,
            Sample_Name            TEXT,
            STR_tis                TEXT,
            STR_dis                TEXT,
            STR_cell               TEXT,
            tissue                 TEXT,
            disease_state_modified TEXT,
            cell_type              TEXT,
            treatment              TEXT,
            sex                    TEXT,
            Age                    TEXT,
            strain                 TEXT,
            genotype               TEXT,
            Instrument             TEXT,
            Platform               TEXT,
            size                   DOUBLE,
            CUI_tis                TEXT,
            CUI_dis                TEXT,
            CUI_cell               TEXT,
            source_file            TEXT,
            created_at             TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at             TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_core_run (Run(64)),
            KEY idx_core_series (Series(64)),
            KEY idx_core_tissue (STR_tis(191)),
            KEY idx_core_disease (STR_dis(191)),
            KEY idx_core_cell (STR_cell(191))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "sra_extended": """
        CREATE TABLE IF NOT EXISTS sra_extended (
            id          BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            Run         TEXT NOT NULL,
            field_name  TEXT NOT NULL,
            field_value TEXT,
            data_type   TEXT,
            source_file TEXT,
            created_at  TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_extended_run_field (Run(64), field_name(128)),
            KEY idx_extended_field (field_name(128))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "sra_vectors": """
        CREATE TABLE IF NOT EXISTS sra_vectors (
            id             BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            Run            TEXT NOT NULL,
            embedding_text MEDIUMTEXT NOT NULL,
            vector_index   INT,
            created_at     TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_vectors_run (Run(64))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # One row per run–study pair. 870 of the 6,199 runs belong to two studies,
    # which is why this cannot be a column on sra_core: 128 of the 888 studies
    # exist only as a run's second membership.
    "sra_series": """
        CREATE TABLE IF NOT EXISTS sra_series (
            id     BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            Run    TEXT NOT NULL,
            Series TEXT NOT NULL,
            UNIQUE KEY uq_series_run_series (Run(64), Series(64)),
            KEY idx_series_run (Run(64)),
            KEY idx_series_series (Series(64))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Copy sra_hybrid.db from SQLite to MySQL/MariaDB")
    here = os.path.dirname(os.path.abspath(__file__))
    default_sqlite = os.path.normpath(os.path.join(here, "..", "..", "data", "sra_hybrid.db"))
    ap.add_argument("--sqlite", default=default_sqlite,
                    help=f"Source SQLite file (default: {default_sqlite})")
    ap.add_argument("--host", default=os.environ.get("DB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("DB_PORT", "3306")))
    ap.add_argument("--user", default=os.environ.get("DB_USER", "genoar"))
    ap.add_argument("--password", default=os.environ.get("DB_PASSWORD", ""))
    ap.add_argument("--db", default=os.environ.get("DB_NAME", "genoar"))
    ap.add_argument("--drop", action="store_true",
                    help="DROP and recreate the tables before copying (full re-import)")
    ap.add_argument("--upsert", action="store_true",
                    help="Non-destructive incremental copy: INSERT ... ON DUPLICATE KEY "
                         "UPDATE keyed on the unique columns (Run / Run+field_name). Adds new "
                         "rows and updates changed ones without dropping the tables, so the DB "
                         "stays queryable. Rows deleted from the source are NOT removed.")
    ap.add_argument("--batch-size", type=int, default=1000)
    return ap.parse_args()


def sqlite_columns(scur, table: str) -> list[str]:
    scur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in scur.fetchall()]


def source_tables(scur) -> list[str]:
    """The tables to copy, refusing to start if the source is missing any.

    Copying whatever happens to be there lets a source without `sra_series`
    migrate cleanly and report success, with the failure surfacing afterwards
    as table-not-found on five endpoints of a running deployment. Every table
    in TABLES is required by the service, so an absent one is a broken source,
    not a smaller one.
    """
    scur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    present = {row[0] for row in scur.fetchall()}
    missing = [t for t in TABLES if t not in present]
    if missing:
        raise SystemExit(
            f"Source is missing required table(s): {', '.join(missing)}.\n"
            f"The service reads all of {', '.join(TABLES)}; migrating without one"
            f" produces a database that connects and then fails at query time.\n"
            f"Rebuild the SQLite source so it carries every serving table first."
        )
    return list(TABLES)


def copy_table(scur, mcur, table: str, batch_size: int, upsert: bool = False) -> int:
    cols = sqlite_columns(scur, table)
    # In upsert mode, let MariaDB keep its own auto-increment id and match rows
    # on the business unique key (Run / Run+field_name). Copying the source id
    # could collide with the primary key of a different row if the source
    # renumbered its ids between builds.
    insert_cols = [c for c in cols if c != "id"] if upsert else cols
    col_list = ", ".join(f"`{c}`" for c in insert_cols)
    placeholders = ", ".join(["%s"] * len(insert_cols))
    insert_sql = f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders})"
    if upsert:
        updates = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in insert_cols)
        insert_sql += f" ON DUPLICATE KEY UPDATE {updates}"

    scur.execute(f"SELECT {col_list} FROM `{table}`")
    total = 0
    while True:
        rows = scur.fetchmany(batch_size)
        if not rows:
            break
        mcur.executemany(insert_sql, rows)
        total += len(rows)
        print(f"    {table}: {total} rows", end="\r", flush=True)
    verb = "upserted" if upsert else "copied"
    print(f"    {table}: {total} rows {verb}.        ")
    return total


def main() -> int:
    args = parse_args()

    try:
        import pymysql
    except ImportError:
        print("error: PyMySQL is required (pip install pymysql).", file=sys.stderr)
        return 2

    if not os.path.exists(args.sqlite):
        print(f"error: SQLite file not found: {args.sqlite}", file=sys.stderr)
        return 2

    sconn = sqlite3.connect(args.sqlite)
    scur = sconn.cursor()
    tables = source_tables(scur)
    if not tables:
        print("error: no known tables found in the SQLite source.", file=sys.stderr)
        return 2

    mconn = pymysql.connect(
        host=args.host, port=args.port, user=args.user,
        password=args.password, db=args.db, charset="utf8mb4", autocommit=False,
    )
    mcur = mconn.cursor()

    mode = "upsert (incremental)" if args.upsert else ("drop + full copy" if args.drop else "full copy")
    print(f"source: {args.sqlite}")
    print(f"target: {args.user}@{args.host}:{args.port}/{args.db}")
    print(f"tables: {', '.join(tables)}")
    print(f"mode:   {mode}")

    mcur.execute("SET FOREIGN_KEY_CHECKS=0")
    for table in tables:
        if args.drop:
            mcur.execute(f"DROP TABLE IF EXISTS `{table}`")
        mcur.execute(SCHEMA[table])
    mconn.commit()

    grand_total = 0
    for table in tables:
        grand_total += copy_table(scur, mcur, table, args.batch_size, upsert=args.upsert)
    mcur.execute("SET FOREIGN_KEY_CHECKS=1")
    mconn.commit()

    # Verify counts. Full copy expects src == dst; upsert expects dst >= src
    # (every source row is present; the target may also keep rows that were
    # deleted from the source).
    print("verifying row counts:")
    ok = True
    for table in tables:
        scur.execute(f"SELECT COUNT(*) FROM `{table}`")
        src_n = scur.fetchone()[0]
        mcur.execute(f"SELECT COUNT(*) FROM `{table}`")
        dst_n = mcur.fetchone()[0]
        good = (dst_n >= src_n) if args.upsert else (src_n == dst_n)
        ok = ok and good
        extra = f" (+{dst_n - src_n} target-only)" if args.upsert and dst_n > src_n else ""
        print(f"    {table}: sqlite={src_n} mysql={dst_n}{extra} [{'OK' if good else 'MISMATCH'}]")
        if args.upsert and dst_n > src_n:
            # Passing the count check does not mean the target matches the
            # source. Upsert never deletes, so when the source is cut down —
            # the 197,757-row crawl down to the 6,199 curated runs is one such
            # cut — the removed rows stay live and served. Saying "OK" and
            # nothing else is how a target keeps serving a population the
            # source does not have.
            print(
                f"      WARNING: {dst_n - src_n} rows in MariaDB are not in the source."
                f" Upsert does not remove them. Re-run with --drop if the source"
                f" population shrank."
            )

    sconn.close()
    mconn.close()
    verb = "upserted" if args.upsert else "copied"
    print(f"done. {grand_total} rows {verb} across {len(tables)} tables.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
