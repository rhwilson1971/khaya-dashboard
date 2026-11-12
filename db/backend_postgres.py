"""PostgreSQL backend for Khaya's database; requires psycopg 3.

SQL uses PostgreSQL ``%s`` placeholders. Writers call :meth:`begin_write` to
take an application-wide advisory lock, serializing concurrent topology edits
and health writes for the rest of the transaction.
"""
from contextlib import contextmanager
from urllib.parse import urlsplit
from db.core import Backend

# Application-wide advisory-lock key. Every writer takes it at the start of
# its transaction, so concurrent writes cannot bypass cycle checks.
ADVISORY_LOCK_ID = 746812
# Bound every statement to keep a slow query from holding the write lock.
STATEMENT_TIMEOUT_MS = 15000


class PostgresSession:
    """Execute SQL statements against one psycopg connection."""

    def __init__(self, conn):
        """Attach the transaction-scoped connection being wrapped."""
        self._conn = conn

    def execute(self, sql, params=()):
        """Run one statement and return its cursor."""
        return self._conn.execute(sql, params)

    def executemany(self, sql, rows):
        """Run one statement for every parameter sequence."""
        cursor = self._conn.cursor()
        cursor.executemany(sql, rows)
        return cursor

    def begin_write(self):
        """Take the advisory write lock for the current transaction."""
        self._conn.execute(f'SELECT pg_advisory_xact_lock({ADVISORY_LOCK_ID})')


class PostgresBackend(Backend):
    """Connect to a networked PostgreSQL server with short transactions.

    Each operation opens a connection, runs one transaction, and closes it.
    For this dashboard's traffic (a poll every few seconds and one write per
    ICMP check) that stays comfortably cheap on a home cluster and keeps
    failure handling simple.
    """

    scheme = 'postgres'

    def __init__(self, url):
        """Store a postgres:// or postgresql:// connection URL."""
        parts = urlsplit(url)
        if parts.scheme not in ('postgres', 'postgresql'):
            raise ValueError(f'Expected a postgresql URL, got {url!r}.')
        if not parts.hostname:
            raise ValueError('The database URL must name a host, such as postgresql://khaya:pass@khaya-db:5432/khaya.')
        self._url = url

    @property
    def url(self):
        return self._url

    def _connect(self):
        """Import psycopg lazily and open one connection."""
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError('The PostgreSQL backend needs psycopg 3. Install it with: pip install "psycopg[binary]"') from exc
        return psycopg.connect(
            self._url,
            row_factory=dict_row,
            connect_timeout=10,
            options=f'-c statement_timeout={STATEMENT_TIMEOUT_MS}',
        )

    @contextmanager
    def connection(self):
        """Yield a session; commit or roll back and close."""
        conn = self._connect()
        try:
            with conn:
                yield PostgresSession(conn)
        finally:
            conn.close()

    def _list_tables(self, db):
        return [row['table_name'] for row in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")]


def resync_identity(db, table, column='id'):
    """Advance a table's identity sequence past explicitly restored ids.

    Restores insert explicit ids, which identity columns accept but do not
    advance; call this within the restore transaction so new rows continue
    the sequence instead of colliding.
    """
    db.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'),"
               f" COALESCE((SELECT MAX({column}) FROM {table}), 0) + 1, false)")
