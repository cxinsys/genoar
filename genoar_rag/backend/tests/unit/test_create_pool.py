"""What create_pool_for connects a dataset to, and what it refuses to.

The guarded failure is serving the wrong corpus: a MariaDB dataset must not
quietly fall back to a file it was never given, and a SQLite dataset must not
quietly become a fresh empty database because its path was mistyped.
"""

import sqlite3
from types import SimpleNamespace

import pytest

from app.db.connection import create_pool_for


def make_settings(**overrides):
    base = dict(
        db_backend="mysql",
        db_host="127.0.0.1",
        db_port=1,  # nothing listens here: connecting fails immediately
        db_user="genoar",
        db_password="",
        db_pool_size=1,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_spec(**overrides):
    base = dict(name="atlas", db_path="", db_name="genoar_atlas")
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def corpus_db(tmp_path):
    """A SQLite file that looks like a served corpus."""
    path = tmp_path / "corpus.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sra_core (Run TEXT UNIQUE NOT NULL)")
    conn.execute("CREATE TABLE sra_extended (Run TEXT, field_name TEXT)")
    conn.execute("CREATE TABLE sra_series (Run TEXT, Series TEXT)")
    conn.execute("INSERT INTO sra_core VALUES ('SRR0000001')")
    conn.commit()
    conn.close()
    return str(path)


class TestMariaDBFallback:
    def test_unreachable_server_without_fallback_file_raises(self):
        with pytest.raises(Exception):
            create_pool_for(make_settings(), make_spec(db_path=""))

    @pytest.mark.parametrize(
        "errno,message",
        [
            (1045, "Access denied for user 'genoar'@'%'"),
            (1049, "Unknown database 'genoar_atlas'"),
        ],
    )
    def test_a_refused_login_or_unknown_schema_never_falls_back(
        self, corpus_db, monkeypatch, errno, message
    ):
        """Only an unreachable server may fall back to SQLite.

        Authentication and provisioning errors mean the operator got the
        deployment wrong — falling back would serve an old SQLite file under
        the dataset's name with nothing but a log line saying so, which is
        the silent wrong-corpus path this feature exists to close.
        """
        import pymysql

        from app.db import connection as connection_module

        class RefusingPool:
            def __init__(self, **kwargs):
                pass

            def initialize(self):
                raise pymysql.err.OperationalError(errno, message)

            def close_all(self):
                pass

        monkeypatch.setattr(
            connection_module, "MySQLConnectionPool", RefusingPool
        )

        with pytest.raises(pymysql.err.OperationalError, match=str(errno)):
            create_pool_for(make_settings(), make_spec(db_path=corpus_db))

    def test_unreachable_server_falls_back_to_the_dataset_own_file(
        self, corpus_db
    ):
        pool = create_pool_for(make_settings(), make_spec(db_path=corpus_db))
        try:
            with pool.get_connection() as conn:
                rows = conn.execute("SELECT Run FROM sra_core")
                assert [r["Run"] for r in rows] == ["SRR0000001"]
        finally:
            pool.close_all()


class TestSQLiteRefusesWhatItCannotServe:
    def test_missing_file_raises_instead_of_creating_an_empty_db(
        self, tmp_path
    ):
        missing = tmp_path / "nowhere" / "corpus.db"
        settings = make_settings(db_backend="sqlite")

        with pytest.raises(Exception, match=str(missing)):
            create_pool_for(settings, make_spec(db_path=str(missing)))

        assert not missing.exists()

    def test_file_without_the_core_table_raises(self, tmp_path):
        path = tmp_path / "empty.db"
        sqlite3.connect(path).close()  # exists, but holds no schema
        settings = make_settings(db_backend="sqlite")

        with pytest.raises(Exception, match="sra_core"):
            create_pool_for(settings, make_spec(db_path=str(path)))

    def test_a_core_only_file_is_not_a_corpus_either(self, tmp_path):
        """Filters, series and stats all read beyond sra_core.

        A database holding sra_core alone must not pass startup: it would
        report healthy and then fail its first filter or series request with
        table-not-found.
        """
        path = tmp_path / "core_only.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE sra_core (Run TEXT UNIQUE NOT NULL)")
        conn.commit()
        conn.close()
        settings = make_settings(db_backend="sqlite")

        with pytest.raises(Exception, match="sra_extended"):
            create_pool_for(settings, make_spec(db_path=str(path)))

    def test_valid_file_serves(self, corpus_db):
        settings = make_settings(db_backend="sqlite")
        pool = create_pool_for(settings, make_spec(db_path=corpus_db))
        try:
            with pool.get_connection() as conn:
                rows = conn.execute("SELECT COUNT(*) AS n FROM sra_core")
                assert next(iter(rows))["n"] == 1
        finally:
            pool.close_all()
