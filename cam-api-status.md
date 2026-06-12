# cam-api-status.py

## Purpose

Test all available Cisco Access Manager (CAM) `/nac/` REST API endpoints and save responses to disk. Automatically discovers and tests parameterized endpoints based on returned data.

## Requirements

- Python 3.x with `uv` package manager
- PEP 723 inline script metadata for dependency management
- Environment variables: `MERAKI_ORG_ID` and `MERAKI_DASHBOARD_API_KEY`

## Environment Variables

- Source environment variables from the environment first, then fall back to a local `.env` file
- `MERAKI_ORG_ID` - Organization ID used in the REST URL path
- `MERAKI_DASHBOARD_API_KEY` - API key for `Authorization: Bearer` token

## Script Style

- Use shebang: `#!/usr/bin/env -S uv run`
- PEP 723 inline metadata for dependencies (requests)
- 4-space indentation
- 120 character line width
- Functional programming patterns where possible
- Google-style docstrings for all functions

## REST API Endpoints

The API base URL is `https://api.meraki.com/api/v1/organizations/${MERAKI_ORG_ID}`

This script tests all available GET endpoints for read-only status checking. For each endpoint that returns collections with IDs, the script automatically fetches the related detail endpoints.

### Static Endpoints (tested directly)

- **Authorization Policies**
  - `GET /nac/authorization/policies`

- **Certificates**
  - `GET /nac/certificates`
  - `GET /nac/certificates/overview`
  - `GET /nac/certificates/authorities/crls`
  - `GET /nac/certificates/authorities/crls/descriptors`

- **Clients**
  - `GET /nac/clients` (paginated - fetches all pages using `meta.totalCount`)
  - `GET /nac/clients/groups`
  - `GET /nac/clients/overview`

- **Dictionaries**
  - `GET /nac/dictionaries`

- **Sessions**
  - `GET /nac/sessions/history`

- **Licensing**
  - `GET /nac/license/usage?startDate=YYYY-MM-DD`

### Dynamic Endpoints (tested based on returned data)

- **Dictionary Attributes** (for each dictionary ID from `/nac/dictionaries`)
  - `GET /nac/dictionaries/{dictionaryId}/attributes`
  - `GET /nac/dictionaries/{dictionaryId}/attributes/{attributeName}/values`

- **Session Details** (for each session ID from `/nac/sessions/history`)
  - `GET /nac/sessions/{sessionId}/details`

### Pagination

The `/nac/clients` endpoint is paginated. The script:

1. Fetches page 1 and reads `meta.totalCount`
2. Continues fetching subsequent pages until all items are retrieved
3. Saves each page response separately

## Implementation Details

### Endpoint Discovery

The script uses a two-phase approach:

1. **Static Phase**: Tests all known GET endpoints from `ENDPOINTS` list
2. **Dynamic Phase**: Based on responses, automatically tests parameterized endpoints:
   - For each dictionary ID → fetch attributes → fetch values for each attribute
   - For each session ID → fetch session details
   - For clients → handle pagination using `meta.totalCount`

### Field Mapping

- Dictionaries use `dictionaryId` (not `id`)
- Attributes use `attributeId` (extract name from URN: `urn:category:name` → `name`)
- Sessions use `id` or `sessionId`

### Error Handling

- HTTP 404 responses on attribute value endpoints are **expected** - not all attributes support enumeration (e.g., free-text fields)
- Request failures are logged to stderr and recorded with status code 0
- Non-JSON responses are logged to stderr
- Timeouts default to 30 seconds per request

### Pagination

The `/nac/clients` endpoint is paginated:

- Page 1 returns `meta.totalCount` with total number of items
- Script continues fetching pages until `len(items) >= totalCount`
- Each page response is saved separately

## Output

### Directory Structure

Creates `api_test/` directory containing:

- One JSON file per endpoint response
- Filenames derived from endpoint paths with sanitized characters:
  - `/` → `_`
  - `?` → `_`
  - `&` → `_`
  - `=` → `_`

### Console Output

Prints status summary with format: `icon status_code method url`

- ✅ for HTTP 2xx responses (success)
- ❌ for HTTP 4xx/5xx responses (client/server errors)

**Example:**

```
✅ 200 GET https://api.meraki.com/api/v1/organizations/12345/nac/clients
✅ 200 GET https://api.meraki.com/api/v1/organizations/12345/nac/dictionaries
❌ 404 GET https://api.meraki.com/api/v1/organizations/12345/nac/dictionaries/urn:identity/attributes/cn/values
```

### Expected Results

- **11 static endpoints**: All should return HTTP 200
- **Dictionary attributes**: 9 dictionaries × attributes = ~20-30 successful requests
- **Dictionary attribute values**: Mixed results - some return 200, many return 404 (expected)
- **Session details**: Variable based on session history (0-N requests)
- **Client pagination**: 1+ requests depending on total client count

Total API calls: **30-100+** depending on data volume

## Usage

### Basic Execution

```sh
# Test all endpoints and save responses
./cam-api-status.py

# Or with uv explicitly
uv run cam-api-status.py
```

### Environment Setup

Create a `.env` file in the same directory:

```sh
MERAKI_ORG_ID=1076202
MERAKI_DASHBOARD_API_KEY=your_api_key_here
```

Or export environment variables:

```sh
export MERAKI_ORG_ID=1076202
export MERAKI_DASHBOARD_API_KEY=your_api_key_here
./cam-api-status.py
```

### Output Files

After execution, check the `api_test/` directory:

```sh
# List all saved responses
ls -lh api_test/

# View a specific response
cat api_test/nac_clients.json | jq '.'

# Count total API calls made
ls -1 api_test/*.json | wc -l

# Find all dictionary attribute files
ls api_test/*_attributes.json

# Find all attribute value files
ls api_test/*_values.json
```

### Troubleshooting

**Missing dependencies:**

```sh
# uv will automatically install dependencies on first run
# To manually install:
uv pip install requests
```

**Missing environment variables:**

```
Missing required environment variable: MERAKI_ORG_ID
```

Solution: Create `.env` file or export variables as shown above.

**Connection errors:**
Check network connectivity and API key validity:

```sh
curl -H "Authorization: Bearer $MERAKI_DASHBOARD_API_KEY" \
  https://api.meraki.com/api/v1/organizations/$MERAKI_ORG_ID/nac/clients
```

## Notes

- The script is **read-only** - it only performs GET requests
- API responses are overwritten on each run
- Some 404 responses are expected for attribute values (design limitation, not a bug)
- Large client lists (>1000) may take 30+ seconds due to pagination
- The script tests ALL available endpoints from the OpenAPI specification
