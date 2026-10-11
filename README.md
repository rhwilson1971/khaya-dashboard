# Khaya

A local home network dashboard with device inventory, computer reachability, ICMP checks, authenticated computer heartbeats, explicit discovery, and an interactive topology map. Your inventory stays in a local SQLite database.

## Run

Requires Python 3.10+ and your operating system's `ping` command. No package installation or frontend build is required.

```sh
cd /Users/drreubenwilson/dev/projects/home_network
python3 -m network.server
```

Open **http://127.0.0.1:8765**. Keep the terminal process running for background collection. Stop it with Ctrl+C. The browser polls every five seconds; ICMP checks run every 30 seconds by default. Inventory, recent checks, and events survive restarts in `data/network.db`.

The first empty inventory opens a clearly labeled sample network. Select **Use my network** to configure your real devices. Sample devices are isolated from persistence and never probed. Adding a real device switches the dashboard to your real inventory. A service-unavailable banner means health is not being collected; any previously displayed observations are stale.

### Access from your phones and other LAN devices

```sh
python3 -m network.server --host 0.0.0.0
```

Open `http://YOUR_COLLECTOR_HOSTNAME.boma.home.arpa:8765` from another device, using the collector's existing Pi-hole DNS record, or use its literal LAN IPv4 address. Allow port 8765 through the host firewall only for your trusted home network. LAN mode provides inventory editing to devices that can reach the service; there is no user login. Keep it on a trusted LAN and do not forward this port to the internet. The host/origin protection accepts private IPv4 addresses and valid hostnames in `boma.home.arpa`; requests must use the same origin and the configured port. Reverse proxies are outside this setup.

Choose another port or database if needed:

```sh
python3 -m network.server --port 8766 --db /path/to/network.db
```

### Local DNS

The home domain is **`boma.home.arpa`**. Device hostname checks and hostname-based computer heartbeats query **Pi-hole at `10.0.10.53` (`jina.boma.home.arpa`)**, directly on DNS port 53. These fixed values are displayed in Settings and defined in `network/config.py`.

Enter a short device target such as `jina` or its full name `jina.boma.home.arpa`. Short names are expanded to the home domain. Existing literal private IPv4 targets continue working and bypass DNS; inventory names, addresses, connections, and history are preserved. Display names are not automatically converted into DNS records. To monitor another device by DNS, edit its target to a name that already exists in Pi-hole.

For dashboard access by hostname, configure each client to obtain DNS server `10.0.10.53` through your LAN DHCP settings or its own network settings. Use the collector's existing `boma.home.arpa` record, or add a record in Pi-hole pointing to the collector's LAN IPv4. For example, if you create `khaya.boma.home.arpa` for the collector, open `http://khaya.boma.home.arpa:8765` after starting the server with `--host 0.0.0.0`. The application does not change DHCP, Pi-hole records, or operating-system DNS settings.

The collector and heartbeat agent must be able to reach `10.0.10.53` on UDP and TCP port 53. Application lookups use Pi-hole even if the host's default resolver differs. DNS resolution has a shared three-second deadline, supports CNAME answers and TCP fallback, and rejects public IPv4 answers. Missing records, malformed responses, and DNS failures appear as **Unknown** monitoring results with an error; no system or public DNS fallback is used. Browser hostname resolution uses the browser client's DNS configuration.

## Configure your home

1. Add your router, switches, and access points. Enter each private IPv4 address or local hostname, type, and location.
2. Add your computers, phones, tablets, TVs, streaming devices, IoT devices, and other clients.
3. Use **Connects to** to select each device's upstream parent, then choose wired or wireless. The graph shows these configured connections and prevents loops. Discovery does not infer switch ports or actual physical topology.
4. In **Settings**, set your actual LAN subnet, check interval, consecutive missed-check threshold, and heartbeat timeout. Appearance (System, Light, Dark) lives in Settings too and is stored per browser; System follows your OS setting automatically.
5. Select **Discover devices**, review the subnet, and start the scan. Import selected responders, then edit their names, types, locations, and parents. Nothing is scanned automatically. Only private IPv4 subnets of /24 or smaller are supported, with at most 254 addresses.

Search devices by name, address, manufacturer, MAC, or location. Filter by type, status, and location. Select a row or graph node for details, the last successful response, and recent check history. The network map supports drag-to-pan, wheel or button zoom, fit-to-view, keyboard node selection, and a presentation view (Escape exits).

## Understand health

| Status | Meaning |
| --- | --- |
| Online | A successful ICMP echo or authenticated heartbeat was observed. The configured missed-check threshold gives ICMP devices a short grace period before changing to offline. |
| Offline | The configured number of consecutive ICMP checks missed a response, or a previously online heartbeat device exceeded its heartbeat timeout. |
| Unknown | No usable check has completed, monitoring is disabled, or a check failed because of a collector error, missing ping, permissions, or DNS resolution. |

ICMP measures **network reachability**, not CPU, memory, application status, or complete computer health. Sleeping phones/tablets, firewalls, client isolation, and some IoT devices block ping. They may appear offline while functioning normally. Use heartbeat monitoring for computers that block ping, or choose **Inventory only** for devices you do not want monitored.

