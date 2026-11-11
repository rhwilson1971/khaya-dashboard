"""SQLite backend: the original embedded storage, now behind a URL."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from db.core import Backend, split_sql


class SqliteBackend(Backend):
    """File-backed SQLite with foreign keys on and short transactions.

    This preserves the connection behavior the application has always used:
    one new connection per operation, a busy timeout of fifteen seconds, and
    ``BEGIN IMMEDIATE`` available for serialized writes.
    """

    scheme = 'sqlite'

    def __init__(self, path):
        """Resolve a database file path, creating its parent directory."""
        if str(path) == ':memory:':
            raise ValueError('In-memory SQLite is unsupported: each operation opens a new connection.')
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def url(self):
        return f'sqlite://{self.path}'

    @contextmanager
    def connection(self):
        """Yield a foreign-key-enabled connection; commit or roll back and close."""
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _list_tables(self, db):
        return [row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    def run_script_unchecked(self, script):
        """Apply a script with foreign-key enforcement disabled.

        Needed only for the one-time rebuild of legacy `devices` tables whose
        self-referencing rows cannot be swapped while constraints are active.
        """
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA foreign_keys=OFF')
            for statement in split_sql(script):
                db.execute(statement)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
