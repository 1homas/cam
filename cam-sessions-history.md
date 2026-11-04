# cam-sessions-history.py

Download Cisco Access Manager (CAM) NAC session history.

## Purpose

Fetches `GET /organizations/{organizationId}/nac/sessions/history` across all pages and exports the full session record set — every authentication/authorization session CAM recorded in the lookback window. Use it to pull raw session data for offline analysis (jq, spreadsheets, notebooks) rather than reading one page at a time in the dashboard.

Originally lived in the CAMalytics repo as `scripts/nac_sessions_history.py`; rewritten here as a standalone PEP 723 script with no dependency on CAMalytics' shared `MerakiClient`.

## Features

- **Full Pagination**: Follows numeric `startingAfter` row offsets until a short or empty page (safety limit: 250 pages)
- **Multiple Output Formats**: JSONL (default), JSON, YAML, CSV, Markdown table
- **Window Metadata**: JSON/YAML wrap sessions with `organizationId`, `fetchedAt`, `window`, and `count`
- **Rate Limit Handling**: Auto-retries with `Retry-After` on HTTP 429
- **File or stdout**: Prints to stdout by default; `--output` writes a file (parent directories created)
- **Verbose Logging**: Optional `-v` flag logs per-page progress to stderr

## Requirements

- Python 3.11+
- `uv` package manager
- Environment variables:
  - `MERAKI_DASHBOARD_API_KEY` - Your Meraki Dashboard API key (required)
  - `MERAKI_ORG_ID` - Organization ID (required unless `--org` is passed)

## Options

| Flag          | Type    | Default | Description                                                             |
| ------------- | ------- | ------- | ----------------------------------------------------------------------- |
| --org         | string  | (none)  | Organization ID (overrides `MERAKI_ORG_ID`)                             |
| --days        | integer | `31`    | Days of history to fetch (1-31; values above 31 are capped with a warning) |
| --t0          | string  | (none)  | Absolute start (ISO 8601, e.g. `2026-08-01T00:00:00Z`). Overrides `--days` |
| --t1          | string  | (none)  | Absolute end (ISO 8601). Bounds the window to `[t0, t1]`; requires `--t0`  |
| --pages, -p   | integer | `0`     | Pages to fetch (`perPage=1000`); `0` fetches all. Forced to `0` whenever `--days` or `--t0`/`--t1` is given explicitly, so a requested window is never truncated |
| --format      | choice  | `jsonl` | Output format: `json`, `jsonl`, `yaml`, `csv`, or `table` (Markdown)    |
| --output, -o  | path    | (none)  | Write output to this file instead of stdout                             |
| --pretty      | flag    | false   | Pretty-print (indent) JSON output                                       |
| -v, --verbose | flag    | false   | Enable verbose logging                                                  |
| --help        | flag    |         | Show command options                                                    |

Note: `-o` is `--output` here (as in `cam-license-usage.py`), not `--org`.

## Usage

```bash
# Fetch the full 31-day window as JSONL on stdout
cam-sessions-history.py

# Pipe JSONL to jq for the most recent session
cam-sessions-history.py | tail -1 | jq .

# Session counts by result
cam-sessions-history.py | jq -s '[.[].status] | group_by(.) | map({status: .[0], total: length})'

# Last 7 days as JSON with metadata, pretty-printed, to a file
cam-sessions-history.py --days 7 --format json --pretty -o ~/Downloads/sessions.json

# Session rows as CSV for a spreadsheet
cam-sessions-history.py --format csv > sessions.csv

# Quick table preview of the last day
cam-sessions-history.py --days 1 --format table

# Override the organization for this run, with progress logging
cam-sessions-history.py --org 123456 --days 14 -v

# Absolute start timestamp (see API Details — not honored by the API)
cam-sessions-history.py --t0 2026-08-01T00:00:00Z
```

## Output Formats

### JSONL (default)

Session records only, one compact JSON object per line (no enclosing array, no metadata), for streaming/line-oriented tooling.

### JSON

Sessions wrapped with request metadata:

