# Cisco Access Manager (CAM) Scripts and Automations

## Updates

If you want to update these scripts or vibe-code your own, you should download the latest Meraki Dashboard OpenAPI Specification from your dashboard and save it to the `references/` folder:

```sh
https://api.meraki.com/api/v1/organizations/$MERAKI_ORG_ID/openapiSpec?version=3
```

## Scripts

### `cam-clients-export.py`

Export NAC clients from CAM in JSON, YAML, CSV, or table format. Supports filtering by any attribute using repeatable `--filter key=value` with dot notation for nested fields (case-insensitive startswith matching).

**Note**: Due to a Meraki API bug, only the first 1,000 clients can be exported.

```sh
uv run cam-clients-export.py                                        # Export first 1000 clients as JSON
uv run cam-clients-export.py --help                                 # Show command options and examples
uv run cam-clients-export.py --format table                         # Pretty-printed ASCII table
uv run cam-clients-export.py --format csv --filter status=Connected # Connected clients as CSV
uv run cam-clients-export.py -f ssid=Guest -f source=Discovered     # Multiple filters (AND logic)
uv run cam-clients-export.py --filter classification.os=iOS         # Filter by nested field
uv run cam-clients-export.py -v                                     # Verbose logging (shows API limitation warning)
```

### `cam-guest-purge.py`

Purge stale Guest clients from CAM. Finds disconnected, discovered clients on guest SSIDs older than a specified age and deletes them via the Meraki bulkDelete API.

```sh
uv run cam-guest-purge.py --ssid Guest          # Delete guests older than 7 days (silent)
uv run cam-guest-purge.py --help                 # Show command options and examples
uv run cam-guest-purge.py --ssid Guest --age 2d12h  # Custom age threshold
uv run cam-guest-purge.py --ssid Visitor --dry-run   # List matches without deleting
uv run cam-guest-purge.py --ssid Guest -v        # Verbose logging
```

### `cam-clients-delete.py`

Delete all NAC clients and groups from CAM. Removes all clients via bulkDelete endpoint and all groups individually. Useful for cleaning test environments or resetting NAC state.

**Note**: Due to a Meraki API bug, only 1000 clients can be fetched per request. Use `--loop` to automatically delete all clients.

```sh
uv run cam-clients-delete.py --dry-run           # Preview what would be deleted
uv run cam-clients-delete.py --help              # Show command options and examples
uv run cam-clients-delete.py --loop              # Delete ALL clients and groups (handles >1000 clients)
uv run cam-clients-delete.py --clients-only      # Delete up to 1000 clients
uv run cam-clients-delete.py --clients-only --loop # Delete ALL clients
uv run cam-clients-delete.py --groups-only       # Delete only groups
uv run cam-clients-delete.py -v --loop           # Verbose logging with loop
```
