# Dashboard domain and device DNS properties

## Proposed behavior

Show `DNS domain: boma.home.arpa` prominently on the network overview,
using the configured domain supplied by the server. Keep the existing
DNS configuration in Settings.

Add a `DNS name` property to every device's details alongside its IP
address or monitoring target. A hostname target is displayed in full,
expanding a short target such as `jina` to `jina.boma.home.arpa`.

For IP-based devices, retrieve the name from Pi-hole at `10.0.10.53`
using a reverse DNS (PTR) lookup. Show a full returned hostname in the
configured local domain. If Pi-hole has no usable local name, display
`No DNS name found`; if the lookup fails, show `DNS lookup unavailable`.
Do not fabricate a record by appending the domain to a display name.

## Implementation approach

Extend the shared DNS parser and transport to support PTR queries with
the existing three-second deadline and Pi-hole-only resolution. Resolve
the DNS property when a device's details are requested; cache successful
results for the returned TTL with a bounded cache. Cache missing names
briefly so repeated detail opens do not flood Pi-hole. Invalidate cached
entries naturally by keying them on the device's current target.

Keep DNS property retrieval independent of reachability monitoring and
inventory persistence. Do not probe sample devices; display sample DNS
names only when explicitly present in the sample fixture.

## Alternatives

An editable DNS-name field would let the owner maintain names manually,
but could drift from Pi-hole. Deriving names from inventory display names
would be simpler, but could show nonexistent DNS records. Automatic
Pi-hole retrieval is recommended because Pi-hole is the configured source.

## Validation

Test overview domain rendering, full DNS property rendering, short-name
expansion, valid local PTR records, missing names, lookup failures, cache
expiry, and changed device targets. Verify that sample inventory causes
no DNS requests and existing monitoring behavior and inventory are preserved.
Run the Python and frontend test suites, JavaScript syntax checks, and
Git whitespace checks.
