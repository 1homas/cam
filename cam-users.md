# cam-users.py

Search, create, update, and delete Meraki IAM (IdP) users.

## Purpose

Manages Meraki dashboard/IdP end users through the organization-scoped IAM/IdP
users API (`/organizations/{organizationId}/iam/users/idps/users` and
`.../iam/users/search`). This is a different resource than the network-scoped
Meraki Auth Users (`merakiAuthUsers`, fields `email`/`name`/`accountType`/
`authorizations` for 802.1X/Guest/Client VPN) which this script previously
covered in read-only form.

There is **no GET/list endpoint** on `idps/users` itself. Use `--search` (which
wraps `POST /organizations/{organizationId}/iam/users/search`) to find a
user's `id`, or capture it from a prior create response, before running
`--update`/`--delete`.

`--search` has no default IdP filter — a plain `--search` returns users across
all IdPs. Pass `--idp-name`/`--idp-id` to narrow it to one.

Use `--groups` (wraps `POST /organizations/{organizationId}/iam/users/idps/groups/search`)
to look up available IdP group IDs/names before passing them to `--group-id`
(search filter) or `groupIds` (`--create` field). It's filterable with
`--query` (group name), `--idp-id`, and `--idp-name`.

`--create` and `--delete` have no IdP field in their request bodies at all —
the API doesn't accept one. Created users always land in, and deleted users
are always removed from, the native `802.1X` local users store (CAM's
built-in local authentication), regardless of any `--idp-name`/`--idp-id`
passed to `--search`.

## Features

- **Search/list** IdP users with filters, sorting, pagination, and multiple output formats
- **Search/list IdP groups** (`--groups`) to discover group IDs for `--group-id`/`groupIds`
- **Create, update, delete** in a single script
- **CSV or single-user JSON** input for `--create`, `--update`, `--delete` — each accepts either a path to a CSV file or an inline JSON object for one user. `--delete` additionally accepts a bare user ID, with no CSV/JSON needed
- **Parallel operations** with configurable concurrency (`--workers`)
- **Automatic batching** (100 rows per chunk) with progress logging
- **Pre-flight validation** of required fields before issuing requests
- **Rate limit handling**: retries once after a 429 `Retry-After` delay
- Operations may be combined in one invocation (e.g. `--create` and `--delete` together)

## Requirements

- Python 3.11+
- `uv` package manager
- Environment variables:
  - `MERAKI_DASHBOARD_API_KEY` - Your Meraki Dashboard API key (required)
  - `MERAKI_ORG_ID` - Organization ID (required, or pass via `--org`)

## Usage

```bash
# Search/list users
./cam-users.py --search
./cam-users.py --search --query miles --format table
./cam-users.py --search --access-type Wireless --status Enabled --limit 50

# Search/list IdP groups (to find group IDs for --group-id / groupIds)
./cam-users.py --groups --format table
./cam-users.py --groups --query finance --idp-name "Azure AD IdP" --format csv

# Create users from CSV or inline JSON
./cam-users.py --create users.csv
./cam-users.py --create '{"email": "mdavis@meraki.com", "displayName": "Miles Davis"}'

# Create with more workers, verbose logging
./cam-users.py -o 123456 --create users.csv --workers 20 -v

# Update users from CSV or inline JSON (requires 'id')
./cam-users.py --update updates.csv
./cam-users.py --update '{"id": "1284392014819", "displayName": "Miles Davis"}'

# Delete users from CSV, inline JSON (requires 'id'), or a bare user ID
./cam-users.py --delete deletes.csv -w 20
./cam-users.py --delete '{"id": "1284392014819"}'
./cam-users.py --delete 1284392014819
```

## Command Line Options

| Option                | Description                                                        | Default |
| --------------------- | -------------------------------------------------------------------- | ------- |
| `--org`, `-o`         | Organization ID (or set `MERAKI_ORG_ID` env var)                      | None    |
| `--search`, `-s`      | Search/list IAM/IdP users                                              | False   |
| `--groups`, `-g`      | Search/list IdP groups (filterable with `--query`, `--idp-id`, `--idp-name`) | False   |
| `--query`, `-q`       | Fuzzy filter by display name, username, and email (`--search`) or group name (`--groups`) | None    |
| `--access-type`, `-a` | Filter by access type (repeatable)                                     | None    |
| `--group-id`, `-G`    | Filter by IdP group ID (repeatable)                                    | None    |
| `--idp-id`, `-i`      | Filter by identity provider ID (repeatable)                            | None    |
| `--idp-name`, `-I`    | Filter by identity provider name (repeatable, resolved to ID via `idps/search`) | None    |
| `--status`, `-S`      | Filter by user status: `Disabled`, `Enabled`, `Mixed` (repeatable)     | None    |
| `--user-id`, `-u`     | Filter by end user ID (repeatable)                                     | None    |
| `--sort-key`, `-k`    | Sort field: `created_at`, `updated_at`, `username`                     | username |
| `--sort-order`, `-r`  | Sort order: `asc`, `desc`                                              | asc     |
| `--per-page`, `-p`    | Results per page, 3-50                                                 | 10      |
| `--limit`, `-l`       | Maximum number of users to fetch (search only, 0 = all)                | 0       |
| `--format`, `-f`      | Search output format: `json`, `jsonl`, `yaml`, `csv`, `table`          | json    |
| `--create`, `-c`      | CSV file path, or a JSON object for a single user, to create. API requires: `email`, `upn`, `displayName`, `password` (`accountType` defaults to `802.1X`). Optional: `sendPassword`, `groupIds` | None    |
| `--template`, `-t`    | Print a JSON template for `--create` and exit                         | False   |
| `--update`, `-U`      | CSV file path, or a JSON object for a single user, to update (fields: `id`, `displayName`, `email`, `password`, `sendPassword`). If set, `password` must meet the policy below | None    |
| `--delete`, `-d`      | CSV file path, a JSON object (field: `id`), or a bare user ID, to delete | None    |
| `--workers`, `-w`     | Number of concurrent workers                                          | 1       |
| `--timeout`           | HTTP timeout in seconds                                                | 30      |
| `--verbose`, `-v`     | Enable verbose logging                                                 | False   |

