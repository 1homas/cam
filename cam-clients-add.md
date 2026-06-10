# cam-clients-add.py

## Purpose

Bulk add NAC clients to Cisco Access Manager (CAM) via the Meraki Dashboard API bulkUpload endpoint.

## Features

- Upload clients from base64-encoded CSV string or CSV file
- Automatic batching for large CSV files (>1000 rows, default 1000 per batch)
- Validate CSV format (requires 'MAC address' column)
- Support for updating existing clients
- Support for creating new client groups
- Multiple output formats: JSON, YAML, summary
- Verbose logging option
- Debug mode to show payload details
- Rate limit handling with automatic retry
- Input validation with helpful error messages
- Detailed error reporting from API

## Usage

```sh
# Upload from CSV file
cam-clients-add.py --file clients.csv

# Upload large file (auto-batches at 1000 rows)
cam-clients-add.py --file large-clients.csv -v

# Upload with custom batch size
cam-clients-add.py --file clients.csv --batch-size 500

# Upload from base64-encoded string
cam-clients-add.py "base64_encoded_csv_string"

# Upload with custom options
cam-clients-add.py --file clients.csv --create-groups --format json

# Skip updating existing clients
cam-clients-add.py --file clients.csv --no-update-clients

# Debug mode (shows CSV content and payload)
cam-clients-add.py --file clients.csv --debug

# Verbose logging
cam-clients-add.py --file clients.csv -v

# Show help
cam-clients-add.py --help
```

## CSV Format

Uses the official Cisco Access Manager (CAM) CSV template format.

### Required Column

- `MAC address` - MAC address of the client (format: `00:0a:9a:6d:00`)

### Optional Columns

- `Endpoint device group` - Client group assignment(s). Use semicolons for multiple groups (e.g., `Guest;Android`)
- `Description` - Client description

### Template

The official CAM template is located at: `references/Client template.csv`

### Example CSV

```csv
MAC address,Endpoint device group,Description
00:0a:95:9d:68:16,Android,Android device
10:0c:95:9d:68:4e,Guest,New device
10:0a:95:9d:68:50,Guest;Android,Multi-group device
01:0a:95:9d:68:16,Meraki,Internal device
```

### Multiple Groups

To assign a client to multiple groups, separate group names with semicolons in the `Endpoint device group` column:

```csv
MAC address,Endpoint device group,Description
11:0a:95:9d:68:16,Cisco;Meraki,Device in multiple groups
```

## Options

- `BASE64_CSV` (positional) - Base64-encoded CSV string
- `--file PATH` - Path to CSV file to upload (will be base64 encoded)
- `--batch-size INT` - Split large CSVs into batches (default: auto-enable at 1000 for large files)
- `--update-clients` - Update existing clients with new data (default: true)
- `--no-update-clients` - Do not update existing clients
- `--create-groups` - Create new client groups if specified in CSV (default: false)
- `--format [json|yaml|summary]` - Output format (default: summary)
- `-v, --verbose` - Enable verbose logging
- `--debug` - Show debug details (CSV content, payload)

## Output Formats

### Summary (default)

Human-readable summary with counts and error details:

```
Upload Summary:
  Total:   3
  Success: 2
  Failed:  1

Details:
  ERROR: 1
    - Duplicated MAC address (rows: [3])
```

### JSON

Full API response in JSON format with all details.

### YAML

Full API response in YAML format with all details.

## Environment Variables

Required variables in `.env`:

- `MERAKI_DASHBOARD_API_KEY` - Your Meraki Dashboard API key
- `MERAKI_ORG_ID` - Your Meraki organization ID

## API Details

- **Endpoint**: `POST /organizations/{organizationId}/nac/clients/bulkUpload`
- **Request Body**: JSON with base64-encoded CSV content
- **Rate Limiting**: Automatic retry with Retry-After header
- **Timeout**: 60 seconds

## Validation

The script validates:

1. CSV must have at least a header row and one data row
2. CSV must contain a `MAC address` column (CAM template format)
3. Base64 input must be valid and decodable
4. File path must exist (when using `--file` option)
5. Required environment variables must be set

Note: The validation is case-insensitive for column headers.

## Response Structure

The API returns:

