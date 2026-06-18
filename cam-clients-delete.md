# cam-clients-delete.py

Delete all NAC clients and groups from Cisco Access Manager (CAM).

## Purpose

Removes all NAC clients and/or groups from a Meraki organization via the Dashboard API. Useful for cleaning test environments or resetting NAC state.

## Architecture

The script uses a continuous producer-consumer pipeline with simple worker allocation:

1. **Single Fetch Worker**: One worker sequentially fetches all pages using offset-based pagination
2. **Multiple Delete Workers**: Remaining workers (N-1) delete clients concurrently as they're fetched
3. **Offset Pagination**: Uses `startingAfter` with numeric offsets (0, 1000, 2000, etc.)
4. **Queue Coordination**: Bounded queue (20 items) manages data flow between fetch and delete
5. **Complete Processing**: Delete workers continue until all fetched clients are deleted

## Features

- **Continuous pipeline architecture**: Fetch and delete happen concurrently
- **Simple worker allocation**: 1 fetch worker + N-1 delete workers (configurable via `--workers`)
- Delete NAC clients via bulk delete endpoint (1000 per batch)
- Delete NAC groups individually
- Sequential fetching with offset-based pagination (`startingAfter`)
- Configurable timeout for bulk delete operations (`--timeout`)
- Filter deletion to clients-only or groups-only
- Dry-run mode to preview deletions
- Rate limit handling with automatic retry logic
- Verbose logging with worker-level visibility

## Requirements

- Always use TDD for testing in `# cam-clients-delete_test.py`
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

## Pagination Strategy

1. **Offset-Based Pagination**: Single fetch worker uses `startingAfter` parameter with numeric offsets
   - Page 1: no `startingAfter` (returns clients 0-999)
   - Page 2: `startingAfter=1000` (returns clients 1000-1999)
   - Page 3: `startingAfter=2000` (returns clients 2000-2999)
   - Continues until API returns fewer than batch size items
2. **Total Count Discovery**: First page response includes total count in metadata
3. **Sequential Fetching**: Worker fetches pages in order, queuing each batch for deletion
4. **Batch Size**: Configurable via `--batch` (default: 1000, max: 1000)
5. **Limit Support**: Use `--limit` to cap total clients processed

## Usage

### Delete all clients and groups (continuous pipeline)

```bash
cam-clients-delete.py
```

### Delete only clients (keep groups)

```bash
cam-clients-delete.py --clients-only
```

### Delete only groups (keep clients)

```bash
cam-clients-delete.py --groups-only
```

### Dry run (preview without deleting)

```bash
cam-clients-delete.py --dry-run
```

### Increase workers for faster processing

```bash
# 8 workers = 1 fetch + 7 delete
cam-clients-delete.py --workers 8
```

### Increase timeout for large bulk deletes

```bash
cam-clients-delete.py --timeout 120
```

### Maximum performance configuration

```bash
# 16 workers (1 fetch + 15 delete) with extended timeout
cam-clients-delete.py --workers 16 --timeout 120 --verbose
```

### Limit deletion to first N clients

```bash
cam-clients-delete.py --limit 1000
```

## API Endpoints

- `GET /organizations/{orgId}/nac/clients` - Fetch clients (max 1000)
- `POST /organizations/{orgId}/nac/clients/bulkDelete` - Delete clients in batches
- `GET /organizations/{orgId}/nac/clients/groups` - Fetch groups
- `DELETE /organizations/{orgId}/nac/clients/groups/{groupId}` - Delete individual group

## Behavior

1. **Client Deletion Pipeline** (unless `--groups-only`):
   - **Single fetch worker** sequentially fetches pages using `startingAfter` (offset 0, 1000, 2000, etc.)
   - First page response includes total count in metadata
   - Each page batch is queued immediately for deletion
   - **Multiple delete workers** (N-1) consume client IDs from queue concurrently
   - Delete in batches of 1000 via bulkDelete endpoint
   - Fetch and delete happen **concurrently** in a continuous pipeline
   - Worker allocation: `--workers 4` = 1 fetch + 3 delete, `--workers 8` = 1 fetch + 7 delete
   - Queue has bounded size (20 items) to prevent memory issues
   - Pipeline runs until all clients are fetched, then waits for all deletions to complete
   - Reports success/failure per batch from each worker

2. **Group Deletion** (unless `--clients-only`):
   - Fetches all groups from API
   - Lists all groups with name and ID (dry-run only)
   - Deletes groups individually (sequential, not parallelized)
   - Reports success/failure per group

3. **Dry Run Mode**:
   - Fetches and lists all items (sequential fetch, no pipeline)
   - Reports what would be deleted
   - Performs no actual deletion

