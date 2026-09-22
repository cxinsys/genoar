"""Which tables the MariaDB migrator copies.

A source missing a table the service reads produces a target that connects and
then fails at query time — table-not-found on five endpoints of a running
deployment, which is worse than not connecting at all. So the set is checked
before anything is copied.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "migrate_sqlite_to_mysql.py"
_spec = importlib.util.spec_from_file_location("migrate_sqlite_to_mysql", _PATH)
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


def cursor_over(tables: list[str]):
    conn = sqlite3.connect(":memory:")
    for table in tables:
        conn.execute(f"CREATE TABLE {table} (Run TEXT)")
    return conn.cursor()


class TestSourceTables:
    def test_a_complete_source_is_accepted(self):
        assert migrate.source_tables(cursor_over(migrate.TABLES)) == list(
            migrate.TABLES
        )

    def test_a_missing_required_table_still_stops_the_run(self):
        with pytest.raises(SystemExit) as excinfo:
            migrate.source_tables(cursor_over(["sra_core", "sra_extended"]))
        assert "sra_series" in str(excinfo.value)

    def test_every_table_it_may_copy_has_ddl(self):
        """A table chosen for copying with no CREATE would fail mid-migration."""
        for table in migrate.TABLES:
            assert table in migrate.SCHEMA, table
