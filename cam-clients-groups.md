# cam-clients-groups.py

Search, create, update, and delete Cisco Access Manager (CAM) NAC client groups.

## Purpose

Manages NAC client groups through the organization-scoped Meraki Dashboard API
(`/organizations/{organizationId}/nac/clients/groups`). With no CRUD flag
given, the default action is to search/export all groups (matches
`cam-clients.py`'s no-flag behavior). `--search`, `--create`, `--update`,
`--delete`, and `--delete-id` follow the same CSV/JSON CRUD pattern as
`cam-users.py` and `cam-clients.py`.

`--update` and `--delete` require a group `id` — obtained via `--search`
(e.g. `--search-query`) or captured from a prior `--create` response. There
is no bulk-delete endpoint for groups, so `--delete` issues one `DELETE`
request per group, in parallel via `--workers`.

## Features

- **Search/list** NAC client groups with fuzzy `--search-query`, sorting, pagination, and multiple output formats
- **Create, update, delete** in a single script
- **CSV or single-group JSON** input for `--create`, `--update`, `--delete` — each accepts either a path to a CSV file or an inline JSON object for one group
- **Parallel operations** with configurable concurrency (`--workers`)
- **Automatic batching** (100 rows per chunk) with progress logging
- **Pre-flight validation** of required fields before issuing requests
- **Rate limit handling**: retries once after a 429 `Retry-After` delay
- **Single-group delete** via `--delete-id` without a CSV file
- **Member management**: `members` on create, `membersAdd`/`membersRemove` on update (semicolon-separated client IDs)
- Operations may be combined in one invocation (e.g. `--search` and `--create` together)

## Requirements

- Python 3.11+
- `uv` package manager
- Environment variables:
  - `MERAKI_DASHBOARD_API_KEY` - Your Meraki Dashboard API key (required)
  - `MERAKI_ORG_ID` - Organization ID (required, or pass via `--org`)

## Usage

```bash
# Search/list groups (default action with no flags)
./cam-clients-groups.py
./cam-clients-groups.py --search
./cam-clients-groups.py --search --search-query Camera --format table
./cam-clients-groups.py --search --sort-key name --sort-order DESC --format csv > groups.csv

# Create groups from CSV or inline JSON
./cam-clients-groups.py --create groups.csv
./cam-clients-groups.py --create '{"name": "Cameras", "description": "Security cameras"}'

# Create with more workers, verbose logging
./cam-clients-groups.py -o 123456 --create groups.csv --workers 20 -v

# Update groups from CSV or inline JSON (requires 'id')
./cam-clients-groups.py --update updates.csv
./cam-clients-groups.py --update '{"id": "627126248111341608", "description": "Updated"}'
./cam-clients-groups.py --update '{"id": "627126248111341608", "membersAdd": "1;2;3"}'

# Delete groups from CSV or inline JSON (requires 'id')
./cam-clients-groups.py --delete deletes.csv -w 20
./cam-clients-groups.py --delete '{"id": "627126248111341608"}'

# Delete a single group by ID
./cam-clients-groups.py --delete-id 627126248111341608
```

## Command Line Options

| Option                | Description                                                        | Default |
| ---------------------- | -------------------------------------------------------------------- | ------- |
| `--org`, `-o`         | Organization ID (or set `MERAKI_ORG_ID` env var)                      | None    |
| `--search`, `-s`      | Search/list NAC client groups (default action when no CRUD flag is given) | False   |
| `--search-query`      | Fuzzy filter by group name                                             | None    |
| `--group-id`          | Filter by group ID (repeatable)                                       | None    |
| `--sort-key`          | Sort field: `name`                                                     | name    |
| `--sort-order`        | Sort order: `ASC`, `DESC`                                              | ASC     |
| `--per-page`          | Results per page, 3-3000                                               | 100     |
| `--limit`             | Maximum number of groups to fetch (search only, 0 = all)               | 0       |
| `--format`            | Search output format: `json`, `jsonl`, `yaml`, `csv`, `table`          | json    |
| `--create`            | CSV file path, or a JSON object for a single group, to create (fields: `name`, `description`, `members`) | None    |
| `--update`            | CSV file path, or a JSON object for a single group, to update (fields: `id`, `name`, `description`, `membersAdd`, `membersRemove`) | None    |
| `--delete`            | CSV file path, or a JSON object for a single group, to delete (field: `id`) | None    |
| `--delete-id`         | Delete a single group by ID                                           | None    |
| `--workers`, `-w`     | Number of concurrent workers                                          | 1       |
| `--timeout`, `-t`     | HTTP timeout in seconds                                                | 30      |
| `--verbose`, `-v`     | Enable verbose logging                                                 | False   |

With no flags at all, the default action is `--search` (export all groups). Multiple actions may be combined in one invocation.

## Input Formats

`--create`, `--update`, and `--delete` each accept **either**:

- a path to a CSV file (multiple groups, batched and processed in parallel), or
- an inline JSON object for a single group (e.g. `'{"name": "Cameras"}'`) — detected by a leading `{`

### Create

```csv
name,description,members
Cameras,Security cameras,1;2;3
```

```json
{"name": "Cameras", "description": "Security cameras", "members": "1;2;3"}
```

- Required: `name`
- Optional: `description`, `members` (semicolon-separated list of client IDs)

### Update

```csv
id,description,membersAdd,membersRemove
627126248111341608,Updated description,4;5,1
```

```json
{"id": "627126248111341608", "description": "Updated description"}
```

- Required: `id`
- Optional (only non-empty fields are sent): `name`, `description`, `membersAdd`, `membersRemove` (both are semicolon-separated lists of client IDs)

### Delete

```csv
id
627126248111341608
```

```json
{"id": "627126248111341608"}
```

- Required: `id`

## Output Formats (search)

| Format  | Description                          |
| ------- | ------------------------------------- |
| `json`  | Pretty-printed JSON array (default)   |
| `jsonl` | JSON Lines - one compact JSON object per group, streamable |
| `yaml`  | YAML document                         |
| `csv`   | Flat CSV with headers                 |
| `table` | Markdown table                        |

### Search Columns

`id`, `name`, `description`, `membersCount`

## API Details

**Endpoints**:

- `GET /organizations/{organizationId}/nac/clients/groups` - Search/list groups
- `POST /organizations/{organizationId}/nac/clients/groups` - Create a group
- `PUT /organizations/{organizationId}/nac/clients/groups/{groupId}` - Update a group
- `DELETE /organizations/{organizationId}/nac/clients/groups/{groupId}` - Delete a group

**Authentication**: Uses `Authorization: Bearer` header

**Pagination** (search): Link header with `startingAfter` parameter

**Rate Limits**: Retries once after the `Retry-After` header value on a 429 response

### Example Group Object

```json
{
  "id": "627126248111341608",
  "name": "Cameras",
  "description": "Security cameras",
  "members": {
    "totalCount": 3,
    "items": [
      {"value": "627126248111374692", "display": "22:33:44:55:66:77"}
    ]
  }
}
```

## Examples

### Find a group's ID before updating or deleting

```bash
./cam-clients-groups.py --search --search-query Camera --format table
```

### Bulk create from CSV

```bash
./cam-clients-groups.py --create new-groups.csv --workers 20 -v
```

### Create a single group inline

```bash
./cam-clients-groups.py --create '{"name": "Cameras", "description": "Security cameras"}'
```

### Add/remove members from a group

```bash
./cam-clients-groups.py --update '{"id": "627126248111341608", "membersAdd": "627126248111374692"}'
```

### Clean up a batch of test groups

```bash
./cam-clients-groups.py --delete test-group-ids.csv
```

### Remove a single group

```bash
./cam-clients-groups.py --delete-id 627126248111341608
```

## Testing

Run unit tests with:

```bash
./cam-clients-groups_test.py
```

All tests use pytest with async support and mock the `httpx.AsyncClient`.

## Error Handling

- **Missing API Key**: Exits with error if `MERAKI_DASHBOARD_API_KEY` not set
- **Missing Organization ID**: Exits with error if `--org` not provided and `MERAKI_ORG_ID` not set
- **Input Validation**: Reports up to 10 missing-required-field errors (from CSV or JSON) before any requests are made
- **Malformed Input**: Invalid JSON or a missing CSV file path exits immediately with a `click.BadParameter` error
- **Per-row Failures**: Failed rows are logged and counted; the run continues and reports a non-zero exit code if any failures occurred
- **Rate Limits**: Retries once after the `Retry-After` header value

## Related Scripts

- `cam-clients.py` - Manage NAC clients (search/create/update/delete via CSV or JSON)
- `cam-users.py` - Manage Meraki IAM/IdP dashboard users (same CSV/JSON CRUD pattern)

## Version

Created: 2026-09-21
Updated: 2026-09-21
