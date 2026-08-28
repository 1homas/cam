# cam-network-clients.py

Download all clients from all networks in a Meraki organization.

## Purpose

Fetches every network in `MERAKI_ORG_ID`, then asynchronously fetches clients from each network (`GET /networks/{networkId}/clients`) via the Meraki Dashboard API, and combines the results into a single export. Unlike `cam-clients.py` (which reads CAM's org-wide NAC client list), this script uses the standard per-network Meraki client endpoint, so it captures every client Meraki has seen on the network — not just NAC-registered ones.

## Features

- **Asynchronous downloads**: Fetches multiple networks concurrently (bounded by `--concurrency`)
- **Multiple Output Formats**: CSV (default), JSON, YAML, Markdown table
- **Flexible Filtering**: Filter by any field using dot notation (e.g. `status=Online`, `usage.sent=100`)
- **Pagination Control**: Configurable batch size and per-network result limits
- **Link Header Pagination**: Automatically follows pagination links for both networks and clients
- **Rate Limit Handling**: Auto-retries with `Retry-After` on HTTP 429
- **Resilient**: A failing network is logged and skipped rather than aborting the whole download
- **Verbose Logging**: Optional `-v` flag for detailed operation logs

## Requirements

- Python 3.11+
- `uv` package manager
- Environment variables:
  - `MERAKI_DASHBOARD_API_KEY` - Your Meraki Dashboard API key (required)
  - `MERAKI_ORG_ID` - Organization ID (required unless `--org` is passed)

## Options

| Flag          | Type    | Default   | Description                                                               |
| ------------- | ------- | --------- | ------------------------------------------------------------------------- |
| --org, -o     | string  | (none)    | Organization ID (overrides `MERAKI_ORG_ID`)                               |
| --network, -n | string  | (none)    | Limit download to a single network ID                                     |
| --format      | choice  | `csv`     | Output format: `csv`, `json`, `yaml`, or `table` (Markdown)               |
| --filter, -f  | string  | (none)    | Filter by key=value (repeatable, dot notation, case-insensitive contains) |
| --timespan    | float   | `2678400` | Lookback window in seconds (max: 2678400 / 31 days, the API max)          |
| --batch       | integer | `1000`    | Batch size per API request (range: 3-5000)                                |
| --limit       | integer | `0`       | Maximum number of clients to fetch per network (0 = all)                  |
| --concurrency | integer | `5`       | Number of networks to fetch concurrently                                  |
| -v, --verbose | flag    | false     | Enable verbose logging                                                    |
| --help        | flag    |           | Show command options and examples                                         |

## Usage

```bash
# Download all clients from all networks (CSV, default)
cam-network-clients.py > all-clients.csv

# Export as JSON
cam-network-clients.py --format json

# Override the organization for this run
cam-network-clients.py --org 123456

# Limit to a single network
cam-network-clients.py -n N_123456789

# Fetch more networks concurrently
cam-network-clients.py --concurrency 10

# Only clients seen in the last day
cam-network-clients.py --timespan 86400

# Filter to online clients
cam-network-clients.py --filter status=Online

# Filter to a specific SSID, as a table, with verbose logging
cam-network-clients.py --format table -f ssid=Guest -v

# Quick preview: first 100 clients per network
cam-network-clients.py --limit 100 --format table
```

## Output Formats

### CSV (default)

```csv
MAC address,Endpoint device group,Description,_networkId,_networkName,id,ip,ip6,vlan,namedVlan,ssid,switchport,status,os,manufacturer,user,usage.sent,usage.recv,recentDeviceConnection,recentDeviceName,firstSeen,lastSeen,notes
22:33:44:55:66:77,,Miles's phone,N_123,HQ,k74272e,1.2.3.4,2001:db8:3c4d:15::1,1,Default,Guest-WiFi,,Online,iOS,Apple,,100.0,200.0,Wireless,AP-01,1717000000,1717003600,
```

The **Endpoint device group** column matches the CAM CSV template convention (`references/csv/*.csv`), but network clients (unlike NAC clients) have no client-group field, so it's always blank.

### JSON

```json
[
  {
    "id": "k74272e",
    "mac": "22:33:44:55:66:77",
    "ip": "1.2.3.4",
    "description": "Miles's phone",
    "status": "Online",
    "manufacturer": "Apple",
    "os": "iOS",
    "usage": { "sent": 100.0, "recv": 200.0 },
    "_networkId": "N_123",
    "_networkName": "HQ"
  }
]
```

### YAML

Same fields as JSON, dumped as a YAML document.

### Table

Markdown table with the same columns as CSV.

### Columns

`MAC address`, `Endpoint device group` (always blank), `Description`, `_networkId`, `_networkName`, `id`, `ip`, `ip6`, `vlan`, `namedVlan`, `ssid`, `switchport`, `status`, `os`, `manufacturer`, `user`, `usage.sent`, `usage.recv`, `recentDeviceConnection`, `recentDeviceName`, `firstSeen`, `lastSeen`, `notes`

## Filtering

Filters use case-insensitive substring matching with dot notation for nested fields:

```bash
# Filter by status
--filter status=Online

# Filter by SSID
--filter ssid=Guest

# Filter by network
--filter _networkId=N_123

# Filter by nested usage field
--filter usage.sent=100

# Combine multiple filters (AND logic)
--filter status=Online --filter ssid=Corp
```

## API Details

**Endpoints**:

- `GET /organizations/{organizationId}/networks` - List all networks
- `GET /networks/{networkId}/clients` - Fetch clients per network

**Authentication**: Uses `Authorization: Bearer` header

**Pagination**: Link header with `startingAfter` parameter for both endpoints

**Rate Limits**: Automatically handles 429 responses with `Retry-After` retry

**Timespan**: The Meraki API only returns clients seen within a lookback window (max 31 days / 2678400 seconds); there is no "all time" option, so `--timespan` defaults to the API maximum

**Network Context**: Each exported client includes `_networkId` and `_networkName` fields

**Concurrency**: Networks are fetched concurrently, bounded by `--concurrency` (an `asyncio.Semaphore`); a network that errors out is logged and skipped rather than failing the whole run

## Behavior

- **Organization**: By default, uses `MERAKI_ORG_ID`; use `--org`/`-o` to override it for a single run
- **Networks**: By default, fetches all networks in the organization; use `--network` to limit to one network
- **Batch size**: Controls number of clients per API request (default: 1000); configurable via `--batch` (range: 3-5000)
- **Limit**: Optionally cap per-network results with `--limit` (0 = no limit)
- **Filtering**: Applied client-side after fetching all records; multiple filters combine with AND logic; matching is case-insensitive substring (contains)
- **Logging**: Structured output with ISO 8601 timestamps to stderr

## Testing

- Use TDD for all operations
- Test file: `cam-network-clients_test.py`
- Tests cover: all formatters, filter logic, network/client pagination, rate-limit retry, and per-network error isolation

```bash
./cam-network-clients_test.py
```

## Related Scripts

- `cam-clients.py` - Export CAM NAC clients (org-wide `/nac/clients` endpoint)
- `cam-users.py` - Export Meraki Auth Users from all networks

## Version

Created: 2026-08-28
