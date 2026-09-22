"""Database connection pools.

Two backends with the same small interface (initialize / get_connection /
close_all): SQLite (file-based, default; dev and tests) and MySQL/MariaDB
(server-based; deployment). `create_pool_for(settings, spec)` picks one per
dataset from config.

The connection object yielded by both pools exposes ``.execute(sql, params)``
returning a cursor whose rows behave like mappings, so the repository layer is
backend-agnostic. SQL is written with ``?`` placeholders; the MySQL pool
rewrites them to ``%s``.
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from queue import Empty, Queue
from typing import Generator, Protocol

logger = logging.getLogger(__name__)


class SQLiteConnectionPool:
    def __init__(self, db_path: str, pool_size: int = 5):
        self._db_path = db_path
        self._pool_size = pool_size
        self._pool: Queue[sqlite3.Connection] = Queue(maxsize=pool_size)
        self._initialized = False

    def initialize(self) -> None:
        """Create pool connections. Call once at startup."""
        if self._initialized:
            return
        for _ in range(self._pool_size):
            conn = self._create_connection()
            self._pool.put(conn)
        self._initialized = True

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # Read-only DB — skip WAL mode
        try:
            conn.execute("PRAGMA query_only=ON")
        except sqlite3.OperationalError:
            pass
        # Negative means KiB rather than pages, so this is 64MB however the file
        # was built — a page-count would mean four times the memory at 16KiB pages
        # and a quarter of it at 1KiB. Per connection, and db_pool_size of them:
        # the ceiling is that product, not this number.
        #
        # 64MB because the curated corpus is about 35MB on disk: one connection
        # holds the whole file and stops evicting pages between queries. At the
        # default pool of 5 that is a 320MB ceiling — small beside the FAISS
        # indexes and embedding models the same process already carries.
        conn.execute("PRAGMA cache_size=-64000")
        return conn

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a connection from the pool."""
        if not self._initialized:
            self.initialize()
        try:
            # Wait, rather than take the Empty branch the moment the pool runs
            # dry: a burst deeper than db_pool_size is usually over in well under
            # a second, and waiting it out reuses a warm connection where giving
            # up opens and closes a new one. Long enough to absorb that burst,
            # short enough that a genuinely saturated pool still answers instead
            # of hanging — it falls through to a connection, never to an error.
            conn = self._pool.get(timeout=5.0)
        except Empty:
            # All connections busy — create a temporary one
            conn = self._create_connection()
            try:
                yield conn
            finally:
                conn.close()
            return

        try:
            yield conn
        finally:
            self._pool.put(conn)

    def close_all(self) -> None:
        """Close all pooled connections."""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except Empty:
                break
        self._initialized = False


class ConnectionPool(Protocol):
    """Common interface for both backends (used for type hints)."""

    def initialize(self) -> None: ...
    def get_connection(self): ...
    def close_all(self) -> None: ...


def _qmark_to_pyformat(sql: str) -> str:
    """Translate SQLite SQL into the form PyMySQL/MySQL expects.

    Three adjustments:
    - ``%`` is escaped to ``%%`` so PyMySQL's ``query % args`` substitution
      leaves it intact (no literal ``%`` appears in this codebase's SQL, but
      the escape keeps it safe).
    - ``?`` placeholders become ``%s``.
    - the query builder's ``ESCAPE '\\'`` clause carries a single backslash,
      which MySQL (unlike SQLite) treats as a string escape and would reject;
      doubling it to ``ESCAPE '\\\\'`` makes MySQL read one backslash, matching
      the backslash escaping ``escape_like`` applies to LIKE patterns.
    """
    sql = sql.replace("%", "%%").replace("?", "%s")
    return sql.replace("ESCAPE '\\'", "ESCAPE '\\\\'")


class _MySQLConnection:
    """Wraps a PyMySQL connection so it quacks like the sqlite3 one.

    Provides ``.execute(sql, params)`` (translating placeholders) returning a
    DictCursor, plus ``.close()``. Rows come back as dicts, matching what the
    repository layer produces from ``sqlite3.Row``.
    """

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params=()):
        cur = self._raw.cursor()
        cur.execute(_qmark_to_pyformat(sql), tuple(params))
        return cur

    def ping(self) -> None:
        self._raw.ping(reconnect=True)

    def close(self) -> None:
        self._raw.close()


class MySQLConnectionPool:
    """MySQL/MariaDB connection pool mirroring SQLiteConnectionPool's interface.

    PyMySQL is imported lazily so the SQLite backend (and the test suite) never
    require the driver to be installed.
    """

    def __init__(self, host: str, port: int, user: str, password: str,
                 db: str, pool_size: int = 5):
        self._conn_kwargs = dict(host=host, port=port, user=user,
                                 password=password, db=db)
        self._pool_size = pool_size
        self._pool: "Queue[_MySQLConnection]" = Queue(maxsize=pool_size)
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        for _ in range(self._pool_size):
            self._pool.put(self._create_connection())
        self._initialized = True

    def _create_connection(self) -> _MySQLConnection:
        import pymysql
        from pymysql.cursors import DictCursor

        raw = pymysql.connect(
            **self._conn_kwargs,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,  # read-only API; each SELECT sees fresh data
        )
        return _MySQLConnection(raw)

    @contextmanager
    def get_connection(self) -> Generator["_MySQLConnection", None, None]:
        if not self._initialized:
            self.initialize()
        try:
            # timeout as in SQLiteConnectionPool above, and for the same reason.
            conn = self._pool.get(timeout=5.0)
        except Empty:
            # All connections busy — create a temporary one
            conn = self._create_connection()
            try:
                yield conn
            finally:
                conn.close()
            return

        try:
            conn.ping()  # revive a connection dropped by the server's idle timeout
            yield conn
        finally:
            self._pool.put(conn)

    def close_all(self) -> None:
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except Empty:
                break
        self._initialized = False


