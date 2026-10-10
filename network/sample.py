"""Illustrative network fixtures kept completely outside the real inventory."""
import time
from network.store import DEFAULTS


def sample_snapshot():
    """Build clearly marked sample devices and events without database writes or probes."""
    now = time.time()
    records = [
        ('gateway', 'Dream Machine Pro', '192.168.1.1', 'router', 'Utility room', None, 'wired', 'online', 1.2, 'Ubiquiti'),
        ('switch', 'USW Pro 24 PoE', '192.168.1.2', 'switch', 'Utility room', 'gateway', 'wired', 'online', 0.8, 'Ubiquiti'),
        ('ap1', 'Living Room AP', '192.168.1.3', 'access_point', 'Living room', 'switch', 'wired', 'online', 2.1, 'Ubiquiti'),
        ('ap2', 'Office AP', '192.168.1.4', 'access_point', 'Office', 'switch', 'wired', 'online', 1.8, 'Ubiquiti'),
        ('mac', 'MacBook Pro', '192.168.1.20', 'computer', 'Office', 'ap2', 'wireless', 'online', 4.2, 'Apple'),
        ('desktop', 'Studio Desktop', '192.168.1.21', 'computer', 'Office', 'switch', 'wired', 'online', 1.4, 'Custom PC'),
        ('nas', 'Home Server', '192.168.1.22', 'computer', 'Utility room', 'switch', 'wired', 'online', 0.9, 'Synology'),
        ('iphone', 'iPhone 16', '192.168.1.30', 'phone', 'Living room', 'ap1', 'wireless', 'online', 8.3, 'Apple'),
        ('ipad', 'iPad Air', '192.168.1.31', 'tablet', 'Living room', 'ap1', 'wireless', 'offline', None, 'Apple'),
        ('tv', 'Living Room TV', '192.168.1.40', 'tv', 'Living room', 'ap1', 'wireless', 'online', 6.8, 'Samsung'),
        ('apple-tv', 'Apple TV 4K', '192.168.1.41', 'streaming', 'Living room', 'switch', 'wired', 'online', 1.9, 'Apple'),
        ('speaker', 'Sonos One', '192.168.1.50', 'iot', 'Kitchen', 'ap1', 'wireless', 'online', 7.2, 'Sonos'),
        ('hue', 'Hue Bridge', '192.168.1.51', 'iot', 'Living room', 'switch', 'wired', 'online', 2.6, 'Philips'),
        ('thermo', 'Nest Thermostat', '192.168.1.52', 'iot', 'Hallway', 'ap1', 'wireless', 'offline', None, 'Google'),
        ('camera', 'Front Door Camera', '192.168.1.53', 'iot', 'Front door', 'ap1', 'wireless', 'unknown', None, 'Reolink'),
        ('phone2', 'Pixel 9', '192.168.1.32', 'phone', 'Office', 'ap2', 'wireless', 'online', 5.1, 'Google'),
    ]
    devices = [{'id': r[0], 'name': r[1], 'target': r[2], 'type': r[3], 'location': r[4], 'parent_id': r[5], 'connection': r[6], 'status': r[7], 'latency': r[8], 'manufacturer': r[9], 'method': 'icmp', 'mac': '', 'notes': '', 'failures': 3 if r[7] == 'offline' else 0, 'last_check': now - 12, 'last_seen': now - (4200 if r[7] == 'offline' else 12) if r[7] != 'unknown' else None, 'last_error': 'No response' if r[7] == 'offline' else '', 'heartbeat_configured': False, 'sample': True} for r in records]
    events = [{'id': i, 'at': now - minutes * 60, 'device_id': device, 'name': name, 'status': status, 'message': message} for i, (device, name, status, message, minutes) in enumerate([
        ('ipad', 'iPad Air', 'offline', 'No response after consecutive checks', 8),
        ('mac', 'MacBook Pro', 'online', 'Responding again', 14),
        ('thermo', 'Nest Thermostat', 'offline', 'No response after consecutive checks', 32),
        ('phone2', 'Pixel 9', 'online', 'Responding again', 48),
    ])]
    return {'sample': True, 'devices': devices, 'events': events, 'counts': {s: sum(d['status'] == s for d in devices) for s in ('online', 'offline', 'unknown')}, 'settings': dict(DEFAULTS)}
