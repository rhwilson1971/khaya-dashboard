"""Local-only HTTP dashboard, inventory API, and authenticated device heartbeats."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from network.monitor import Collector
from network.store import Store, local_ipv4

ROOT = Path(__file__).resolve().parent.parent
LOG = logging.getLogger('khaya')


class DashboardServer(ThreadingHTTPServer):
    """Serve independent dashboard requests with attached storage and collector."""
    daemon_threads = True

    def __init__(self, address, store, collector):
        """Bind the given address and expose shared application services to handlers."""
        self.store = store
        self.collector = collector
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    """Dispatch narrowly scoped static and JSON API routes without CORS access."""

    def log_message(self, format, *args):
        """Log request summaries without request bodies or heartbeat credentials."""
        LOG.info('%s %s', self.address_string(), format % args)

    def reply(self, status, value, content_type='application/json; charset=utf-8'):
        """Send a length-delimited response with browser security and cache headers."""
        body = json.dumps(value).encode() if isinstance(value, (dict, list)) else value
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def trusted_request(self):
        """Enforce literal local hosts and same-origin requests to resist rebinding and CSRF."""
        host = self.headers.get('Host', '')
        try:
            parsed = urlsplit('http://' + host)
            hostname = parsed.hostname
            port = parsed.port or 80
        except ValueError:
            return False
        allowed = hostname == 'localhost' or local_ipv4(hostname or '')
        if self.server.server_address[0] in ('127.0.0.1', 'localhost'):
            allowed = hostname in ('localhost', '127.0.0.1')
        if not allowed or port != self.server.server_port or parsed.username or parsed.path:
            return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            return False
        return self.headers.get('Sec-Fetch-Site') not in ('cross-site',)

    def body(self):
        """Decode a small JSON object and reject form posts or malformed request lengths."""
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Transfer-encoded requests are not supported.')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError as exc:
            raise ValueError('Invalid Content-Length.') from exc
        if not 0 <= length <= 65536:
            raise ValueError('Request body is too large.')
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('Send Content-Type: application/json.')
        try:
            data = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError('Request must contain valid JSON.') from exc
        if not isinstance(data, dict):
            raise ValueError('Request must be a JSON object.')
        return data

    def dispatch(self, method):
        """Handle an HTTP method and translate expected validation failures into JSON errors."""
        self.connection.settimeout(10)
        if not self.trusted_request():
            self.reply(403, {'error': 'Use this dashboard on its local URL with a same-origin request.'})
            return
        try:
            self.route(method)
        except KeyError as exc:
            self.reply(404, {'error': str(exc.args[0])})
        except ValueError as exc:
            self.reply(400, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.exception('Request failed')
            self.reply(500, {'error': 'The local service encountered an error. Check its console log.'})

    def route(self, method):
        """Serve recognized assets, inventory, settings, checks, discovery, and heartbeat routes."""
        url = urlsplit(self.path)
        parts = url.path.strip('/').split('/')
        query = parse_qs(url.query)
        store, collector = self.server.store, self.server.collector
        if method == 'GET' and url.path in ('/', '/index.html', '/styles.css', '/app.js', '/sample-data.js', '/favicon.svg'):
            name = 'index.html' if url.path == '/' else url.path[1:]
            types = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml'}
            asset = ROOT / 'static' / name
            self.reply(200, asset.read_bytes(), types[asset.suffix] + '; charset=utf-8')
            return
        if parts == ['api', 'snapshot'] and method == 'GET':
            self.reply(200, {**store.snapshot(sample=query.get('sample') == ['1']), 'collector': collector.status()})
            return
        if parts == ['api', 'devices'] and method == 'POST':
            self.reply(201, store.save_device(self.body()))
            collector.request_check()
            return
        if len(parts) >= 3 and parts[:2] == ['api', 'devices']:
            device_id = parts[2]
            if len(parts) == 3:
                if method == 'GET':
                    self.reply(200, {'device': store.get_device(device_id), 'history': store.history(device_id)})
                    return
                if method == 'PATCH':
                    self.reply(200, store.save_device(self.body(), device_id))
                    collector.request_check()
                    return
                if method == 'DELETE':
                    store.delete_device(device_id)
                    self.reply(200, {'deleted': True})
                    return
            if len(parts) == 4 and parts[3] == 'token' and method == 'POST':
                self.body()
                self.reply(200, {'token': store.rotate_heartbeat(device_id)})
                return
        if parts == ['api', 'settings']:
            if method == 'GET':
                self.reply(200, store.settings())
                return
            if method == 'PATCH':
                self.reply(200, store.save_settings(self.body()))
                collector.request_check()
                return
        if parts == ['api', 'check'] and method == 'POST':
            self.body()
            collector.request_check()
            self.reply(202, {'queued': True})
            return
        if parts == ['api', 'discovery'] and method == 'POST':
            data = self.body()
            self.reply(202, collector.start_discovery(data.get('subnet')))
            return
        if len(parts) in (3, 4) and parts[:2] == ['api', 'discovery']:
            job = collector.discovery_status(parts[2])
            if len(parts) == 3 and method == 'GET':
                self.reply(200, job)
                return
            if len(parts) == 4 and parts[3] == 'import' and method == 'POST':
                if job['state'] != 'complete':
                    raise ValueError('Wait until discovery completes before importing devices.')
                data = self.body()
                targets = data.get('targets')
                candidates = {d['target']: d for d in job['candidates']}
                if not isinstance(targets, list) or not targets or len(targets) > 254 or any(not isinstance(t, str) or t not in candidates for t in targets):
                    raise ValueError('Select addresses from this completed discovery job.')
                imported, skipped = [], []
                for target in dict.fromkeys(targets):
                    if target in {d['target'] for d in store.list_devices()}:
                        skipped.append(target)
                        continue
                    try:
                        imported.append(store.save_device({'name': candidates[target]['name'], 'target': target, 'type': 'other'}))
                    except ValueError:
                        # Only tolerate a device added concurrently, not unrelated validation failures.
                        if target not in {d['target'] for d in store.list_devices()}:
                            raise
                        skipped.append(target)
                collector.request_check()
                self.reply(200, {'imported': imported, 'skipped': skipped})
                return
        if len(parts) == 3 and parts[:2] == ['api', 'heartbeat'] and method == 'POST':
            self.body()
            auth = self.headers.get('Authorization', '')
            if not auth.startswith('Bearer ') or not store.heartbeat(parts[2], auth[7:]):
                self.reply(401, {'error': 'Invalid heartbeat credentials.'})
                return
            self.reply(200, {'accepted': True})
            return
        self.reply(404, {'error': 'Route not found.'})

    def do_GET(self):
        """Dispatch read requests for dashboard assets and JSON snapshots."""
        self.dispatch('GET')

    def do_POST(self):
        """Dispatch inventory creation, checks, discovery, and authenticated heartbeats."""
        self.dispatch('POST')

    def do_PATCH(self):
        """Dispatch validated partial device and settings updates."""
        self.dispatch('PATCH')

    def do_DELETE(self):
        """Dispatch removal of a real inventory device."""
        self.dispatch('DELETE')


def create_server(store, collector, host='127.0.0.1', port=8765):
    """Construct an HTTP server without starting collector activity or serving requests."""
    return DashboardServer((host, port), store, collector)


def main():
    """Parse launch options, start the local collector, and serve until interrupted."""
    parser = argparse.ArgumentParser(description='Khaya inventory and health dashboard')
    parser.add_argument('--host', default='127.0.0.1', help='Bind address; use 0.0.0.0 for trusted LAN access')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', default=str(ROOT / 'data' / 'network.db'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    store = Store(args.db)
    collector = Collector(store)
    server = create_server(store, collector, args.host, args.port)
    collector.start()
    print(f'Khaya is running at http://{"127.0.0.1" if args.host == "0.0.0.0" else args.host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        server.server_close()


if __name__ == '__main__':
    main()