def _sqlite_pool(settings, db_path: str) -> "SQLiteConnectionPool":
    _require_corpus_file(db_path)
    pool = SQLiteConnectionPool(db_path, settings.db_pool_size)
    pool.initialize()
    return pool


# What serving actually reads: samples and search (sra_core), the detail
# page's extra fields and the filter catalogue (sra_extended), study counts
# and the sibling panel (sra_series). sra_vectors is only read when building
# an index, so a serving database may omit it.
REQUIRED_TABLES = ("sra_core", "sra_extended", "sra_series")


def _require_corpus_file(db_path: str) -> None:
    """Refuse a SQLite path that does not hold a corpus.

    ``sqlite3.connect`` happily creates an empty database at any writable
    path, so a mistyped path would start up, report healthy, and serve
    nothing until the first real query. Failing here turns that into a
    startup error naming the path.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"SQLite database not found: {db_path!r}. It is not created "
            "automatically — an empty database would serve nothing."
        )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    _require_tables({row[0] for row in rows}, f"SQLite database {db_path!r}")


def _require_tables(present: set, what: str) -> None:
    missing = [t for t in REQUIRED_TABLES if t not in present]
    if missing:
        raise ValueError(
            f"{what} is missing the {', '.join(missing)} table(s) the "
            "service reads: it is not a corpus this service can serve"
        )


# The MySQL client errno range for "the server could not be reached at all":
# 2002/2003 can't connect (socket/TCP), 2006 gone away, 2013 lost connection.
# Everything else the server *said* — wrong password (1045), unknown database
# (1049), missing grants — is a misconfiguration, not an outage.
_UNREACHABLE_ERRNOS = {2002, 2003, 2006, 2013}


def _server_unreachable(exc: Exception) -> bool:
    """Whether this startup failure means the server could not be reached.

    PyMySQL wraps connection-level failures in OperationalError with the
    client errno first. An unrecognized error counts as *not* unreachable,
    so anything unclassifiable fails closed instead of falling back.
    """
    args = getattr(exc, "args", ())
    return bool(args) and args[0] in _UNREACHABLE_ERRNOS


def create_pool_for(settings, spec) -> ConnectionPool:
    """Build and connect one dataset's pool, per ``settings.db_backend``.

    The backend, host, credentials and pool size are the deployment's; the
    database the pool reaches is the dataset's. That is the whole of what a
    second dataset changes here — a MariaDB deployment gives it another schema
    name, a SQLite one another file.

    Default is ``mysql`` (MySQL/MariaDB). If the server cannot be *reached* at
    startup — and only then — fall back to this dataset's SQLite file so the
    read-only API still serves; and only when that file exists, since an empty
    auto-created SQLite file would serve nothing. Every other failure raises:
    a refused login, an unknown schema, or a schema without the serving tables
    means the deployment is wrong, and a fallback would serve an old SQLite
    file under the dataset's name with nothing but a log line saying so. The
    ``sqlite`` backend has no fallback.
    """
    backend = settings.db_backend
    if backend == "mysql":
        pool = MySQLConnectionPool(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            db=spec.db_name,
            pool_size=settings.db_pool_size,
        )
        try:
            pool.initialize()
        except Exception as exc:
            pool.close_all()
            if not _server_unreachable(exc):
                logger.error(
                    "MariaDB (%s:%s/%s) refused this deployment's "
                    "configuration — not an outage, so no SQLite fallback",
                    settings.db_host, settings.db_port, spec.db_name,
                )
                raise
            if os.path.exists(spec.db_path):
                logger.warning(
                    "MariaDB (%s:%s/%s) unreachable at startup; falling back to"
                    " SQLite at %s",
                    settings.db_host, settings.db_port, spec.db_name,
                    spec.db_path, exc_info=True,
                )
                return _sqlite_pool(settings, spec.db_path)
            logger.error(
                "MariaDB (%s:%s/%s) unreachable and no SQLite fallback file at %s",
                settings.db_host, settings.db_port, spec.db_name, spec.db_path,
            )
            raise
        try:
            # Connecting proves the schema exists, not that it holds a
            # corpus — a freshly created schema that migration never reached
            # connects fine and dies on the first real query.
            with pool.get_connection() as conn:
                rows = conn.execute(
                    "SELECT table_name AS name FROM information_schema.tables"
                    " WHERE table_schema = DATABASE()"
                ).fetchall()
            _require_tables(
                {row["name"] for row in rows},
                f"MariaDB schema {spec.db_name!r}",
            )
        except Exception:
            pool.close_all()
            raise
        return pool
    if backend != "sqlite":
        raise ValueError(f"Unknown db_backend: {backend!r} (use 'mysql' or 'sqlite')")
    return _sqlite_pool(settings, spec.db_path)
