"""PostgreSQL backend for a cluster-hosted database; requires psycopg 3.

The application writes portable SQL (``?`` placeholders, ``BEGIN IMMEDIATE``
for serialized writes). :func:`translate_sql` converts it to PostgreSQL, and
``pg_advisory_xact_lock`` reproduces SQLite's single-writer behavior so
concurrent topology edits still cannot bypass cycle checks.
"""
from contextlib import contextmanager
from urllib.parse import urlsplit
from db.core import Backend, split_sql

# Application-wide advisory-lock key. Every writer takes it at the start of
# its transaction, mirroring SQLite BEGIN IMMEDIATE semantics.
ADVISORY_LOCK_ID = 746812
# Bound every statement like the SQLite backend's 15-second busy timeout.
STATEMENT_TIMEOUT_MS = 15000


def translate_sql(sql):
    """Translate portable Khaya SQL into the PostgreSQL dialect.

    Rewrites ``?`` placeholders to ``%s`` outside quoted strings, passes
    ``--`` comments through untouched, and turns ``BEGIN IMMEDIATE`` into an
    advisory write lock taken for the rest of the transaction.
    """
    if sql.strip().upper() == 'BEGIN IMMEDIATE':
        return f'SELECT pg_advisory_xact_lock({ADVISORY_LOCK_ID})'
    out, quote, i, length = [], None, 0, len(sql)
    while i < length:
        char = sql[i]
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in ("'", '"'):
            quote = char
            out.append(char)
            i += 1
            continue
        if char == '-' and sql[i:i + 2] == '--':
            end = sql.find('\n', i)
            end = length if end < 0 else end
            out.append(sql[i:end])
            i = end
            continue
        out.append('%s' if char == '?' else char)
        i += 1
    return ''.join(out)


class PostgresSession:
    """Execute portable SQL statements against one psycopg connection."""

    def __init__(self, conn):
        """Attach the transaction-scoped connection being wrapped."""
        self._conn = conn

    def execute(self, sql, params=()):
        """Run one translated statement and return its cursor."""
        return self._conn.execute(translate_sql(sql), params)

    def executemany(self, sql, rows):
        """Run one translated statement for every parameter sequence."""
        cursor = self._conn.cursor()
        cursor.executemany(translate_sql(sql), rows)
        return cursor


class PostgresBackend(Backend):
    """Connect to a networked PostgreSQL server with short transactions.

    Like the SQLite backend, each operation opens a connection, runs one
    transaction, and closes it. For this dashboard's traffic (a poll every
    few seconds and one write per ICMP check) that stays comfortably cheap
    on a home cluster and keeps failure handling simple.
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
        """Import psycopg lazily so SQLite-only installs need no driver."""
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
        """Yield a translating session; commit or roll back and close."""
        conn = self._connect()
        try:
            with conn:
                yield PostgresSession(conn)
        finally:
            conn.close()

    def _list_tables(self, db):
        return [row['table_name'] for row in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")]

    def run_script_unchecked(self, script):
        """PostgreSQL rebuilds run inside normal transactions; apply directly."""
        self.run_script(script)


def resync_identity(db, table, column='id'):
    """Advance a table's identity sequence past explicitly restored ids.

    Restores insert explicit ids, which identity columns accept but do not
    advance; call this within the restore transaction so new rows continue
    the sequence instead of colliding.
    """
    db.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'),"
               f" COALESCE((SELECT MAX({column}) FROM {table}), 0) + 1, false)")