At least one of `--search`, `--groups`, `--create`, `--update`, or `--delete` is required. Multiple may be combined in one invocation.

### Groups output columns

`--groups` output includes: `groupId`, `name`, `memberships`, `idp.name`, `idp.type`, `externalId`. Use the `groupId` value for `--group-id` (search filter) or `groupIds` (`--create` field).

## Input Formats

`--create`, `--update`, and `--delete` each accept **either**:

- a path to a CSV file (multiple users, batched and processed in parallel), or
- an inline JSON object for a single user (e.g. `'{"email": "a@b.com"}'`) — detected by a leading `{`

### Create

```csv
email,displayName,password,sendPassword
mdavis@meraki.com,Miles Davis,AL0ngEnoughPassword,false
```

```json
{"email": "mdavis@meraki.com", "displayName": "Miles Davis", "password": "AL0ngEnoughPassword", "sendPassword": false}
```

- **Script validates only**: `email`
- **Live API actually requires**: `email`, `upn`, `displayName`, `password`, `accountType` (this script defaults `accountType` to `802.1X` if omitted, so it's effectively optional)
  - `password` must be 12+ characters, contain no common words, and include at least 3 of: uppercase, lowercase, number, symbol
  - A request with only `email`/`displayName` passes this script's pre-flight validation but is rejected by the API with a 400 (`"UPN field cannot be empty"`, then `"Must provide a password"`, then `"Display name cannot be empty"` if also missing, surfaced one at a time)
- Optional: `sendPassword`, `groupIds` (CSV boolean: `true`/`false`/`1`/`0`/`yes`/`no`; JSON: native boolean)

### Update

```csv
id,displayName,email,password,sendPassword
1284392014819,Miles Davis,mdavis@meraki.com,,true
```

```json
{"id": "1284392014819", "displayName": "Miles Davis"}
```

- Required: `id`
- Optional (only non-empty fields are sent): `displayName`, `email`, `password`, `sendPassword`
  - If set, `password` must meet the same policy as `--create`: 12+ characters, no common words, at least 3 of uppercase/lowercase/number/symbol

### Delete

`--delete` accepts a CSV file, an inline JSON object, **or** a bare user ID
(no CSV/JSON needed for a single delete) — detected in that order: a value
starting with `{` is parsed as JSON, an existing file path is parsed as CSV,
anything else is treated as a literal user ID.

```csv
id
1284392014819
```

```json
{"id": "1284392014819"}
```

```text
1284392014819
```

- Required: `id` (for CSV/JSON forms; the bare-ID form needs nothing else)

## API Details

**Endpoints**:

- `POST /organizations/{organizationId}/iam/users/search` - Search/list users
- `POST /organizations/{organizationId}/iam/users/idps/groups/search` - Search/list IdP groups (`--groups`)
- `POST /organizations/{organizationId}/iam/users/idps/search` - List identity providers (used to resolve `--idp-name` to an ID; no name filter exists on the users-search or groups-search APIs themselves)
- `POST /organizations/{organizationId}/iam/users/idps/users` - Create a user
- `PUT /organizations/{organizationId}/iam/users/idps/users/{id}` - Update a user
- `DELETE /organizations/{organizationId}/iam/users/idps/users/{id}` - Delete a user

**Authentication**: Uses `X-Cisco-Meraki-API-Key` header

**Pagination** (search): Link header with `startingAfter` parameter

**Rate Limits**: Retries once after the `Retry-After` delay on a 429 response

**Release Stage**: This API is `beta` per the OpenAPI spec and may change

## Examples

### Find a user's ID before updating or deleting

```bash
./cam-users.py --search --query miles --format table
```

### Filter by identity provider name

`--search` with no filters returns users across all IdPs:

```bash
./cam-users.py --search --format table
```

Scope to a specific IdP, e.g. CAM's built-in local users store:

```bash
./cam-users.py --search --idp-name "802.1X" --format table
./cam-users.py --search --idp-name "Azure AD IdP" --format table
```

### Bulk create from CSV

```bash
./cam-users.py --create new-users.csv --workers 20 -v
```

### Create a single user inline

```bash
./cam-users.py --create '{"email": "mdavis@meraki.com", "displayName": "Miles Davis"}'
```

### Update display names after an import

```bash
./cam-users.py --update display-name-fixes.csv
```

### Clean up a batch of test users

```bash
./cam-users.py --delete test-user-ids.csv
```

### Remove a single user

```bash
./cam-users.py --delete 1284392014819
```

## Testing

Run unit tests with:

```bash
./cam-users_test.py
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

- `cam-clients-groups.py` - Manage NAC client groups (create/delete via CSV)

## Version

Created: 2026-09-21
Updated: 2026-09-21
