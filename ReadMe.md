# Cisco Access Manager (CAM) Scripts and Automations

## Setup

### 1. Install `uv`

These scripts use [PEP 723](https://peps.python.org/pep-0723/) inline dependency metadata and run directly via `uv` — no virtualenv or `pip install` needed.

```sh
# macOS
brew install uv
# Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify
uv --version
```

### 2. Get your Meraki Dashboard API key

1. Sign in to the [Meraki Dashboard](https://dashboard.meraki.com/).
2. Enable API access for your organization: **Organization > Settings > Dashboard API access** (toggle "Enable access to the Cisco Meraki Dashboard API").
3. Generate a key under your profile: click your name (top right) > **My profile** > **API access** > **Generate new API key**.
4. Copy the key immediately — Meraki only shows it once. Treat it like a password; it grants full access to everything your account can see.

### 3. Find your Organization ID

```sh
curl -s -L -H "Authorization: Bearer $MERAKI_DASHBOARD_API_KEY" \
  https://api.meraki.com/api/v1/organizations | jq '.[] | {id, name}'
```

Or find it in the dashboard URL when viewing an organization (`.../o/<ORG_ID>/...`).

### 4. Configure environment variables

Copy the example file and fill in your values:

```sh
cp .env.example.txt .env
```

```sh
# .env
MERAKI_DASHBOARD_API_KEY=your_api_key_here
MERAKI_ORG_ID=your_org_id_here
```

`.env` is loaded automatically via `python-dotenv` and is gitignored — never commit it.

### 5. Run a script

Scripts are executable and self-install their own dependencies on first run:

```sh
./cam-clients.py -v
```

## Updates

If you want to update these scripts or vibe-code your own, you should download the latest Meraki Dashboard OpenAPI Specification from your dashboard and save it to the `references/` folder:

```sh
https://api.meraki.com/api/v1/organizations/$MERAKI_ORG_ID/openapiSpec?version=3
```

## Scripts

### `cam-api-status.py`

Test **all available** Cisco Access Manager (CAM) `/nac/` API endpoints and save responses. Automatically discovers and tests parameterized endpoints (dictionary attributes, session details) based on returned data.

```sh
# Test all endpoints and save responses to api_test/
./cam-api-status.py

# View specific response
cat api_test/nac_clients.json | jq '.'

# Count total API calls
ls -1 api_test/*.json | wc -l
```

### `cam-backup.py`

Backup all CAM data from multiple API endpoints to date-stamped JSON files. Creates `backups/YYYY-MM-DD/` directory structure and fetches clients, groups, policies, certificates, and other NAC configuration data. Supports automatic pagination for large datasets.

```sh
# Backup to ./backups/2026-06-12/
cam-backup.py

# Override the organization for this run
cam-backup.py --org 123456

# Backup to specific directory
cam-backup.py --dir /data

# Verbose logging with progress details
cam-backup.py -v
```

### `cam-clients.py`

Search, create, update, and delete NAC clients in CAM. With no CRUD flag given, the default action is to search/export all clients — supports filtering by any attribute using repeatable `--filter key=value` with dot notation for nested fields (case-insensitive substring/contains matching), plus pagination via `--batch`/`--limit`.

**Features**: Offset-based pagination fetches all clients for search. `--create`/`--update` accept a CSV file or inline JSON for one client, run in parallel (`--workers`). `--delete`/`--delete-id` use the batched `bulkDelete` API.

```sh
# Export all clients as JSON (default batch size: 1000)
cam-clients.py

# Export first 100 clients for quick preview
cam-clients.py --limit 100

# Export with custom batch size
cam-clients.py --batch 500

# Export all clients to CSV for spreadsheet import
cam-clients.py --format csv > clients.csv

# Quick preview of first 50 clients in table format
cam-clients.py --limit 50 --format table

# View all clients in a table
cam-clients.py --format table

# Export all connected clients to CSV
cam-clients.py --format csv --filter status=Connected > connected.csv

# Find all Guest SSID devices that were discovered (not provisioned)
cam-clients.py --format table -f ssid=Guest -f source=Discovered

# Find all iOS devices using nested field filtering
cam-clients.py --filter classification.os=iOS

# Multiple filters work with AND logic
cam-clients.py -f owner=jsmith -f ssid=Corp --format table

# Combine limit with filters for targeted preview
cam-clients.py --limit 25 --filter ssid=Guest --format table

# Create clients from CSV or inline JSON
cam-clients.py --create clients.csv
cam-clients.py --create '{"mac": "AA:BB:CC:DD:EE:FF", "description": "Printer"}'

# Update clients from CSV or inline JSON (requires 'id')
cam-clients.py --update updates.csv
cam-clients.py --update '{"id": "627126248111374692", "description": "Reassigned"}'

# Delete clients from CSV or inline JSON, or by a single ID
cam-clients.py --delete deletes.csv
cam-clients.py --delete-id 627126248111374692
```

### `cam-clients-groups.py`

Search, create, update, and delete NAC client groups in CAM. Follows the same CSV/JSON CRUD pattern as `cam-clients.py` and `cam-users.py`.

**Features**: `--search` supports fuzzy `--search-query`, sorting, and pagination. `--create`/`--update`/`--delete` accept a CSV file or inline JSON for one group, run in parallel (`--workers`). `members`/`membersAdd`/`membersRemove` accept semicolon-separated client IDs.

```sh
# Search/list groups
cam-clients-groups.py --search
cam-clients-groups.py --search --search-query Camera --format table

# Create groups from CSV or inline JSON
cam-clients-groups.py --create groups.csv
cam-clients-groups.py --create '{"name": "Cameras", "description": "Security cameras"}'

# Update groups from CSV or inline JSON (requires 'id')
cam-clients-groups.py --update updates.csv
cam-clients-groups.py --update '{"id": "627126248111341608", "membersAdd": "1;2;3"}'

# Delete groups from CSV or inline JSON, or by a single ID
cam-clients-groups.py --delete deletes.csv -w 20
cam-clients-groups.py --delete-id 627126248111341608
```

### `cam-network-clients.py`

Download all clients from all networks in the org, asynchronously. Uses the standard per-network Meraki client endpoint (`/networks/{id}/clients`), not the CAM NAC endpoint used by `cam-clients.py`, so it captures every client Meraki has seen — not just NAC-registered ones. Supports the same `--filter key=value` filtering with dot notation.

**Features**: Fetches networks concurrently (bounded by `--concurrency`, default 5). A network that errors out is logged and skipped rather than aborting the run. Each client is tagged with `_networkId`/`_networkName`. Default output format is CSV.

**Output columns** (CSV/table, in order): `MAC address`, `Endpoint device group` (always blank — no such field on network clients), `Description`, `_networkId`, `_networkName`, `id`, `ip`, `ip6`, `vlan`, `namedVlan`, `ssid`, `switchport`, `status`, `os`, `manufacturer`, `user`, `usage.sent`, `usage.recv`, `recentDeviceConnection`, `recentDeviceName`, `firstSeen`, `lastSeen`, `notes`

```sh
# Download all clients from all networks as CSV (default)
cam-network-clients.py > all-clients.csv

# Export as JSON
cam-network-clients.py --format json

# Override the organization for this run
cam-network-clients.py --org 123456

# Limit to a single network
cam-network-clients.py -n N_123456789

# Fetch more networks concurrently
cam-network-clients.py --concurrency 10

# Only clients seen in the last day (default lookback is the API max: 31 days)
cam-network-clients.py --timespan 86400

# Filter to online clients on a specific SSID
cam-network-clients.py --filter status=Online -f ssid=Guest

# Quick preview: first 100 clients per network, as a table
cam-network-clients.py --limit 100 --format table
```

### `cam-clients-add.py`

Bulk add NAC clients to CAM from a CSV file. Supports updating existing clients and creating new groups. Automatically batches large uploads (>1000 rows).

```sh
# Upload clients from CSV file
cam-clients-add.py --file clients.csv

# Upload and automatically create any missing groups
cam-clients-add.py --file clients.csv --create-groups

# Upload from base64-encoded CSV (useful for APIs/automation)
cam-clients-add.py "$(base64 < clients.csv)"

# Upload large file with verbose progress
cam-clients-add.py --file 10000-clients.csv -v

# Upload with custom timeout (default: 60s)
cam-clients-add.py --file clients.csv --timeout 120
```

### `cam-guest-purge.py`

Purge stale guest clients from CAM. Finds disconnected, discovered clients on guest SSIDs older than 7 days (default) and deletes them.

```sh
# Delete Guest SSID clients older than 7 days
cam-guest-purge.py --ssid Guest

# Delete Visitor SSID clients older than 2.5 days
cam-guest-purge.py --ssid Visitor --age 2d12h

# Preview what would be deleted without actually deleting
cam-guest-purge.py --ssid Guest --dry-run

# Show detailed progress
cam-guest-purge.py --ssid Guest -v
```

### `cam-clients-delete.py`

Delete all NAC clients and groups from CAM using a continuous pipeline architecture. Useful for cleaning test environments or resetting NAC state.

**Architecture**: Uses a producer-consumer pipeline with 1 fetch worker sequentially fetching all pages while N-1 delete workers process them concurrently. Simple, efficient, and easy to reason about.

```sh
# Preview what would be deleted (safe, no changes)
cam-clients-delete.py --dry-run

# Delete ALL clients and groups (continuous pipeline)
cam-clients-delete.py

# Delete with 8 workers (1 fetch + 7 delete) for faster operation
cam-clients-delete.py --workers 8

# Delete with 16 workers (1 fetch + 15 delete) for maximum performance
cam-clients-delete.py --workers 16 --timeout 120 --verbose

# Delete with increased timeout for large batches
cam-clients-delete.py --timeout 120

# Delete only clients, keep groups
cam-clients-delete.py --clients-only

# Delete only groups, keep clients
cam-clients-delete.py --groups-only

# Limit deletion to first N clients
cam-clients-delete.py --limit 5000
```

### `cam-users-generator.py`

Generate CSV files with N Meraki IAM (IdP) users (columns: `email`, `displayName`, `password`, `sendPassword`) for load-testing `cam-users.py --create`.

```sh
# Generate 1,000,000 users (~150 MB CSV)
cam-users-generator.py --count 1000000 --output million-users.csv

# Generate 100,000 users
cam-users-generator.py -c 100000 -o users-100k.csv

# Generate 10,000 users with sendPassword=true
cam-users-generator.py -c 10000 -o users-10k.csv --send-password
```

### `mac-generator.py`

Generate random MAC addresses with optional OUI specification. Useful for creating test data, bulk client uploads, or network simulations.

```sh
# Generate a single MAC address with random OUI (default)
./mac-generator.py

# Generate 5 MAC addresses with a specific OUI (e.g., Cisco's OUI)
./mac-generator.py -c 5 -o c0:ff:ee

# Generate 10 MAC addresses in uppercase
./mac-generator.py -c 10 --upper

# Generate 3 MAC addresses with random OUIs
./mac-generator.py -c 3

# Generate MAC addresses with specific OUI in uppercase
./mac-generator.py -c 5 -o c0:ff:ee --upper
```
