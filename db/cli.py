"""Command-line entry points for database provisioning and maintenance.

Run `python -m db <command>` without the dashboard; every command accepts
`--url` or the KHAYA_DATABASE_URL environment variable.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from db import DATABASE_URL_ENV, open_backend, resolve_url
from db import dump as dump_module
from db import migrations
from db.schema import DATA_TABLES, REQUIRED_TABLES


def _connection_errors():
    """Return exception types that mean the database could not be reached."""
    try:
        import psycopg
    except ImportError:
        return ()
    return (psycopg.OperationalError,)


def run_with_retry(action, retry_seconds):
    """Run action, retrying connection failures until the deadline passes."""
    if retry_seconds <= 0:
        return action()
    deadline = time.monotonic() + retry_seconds
    attempt = 1
    while True:
        try:
            return action()
        except _connection_errors() as exc:
            if time.monotonic() >= deadline:
                raise
            print(f'waiting for the database to accept connections (attempt {attempt}): {exc}', file=sys.stderr)
            attempt += 1
            time.sleep(2.0)


def _print_report(report):
    """Show what each migration did when anything was applied."""
    for name, note in report:
        print(f'{name}: {note}')


def _print_counts(counts):
    """Show restored row counts per table."""
    print(', '.join(f'{table}: {count}' for table, count in counts.items()))


def command_init(args):
    """Create or upgrade the schema and seed default settings."""
    url = resolve_url(args.url)
    report = run_with_retry(lambda: migrations.initialize(url), args.retry_seconds)
    _print_report(report)
    backend = open_backend(url)
    print(f'{backend.scheme} database ready at {backend.redacted_url()}')
    return 0


def command_migrate(args):
    """Apply pending migrations; equivalent to init on a fresh database."""
    url = resolve_url(args.url)
    report = run_with_retry(lambda: migrations.migrate(url), args.retry_seconds)
    _print_report(report)
    print('no pending migrations' if not report else 'migrations applied')
    return 0


def command_status(args):
    """Show backend, migration state, and table row counts."""
    backend = open_backend(resolve_url(args.url))
    tables = backend.tables()
    missing = [table for table in REQUIRED_TABLES if table not in tables]
    print(f'backend: {backend.scheme}')
    print(f'url: {backend.redacted_url()}')
    if missing:
        print(f'not initialized (missing {", ".join(missing)}); run `python -m db init`')
        return 1
    recorded = migrations.applied_names(backend)
    print('applied migrations:')
    for migration in migrations.MIGRATIONS:
        marker = 'applied' if migration.name in recorded else 'PENDING'
        print(f'  {migration.name} [{marker}] {migration.description}')
    with backend.connection() as db:
        counts = {table: db.execute(f'SELECT COUNT(*) AS count FROM {table}').fetchone()['count'] for table in DATA_TABLES}
    print('rows: ' + ', '.join(f'{table}={count}' for table, count in counts.items()))
    return 0


def command_backup(args):
    """Write a portable JSON dump of every data table."""
    url = resolve_url(args.url)
    data = run_with_retry(lambda: dump_module.dump(url), args.retry_seconds)
    path = Path(args.output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    _print_counts({table: len(rows) for table, rows in data['tables'].items()})
    print(f'backup written to {path}')
    return 0


def command_restore(args):
    """Replace the target database's data with a dump file."""
    if not args.yes:
        print('restore replaces every device, observation, event, and setting in the target database.', file=sys.stderr)
        print('Re-run with --yes to confirm.', file=sys.stderr)
        return 1
    data = json.loads(Path(args.input).expanduser().read_text(encoding='utf-8'))
    counts = dump_module.restore(resolve_url(args.url), data)
    _print_counts(counts)
    print(f'restored from {args.input}')
    return 0


def main(argv=None):
    """Parse arguments and dispatch one database command."""
    parser = argparse.ArgumentParser(prog='python -m db', description='Khaya database provisioning and maintenance (runnable without the dashboard)')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--url', help=f'PostgreSQL URL (default: ${DATABASE_URL_ENV})')
    sub = parser.add_subparsers(dest='command', required=True, metavar='command')

    init = sub.add_parser('init', parents=[common], help='create the schema, apply migrations, and seed defaults')
    init.add_argument('--retry-seconds', type=int, default=0, metavar='N', help='keep retrying connection failures for N seconds (useful in containers)')
    init.set_defaults(handler=command_init)

    migrate = sub.add_parser('migrate', parents=[common], help='apply pending schema migrations')
    migrate.add_argument('--retry-seconds', type=int, default=0, metavar='N', help='keep retrying connection failures for N seconds')
    migrate.set_defaults(handler=command_migrate)

    status = sub.add_parser('status', parents=[common], help='show backend, migrations, and row counts')
    status.set_defaults(handler=command_status)

    backup = sub.add_parser('backup', parents=[common], help='write a portable JSON dump of all tables')
    backup.add_argument('--output', '-o', required=True, help='dump file to write')
    backup.add_argument('--retry-seconds', type=int, default=0, metavar='N', help='keep retrying connection failures for N seconds')
    backup.set_defaults(handler=command_backup)

    restore = sub.add_parser('restore', parents=[common], help='replace database contents from a dump')
    restore.add_argument('--input', '-i', required=True, help='dump file to read')
    restore.add_argument('--yes', action='store_true', help='confirm replacing all target data')
    restore.set_defaults(handler=command_restore)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except _connection_errors() as exc:
        print(f'error: could not reach the database: {exc}', file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
