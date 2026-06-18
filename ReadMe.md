# Cisco Access Manager (CAM) Scripts and Automations

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
