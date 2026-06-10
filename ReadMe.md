# Cisco Access Manager (CAM) Scripts and Automations

## Updates

If you want to update these scripts or vibe-code your own, you should download the latest Meraki Dashboard OpenAPI Specification from your dashboard and save it to the `references/` folder:

```sh
https://api.meraki.com/api/v1/organizations/$MERAKI_ORG_ID/openapiSpec?version=3
```

## Scripts

### `cam-clients-export.py`

Export NAC clients from CAM with filtering. Supports filtering by any attribute using repeatable `--filter key=value` with dot notation for nested fields (case-insensitive startswith matching).

**Note**: Due to a Meraki API bug, only the first 1,000 clients can be exported.

```sh
# Export all clients as JSON
cam-clients-export.py

# Export all clients to CSV for spreadsheet import
cam-clients-export.py --format csv > clients.csv

# View all clients in a table
cam-clients-export.py --format table

# Export all connected clients to CSV
cam-clients-export.py --format csv --filter status=Connected > connected.csv

# Find all Guest SSID devices that were discovered (not provisioned)
cam-clients-export.py --format table -f ssid=Guest -f source=Discovered

# Find all iOS devices using nested field filtering
cam-clients-export.py --filter classification.os=iOS

# Multiple filters work with AND logic
cam-clients-export.py -f owner=jsmith -f ssid=Corp --format table
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

Delete all NAC clients and groups from CAM. Useful for cleaning test environments or resetting NAC state.

**Note**: Due to a Meraki API bug, only 1000 clients can be fetched per request. Use `--loop` to delete all clients when you have more than 1000.

```sh
# Preview what would be deleted (safe, no changes)
cam-clients-delete.py --dry-run

# Delete ALL clients and groups (handles >1000 clients automatically)
cam-clients-delete.py --loop

# Delete only clients, keep groups
cam-clients-delete.py --clients-only --loop

# Delete only groups, keep clients
cam-clients-delete.py --groups-only
```
