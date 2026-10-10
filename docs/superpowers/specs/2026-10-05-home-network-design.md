# Home network dashboard design

## Recommended deployment
Run a local web application and monitoring service on an always-on computer or home server. Open the dashboard over the LAN. An optional hosted dashboard would require a local collector; a standalone hosted website cannot perform ICMP checks against LAN devices.

## Experience
Use a modern, responsive layout with a persistent navigation rail, calm neutral surfaces, blue accents, and clear status colors. Overview shows online, offline, unknown, and total device counts plus recent status changes. Devices provides search and filters by type, location, and health. Network Map shows router, switches, access points, and connected clients with selectable nodes, zoom, pan, and a presentation view. Device detail shows configuration, parent connection, last check, last successful response, latency, and recent health history.

## Inventory
Support routers, switches, access points, IoT, streaming devices, tablets, phones, computers, TVs, and custom types. Store names, IP addresses or hostnames, MAC addresses when available, manufacturer, location, notes, connection type, parent device, and monitoring method. Manual device creation and editing remain available regardless of discovery. Connections are user-editable because discovery alone does not reliably reveal physical topology.

## Monitoring and discovery
A background collector performs bounded ICMP checks at configurable intervals and records results in SQLite. Failed checks use a configurable consecutive-failure threshold before declaring a device offline. Devices without completed checks are unknown. Display ICMP reachability as the health signal and distinguish it from computer or application health. Heartbeat monitoring is optional for computers that cannot respond to ICMP; stale heartbeats become offline. A manual discovery action scans only the configured local subnet and presents candidates for import. Sleeping devices or devices that block ICMP may remain unknown or appear offline; the UI explains the last observed check.

## Components and data flow
The web frontend calls a local API. The API manages inventory, connection metadata, monitoring settings, and discovery jobs. A collector runs independently of browser visits, checks targets with bounded concurrency, and stores timestamped observations. SQLite persists inventory and recent health history. The frontend periodically refreshes summaries and device state. Provide install/run instructions and a clearly labeled sample-data mode until real devices are added or discovered.

## Error handling
Show explicit collector-unavailable, discovery-failed, and check-permission errors. Never represent sample results or stale observations as live measurements. Validate device targets and parent relationships; prevent topology cycles. Use argument-based subprocess calls for ping and avoid shell interpolation.

## Verification
Test inventory persistence, target validation, cycle prevention, monitoring state transitions, heartbeat expiry, and discovery imports. Verify navigation, manual entry, filtering, graph selection and presentation, and responsive layouts in a browser. Exercise actual local checks separately from deterministic collector tests.

## Alternatives
1. Local dashboard and collector: simplest path to real network monitoring; requires an always-on machine. Recommended.
2. Private hosted dashboard with local collector: remote access, with additional collector connectivity and authentication setup.
3. Browser-only dashboard: fast visual inventory and editable map, but cannot perform real ICMP checks or LAN discovery by itself.
