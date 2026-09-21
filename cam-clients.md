# cam-clients.py

Search, create, update, and delete Cisco Access Manager (CAM) NAC clients.

## Purpose

Manages NAC clients through the organization-scoped Meraki Dashboard API
(`/organizations/{organizationId}/nac/clients`). With no CRUD flag given, the
default action is to search/export all clients (this preserves the prior
no-flag export behavior).

`--create` behaves differently depending on its input:

- **CSV file path** — uploads the whole file via the `bulkUpload` API using
  the **CAM template format** (`MAC address`, `Endpoint device group`,
  `Description`, `IPSK`), automatically batched at 1000 rows. Missing groups
  named in `Endpoint device group` are auto-created, and existing clients
  matched by MAC are upserted — both on by default (`--no-create-groups`,
  `--no-update-clients` to disable).
- **Inline JSON object** — creates a single client via one `POST` request
  using cam-clients.py's own field names (`mac`, `type`, `owner`,
  `description`, `ipsk`, `groups` by ID).

`--update` and `--delete` each accept a path to a CSV file or an inline JSON
object for one client, using cam-clients.py's own field names. `--update`
requires a client `id` — obtained via `--search` (e.g. `--filter mac=...`) or
captured from a prior `--create` response. There is no single-client delete
endpoint, so `--delete`/`--delete-id` use the `bulkDelete` API, batched in
chunks of up to 500 ids per request.

## Features

