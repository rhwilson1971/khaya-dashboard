"""Khaya database access, independent of the dashboard application.

The dashboard in ``network/`` depends on this package; this package never
imports ``network/``, so every command runs standalone through
``python -m db`` (init, migrate, status, backup, restore, import-sqlite).

Supported database URLs:

    /path/to/network.db                SQLite file (bare path, local runs)
    sqlite:///path/to/network.db       SQLite file
    postgresql://user:pass@host:5432/khaya    PostgreSQL (needs psycopg 3)

Set KHAYA_DATABASE_URL to configure the environment once; explicit --url
values and --db flags take precedence.
"""
from pathlib import Path
from db.backend_postgres import PostgresBackend
from db.backend_sqlite import SqliteBackend
from db.core import Backend, redact_url, split_sql

DATABASE_URL_ENV = 'KHAYA_DATABASE_URL'
__all__ = ['Backend', 'SqliteBackend', 'PostgresBackend', 'open_backend', 'default_url', 'redact_url', 'split_sql', 'DATABASE_URL_ENV']


def open_backend(url):
    """Create the backend for a Backend, bare SQLite path, sqlite:// URL, or postgres URL."""
    if isinstance(url, Backend):
        return url
    if isinstance(url, Path):
        url = str(url)
    if not isinstance(url, str) or not url.strip():
        raise ValueError('Provide a database URL such as sqlite:///data/network.db or postgresql://user:pass@host/db.')
    url = url.strip()
    if '://' in url:
        scheme, _, rest = url.partition('://')
        scheme = scheme.lower()
        if scheme == 'sqlite':
            return SqliteBackend(rest)
        if scheme in ('postgres', 'postgresql'):
            return PostgresBackend(url)
        raise ValueError(f'Unsupported database scheme {scheme!r}; use sqlite or postgresql.')
    if url.startswith('sqlite:'):
        return SqliteBackend(url[len('sqlite:'):])
    return SqliteBackend(url)


def default_url():
    """Return the bundled local SQLite URL used when nothing is configured."""
    return f'sqlite://{Path(__file__).resolve().parent.parent / "data" / "network.db"}'
