"""Integration tests for SQLiteConnectionPool."""

from app.db.connection import SQLiteConnectionPool


class TestSQLiteConnectionPool:
    def test_pool_initializes(self, db_pool):
        assert db_pool._initialized is True

    def test_get_connection_returns_rows(self, db_conn):
        cur = db_conn.execute("SELECT COUNT(*) FROM sra_core")
        count = cur.fetchone()[0]
        assert count == 10

    def test_row_factory_works(self, db_conn):
        cur = db_conn.execute("SELECT Run, tissue FROM sra_core WHERE Run='SRR001'")
        row = cur.fetchone()
        assert row["Run"] == "SRR001"
        assert row["tissue"] == "Bone Marrow"

    def test_wal_mode(self, db_conn):
        cur = db_conn.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
        assert mode == "wal"

    def test_pool_close_all(self, test_db_path):
        pool = SQLiteConnectionPool(test_db_path, pool_size=2)
        pool.initialize()
        pool.close_all()
        assert pool._initialized is False