Targets must be IPv4 in RFC1918 private ranges or loopback. Local hostnames must resolve exclusively to those IPv4 ranges. IPv6, public addresses, SNMP, router vendor APIs, automatic manufacturer identification, and multi-parent/redundant topology are outside this version. A configured /24 ping discovery takes roughly 15–60 seconds depending on responses and the operating system.

## Computer heartbeats

1. Add or edit a computer and select **Computer heartbeat** under Health check.
2. Open the device details and select **Set up heartbeat**. Save the generated secret; the database retains only its SHA-256 verifier. Generating a replacement immediately invalidates the old secret.
3. Copy `heartbeat_agent.py` and the `network` folder to the monitored computer (or use this checkout there). Python 3.10+ is the only requirement.
4. On that computer, set the secret in an environment variable and run the agent against the displayed heartbeat endpoint. For a different computer, replace `127.0.0.1` with the collector's actual `boma.home.arpa` hostname or LAN IPv4 address and enable LAN mode on the collector.

macOS / Linux:

```sh
export KHAYA_HEARTBEAT_TOKEN='YOUR_GENERATED_SECRET'
python3 heartbeat_agent.py --url http://YOUR_COLLECTOR_HOSTNAME.boma.home.arpa:8765/api/heartbeat/YOUR_DEVICE_ID
```

Windows PowerShell:

```powershell
$env:KHAYA_HEARTBEAT_TOKEN = 'YOUR_GENERATED_SECRET'
python heartbeat_agent.py --url http://YOUR_COLLECTOR_HOSTNAME.boma.home.arpa:8765/api/heartbeat/YOUR_DEVICE_ID
```

The default heartbeat interval is 30 seconds. Set the dashboard's heartbeat timeout comfortably above that interval (default 180 seconds). Use `--interval 20` to change the agent interval or `--once` for a single connectivity check. Run the agent as a startup task on computers you want to monitor continuously. Each hostname-based send resolves through Pi-hole and connects to the validated private IP while retaining the original HTTP Host header. The agent refuses public destinations and redirects, bypasses environment proxies for the local endpoint, and never prints the secret in its logs. Transport is local HTTP, so use only on your trusted LAN.

A heartbeat certifies that the agent could run and contact the collector. It does not inspect the computer's applications or resource usage.

## Persistence and privacy

- `data/network.db`: real inventory, settings, the latest 500 observations per device, and recent events.
- `static/sample-data.js` and `network/sample.py`: illustrative fixtures, independent of real inventory.
- Browser storage: selected live/sample mode only.
- No external fonts, analytics, cloud services, or JavaScript packages.
- API responses exclude heartbeat verifiers; heartbeat credentials are accepted only through `Authorization: Bearer …`.
- Browser requests are guarded by private-IP or configured home-domain host validation, same-origin checks, JSON request bodies, and no CORS access. Loopback mode remains restricted to `localhost` and `127.0.0.1`. These guards are not a substitute for LAN access control.

Back up the database while the service is stopped. Removing a device permanently removes its observations and detaches child devices; the UI asks for confirmation.

## Verify

```sh
python3 -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
node --check static/app.js
```

Python tests exercise Pi-hole DNS packets and transport, SQLite persistence and validation, topology cycles, probe result interpretation, thresholds, heartbeat destination pinning, authentication and expiry, collector behavior, discovery/import, and the actual HTTP request parser on an in-memory transport. The memory transport works in environments that prohibit listening sockets. Frontend tests exercise rendered state, DNS configuration display, escaping, search/filter logic, and graph topology using Node's built-in test runner; Node is needed only for those development checks.

During development in this session, the sandbox blocked binding a local server and sending even a localhost ICMP packet. Browser policy also blocked opening a local-file preview. Automated tests and syntax checks can run here, but live server, real network discovery, browser interactions, and responsive visual QA require running the application outside this sandbox. No real home devices were discovered or configured during development.


## Imported master inventory

The actual database was populated from `Home Network & Lab Master Inventory.xlsx` on October 5, 2026 (local time): all 43 addressed rows from **DHCP & Device Inventory**. Names, IPs, MACs, locations, and original notes were retained. Source roles, hardware models, and alternate identifiers are preserved in device notes. Servers, workstations, and laptops appear under Computer; the source role remains in notes.

The four APs are linked to DRREUBZ-POE-8-SWITCH using the documented MS510TXPP switch schedule. tembo, mahadoha, saa, and the work laptop dock have documented wired connections to the unmanaged Tenda switch, which has no management IP, so their parent remains unmapped in the monitored-device graph. Other undocumented connections are Unknown rather than inferred as Ethernet or Wi-Fi. The two explicitly identified Wireless Client entries retain wireless connections.

Discovery defaults to `10.0.10.0/24`. The inventory IPs were preserved even though the VLAN architecture tab describes separate IoT, media, and security subnets. The owner confirmed on October 6, 2026 that Tenda TEG1024G is unmanaged, so no management IP or ICMP check is expected. CAM-01 through CAM-04 are planned wired cameras not yet owned and are excluded from actual inventory. The import report records these corrections; the original workbook sheets remain unchanged as source evidence. Combined hardware descriptions and spare drops are not uniquely identified devices.

`data/imports/` contains the database backup from before import and a JSON reconciliation report, including the workbook's source sheets and checksum. The workbook itself was not modified. Health observations come from the collector, not the spreadsheet. Restart the local service after code updates and refresh the dashboard; select **Use my network** if your browser remains in sample mode.
# khaya-dashboard
