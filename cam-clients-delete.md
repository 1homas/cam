# cam-clients-delete.py

Delete all NAC clients and groups from Cisco Access Manager (CAM).

## Purpose

Removes all NAC clients and/or groups from a Meraki organization via the Dashboard API. Useful for cleaning test environments or resetting NAC state.

## ⚠️ Important: Meraki API Limitation

The Meraki Dashboard API for `/nac/clients` has a pagination bug:

- Returns `meta.totalCount` showing the real total (e.g., 25,853 clients)
- Only fetches up to 1,000 clients per request
- Does NOT provide Link headers for pagination
- Returns 500 errors when using `startingAfter` manually

**Workaround**: Use the `--loop` flag to automatically run multiple iterations until all clients are deleted.

## Features

- Delete NAC clients via bulk delete endpoint (100 per batch)
- Delete NAC groups individually
- **`--loop` flag** to handle API's 1000-item limit by running multiple iterations
- Filter deletion to clients-only or groups-only
- Dry-run mode to preview deletions
- Rate limit handling with retry logic
- Verbose logging option

## Requirements

- Python 3.11+
- `uv` package manager
- Meraki Dashboard API key with write access
- Organization ID

## Configuration

Environment variables (from `.env` file):

```bash
MERAKI_DASHBOARD_API_KEY=your_api_key_here
MERAKI_ORG_ID=your_org_id_here
```

## Usage

### Delete all clients and groups (with --loop for >1000 clients)

```bash
uv run cam-clients-delete.py --loop
```

### Delete all clients in one batch (max 1000)

```bash
uv run cam-clients-delete.py --clients-only
```

### Delete only groups

```bash
uv run cam-clients-delete.py --groups-only
```

### Dry run (preview without deleting)

```bash
uv run cam-clients-delete.py --dry-run
```

### Verbose output

```bash
uv run cam-clients-delete.py --verbose --loop
```

## API Endpoints

- `GET /organizations/{orgId}/nac/clients` - Fetch clients (max 1000)
- `POST /organizations/{orgId}/nac/clients/bulkDelete` - Delete clients in batches
- `GET /organizations/{orgId}/nac/clients/groups` - Fetch groups
- `DELETE /organizations/{orgId}/nac/clients/groups/{groupId}` - Delete individual group

## Behavior

1. **Client Deletion** (unless `--groups-only`):
   - Fetches up to 1000 clients per iteration
   - Lists all clients with MAC, name, and ID (dry-run only)
   - Deletes in batches of 100 via bulkDelete endpoint
   - Reports success/failure per batch
   - With `--loop`: repeats until no clients remain

2. **Group Deletion** (unless `--clients-only`):
   - Fetches groups (once, not repeated in loop)
   - Lists all groups with name and ID (dry-run only)
   - Deletes groups individually
   - Reports success/failure per group

3. **Dry Run Mode**:
   - Fetches and lists all items
   - Reports what would be deleted
   - Performs no actual deletion
   - `--loop` has no effect in dry-run

## Error Handling

- Validates required environment variables
- Handles rate limiting (429) with automatic retry
- Logs API failures with status codes
- Continues processing on individual failures
- Returns non-zero exit code for configuration errors
- Warns when API limit is reached (shows total vs. fetched count)

## Logging

- Default: WARNING level (errors only)
- `--verbose` or `--dry-run`: INFO level (detailed progress)
- Format: `YYYY-MM-DDTHH:MM:SS LEVEL message`
- Logs to stderr

## Output

Example dry-run output:

```
2026-05-29T14:35:03 INFO Fetched 1000 clients (total: 1000)
2026-05-29T14:35:03 WARNING API shows 25853 total clients but pagination unavailable (Meraki API limitation)
2026-05-29T14:35:03 WARNING Will delete 1000 clients this run - run script again to delete remaining clients
2026-05-29T14:35:03 INFO Dry run: would delete 1000 clients
2026-05-29T14:35:04 INFO Fetched 0 groups (total: 0)
2026-05-29T14:35:04 INFO Deletion complete in 1.1s
```

Example with `--loop` (actual deletion):

```
2026-05-29T10:30:15 INFO Fetched 1000 clients (total: 1000)
2026-05-29T10:30:15 WARNING API shows 25853 total clients but pagination unavailable
2026-05-29T10:30:16 INFO ✅ Deleted client batch 1 (100 clients)
2026-05-29T10:30:17 INFO ✅ Deleted client batch 2 (100 clients)
...
2026-05-29T10:30:25 INFO ✅ Deleted client batch 10 (100 clients)
2026-05-29T10:30:25 INFO Clients deleted this iteration: 1000/1000
2026-05-29T10:30:25 INFO Iteration 1 complete in 10.2s

============================================================
Starting iteration 2
============================================================

2026-05-29T10:30:26 INFO Fetched 1000 clients (total: 1000)
2026-05-29T10:30:26 WARNING API shows 24853 total clients but pagination unavailable
...
(continues until all clients deleted)

============================================================
All deletions complete after 26 iteration(s) in 265.3s
Total clients deleted: 25853
Total groups deleted: 0
============================================================
```

## Safety

⚠️ **WARNING**: This script deletes ALL clients and groups in the organization. Always:

- Use `--dry-run` first to verify what will be deleted
- Test in non-production environments
- Ensure you have backups or can recreate the data
- Consider using `--clients-only` or `--groups-only` for targeted deletion
- Use `--loop` for organizations with >1000 clients

## Dependencies

Managed via PEP 723 inline script metadata:

- `httpx>=0.27.0` - Async HTTP client
- `python-dotenv>=1.0.0` - Environment variable loading
- `click>=8.0.0` - CLI argument parsing

## Testing

Run unit tests:

```bash
pytest cam-clients-delete_test.py -v
```

## Known Issues

### Meraki API Pagination Bug

The `/organizations/{organizationId}/nac/clients` endpoint has a pagination bug:

- API spec says to use Link headers with `startingAfter`
- API returns `meta.totalCount` showing total clients exist
- API does NOT return Link headers
- Manual use of `startingAfter` with client ID or MAC causes 500 errors

**Impact**: Script fetches max 1000 clients per API call

**Workaround**: Use `--loop` flag to run multiple iterations

## Related Scripts

- `cam-guest-purge.py` - Selective deletion of stale guest clients
- `cam-clients-export.py` - Export client data before deletion
