"""Send periodic authenticated heartbeats from a monitored computer to Khaya."""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from network.store import local_ipv4


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep the heartbeat credential on the explicitly configured collector endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Refuse redirects so Authorization is never forwarded to another endpoint."""
        return None


def validate_url(url):
    """Require an explicit local IPv4 or localhost HTTP heartbeat endpoint without extras."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Enter the local collector heartbeat URL.') from exc
    if parsed.scheme != 'http' or not (parsed.hostname == 'localhost' or local_ipv4(parsed.hostname or '')) or parsed.username or parsed.password or parsed.query or parsed.fragment or not re.fullmatch(r'/api/heartbeat/[a-zA-Z0-9]+', parsed.path) or (port is not None and not 1 <= port <= 65535):
        raise ValueError('Use http://COLLECTOR_LAN_IP:8765/api/heartbeat/DEVICE_ID without credentials or query parameters.')
    return url


def make_request(url, token):
    """Build one JSON POST with the credential in Authorization, never in query parameters."""
    validate_url(url)
    if not token or not re.fullmatch(r'[A-Za-z0-9_-]+', token):
        raise ValueError('Set KHAYA_HEARTBEAT_TOKEN to the secret generated in the dashboard.')
    return urllib.request.Request(url, data=b'{}', headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='POST')


def main():
    """Send heartbeats until interrupted; report failures without printing the secret."""
    parser = argparse.ArgumentParser(description='Computer heartbeat agent for a local Khaya collector')
    parser.add_argument('--url', required=True, help='Collector URL shown in the device heartbeat setup panel')
    parser.add_argument('--interval', type=int, default=30, help='Seconds between heartbeats (default: 30)')
    parser.add_argument('--once', action='store_true', help='Send one heartbeat and exit')
    args = parser.parse_args()
    if not 5 <= args.interval <= 3600:
        parser.error('--interval must be between 5 and 3600 seconds')
    try:
        request = make_request(args.url, os.environ.get('KHAYA_HEARTBEAT_TOKEN', ''))
    except ValueError as exc:
        parser.error(str(exc))
    # Avoid environment proxy forwarding for this explicitly local endpoint.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        while True:
            success = False
            try:
                with opener.open(request, timeout=10) as response:
                    result = json.loads(response.read())
                    success = bool(result.get('accepted'))
                print(time.strftime('%H:%M:%S'), 'Heartbeat accepted' if success else 'Heartbeat rejected', flush=True)
            except urllib.error.HTTPError as exc:
                print(time.strftime('%H:%M:%S'), f'Collector returned HTTP {exc.code}; verify your device and secret.', file=sys.stderr, flush=True)
            except (OSError, ValueError):
                print(time.strftime('%H:%M:%S'), 'Collector unavailable; verify its LAN address and that the service is running.', file=sys.stderr, flush=True)
            if args.once:
                return 0 if success else 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
