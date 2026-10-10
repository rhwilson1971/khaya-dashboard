"""Bounded private-network ICMP checks and explicitly requested discovery jobs."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import platform
import re
import socket
import subprocess
import threading
import time
import uuid
from network.store import local_ipv4, validate_target, validate_subnet


def resolve_target(target):
    """Resolve a validated target to private IPv4; reject public DNS answers."""
    target = validate_target(target)
    if local_ipv4(target):
        return target
    addresses = {r[4][0] for r in socket.getaddrinfo(target, None, socket.AF_INET, socket.SOCK_DGRAM)}
    if not addresses or not all(local_ipv4(a) for a in addresses):
        raise ValueError('Hostname must resolve exclusively to private IPv4 addresses.')
    return sorted(addresses)[0]


def probe(target):
    """Run one bounded ICMP echo and return reachability, latency, and execution error."""
    try:
        address = resolve_target(target)
        system = platform.system()
        if system == 'Windows':
            args = ['ping', '-n', '1', '-w', '1000', address]
        elif system == 'Darwin':
            args = ['ping', '-n', '-c', '1', '-W', '1000', '-t', '2', address]
        else:
            args = ['ping', '-n', '-c', '1', '-W', '1', address]
        result = subprocess.run(args, capture_output=True, text=True, timeout=3)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            latency = re.search(r'time[=<]\s*([\d.]+)\s*ms', output, re.IGNORECASE)
            return {'reachable': True, 'latency': float(latency.group(1)) if latency else None, 'error': ''}
        if re.search(r'permission|not permitted|invalid option|illegal option|unknown option|socket:|cannot resolve', output, re.IGNORECASE):
            return {'reachable': None, 'latency': None, 'error': output.strip()[:240] or 'ICMP execution failed'}
        return {'reachable': False, 'latency': None, 'error': 'No ICMP response'}
    except subprocess.TimeoutExpired:
        return {'reachable': False, 'latency': None, 'error': 'ICMP check timed out'}
    except FileNotFoundError:
        return {'reachable': None, 'latency': None, 'error': 'ping is not installed or is unavailable on PATH'}
    except (ValueError, OSError) as exc:
        return {'reachable': None, 'latency': None, 'error': str(exc)[:240]}


class Collector:
    """Run periodic collection and one concurrent, in-memory discovery job."""

    def __init__(self, store, probe_fn=probe):
        """Attach storage and an injectable probe, without starting network activity."""
        self.store = store
        self.probe_fn = probe_fn
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.lock = threading.Lock()
        self.cycle_lock = threading.Lock()
        self.thread = None
        self.heartbeat_thread = None
        self.heartbeat_error = ''
        self.discovery_thread = None
        self.last_cycle = None
        self.last_error = ''
        self.checking = False
        self.job = None

    def status(self):
        """Return collector liveness, last completed cycle, and visible errors."""
        with self.lock:
            return {'running': bool(self.thread and self.thread.is_alive() and self.heartbeat_thread and self.heartbeat_thread.is_alive()), 'checking': self.checking, 'last_cycle': self.last_cycle, 'error': self.last_error or self.heartbeat_error}

    def start(self):
        """Launch ICMP collection and an independent heartbeat-expiry daemon."""
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, name='heartbeat-expiry', daemon=True)
        self.heartbeat_thread.start()
        self.thread = threading.Thread(target=self.loop, name='network-collector', daemon=True)
        self.thread.start()

    def stop(self):
        """Signal collection and discovery to stop and wake the sleeping scheduler."""
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=3)

    def request_check(self):
        """Wake the scheduler to perform a new check without blocking the caller."""
        self.wake_event.set()

    def heartbeat_loop(self):
        """Expire heartbeat devices every two seconds, independently of slow ICMP batches."""
        while not self.stop_event.is_set():
            try:
                self.store.expire_heartbeats()
                with self.lock:
                    self.heartbeat_error = ''
            except Exception as exc:
                with self.lock:
                    self.heartbeat_error = str(exc)[:240]
            self.stop_event.wait(timeout=2)

    def loop(self):
        """Schedule periodic ICMP cycles without blocking the independent heartbeat timer."""
        next_cycle = 0
        while not self.stop_event.is_set():
            try:
                if time.monotonic() >= next_cycle or self.wake_event.is_set():
                    self.wake_event.clear()
                    self.run_cycle()
                    next_cycle = time.monotonic() + self.store.settings()['interval']
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)[:240]
                next_cycle = time.monotonic() + 10
            self.wake_event.wait(timeout=min(2, max(0.1, next_cycle - time.monotonic())))

    def run_cycle(self):
        """Check only real ICMP devices with at most eight concurrent subprocesses."""
        if not self.cycle_lock.acquire(blocking=False):
            return
        with self.lock:
            self.checking = True
        try:
            devices = [d for d in self.store.list_devices() if d['method'] == 'icmp']
            errors = []
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(self.probe_fn, d['target']): d for d in devices}
                for future in as_completed(futures):
                    d = futures[future]
                    result = future.result()
                    self.store.record_probe(d['id'], **result, expected_target=d['target'])
                    if result['reachable'] is None:
                        errors.append(result['error'])
            with self.lock:
                self.last_cycle = time.time()
                self.last_error = errors[0] if errors else ''
        finally:
            with self.lock:
                self.checking = False
            self.cycle_lock.release()

    def start_discovery(self, subnet=None):
        """Start one explicit private subnet scan; preserve candidates for manual import."""
        subnet = validate_subnet(subnet or self.store.settings()['subnet'])
        addresses = [str(a) for a in ipaddress.IPv4Network(subnet).hosts()]
        with self.lock:
            if self.job and self.job['state'] == 'running':
                raise ValueError('Discovery is already running. Wait for it to finish.')
            self.job = {'id': uuid.uuid4().hex, 'state': 'running', 'subnet': subnet, 'checked': 0, 'total': len(addresses), 'candidates': [], 'errors': [], 'started_at': time.time()}
            job_id = self.job['id']
            self.discovery_thread = threading.Thread(target=self.discover, args=(job_id, addresses), name='network-discovery', daemon=True)
            self.discovery_thread.start()
            return dict(self.job)

    def discovery_status(self, job_id):
        """Return a detached copy of one current discovery job or raise KeyError."""
        with self.lock:
            if not self.job or self.job['id'] != job_id:
                raise KeyError('Discovery job not found.')
            return {**self.job, 'candidates': [dict(d) for d in self.job['candidates']], 'errors': list(self.job['errors'])}

    def discover(self, job_id, addresses):
        """Probe a bounded subnet, increment progress, and retain responding addresses."""
        try:
            existing = {d['target'] for d in self.store.list_devices()}
            with ThreadPoolExecutor(max_workers=16) as pool:
                futures = {pool.submit(self.probe_fn, address): address for address in addresses}
                for future in as_completed(futures):
                    if self.stop_event.is_set():
                        for pending in futures:
                            pending.cancel()
                        break
                    result = future.result()
                    address = futures[future]
                    with self.lock:
                        self.job['checked'] += 1
                        if result['reachable']:
                            self.job['candidates'].append({'target': address, 'name': f'Device {address}', 'latency': result['latency'], 'existing': address in existing})
                        elif result['reachable'] is None and result['error'] not in self.job['errors']:
                            self.job['errors'].append(result['error'])
            with self.lock:
                self.job['candidates'].sort(key=lambda d: ipaddress.IPv4Address(d['target']))
                self.job['state'] = 'cancelled' if self.stop_event.is_set() else ('failed' if self.job['errors'] and not self.job['candidates'] else 'complete')
        except Exception as exc:
            with self.lock:
                if self.job and self.job['id'] == job_id:
                    self.job['state'] = 'failed'
                    self.job['errors'].append(str(exc)[:240])