```json
{
  "organizationId": "627126248111354962",
  "fetchedAt": "2026-10-19T20:22:58Z",
  "window": { "days": 7, "t0": null, "t1": null },
  "count": 219,
  "sessions": [
    {
      "sessionId": "034fb765-eb67-4e25-aacb-53bf3547d689",
      "ts": "2026-05-23T00:34:28.563Z",
      "status": "Success",
      "user": { "id": "9c8ecd37da13" },
      "client": { "id": "9c:8e:cd:37:da:13", "url": "/Demo/n/1o3zXbYb/manage/clients" },
      "authentication": { "protocol": "MAB" },
      "ssid": { "name": "" },
      "classification": { "type": "IP Camera" },
      "details": { "ruleDetails": "Cameras" },
      "counts": { "events": { "total": 1 } },
      "device": { "url": "/Demo/n/1o3zXbYb/manage/nodes/new_list/115335421839104/summary" }
    }
  ]
}
```

`window.days` is `null` when `--t0` is used; `window.t0`/`window.t1` are `null` when `--days` is used.

### YAML

The same payload (metadata + sessions), dumped as a YAML document.

### CSV

Session rows only, with nested fields flattened to dotted columns:

```csv
ts,sessionId,status,user.id,client.id,authentication.protocol,ssid.name,classification.type,details.ruleDetails,counts.events.total,client.url,device.url
2026-10-19T20:20:52.648Z,d9dc618f-12bc-482f-aa84-d69d57e682a6,Success,f82793502d75,f8:27:93:50:2d:75,MAB,pseudoco-guest,Mobile,Guests,1,/Demo/n/MtIhebYb/manage/clients,/Demo/n/MtIhebYb/manage/nodes/new_list/114665195004320/summary
```

### Table

Markdown table with the same columns as CSV.

### Columns

`ts`, `sessionId`, `status`, `user.id`, `client.id`, `authentication.protocol`, `ssid.name`, `classification.type`, `details.ruleDetails`, `counts.events.total`, `client.url`, `device.url`

CSV, JSONL, and table formats carry session records only — use JSON or YAML when you need the `organizationId`/`fetchedAt`/`window`/`count` metadata.

## API Details

**Endpoint**: `GET /organizations/{organizationId}/nac/sessions/history`

**Authentication**: Uses `Authorization: Bearer` header

**Pagination**: Numeric row offset — the `Link` header advances `startingAfter` by rows (0, 1000, 2000, ...) rather than an opaque cursor, so the script drives the offset directly with `perPage=1000` (the Meraki max for this endpoint) and stops on a short or empty page. A 250-page safety limit prevents runaway pagination.

**Rate Limits**: Automatically handles 429 responses with `Retry-After` retry

**Lookback window**: Maximum 31 days (2678400 seconds); there is no "all time" option, so `--days` defaults to the API maximum.

**`t0`/`t1` caveat**: The endpoint does **not** honor `t0`/`t1` as a time bound. It ignores the range and applies its own short default window, returning only the most recent handful of sessions. Verified in CAMalytics (2026-08-19): a 2026-08-10→now window returned 10 sessions via `t0` vs. 185 via `timespan`. The flags are kept for parity with the API surface — use `--days` for reliable results.

**Mutually exclusive parameters**: `t0` and `timespan` cannot be combined (Meraki returns 400), so `--t0` replaces the `--days` timespan rather than narrowing it.

## Behavior

- **Organization**: By default, uses `MERAKI_ORG_ID`; use `--org` to override it for a single run
- **Days**: `--days` is converted to a `timespan` in seconds and capped at the API maximum (31 days) with a warning
- **Pages**: Defaults to `0` (all pages). If `--pages` is explicitly set to a nonzero value but `--days` or `--t0`/`--t1` is also given explicitly, the page limit is ignored (forced back to `0`) with a warning — a requested time window is always returned in full
- **Output**: stdout by default; `--output` writes the file and creates parent directories as needed
- **Logging**: Structured output with ISO 8601 timestamps to stderr, so stdout stays pipeable to `jq`

## Testing

- Use TDD for all operations
- Test file: `cam-sessions-history_test.py`
- Tests cover: window parameter building, offset pagination, short/empty-page termination, rate-limit retry, max-page safety limit, payload metadata, all five formatters, and CLI validation/output paths

```bash
./cam-sessions-history_test.py
```

## Related Scripts

- `cam-license-usage.py` - Daily peak concurrent sessions and rolling 90th percentile chart
- `cam-clients.py` - Export CAM NAC clients (org-wide `/nac/clients` endpoint)
- `cam-api-status.py` - Probe all `/nac/` endpoints, including `sessions/history`

## Version

Created: 2026-10-19 (moved from CAMalytics `scripts/nac_sessions_history.py`)
