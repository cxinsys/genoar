"""Base repository with shared DB access pattern.

Works over any connection exposing ``.execute(sql, params)`` that returns a
cursor of mapping-like rows — both the sqlite3 connection (with a Row factory)
and the MySQL wrapper in ``app.db.connection`` qualify.
"""

from typing import Any


class BaseRepository:
    """Rows from whichever dataset the connection reaches.

    A repository is handed a connection and knows nothing about datasets. It
    used to: when the two bodies of data shared one database, every query
    carried a clause naming which of them it meant, and every repository had
    to remember to add it. Keeping the datasets in separate databases moves
    that decision to where the connection is acquired, and nineteen clauses
    stop existing.
    """

    def __init__(self, conn):
        self._conn = conn

    def _fetchone(self, sql: str, params: list | tuple = ()) -> dict | None:
        cur = self._conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def _fetchall(self, sql: str, params: list | tuple = ()) -> list[dict]:
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def _iterall(self, sql: str, params: list | tuple = (), chunk: int = 500):
        """Yield rows in chunks, holding only one chunk at a time.

        For results too large to want in memory at once. `fetchmany` leaves the
        rest with the driver, so a caller can stream two hundred thousand rows
        through a response without the list of them ever existing.
        """
        cur = self._conn.execute(sql, params)
        while True:
            rows = cur.fetchmany(chunk)
            if not rows:
                return
            yield [dict(row) for row in rows]

    def _fetchval(self, sql: str, params: list | tuple = ()) -> Any:
        cur = self._conn.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return None
        # First column, regardless of backend: sqlite3.Row is index-addressable
        # while the MySQL DictCursor returns a dict keyed by column name.
        return next(iter(dict(row).values()), None)
