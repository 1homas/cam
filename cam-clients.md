# cam-clients.py

## Purpose

List all NAC clients from Cisco Access Manager (CAM) via the Meraki Dashboard API.

## Features

- Fetch all NAC clients from the organization
- Handle pagination automatically (with Link header support)
- Output in multiple formats: JSON (default), YAML, CSV, table
- Verbose logging option
- Rate limit handling with automatic retry

## Usage

```sh
cam-clients.py                    # List all clients as JSON
cam-clients.py --help             # Show command options
cam-clients.py --format table     # Pretty-printed ASCII table
cam-clients.py --format csv       # CSV format
cam-clients.py --format yaml      # YAML format
cam-clients.py -v                 # Verbose logging
```

## Output Formats

### JSON (default)

Standard JSON array with full client details.

### YAML

Human-readable YAML format with full client details.

### CSV

Comma-separated values with columns: id, mac, status, ssid, type

### Table

Markdown-formatted table for terminal display with columns: id, mac, status, ssid, type

## Environment Variables

Required variables in `.env`:

- `MERAKI_DASHBOARD_API_KEY` - Your Meraki Dashboard API key
- `MERAKI_ORG_ID` - Your Meraki organization ID

## API Details

- **Endpoint**: `GET /organizations/{organizationId}/nac/clients`
- **Pagination**: Automatic via Link headers (1000 per page)
- **Rate Limiting**: Automatic retry with Retry-After header

## Known Limitations

The Meraki API may have pagination issues where `meta.totalCount` is provided but Link headers are not consistently returned. The script fetches as many clients as possible given the API's pagination support.

## Exit Codes

- `0` - Success
- `1` - Missing required environment variables

## Logging

When verbose mode is enabled (`-v`):

- Logs each page fetch with item counts
- Warns if API reports more total clients than returned
- Logs rate limit delays
- Reports total fetch time

Default log file: `cam-clients.log` (ISO 8601 timestamps)
