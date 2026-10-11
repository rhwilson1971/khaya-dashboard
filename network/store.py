"""SQLite inventory, topology, and health observations for a local network."""
from contextlib import contextmanager
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid
from network.config import DNS_SETTINGS

TYPES = ('router', 'switch', 'access_point', 'computer', 'phone', 'tablet', 'tv', 'streaming', 'iot', 'other')
METHODS = ('icmp', 'heartbeat', 'none')
DEFAULTS = {'interval': 30, 'failure_threshold': 3, 'heartbeat_timeout': 180, 'subnet': '192.168.1.0/24'}
PRIVATE = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))


def local_ipv4(value):
    """Return whether an address is RFC1918 or IPv4 loopback, not a public target."""
    try:
        address = ipaddress.IPv4Address(value)
        return any(address in net for net in PRIVATE)
    except ipaddress.AddressValueError:
        return False


def validate_target(value):
    """Normalize an IPv4 or DNS target; reject shell syntax and public literal IPs."""
    if not isinstance(value, str):
        raise ValueError('Enter an IPv4 address or local hostname.')
    value = value.strip().lower()
    if len(value) > 253 or not value:
        raise ValueError('Enter an IPv4 address or local hostname.')
    if re.fullmatch(r'[0-9.]+', value):
        if not local_ipv4(value):
            raise ValueError('Use a valid private IPv4 address or loopback address.')
    elif not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in value.split('.')):
        raise ValueError('Use a valid hostname without spaces or command characters.')
    return value


def validate_subnet(value):
    """Return a canonical private subnet with no more than 254 usable hosts."""
    try:
        net = ipaddress.IPv4Network(str(value), strict=False)
    except ValueError as exc:
        raise ValueError('Enter a private IPv4 subnet, such as 192.168.1.0/24.') from exc
    if net.prefixlen < 24 or not any(net.subnet_of(n) for n in PRIVATE[:3]):
        raise ValueError('Discovery requires a private IPv4 subnet of /24 or smaller.')
    return str(net)


