# Cisco Access Manager (CAM) Scripts and Automations

## Updates

If you want to update these scripts or vibe-code your own, you should download the latest Meraki Dashboard OpenAPI Specification from your dashboard and save it to the `references/` folder:

```sh
https://api.meraki.com/api/v1/organizations/$MERAKI_ORG_ID/openapiSpec?version=3
```

## Scripts

### `cam-clients.py`

List all NAC clients from CAM in JSON, YAML, CSV, or table format.

```sh
cam-clients.py                    # List all clients as JSON
cam-clients.py --help             # Show command options
cam-clients.py --format table     # Pretty-printed ASCII table
cam-clients.py --format csv       # CSV format
cam-clients.py -v                 # Verbose logging
```

### `cam-clients-export.py`

Export NAC clients from CAM in JSON, YAML, CSV, or table format. Supports filtering by any attribute using repeatable `--filter key=value` with dot notation for nested fields (case-insensitive startswith matching).

**Note**: Due to a Meraki API bug, only the first 1,000 clients can be exported.

```sh
cam-clients-export.py                                        # Export first 1000 clients as JSON
cam-clients-export.py --help                                 # Show command options and examples
cam-clients-export.py --format table                         # Pretty-printed ASCII table
cam-clients-export.py --format csv --filter status=Connected # Connected clients as CSV
cam-clients-export.py -f ssid=Guest -f source=Discovered     # Multiple filters (AND logic)
cam-clients-export.py --filter classification.os=iOS         # Filter by nested field
cam-clients-export.py -v                                     # Verbose logging (shows API limitation warning)
```

### `cam-clients-add.py`

Bulk add NAC clients to CAM from a base64-encoded CSV file or CSV file path. Supports updating existing clients and creating new groups.

```sh
cam-clients-add.py --file clients.csv                        # Upload clients from CSV file
cam-clients-add.py --help                                    # Show command options and examples
cam-clients-add.py "base64_string"                           # Upload from base64-encoded CSV
cam-clients-add.py --file clients.csv --create-groups        # Upload and create groups
cam-clients-add.py --file clients.csv --no-update-clients    # Skip updating existing clients
cam-clients-add.py --file clients.csv --format json          # Full JSON response
cam-clients-add.py --file clients.csv -v                     # Verbose logging
```

### `cam-guest-purge.py`

Purge stale Guest clients from CAM. Finds disconnected, discovered clients on guest SSIDs older than a specified age and deletes them via the Meraki bulkDelete API.

```sh
cam-guest-purge.py --ssid Guest          # Delete guests older than 7 days (silent)
cam-guest-purge.py --help                 # Show command options and examples
cam-guest-purge.py --ssid Guest --age 2d12h  # Custom age threshold
cam-guest-purge.py --ssid Visitor --dry-run   # List matches without deleting
cam-guest-purge.py --ssid Guest -v        # Verbose logging
```

### `cam-clients-delete.py`

Delete all NAC clients and groups from CAM. Removes all clients via bulkDelete endpoint and all groups individually. Useful for cleaning test environments or resetting NAC state.

**Note**: Due to a Meraki API bug, only 1000 clients can be fetched per request. Use `--loop` to automatically delete all clients.

```sh
cam-clients-delete.py --dry-run           # Preview what would be deleted
cam-clients-delete.py --help              # Show command options and examples
cam-clients-delete.py --loop              # Delete ALL clients and groups (handles >1000 clients)
cam-clients-delete.py --clients-only      # Delete up to 1000 clients
cam-clients-delete.py --clients-only --loop # Delete ALL clients
cam-clients-delete.py --groups-only       # Delete only groups
cam-clients-delete.py -v --loop           # Verbose logging with loop
```
