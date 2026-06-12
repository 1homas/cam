# CAM Clients Export

PEP 723 Python script that fetches NAC clients from Cisco Access Manager via the Meraki Dashboard API and exports them in multiple formats. Supports filtering by any attribute using repeatable `--filter key=value` options with dot notation for nested fields.

## ⚠️ Important: API Limitation

Due to a Meraki API pagination bug, **only the first 1,000 clients can be exported**. The API reports the total count via `meta.totalCount` but does not provide Link headers for pagination and returns 500 errors when using `startingAfter` manually.

**For deleting all clients** (not exporting), use `cam-clients-delete.py --loop` instead.

## Options

Must show these in the `--help` option along with example usage:

| Flag          | Type   | Default | Description                                                               |
| ------------- | ------ | ------- | ------------------------------------------------------------------------- |
| --format      | choice | `json`  | Output format: `json`, `yaml`, `csv`, or `table` (Markdown)               |
| --filter, -f  | string | (none)  | Filter by key=value (repeatable, dot notation, case-insensitive contains) |
| -v, --verbose | flag   | false   | Enable verbose logging                                                    |
| --help        | flag   |         | Show command options and examples                                         |

## Environment Variables

| Variable                   | Description              |
| -------------------------- | ------------------------ |
| `MERAKI_DASHBOARD_API_KEY` | Meraki Dashboard API key |
| `MERAKI_ORG_ID`            | Meraki organization ID   |

Loaded from `.env` via `python-dotenv`.

## Output Formats

| Format  | Description                                                                        |
| ------- | ---------------------------------------------------------------------------------- |
| `json`  | Pretty-printed JSON array (default)                                                |
| `yaml`  | YAML document                                                                      |
| `csv`   | Flat CSV with headers; nested fields use dot notation (e.g. `lastLogin.timestamp`) |
| `table` | Markdown table (streamable, no column-width pre-calculation)                       |

### Columns

`id`, `mac`, `owner`, `type`, `status`, `ssid`, `source`, `ipAddress`, `description`, `lastLogin.timestamp`, `lastLogin.location`, `firstLogin.timestamp`, `firstLogin.location`, `classification.type`, `classification.manufacturer`, `classification.model`, `classification.os`

## Behavior

- **Pagination**: Limited to first 1,000 clients due to Meraki API bug (no Link headers provided, `startingAfter` returns 500 errors)
- **Rate limiting**: Retries on HTTP 429 using `Retry-After` header
- **Filtering**: Applied client-side after fetching records; multiple filters combine with AND logic; matching is case-insensitive substring (contains)
- **Dot notation**: Nested fields are accessed via dot notation (e.g. `classification.os`, `lastLogin.location`)
- **Logging**: Structured output with ISO 8601 timestamps to stderr
- **Warning**: Script warns when `meta.totalCount` shows more than 1,000 clients exist

## API Endpoint

GET https://api.meraki.com/api/v1/organizations/$MERAKI_ORG_ID/nac/clients

```json
{
  "id": "627126248111374692",
  "type": "corporate",
  "owner": "milesmeraki",
  "mac": "22:33:44:55:66:77",
  "description": "Miles's phone",
  "status": "Disconnected",
  "ssid": "AM-Guest",
  "source": "Discovered",
  "ipAddress": "192.168.128.3",
  "nadName": "68-49-92-37-61-A0:vap2",
  "hasPrivateMac": true,
  "firstLogin": {
    "timestamp": "2026-01-15T08:00:00Z",
    "location": "Building A"
  },
  "lastLogin": {
    "timestamp": "2026-05-23T00:02:29Z",
    "location": "Demo"
  },
  "classification": {
    "type": "Mobile",
    "manufacturer": "Apple",
    "model": "iPhone 15",
    "os": "iOS"
  }
}
```

## Usage Examples

```bash
# Export all clients as JSON (default)
cam-clients.py

# Export as pretty table
cam-clients.py --format table

# Export connected clients as CSV
cam-clients.py --format csv --filter status=Connected

# Export guest SSID clients as YAML
cam-clients.py --format yaml --filter ssid=Guest

# Filter by nested field (classification.os)
cam-clients.py --filter classification.os=iOS

# Multiple filters (AND logic)
cam-clients.py --filter status=Connected --filter source=Provisioned

# Short flag form
cam-clients.py -f owner=jsmith -f ssid=Corp

# Export discovered clients with verbose logging
cam-clients.py --filter source=Discovered -v
```

## Testing

- Use TDD for all operations
- Test file: `cam-clients_test.py`
- Tests cover: all formatters, filter logic, pagination, and rate-limit retry
