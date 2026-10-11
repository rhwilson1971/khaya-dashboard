# Local DNS for Khaya

Use `boma.home.arpa` as the local DNS domain and query Pi-hole at
`10.0.10.53` (`jina.boma.home.arpa`) for hostname resolution.

## Proposed behavior

- Resolve short device targets such as `jina` as `jina.boma.home.arpa`.
- Resolve fully qualified local device targets directly through Pi-hole,
  without falling back to the operating system resolver or public DNS.
- Keep existing literal private IPv4 targets working. Preserve inventory
  addresses, names, topology, and history; do not invent DNS records from
  display names or automatically replace existing targets.
- Support dashboard access and computer heartbeat URLs under
  `boma.home.arpa` while preserving same-origin validation, private-address
  checks, redirect refusal, and credential protection. Resolve heartbeat
  destinations through Pi-hole and connect to the validated address.
- Show DNS domain and server in the dashboard settings and document how
  to configure client DNS and a collector record in Pi-hole. Browsers use
  their client DNS configuration; this application cannot configure DNS
  on other devices.
- Treat DNS timeouts, missing records, malformed answers, and public
  answers as visible monitoring errors, with bounded lookup time.

## Approach

Prefer an application DNS resolver shared by monitoring and heartbeats.
This guarantees use of the requested server even when the collector's
system resolver differs. Using only system DNS would require configuring
every host separately and would not guarantee Pi-hole use. Merely changing
inventory targets would leave resolution and dashboard access unresolved.

## Validation

Test short-name expansion, Pi-hole selection, valid local answers, rejected
public answers, lookup failures, local-domain HTTP access, same-origin
rejection, and heartbeat address pinning. Run existing Python and frontend
checks. Live DNS verification requires network access to Pi-hole.
