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

# Backup to specific directory
cam-backup.py --dir /data

# Verbose logging with progress details
cam-backup.py -v
```

### `cam-clients.py`

Export NAC clients from CAM with filtering and pagination. Supports filtering by any attribute using repeatable `--filter key=value` with dot notation for nested fields (case-insensitive substring/contains matching). Configure batch size and limits for flexible data retrieval.

**Features**: Offset-based pagination fetches all clients. Use `--batch` to control request size (default: 1000) and `--limit` to cap results.

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

### `cam-users.py`

Export Meraki Auth Users (local authentication users) from Meraki networks. **By default, fetches users from ALL networks in the organization.** Fetches 802.1X, Guest, and Client VPN users with filtering and pagination. Supports filtering by any attribute using repeatable `--filter key=value` with dot notation for nested fields.

**Features**: Link header pagination fetches all users. Use `--batch` to control request size (default: 100) and `--limit` to cap results per network. Each user includes `_networkId` field for network context.

```sh
# Export all users from ALL networks (default)
cam-users.py

# Export all users to CSV
cam-users.py --format csv > all-users.csv

# Export users from a specific network only
cam-users.py --network N_123456789

# Export first 50 users per network for quick preview
cam-users.py --limit 50 --format table

# Export all Guest users to CSV (across all networks)
cam-users.py --filter accountType=Guest --format csv > guests.csv

# Find all 802.1X users in a specific network
cam-users.py -n N_123 --filter accountType=802.1X --format table

# Find users by email domain (all networks)
cam-users.py --filter email=@example.com

# Find expiring guest accounts across all networks
cam-users.py --filter accountType=Guest -f authorizations.0.expiresAt=2026-12

# Export in different formats with verbose logging
cam-users.py --format yaml -v
cam-users.py -n N_123 --format table -v
```

### `cam-users-add.py`

Bulk import Meraki Auth Users from CSV file. Imports local authentication users (802.1X, Guest, Client VPN) with parallel uploads, automatic batching, and validation. Designed for scale with support for millions of users.

**Features**: Pre-flight validation, parallel uploads (configurable workers), automatic chunking, rate limit handling, progress reporting.

```sh
# Import users from CSV
cam-users-add.py --network N_123456789 --file users.csv

# Import with more workers for faster processing
cam-users-add.py -n N_123 -f users.csv --workers 20 -v

# Import a million users with high concurrency
cam-users-add.py -n N_123 -f million-users.csv --workers 50 --timeout 60 -v

# Generate test data first
user-generator.py --count 100000 --output test-100k.csv
cam-users-add.py -n N_123 -f test-100k.csv -w 20
```

### `user-generator.py`

Generate CSV files with N Meraki Auth Users for bulk import testing. Creates test data for validating cam-users-add.py at scale.

```sh
# Generate 1,000,000 users (~150 MB CSV)
user-generator.py --count 1000000 --output million-users.csv

# Generate 100,000 802.1X users
user-generator.py -c 100000 -o users-100k.csv --type "802.1X"

# Generate 50,000 Guest users on SSID 2
user-generator.py -c 50000 -o guests.csv --type Guest --ssid 2

# Generate 10,000 Client VPN users
user-generator.py -c 10000 -o vpn-users.csv --type "Client VPN"
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