- `contents` - Echo of the uploaded base64 CSV
- `items` - Array of errors/info with counts and details
- `meta.counts` - Total, success, and failure counts

## Exit Codes

- `0` - Success
- `1` - Error (missing env vars, invalid input, API error)

## Logging

When verbose mode is enabled (`-v`):

- Logs CSV file loading
- Logs client count
- Logs upload progress
- Logs rate limit delays
- Reports upload time and success/failure counts

Default log file: `cam-clients-add.log` (ISO 8601 timestamps)

## Error Handling

Common errors reported by the API:

- Duplicated MAC address
- Invalid MAC address format
- Missing required fields
- Client group not found (if referencing non-existent group)

## Examples

### Basic Upload

```sh
# Create a CSV file using CAM template format
cat > clients.csv << 'EOF'
MAC address,Endpoint device group,Description
00:11:22:33:44:55,Guest,Test Device 1
00:11:22:33:44:56,Android,Test Device 2
EOF

# Upload the clients
cam-clients-add.py --file clients.csv
```

### Upload with Groups

```sh
# CSV with group assignments (use semicolons for multiple groups)
cat > clients-with-groups.csv << 'EOF'
MAC address,Endpoint device group,Description
00:11:22:33:44:55,Engineering,Engineering Device
00:11:22:33:44:56,Sales,Sales Device
10:0a:95:9d:68:50,Guest;Android,Multi-group device
EOF

# Upload and create groups
cam-clients-add.py --file clients-with-groups.csv --create-groups
```

### Using the Official Template

```sh
# Copy the official template
cp references/Client\ template.csv my-clients.csv

# Edit with your clients
# ... edit my-clients.csv ...

# Upload
cam-clients-add.py --file my-clients.csv --create-groups
```

### Base64 String Upload

```sh
# Encode CSV to base64 using cat
CSV_DATA=$(cat clients.csv | base64)
cam-clients-add.py "$CSV_DATA"

# Encode CSV to base64 using input redirection
CSV_DATA=$(base64 < clients.csv)
cam-clients-add.py "$CSV_DATA"

# One-liner with input redirection
cam-clients-add.py "$(base64 < clients.csv)"

# One-liner with pipe
cam-clients-add.py "$(cat clients.csv | base64)"
```

### Large File Upload with Batching

```sh
# Upload 20,000 clients (auto-batches into 20 batches of 1000)
cam-clients-add.py --file large-clients.csv -v

# Example output:
# 2026-06-10T13:57:38 INFO Auto-enabling batching (file has 20000 rows)
# 2026-06-10T13:57:38 INFO Splitting 20000 clients into 20 batches of 1000
# 2026-06-10T13:57:38 INFO Uploading batch 1/20 (1000 clients)
# 2026-06-10T13:57:40 INFO Batch 1 complete: 1000 succeeded, 0 failed
# ...
# 2026-06-10T13:58:40 INFO Upload completed in 62.6s: 20000 succeeded, 0 failed
```

## Batching

For large CSV files (>1000 rows), the script automatically splits the upload into batches to avoid API size limits and timeouts.

### Automatic Batching

- **Threshold**: Files with more than 1000 rows automatically enable batching
- **Default Batch Size**: 1000 clients per batch
- **Process**: Each batch is uploaded sequentially with progress logging

### Manual Batch Size

You can override the batch size with `--batch-size`:

```sh
# Use 500 clients per batch
cam-clients-add.py --file large-clients.csv --batch-size 500

# Use 2000 clients per batch (if API supports it)
cam-clients-add.py --file large-clients.csv --batch-size 2000
```

### Batch Results

When batching is used:

- Progress is logged for each batch
- Summary shows total counts across all batches
- Individual batch results are aggregated in the output

Example output:

```
Upload Summary:
  Total:   20000
  Success: 20000
  Failed:  0
  Batches: 20
```

## Known Limitations

- API is in beta (`x-release-stage: beta`)
- Batches are uploaded sequentially, not in parallel
- Each batch takes ~2-4 seconds to process

## Related Scripts

- `cam-clients.py` - List all NAC clients
- `cam-clients-export.py` - Export NAC clients with filtering
- `cam-clients-delete.py` - Delete NAC clients and groups
- `cam-guest-purge.py` - Purge stale guest clients
