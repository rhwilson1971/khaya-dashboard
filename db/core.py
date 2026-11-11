"""Backend contract and shared SQL helpers for Khaya database access."""
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit


def split_sql(script):
    """Split a SQL script into statements, dropping `--` line comments.

    Semicolons inside single- or double-quoted strings do not split, which
    is sufficient for the schema scripts shipped in this package.
    """
    statements, buffer, quote = [], [], None
    i = 0
    while i < len(script):
        char = script[i]
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            i += 1
            continue
        if char == '-' and script[i:i + 2] == '--':
            end = script.find('\n', i)
            i = len(script) if end < 0 else end
            continue
        if char == ';':
            statement = ''.join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            i += 1
            continue
        buffer.append(char)
        i += 1
    statement = ''.join(buffer).strip()
    if statement:
        statements.append(statement)
    return statements


def redact_url(url):
    """Hide the password of a database URL so it can be shown in logs."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.password is None:
        return url
    host = parts.hostname or ''
    netloc = f'{parts.username}@{host}' if parts.username else host
    if parts.port is not None:
        netloc += f':{parts.port}'
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class Backend:
    """Connection lifecycle and introspection for Khaya's PostgreSQL backend."""

    scheme = ''

    @contextmanager
    def connection(self):
        """Yield a short-lived session; commit on success, roll back on error."""
        raise NotImplementedError

    def tables(self):
        """Return the set of user tables in the main schema."""
        with self.connection() as db:
            return set(self._list_tables(db))

    def _list_tables(self, db):
        raise NotImplementedError

    def run_script(self, script):
        """Apply every statement of a script inside one transaction."""
        with self.connection() as db:
            for statement in split_sql(script):
                db.execute(statement)

    @property
    def url(self):
        """Return the URL this backend was opened from."""
        raise NotImplementedError

    def redacted_url(self):
        """Return the backend URL with any password hidden."""
        return redact_url(self.url)
