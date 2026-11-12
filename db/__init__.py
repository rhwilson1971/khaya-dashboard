"""Khaya database access, independent of the dashboard application.

The dashboard in ``network/`` depends on this package; this package never
imports ``network/``, so every command runs standalone through
``python -m db`` (init, migrate, status, backup, restore).

PostgreSQL is the only supported backend:

    postgresql://user:pass@host:5432/khaya

Set KHAYA_DATABASE_URL to configure the environment once; explicit ``--url``
values and ``--db`` flags take precedence.
"""
import os
from db.backend_postgres import PostgresBackend
from db.core import Backend, redact_url, split_sql

DATABASE_URL_ENV = 'KHAYA_DATABASE_URL'
__all__ = ['Backend', 'PostgresBackend', 'open_backend', 'resolve_url', 'redact_url', 'split_sql', 'DATABASE_URL_ENV']


def resolve_url(url=None):
    """Return an explicit URL, then KHAYA_DATABASE_URL; otherwise raise."""
    resolved = url or os.environ.get(DATABASE_URL_ENV)
    if not isinstance(resolved, str) or not resolved.strip():
        raise ValueError(f'Provide a PostgreSQL URL with --url or --db, or set {DATABASE_URL_ENV}.')
    return resolved.strip()


def open_backend(url):
    """Create the PostgreSQL backend for a postgres:// or postgresql:// URL."""
    if isinstance(url, Backend):
        return url
    if not isinstance(url, str) or not url.strip():
        raise ValueError('Provide a PostgreSQL URL such as postgresql://user:pass@host:5432/khaya.')
    url = url.strip()
    scheme, separator, _ = url.partition('://')
    if not separator or scheme.lower() not in ('postgres', 'postgresql'):
        raise ValueError(f'Unsupported database URL {url!r}; Khaya requires PostgreSQL.')
    return PostgresBackend(url)