class Store:
    """Persist inventory and checks using short, independent SQLite transactions."""

    def __init__(self, path):
        """Create the database and schema at path; initialize monitoring settings."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._drop_target_uniqueness()
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, target TEXT NOT NULL,
                    type TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
                    manufacturer TEXT NOT NULL DEFAULT '', mac TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '', connection TEXT NOT NULL DEFAULT 'wired',
                    parent_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
                    method TEXT NOT NULL DEFAULT 'icmp', status TEXT NOT NULL DEFAULT 'unknown',
                    last_check REAL, last_seen REAL, latency REAL, failures INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '', heartbeat_hash TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY, device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
                    at REAL NOT NULL, reachable INTEGER, latency REAL, error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS observation_device ON observations(device_id, at);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, device_id TEXT, name TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            for key, value in DEFAULTS.items():
                db.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', (key, json.dumps(value)))

    def _drop_target_uniqueness(self):
        """Rebuild a legacy devices table that still enforces unique addresses."""
        db = sqlite3.connect(self.path, timeout=15)
        try:
            row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='devices'").fetchone()
            if not row or 'UNIQUE' not in (row[0] or ''):
                return
            columns = ('id, name, target, type, location, manufacturer, mac, notes, connection,'
                       ' parent_id, method, status, last_check, last_seen, latency,'
                       ' failures, last_error, heartbeat_hash, created_at')
            db.execute('PRAGMA foreign_keys=OFF')
            db.executescript(f'''
                CREATE TABLE devices_new (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, target TEXT NOT NULL,
                    type TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
                    manufacturer TEXT NOT NULL DEFAULT '', mac TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '', connection TEXT NOT NULL DEFAULT 'wired',
                    parent_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
                    method TEXT NOT NULL DEFAULT 'icmp', status TEXT NOT NULL DEFAULT 'unknown',
                    last_check REAL, last_seen REAL, latency REAL, failures INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '', heartbeat_hash TEXT,
                    created_at REAL NOT NULL
                );
                INSERT INTO devices_new ({columns}) SELECT {columns} FROM devices;
                DROP TABLE devices;
                ALTER TABLE devices_new RENAME TO devices;
            ''')
            db.commit()
        finally:
            db.close()

    @contextmanager
    def connection(self):
        """Yield a foreign-key-enabled connection; commit or roll back and always close."""
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        """Return persisted collector settings plus fixed, read-only home DNS configuration."""
        with self.connection() as db:
            return {**{r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}, **DNS_SETTINGS}

    def save_settings(self, changes):
        """Validate and persist supplied interval, threshold, timeout, and subnet values."""
        if not isinstance(changes, dict) or set(changes) - set(DEFAULTS):
            raise ValueError('Unrecognized monitoring setting.')
        updated = {**{k: v for k, v in self.settings().items() if k in DEFAULTS}, **changes}
        for key, low, high in [('interval', 10, 3600), ('failure_threshold', 1, 10), ('heartbeat_timeout', 30, 86400)]:
            if type(updated[key]) is not int or not low <= updated[key] <= high:
                raise ValueError(f'{key.replace("_", " ")} must be between {low} and {high}.')
        updated['subnet'] = validate_subnet(updated['subnet'])
        with self.connection() as db:
            db.executemany('INSERT OR REPLACE INTO settings VALUES (?, ?)', [(k, json.dumps(v)) for k, v in updated.items()])
        return {**updated, **DNS_SETTINGS}

    @staticmethod
    def public(row):
        """Convert a row to a device mapping without exposing its heartbeat verifier."""
        if row is None:
            raise KeyError('Device not found.')
        device = dict(row)
        device['heartbeat_configured'] = bool(device.pop('heartbeat_hash', None))
        return device

    def list_devices(self):
        """Return real inventory sorted by creation time then device name."""
        with self.connection() as db:
            return [self.public(r) for r in db.execute('SELECT * FROM devices ORDER BY created_at, name')]

    def get_device(self, device_id):
        """Return one real device, raising KeyError for an unknown identifier."""
        with self.connection() as db:
            return self.public(db.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone())

    def save_device(self, data, device_id=None):
        """Create or patch a validated device; reject cyclic parent links.

        Shared addresses are permitted: DHCP reassignments mean one address can
        briefly belong to two records while the inventory is being updated.
        """
        fields = ('name', 'target', 'type', 'location', 'manufacturer', 'mac', 'notes', 'connection', 'parent_id', 'method')
        if not isinstance(data, dict) or set(data) - set(fields):
            raise ValueError('Unrecognized device field.')
        with self.connection() as db:
            # A write reservation keeps concurrent topology edits from bypassing cycle checks.
            db.execute('BEGIN IMMEDIATE')
            old = dict(db.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone() or {}) if device_id else {}
            if device_id and not old:
                raise KeyError('Device not found.')
            d = {'name': '', 'target': '', 'type': 'other', 'location': '', 'manufacturer': '', 'mac': '', 'notes': '', 'connection': 'wired', 'parent_id': None, 'method': 'icmp', **old, **data}
            for key, limit in [('name', 100), ('location', 100), ('manufacturer', 100), ('mac', 32), ('notes', 2000)]:
                if not isinstance(d[key], str) or len(d[key]) > limit:
                    raise ValueError(f'{key.title()} must be text of at most {limit} characters.')
                d[key] = d[key].strip()
            if not d['name']:
                raise ValueError('A device name is required.')
            d['target'] = validate_target(d['target'])
            if d['type'] not in TYPES or d['method'] not in METHODS or d['connection'] not in ('wired', 'wireless', 'unknown'):
                raise ValueError('Select a supported device type, connection, and monitoring method.')
            if d['mac'] and not re.fullmatch(r'(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}', d['mac']):
                raise ValueError('Use a MAC address such as AA:BB:CC:DD:EE:FF.')
            device_id = device_id or uuid.uuid4().hex
            parent = d['parent_id'] or None
            if parent is not None and not isinstance(parent, str):
                raise ValueError('Choose a valid parent device.')
            visited = {device_id}
            while parent:
                if parent in visited:
                    raise ValueError('This connection would create a loop in the network map.')
                visited.add(parent)
                row = db.execute('SELECT parent_id FROM devices WHERE id=?', (parent,)).fetchone()
                if not row:
                    raise ValueError('Parent device does not exist.')
                parent = row['parent_id']
            d['parent_id'] = d['parent_id'] or None
            if old:
                db.execute('UPDATE devices SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', (*[d[k] for k in fields], device_id))
                if old['target'] != d['target'] or old['method'] != d['method']:
                    db.execute("UPDATE devices SET status='unknown', last_check=NULL, last_seen=NULL, latency=NULL, failures=0, last_error='', heartbeat_hash=NULL WHERE id=?", (device_id,))
                    db.execute('DELETE FROM observations WHERE device_id=?', (device_id,))
            else:
                db.execute('INSERT INTO devices (id,' + ','.join(fields) + ',created_at) VALUES (' + ','.join('?' for _ in range(len(fields) + 2)) + ')', (device_id, *[d[k] for k in fields], time.time()))
                db.execute('INSERT INTO events(at,device_id,name,status,message) VALUES(?,?,?,?,?)', (time.time(), device_id, d['name'], 'unknown', 'Added to your network'))
        return self.get_device(device_id)

    def delete_device(self, device_id):
        """Delete a device and its observations; detach connected children."""
        with self.connection() as db:
            if not db.execute('DELETE FROM devices WHERE id=?', (device_id,)).rowcount:
                raise KeyError('Device not found.')

    def record_probe(self, device_id, reachable, latency, error='', at=None, expected_target=None):
        """Record a probe and transition health; ignore results for changed ICMP targets."""
        at = time.time() if at is None else at
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
            if not row or (expected_target is not None and (row['target'] != expected_target or row['method'] != 'icmp')):
                return
            threshold = json.loads(db.execute("SELECT value FROM settings WHERE key='failure_threshold'").fetchone()[0])
            failures = 0 if reachable is True else (row['failures'] + 1 if reachable is False else row['failures'])
            status = 'online' if reachable is True else ('offline' if reachable is False and failures >= threshold else ('unknown' if reachable is None else row['status']))
            db.execute('UPDATE devices SET status=?, last_check=?, last_seen=?, latency=?, failures=?, last_error=? WHERE id=?', (status, at, at if reachable is True else row['last_seen'], latency, failures, error, device_id))
            db.execute('INSERT INTO observations(device_id,at,reachable,latency,error) VALUES(?,?,?,?,?)', (device_id, at, reachable, latency, error))
            db.execute('DELETE FROM observations WHERE device_id=? AND id NOT IN (SELECT id FROM observations WHERE device_id=? ORDER BY id DESC LIMIT 500)', (device_id, device_id))
            if status != row['status']:
                db.execute('INSERT INTO events(at,device_id,name,status,message) VALUES(?,?,?,?,?)', (at, device_id, row['name'], status, {'online': 'Responding again', 'offline': 'No response after consecutive checks', 'unknown': error or 'Status unknown'}[status]))
            db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)')

    def history(self, device_id):
        """Return up to 500 observations in ascending recorded order."""
        self.get_device(device_id)
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT at,reachable,latency,error FROM observations WHERE device_id=? ORDER BY id', (device_id,))]

    def rotate_heartbeat(self, device_id):
        """Replace a heartbeat secret and return its plaintext once for agent setup."""
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            row = db.execute('SELECT method FROM devices WHERE id=?', (device_id,)).fetchone()
            if not row:
                raise KeyError('Device not found.')
            if row['method'] != 'heartbeat':
                raise ValueError('Select heartbeat monitoring for this device first.')
            db.execute('UPDATE devices SET heartbeat_hash=? WHERE id=?', (hashlib.sha256(token.encode()).hexdigest(), device_id))
        return token

    def heartbeat(self, device_id, token, at=None):
        """Authenticate a device heartbeat and atomically record a successful observation."""
        at = time.time() if at is None else at
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
            if not row or row['method'] != 'heartbeat' or not row['heartbeat_hash'] or not hmac.compare_digest(row['heartbeat_hash'], hashlib.sha256(token.encode()).hexdigest()):
                return False
            db.execute("UPDATE devices SET status='online',last_check=?,last_seen=?,latency=NULL,failures=0,last_error='' WHERE id=?", (at, at, device_id))
            db.execute('INSERT INTO observations(device_id,at,reachable) VALUES(?,?,1)', (device_id, at))
            db.execute('DELETE FROM observations WHERE device_id=? AND id NOT IN (SELECT id FROM observations WHERE device_id=? ORDER BY id DESC LIMIT 500)', (device_id, device_id))
            if row['status'] != 'online':
                db.execute('INSERT INTO events(at,device_id,name,status,message) VALUES(?,?,?,?,?)', (at, device_id, row['name'], 'online', 'Heartbeat received'))
        return True

    def expire_heartbeats(self, now=None):
        """Mark expired heartbeat devices offline and emit one transition event."""
        now = time.time() if now is None else now
        timeout = self.settings()['heartbeat_timeout']
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM devices WHERE method='heartbeat' AND status='online' AND last_seen<?", (now - timeout,)).fetchall():
                db.execute("UPDATE devices SET status='offline',last_check=?,last_error='Heartbeat expired' WHERE id=?", (now, row['id']))
                db.execute('INSERT INTO observations(device_id,at,reachable,error) VALUES(?,?,0,?)', (row['id'], now, 'Heartbeat expired'))
                db.execute('INSERT INTO events(at,device_id,name,status,message) VALUES(?,?,?,?,?)', (now, row['id'], row['name'], 'offline', 'Heartbeat expired'))

    def snapshot(self, sample=False):
        """Return inventory, status totals, and recent events; optionally return isolated fixtures."""
        if sample:
            from network.sample import sample_snapshot
            return {**sample_snapshot(), 'settings': self.settings()}
        devices = self.list_devices()
        with self.connection() as db:
            events = [dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]
        return {'sample': False, 'devices': devices, 'events': events, 'counts': {s: sum(d['status'] == s for d in devices) for s in ('online', 'offline', 'unknown')}, 'settings': self.settings()}