4. **Worker Allocation**:
   - Default: 4 workers (1 fetch + 3 delete)
   - Minimum: 1 worker (fetch only, no concurrent delete)
   - Configurable via `--workers` flag
   - Always 1 fetch worker, remaining workers delete
   - Examples:
     - `--workers 4` = 1 fetch + 3 delete
     - `--workers 8` = 1 fetch + 7 delete
     - `--workers 16` = 1 fetch + 15 delete

5. **Timeout Handling**:
   - Default: 60 seconds for bulk delete operations
   - Configurable via `--timeout` flag
   - Increase for large deletions (1000+ clients per batch)

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
- Fetch worker logs include:
  - Total clients fetched so far
  - Total clients queued for deletion (shows if delete workers are keeping up)

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

Example with continuous pipeline (4 workers = 1 fetch + 3 delete):

```
2026-06-18T10:30:15 INFO Starting continuous pipeline with 4 workers...
2026-06-18T10:30:15 INFO Starting pipeline: 1 fetch worker, 3 delete workers
2026-06-18T10:30:17 INFO Fetched 1000/25853 clients, 1000 queued
2026-06-18T10:30:18 INFO ✅ Delete worker 1: Deleted batch 1 (1000 clients)
2026-06-18T10:30:18 INFO Fetched 2000/25853 clients, 1000 queued
2026-06-18T10:30:19 INFO ✅ Delete worker 2: Deleted batch 2 (1000 clients)
2026-06-18T10:30:19 INFO Fetched 3000/25853 clients, 1000 queued
2026-06-18T10:30:20 INFO ✅ Delete worker 3: Deleted batch 3 (1000 clients)
2026-06-18T10:30:20 INFO Fetched 4000/25853 clients, 1000 queued
2026-06-18T10:30:21 INFO ✅ Delete worker 1: Deleted batch 4 (1000 clients)
2026-06-18T10:30:21 INFO Fetched 5000/25853 clients, 1000 queued
...
(fetch and delete continue concurrently)
...
2026-06-18T10:32:40 INFO Fetched all available clients
2026-06-18T10:32:40 INFO Fetch complete - fetched 25853 total clients
2026-06-18T10:32:40 INFO Fetch worker stopped: 25853 clients queued for deletion
2026-06-18T10:32:45 INFO All deletions complete: 25853 clients deleted
2026-06-18T10:32:45 INFO All delete workers stopped
2026-06-18T10:32:45 INFO Pipeline complete: 25853/25853 clients deleted

============================================================
All deletions complete in 150.2s
Total clients deleted: 25853
Total groups deleted: 0
============================================================
```

Example with 16 workers for maximum performance (1 fetch + 15 delete):

```
2026-06-18T10:35:15 INFO Starting continuous pipeline with 16 workers...
2026-06-18T10:35:15 INFO Starting pipeline: 1 fetch worker, 15 delete workers
2026-06-18T10:35:17 INFO Fetched 1000/25853 clients, 1000 queued
2026-06-18T10:35:18 INFO ✅ Delete worker 1: Deleted batch 1 (1000 clients)
2026-06-18T10:35:18 INFO Fetched 2000/25853 clients, 1000 queued
2026-06-18T10:35:18 INFO ✅ Delete worker 2: Deleted batch 2 (1000 clients)
2026-06-18T10:35:18 INFO ✅ Delete worker 3: Deleted batch 3 (1000 clients)
2026-06-18T10:35:18 INFO Fetched 3000/25853 clients, 1000 queued
2026-06-18T10:35:19 INFO ✅ Delete worker 4: Deleted batch 4 (1000 clients)
2026-06-18T10:35:19 INFO ✅ Delete worker 5: Deleted batch 5 (1000 clients)
2026-06-18T10:35:19 INFO Fetched 4000/25853 clients, 1000 queued
...
(15 delete workers processing concurrently)
...
2026-06-18T10:36:10 INFO Fetch complete - fetched 25853 total clients
2026-06-18T10:36:32 INFO Pipeline complete: 25853/25853 clients deleted

============================================================
All deletions complete in 77.1s
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

## Performance

The continuous pipeline architecture provides optimal performance:

- **Concurrent operations**: Fetch and delete happen simultaneously
- **No wait time**: Delete workers start as soon as first page is fetched
- **Scalable**: Add more workers to increase throughput
- **Memory efficient**: Bounded queue prevents excessive memory usage

**Performance guidelines**:

- 4 workers (default): Good for most use cases
- 8 workers: 2x faster for large deletions
- 16+ workers: Maximum performance, but may hit API rate limits

## Related Scripts

- `cam-guest-purge.py` - Selective deletion of stale guest clients
- `cam-clients-export.py` - Export client data before deletion
