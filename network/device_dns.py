"""DNS names for device properties, independent of inventory and health monitoring."""
from collections import OrderedDict
import threading
import time
from network.dns import qualify_name, reverse_lookup
from network.store import local_ipv4, validate_target


class DeviceDNS:
    """Cache DNS properties for at most 256 device targets, with no database writes."""

    def __init__(self, lookup_fn=None, clock=time.monotonic):
        """Attach a PTR lookup and monotonic clock; construct an empty, thread-safe cache."""
        self.lookup_fn = lookup_fn if lookup_fn is not None else reverse_lookup
        self.clock = clock
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def describe(self, target):
        """Return a detached mapping containing the full DNS name and its lookup status.

        Hostname targets are qualified without a lookup. Private IPs use cached
        PTR results, refreshing after their TTL (capped at an hour). Missing
        records are cached for 30 seconds and failures for five seconds. Network
        I/O runs outside the cache lock; results never alter monitoring targets.
        """
        target = validate_target(target)
        if target == 'localhost':
            return {'dns_name': None, 'dns_status': 'not_found'}
        if not local_ipv4(target):
            return {'dns_name': qualify_name(target), 'dns_status': 'configured'}
        with self.lock:
            cached = self.cache.get(target)
            if cached and self.clock() < cached[0]:
                self.cache.move_to_end(target)
                return dict(cached[1])
            self.cache.pop(target, None)
        try:
            names, ttl = self.lookup_fn(target)
            result = {'dns_name': names[0] if names else None, 'dns_status': 'resolved' if names else 'not_found'}
            lifetime = min(max(ttl, 0), 3600) if names else 30
        except (OSError, ValueError):
            result = {'dns_name': None, 'dns_status': 'unavailable'}
            lifetime = 5
        with self.lock:
            if lifetime > 0:
                self.cache[target] = (self.clock() + lifetime, dict(result))
                self.cache.move_to_end(target)
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
        return dict(result)