- **Search/list** NAC clients with `--filter key=value` filtering (dot notation for nested fields), pagination, and multiple output formats
- **Bulk CSV create** via the `bulkUpload` API (CAM template format), auto-creating missing groups and upserting by MAC, automatically batched at 1000 rows
- **Single-client create** via inline JSON, and **update**/**delete** in the same script
- **Parallel operations** for single-client create/update with configurable concurrency (`--workers`)
- **Batched bulk delete** (up to 500 ids per request) via the `bulkDelete` API
- **`--debug`**: shows the decoded CSV and payload flags for bulk CSV uploads
- **`summary` output format**: a human-readable success/failure/error breakdown for bulk CSV uploads (the default when `--format` isn't explicitly set); `json`/`yaml` show the raw API response instead
- **Pre-flight validation** of required fields before issuing requests
- **Rate limit handling**: retries once after a 429 `Retry-After` delay
- **Single-client delete** via `--delete-id` without a CSV file
- Operations may be combined in one invocation (e.g. `--search` and `--create` together)

## Requirements

- Python 3.11+
- `uv` package manager
- Environment variables:
  - `MERAKI_DASHBOARD_API_KEY` - Your Meraki Dashboard API key (required)
  - `MERAKI_ORG_ID` - Organization ID (required, or pass via `--org`)

## Usage

```bash
# Search/export clients (default action with no flags)
./cam-clients.py
./cam-clients.py --limit 100
./cam-clients.py --batch 500
./cam-clients.py --filter status=Connected
./cam-clients.py --format csv -f ssid=Guest -f source=Discovered
./cam-clients.py --filter classification.os=iOS
./cam-clients.py --format table -f owner=jsmith -v

# Bulk-create clients from a CAM template CSV via bulkUpload (auto-creates groups + upserts by MAC)
./cam-clients.py --create clients.csv
./cam-clients.py --create clients.csv --debug
./cam-clients.py --create clients.csv --no-create-groups --no-update-clients
./cam-clients.py --create clients.csv --format json

# Create a single client inline via JSON
./cam-clients.py --create '{"mac": "AA:BB:CC:DD:EE:FF", "description": "Printer"}'

# Bulk create with a custom batch size, verbose logging
./cam-clients.py -o 123456 --create clients.csv --batch 500 -v

# Update clients from CSV or inline JSON (requires 'id')
./cam-clients.py --update updates.csv
./cam-clients.py --update '{"id": "627126248111374692", "description": "Reassigned"}'

# Delete clients from CSV or inline JSON (requires 'id')
./cam-clients.py --delete deletes.csv
./cam-clients.py --delete '{"id": "627126248111374692"}'

# Delete a single client by ID
./cam-clients.py --delete-id 627126248111374692
```

## Command Line Options

| Option                | Description                                                        | Default |
| ---------------------- | -------------------------------------------------------------------- | ------- |
| `--org`, `-o`         | Organization ID (or set `MERAKI_ORG_ID` env var)                      | None    |
| `--search`, `-s`      | Search/list NAC clients (default action when no CRUD flag is given)   | False   |
| `--format`            | Output format for search (`json`, `jsonl`, `yaml`, `csv`, `table`, `summary`); for a CSV `--create`, defaults to `summary` unless set explicitly | json    |
| `--filter`, `-f`      | Filter by key=value (repeatable, dot notation, case-insensitive contains) | None    |
| `--batch`             | Batch size per API request, and per CSV chunk for bulk `--create` uploads | 1000    |
| `--limit`             | Maximum number of clients to fetch (0 = all)                           | 0       |
| `--create`            | CSV file path (CAM template: `MAC address`, `Endpoint device group`, `Description`, `IPSK`; uploaded via `bulkUpload`), or a JSON object for a single client (fields: `mac`, `type`, `owner`, `description`, `ipsk`, `groups`) | None    |
| `--create-groups` / `--no-create-groups` | Auto-create any missing groups named in a CSV `--create` upload | true |
| `--update-clients` / `--no-update-clients` | Upsert existing clients matched by MAC in a CSV `--create` upload | true |
| `--debug`             | Show decoded CSV and payload details for CSV `--create` uploads       | False   |
| `--update`            | CSV file path, or a JSON object for a single client, to update (fields: `id`, `type`, `owner`, `mac`, `description`, `ipsk`, `groupsAdd`, `groupsRemove`) | None    |
| `--delete`            | CSV file path, or a JSON object for a single client, to delete (field: `id`) | None    |
| `--delete-id`         | Delete a single client by ID                                          | None    |
| `--workers`, `-w`     | Number of concurrent workers for single-client create/update           | 1       |
| `--timeout`, `-t`     | HTTP timeout in seconds                                                | 30      |
| `--verbose`, `-v`     | Enable verbose logging                                                 | False   |

With no flags at all, the default action is `--search` (export all clients). Multiple actions may be combined in one invocation.

## Input Formats

`--create`, `--update`, and `--delete` each accept **either**:

- a path to a CSV file, or
- an inline JSON object for a single client (e.g. `'{"mac": "AA:BB:CC:DD:EE:FF"}'`) — detected by a leading `{`

### Create

A CSV file uses the **CAM template format** and is uploaded whole via `bulkUpload`:

```csv
MAC address,Endpoint device group,Description,IPSK
AA:BB:CC:DD:EE:FF,Printers,Front desk printer,
AA:BB:CC:DD:EE:02,Cameras;Guests,Lobby camera,
```

- Required: `MAC address`
- Optional: `Endpoint device group` (semicolon-separated group *names* — created automatically if missing), `Description`, `IPSK`
- Rows over 1000 are automatically split into multiple `bulkUpload` batches (`--batch` to change the chunk size)

An inline JSON object creates a single client via `POST /nac/clients` using cam-clients.py's own field names:

```json
{"mac": "AA:BB:CC:DD:EE:FF", "type": "corporate", "owner": "jsmith", "description": "Printer", "groups": "100;200"}
```

- Required: `mac`
- Optional: `type`, `owner`, `description`, `ipsk`, `groups` (semicolon-separated list of existing group *IDs* — not auto-created)

### Update

```csv
id,description,groupsAdd,groupsRemove
627126248111374692,Reassigned,300,100
```

```json
{"id": "627126248111374692", "description": "Reassigned"}
```

- Required: `id`
- Optional (only non-empty fields are sent): `type`, `owner`, `mac`, `description`, `ipsk`, `groupsAdd`, `groupsRemove` (both are semicolon-separated lists of group IDs)

### Delete

```csv
id
627126248111374692
```

```json
{"id": "627126248111374692"}
```

- Required: `id`

## Output Formats (search)

| Format  | Description                                                                        |
| ------- | ---------------------------------------------------------------------------------- |
| `json`  | Pretty-printed JSON array (default)                                                |
| `jsonl` | JSON Lines - one compact JSON object per client, streamable                       |
| `yaml`  | YAML document                                                                      |
| `csv`   | Flat CSV with headers; nested fields use dot notation (e.g. `lastLogin.timestamp`) |
| `table` | Markdown table (streamable, no column-width pre-calculation)                       |

### Search Columns

`id`, `mac`, `owner`, `type`, `status`, `ssid`, `source`, `ipAddress`, `description`, `lastLogin.timestamp`, `lastLogin.location`, `firstLogin.timestamp`, `firstLogin.location`, `classification.type`, `classification.manufacturer`, `classification.model`, `classification.os`

## API Details

**Endpoints**:

- `GET /organizations/{organizationId}/nac/clients` - Search/list clients
- `POST /organizations/{organizationId}/nac/clients` - Create a single client (inline JSON `--create`)
- `POST /organizations/{organizationId}/nac/clients/bulkUpload` - Bulk-create clients from a CSV file `--create` (base64-encoded CAM template CSV, auto-creates groups, upserts by MAC, batched at 1000 rows)
- `PUT /organizations/{organizationId}/nac/clients/{clientId}` - Update a client
- `POST /organizations/{organizationId}/nac/clients/bulkDelete` - Delete clients (batched, up to 500 ids per request)

**Authentication**: Uses `Authorization: Bearer` header

**Pagination** (search): Offset-based via `startingAfter` parameter, driven by `meta.totalCount`

**Rate Limits**: Retries once after the `Retry-After` header value on a 429 response

### Example Client Object

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

## Examples

### Find a client's ID before updating or deleting

```bash
./cam-clients.py --filter mac=AA:BB:CC:DD:EE:FF --format table
```

### Bulk create from a CAM template CSV (auto-creates groups, upserts by MAC)

```bash
./cam-clients.py --create new-clients.csv -v
```

### See the raw bulkUpload API response instead of the summary

```bash
./cam-clients.py --create new-clients.csv --format json
```

### Debug a bulk upload (show decoded CSV and payload flags)

```bash
./cam-clients.py --create new-clients.csv --debug
```

### Create clients without touching groups or existing clients

```bash
./cam-clients.py --create new-clients.csv --no-create-groups --no-update-clients
```

### Create a single client inline

```bash
./cam-clients.py --create '{"mac": "AA:BB:CC:DD:EE:FF", "description": "Printer"}'
```

### Reassign group membership

```bash
./cam-clients.py --update '{"id": "627126248111374692", "groupsAdd": "300", "groupsRemove": "100"}'
```

### Clean up a batch of test clients

```bash
./cam-clients.py --delete test-client-ids.csv
```

### Remove a single client

```bash
./cam-clients.py --delete-id 627126248111374692
```

## Testing

Run unit tests with:

```bash
./cam-clients_test.py
```

All tests use pytest with async support and mock the `httpx.AsyncClient`.

## Error Handling

- **Missing API Key**: Exits with error if `MERAKI_DASHBOARD_API_KEY` not set
- **Missing Organization ID**: Exits with error if `--org` not provided and `MERAKI_ORG_ID` not set
- **Input Validation**: Reports up to 10 missing-required-field errors (from CSV or JSON) before any requests are made
- **Malformed Input**: Invalid JSON or a missing CSV file path exits immediately with a `click.BadParameter` error
- **Per-row Failures**: Failed rows/chunks are logged and counted; the run continues and reports a non-zero exit code if any failures occurred
- **Rate Limits**: Retries once after the `Retry-After` header value

## Related Scripts

- `cam-clients-add.py` - The original single-purpose bulkUpload script this CSV `--create` path is based on (base64 CLI arg or `--file`, `--create-groups`/`--update-clients` flags default off)
- `cam-clients-groups.py` - Manage NAC client groups (search/create/update/delete via CSV or JSON)
- `cam-users.py` - Manage Meraki IAM/IdP dashboard users (same CSV/JSON CRUD pattern)

## Version

Created: 2026-06-10
Updated: 2026-09-21
