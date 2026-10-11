"""Device DNS property and cache tests without a live DNS server."""
import threading
import unittest
from unittest.mock import Mock
from network.device_dns import DeviceDNS


class DeviceDNSTests(unittest.TestCase):
    """Verify DNS property sourcing, cache lifetime, and bounded concurrency."""

    def test_hostname_targets_expand_without_network_lookup(self):
        """Display configured hostname targets without fabricating names from labels."""
        lookup = Mock()
        service = DeviceDNS(lookup_fn=lookup)
        self.assertEqual(service.describe('jina'), {'dns_name': 'jina.boma.home.arpa', 'dns_status': 'configured'})
        self.assertEqual(service.describe('JINA.BOMA.HOME.ARPA')['dns_name'], 'jina.boma.home.arpa')
        self.assertIsNone(service.describe('localhost')['dns_name'])
        lookup.assert_not_called()

    def test_ptr_name_cached_until_ttl_and_returned_as_copy(self):
        """Refresh an expired name and prevent callers from changing a cached result."""
        clock = Mock(return_value=10)
        lookup = Mock(side_effect=[(['jina.boma.home.arpa'], 60), (['replacement.boma.home.arpa'], 60)])
        service = DeviceDNS(lookup_fn=lookup, clock=clock)
        first = service.describe('10.0.10.53')
        self.assertEqual(first, {'dns_name': 'jina.boma.home.arpa', 'dns_status': 'resolved'})
        first['dns_name'] = 'mutated'
        self.assertEqual(service.describe('10.0.10.53')['dns_name'], 'jina.boma.home.arpa')
        self.assertEqual(lookup.call_count, 1)
        clock.return_value = 70
        self.assertEqual(service.describe('10.0.10.53')['dns_name'], 'replacement.boma.home.arpa')

    def test_changed_target_has_independent_lookup(self):
        """Changing a device's address cannot reuse the old address's DNS name."""
        lookup = Mock(side_effect=[(['first.boma.home.arpa'], 60), (['second.boma.home.arpa'], 60)])
        service = DeviceDNS(lookup_fn=lookup)
        self.assertEqual(service.describe('10.0.10.20')['dns_name'], 'first.boma.home.arpa')
        self.assertEqual(service.describe('10.0.10.21')['dns_name'], 'second.boma.home.arpa')

    def test_missing_and_failed_lookups_have_distinct_short_cache_lifetimes(self):
        """Cache missing records briefly and retry transport errors sooner."""
        for outcome, status, expiry in [(([], 30), 'not_found', 30), (OSError('timeout'), 'unavailable', 5), (ValueError('bad reply'), 'unavailable', 5)]:
            with self.subTest(status=status, outcome=outcome):
                lookup = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
                clock = Mock(return_value=0)
                service = DeviceDNS(lookup_fn=lookup, clock=clock)
                self.assertEqual(service.describe('10.0.10.53'), {'dns_name': None, 'dns_status': status})
                clock.return_value = expiry - 1
                service.describe('10.0.10.53')
                self.assertEqual(lookup.call_count, 1)
                clock.return_value = expiry
                service.describe('10.0.10.53')
                self.assertEqual(lookup.call_count, 2)

    def test_zero_ttl_is_not_cached_and_large_ttl_is_capped(self):
        """Respect zero TTL records and avoid retaining stale names for days."""
        for ttl, expiry in [(0, 0), (86400, 3600)]:
            with self.subTest(ttl=ttl):
                clock = Mock(return_value=0)
                lookup = Mock(return_value=(['pc.boma.home.arpa'], ttl))
                service = DeviceDNS(lookup_fn=lookup, clock=clock)
                service.describe('10.0.10.20')
                clock.return_value = expiry
                service.describe('10.0.10.20')
                self.assertEqual(lookup.call_count, 2)

    def test_cache_evicts_old_entries_when_capacity_is_exceeded(self):
        """Keep the cache bounded to 256 targets rather than growing indefinitely."""
        lookup = Mock(return_value=(['pc.boma.home.arpa'], 60))
        service = DeviceDNS(lookup_fn=lookup)
        for number in range(257):
            service.describe(f'10.0.{number // 256}.{number % 256}')
        service.describe('10.0.0.0')
        self.assertEqual(lookup.call_count, 258)

    def test_lookup_for_one_device_does_not_hold_cache_lock(self):
        """Allow another device's properties to return while a DNS query is blocked."""
        entered, release, second_done = threading.Event(), threading.Event(), threading.Event()

        def lookup(address):
            """Hold one query until an independent query has completed."""
            if address == '10.0.10.20':
                entered.set()
                release.wait(3)
            return ['pc.boma.home.arpa'], 60

        service = DeviceDNS(lookup_fn=lookup)
        first = threading.Thread(target=service.describe, args=('10.0.10.20',))

        def describe_second():
            """Signal when the independent lookup and cache update finish."""
            service.describe('10.0.10.21')
            second_done.set()

        second = threading.Thread(target=describe_second)
        first.start()
        try:
            self.assertTrue(entered.wait(1))
            second.start()
            self.assertTrue(second_done.wait(1))
        finally:
            release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
