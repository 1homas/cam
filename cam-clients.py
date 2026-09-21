#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "click>=8.0.0",
#     "pyyaml>=6.0.0",
# ]
# ///
"""
Search, create, update, and delete Cisco Access Manager (CAM) NAC clients.

Fetches, filters, creates, updates, and deletes NAC clients through the
organization-scoped Meraki Dashboard API (/organizations/{organizationId}/nac/clients).
Search results can be exported in JSON, JSONL, YAML, CSV, or table format and filtered
by any attribute using --filter key=value (repeatable, dot notation for nested fields).

--create with a CSV file path uploads the whole file via the bulkUpload API using the
CAM template format (MAC address, Endpoint device group, Description, IPSK), automatically
batched at 1000 rows, auto-creating any missing groups and upserting existing clients
matched by MAC (both on by default; see --no-create-groups/--no-update-clients). --create
with an inline JSON object creates a single client via one POST request instead. --update
(one request per client, concurrency via --workers) and --delete (batched bulkDelete) each
accept a CSV file or inline JSON object and require a client `id`, obtained via --search
(or --filter mac=...) or captured from a prior create response.

Usage:
    cam-clients.py [--filter key=value]... [--format json|jsonl|yaml|csv|table] [-v]
    cam-clients.py --org ORG_ID --create clients.csv [--no-create-groups] [--no-update-clients] [--debug] [-v]
    cam-clients.py --org ORG_ID --create '{"mac": "..."}' [-v]
    cam-clients.py --org ORG_ID --update (clients.csv|'{"id": "...", ...}') [--workers N] [-v]
    cam-clients.py --org ORG_ID --delete (clients.csv|'{"id": "..."}') [-v]
    cam-clients.py --org ORG_ID --delete-id CLIENT_ID

Examples:
    cam-clients.py                          # Export all clients (default batch 1000)
    cam-clients.py --limit 100              # Export first 100 clients
    cam-clients.py --filter status=Connected
    cam-clients.py --format csv -f ssid=Guest -f source=Discovered
    cam-clients.py --filter classification.os=iOS
    cam-clients.py --format table -f owner=jsmith -v
    cam-clients.py --create clients.csv                              # bulkUpload, auto-create groups + upsert
    cam-clients.py --create clients.csv --debug                      # show decoded CSV and payload
    cam-clients.py --create clients.csv --no-create-groups --no-update-clients
    cam-clients.py --create '{"mac": "AA:BB:CC:DD:EE:FF", "description": "Printer"}'
    cam-clients.py -o 123456 --create clients.csv --batch 500 -v
    cam-clients.py --update updates.csv
    cam-clients.py --update '{"id": "627126248111374692", "description": "Reassigned"}'
    cam-clients.py --delete deletes.csv
    cam-clients.py --delete-id 627126248111374692
"""

import asyncio
import base64
import csv
import io
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import click
import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cam-clients")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"


def run_main(coro) -> None:
    """Run the top-level coroutine with graceful Ctrl+C / SIGINT / SIGTERM shutdown."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(*_args):
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)

    try:
        loop.run_until_complete(coro)
    except asyncio.CancelledError:
        logger.warning("Interrupted, shutting down...")
        sys.exit(130)
    finally:
        loop.close()


CSV_COLUMNS = [
    "id",
    "mac",
    "owner",
    "type",
    "status",
    "ssid",
    "source",
    "ipAddress",
    "description",
    "lastLogin.timestamp",
    "lastLogin.location",
    "firstLogin.timestamp",
    "firstLogin.location",
    "classification.type",
    "classification.manufacturer",
    "classification.model",
    "classification.os",
]

CREATE_REQUIRED_COLUMNS = ["mac"]
CREATE_OPTIONAL_COLUMNS = ["type", "owner", "description", "ipsk", "groups"]

UPDATE_REQUIRED_COLUMNS = ["id"]
UPDATE_OPTIONAL_COLUMNS = ["type", "owner", "mac", "description", "ipsk", "groupsAdd", "groupsRemove"]

DELETE_REQUIRED_COLUMNS = ["id"]

DELETE_CHUNK_SIZE = 500

# CSV files passed to --create are uploaded whole via the bulkUpload API using
# the CAM template format (not the mac/type/owner/... row schema above, which
# only applies to a single-client inline JSON object).
BULK_BATCH_SIZE = 1000


def parse_csv(csv_content: str) -> list[dict]:
    """Parse CSV content into a list of row dicts, dropping empty values and rows."""
    reader = csv.DictReader(io.StringIO(csv_content))
    rows = []
    for row in reader:
        cleaned = {k: v for k, v in row.items() if v}
        if cleaned:
            rows.append(cleaned)
    return rows


def load_rows(value: str) -> list[dict]:
    """Load rows from a CSV file path or an inline JSON object for a single client.

    A value starting with '{' is parsed as a single-client JSON object;
    otherwise it is treated as a path to a CSV file.
    """
    stripped = value.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"Invalid JSON: {e}")
        if not isinstance(obj, dict):
            raise click.BadParameter("JSON block must be a single client object")
        row = {k: v for k, v in obj.items() if v not in (None, "")}
        return [row]

    path = Path(stripped)
    if not path.exists():
        raise click.BadParameter(f"File not found: {stripped}")
    return parse_csv(path.read_text())


def validate_create_row(row: dict, row_num: int) -> list[str]:
    """Validate a create row. Returns list of error messages."""
    errors = []
    if not row.get("mac"):
        errors.append(f"Row {row_num}: Missing required field 'mac'")
    return errors


def validate_update_row(row: dict, row_num: int) -> list[str]:
    """Validate an update row. Returns list of error messages."""
    errors = []
    if not row.get("id"):
        errors.append(f"Row {row_num}: Missing required field 'id'")
    return errors


def validate_delete_row(row: dict, row_num: int) -> list[str]:
    """Validate a delete row. Returns list of error messages."""
    errors = []
    if not row.get("id"):
        errors.append(f"Row {row_num}: Missing required field 'id'")
    return errors


def _split_ids(value) -> list[str]:
    """Split a semicolon-separated string of ids into a cleaned list."""
    if not value:
        return []
    return [v.strip() for v in str(value).split(";") if v.strip()]


def build_create_payload(row: dict) -> dict:
    """Build a create-client API payload from a CSV row or JSON object."""
    payload = {"mac": row["mac"]}
    for field in ("type", "owner", "description", "ipsk"):
        if row.get(field):
            payload[field] = row[field]
    group_ids = _split_ids(row.get("groups"))
    if group_ids:
        payload["groups"] = [{"value": gid} for gid in group_ids]
    return payload


def build_update_payload(row: dict) -> dict:
    """Build an update-client API payload from a CSV row or JSON object (excludes 'id')."""
    payload = {}
    for field in ("type", "owner", "mac", "description", "ipsk"):
        if row.get(field):
            payload[field] = row[field]
    add_ids = _split_ids(row.get("groupsAdd"))
    remove_ids = _split_ids(row.get("groupsRemove"))
    if add_ids or remove_ids:
        groups = {}
        if add_ids:
            groups["addList"] = [{"value": gid} for gid in add_ids]
        if remove_ids:
            groups["removeList"] = [{"value": gid} for gid in remove_ids]
        payload["groups"] = groups
    return payload


def chunk_rows(rows: list, chunk_size: int = 100):
    """Split rows into chunks for batching."""
    for i in range(0, len(rows), chunk_size):
        yield rows[i : i + chunk_size]


def validate_bulk_csv(csv_content: str) -> tuple[bool, str]:
    """Validate CSV content has the required CAM template header.

    Accepts the CAM template format with a 'MAC address' column (case-insensitive).

    Returns:
        Tuple of (is_valid, error_message)
    """
    lines = csv_content.strip().split("\n")
    if len(lines) < 2:
        return False, "CSV must have at least a header row and one data row"

    headers = [h.strip().lower() for h in lines[0].split(",")]
    if "mac address" not in headers:
        return False, "CSV must have a 'MAC address' column (CAM template format)"

    return True, ""


def split_csv_into_batches(csv_content: str, batch_size: int) -> list[str]:
    """Split CSV content into batches of rows, each with its own header line.

    Args:
        csv_content: Full CSV content with headers
        batch_size: Maximum number of data rows per batch

    Returns:
        List of CSV strings, each with headers and up to batch_size rows
    """
    lines = csv_content.strip().split("\n")
    if len(lines) <= 1:
        return [csv_content]

    header = lines[0]
    data_rows = lines[1:]

    batches = []
    for i in range(0, len(data_rows), batch_size):
        batch_rows = data_rows[i : i + batch_size]
        batches.append(header + "\n" + "\n".join(batch_rows))
    return batches


def format_bulk_result(result: dict, fmt: str) -> str:
    """Format a bulkUpload result. Supports 'json', 'yaml', and 'summary'; anything else falls back to 'summary'."""
    if fmt == "json":
        return json.dumps(result, indent=2, default=str)
    if fmt == "yaml":
        return yaml.dump(result, default_flow_style=False, sort_keys=False)

    meta = result.get("meta", {})
    counts = meta.get("counts", {})
    items = result.get("items", [])
    batches = result.get("batches", 0)

    lines = ["Upload Summary:"]
    lines.append(f"  Total:   {counts.get('total', 0)}")
    lines.append(f"  Success: {counts.get('success', 0)}")
    lines.append(f"  Failed:  {counts.get('failure', 0)}")
    if batches > 1:
        lines.append(f"  Batches: {batches}")

    if batches > 1:
        all_items = []
        for batch_result in result.get("results", []):
            all_items.extend(batch_result.get("items", []))
        items = all_items

    if items:
        lines.append("\nDetails:")
        error_items = [i for i in items if i.get("type") == "error"]
        info_items = [i for i in items if i.get("type") == "info"]
        for item in error_items + info_items:
            item_type = item.get("type", "unknown")
            count = item.get("count", 0)
            if count > 0:
                lines.append(f"  {item_type.upper()}: {count}")
                for detail in item.get("details", []):
                    message = detail.get("message", "")
                    detail_rows = detail.get("rows", [])
                    if len(detail_rows) > 10:
                        lines.append(f"    - {message} ({len(detail_rows)} rows)")
                    else:
                        lines.append(f"    - {message} (rows: {detail_rows})")

    return "\n".join(lines)


def parse_next_starting_after(link_header: str) -> Optional[str]:
    """Extract startingAfter token from Link header."""
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part or "rel='next'" in part:
            url_match = part.split(";")[0].strip("<> ")
            parsed = urlparse(url_match)
            params = parse_qs(parsed.query)
            if "startingAfter" in params:
                return params["startingAfter"][0]
    return None


async def fetch_all_clients(client: httpx.AsyncClient, org_id: str, batch_size: int = 1000, limit: int = 0) -> list[dict]:
    """Fetch NAC clients with configurable batch size and limit.

    Args:
        client: HTTP client
        org_id: Organization ID
        batch_size: Number of clients per API request (default: 1000)
        limit: Maximum number of clients to fetch (0 = no limit)

    Returns:
        List of client dictionaries

    Uses offset-based pagination with startingAfter parameter to fetch all clients.
    """
    clients: list[dict] = []
    params: dict = {"perPage": batch_size}
    total_count: int = 0

    while True:
        response = await client.get(f"{BASE_URL}/organizations/{org_id}/nac/clients", params=params)

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 1))
            logger.warning(f"Rate limited, sleeping {retry_after}s")
            await asyncio.sleep(retry_after)
            continue

        response.raise_for_status()
        data = response.json()
        items = data.get("items", data) if isinstance(data, dict) else data

        # Get total count from first response
        if isinstance(data, dict) and 'meta' in data and total_count == 0:
            total_count = data['meta'].get('totalCount', 0)

        # No more items returned
        if not items:
            break

        # Apply limit if specified
        if limit > 0:
            remaining = limit - len(clients)
            if remaining <= 0:
                break
            items = items[:remaining]

        clients.extend(items)
        logger.info(f"Fetched {len(items)} clients (total: {len(clients)})")

        # Stop if we've reached the limit
        if limit > 0 and len(clients) >= limit:
            break

        # Check if we've fetched all available clients
        if total_count > 0 and len(clients) >= total_count:
            break

        # Use offset-based pagination with startingAfter
        # Set startingAfter to the current count to fetch the next page
        params["startingAfter"] = str(len(clients))

    return clients


def filter_clients(clients: list[dict], filters: list[tuple[str, str]]) -> list[dict]:
    """Filter clients by arbitrary key=value pairs. Supports dot notation for nested fields.

    Uses case-insensitive substring matching (contains).
    """
    result = clients
    for key, value in filters:
        value_lower = value.lower()
        result = [c for c in result if value_lower in _get_nested(c, key).lower()]
    return result


def _get_nested(obj: dict, dotted_key: str) -> str:
    """Resolve a dotted key path like 'lastLogin.timestamp' from a nested dict."""
    parts = dotted_key.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part, "")
        else:
            return ""
    return str(current) if current else ""


def format_json(clients: list[dict]) -> str:
    """Format clients as JSON."""
    return json.dumps(clients, indent=2, default=str)


def format_jsonl(clients: list[dict]) -> str:
    """Format clients as JSON Lines (one compact JSON object per line)."""
    return "\n".join(json.dumps(c, default=str) for c in clients)


def format_yaml(clients: list[dict]) -> str:
    """Format clients as YAML."""
    return yaml.dump(clients, default_flow_style=False, sort_keys=False)


def format_csv(clients: list[dict]) -> str:
    """Format clients as CSV with flattened nested fields."""
    if not clients:
        return ""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    for c in clients:
        row = [_get_nested(c, col) for col in CSV_COLUMNS]
        writer.writerow(row)
    return output.getvalue()


def format_table(clients: list[dict]) -> str:
    """Format clients as a Markdown table."""
    if not clients:
        return ""
    lines = [
        "| " + " | ".join(CSV_COLUMNS) + " |",
        "| " + " | ".join("---" for _ in CSV_COLUMNS) + " |",
    ]
    for c in clients:
        row = [_get_nested(c, col) for col in CSV_COLUMNS]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


FORMATTERS = {
    "json": format_json,
    "jsonl": format_jsonl,
    "yaml": format_yaml,
    "csv": format_csv,
    "table": format_table,
}


async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Issue a request, retrying once after a 429 Retry-After delay."""
    response = await client.request(method, url, **kwargs)
    if response.status_code == 429:
        retry_after = float(response.headers.get("Retry-After", 1))
        logger.warning(f"Rate limited, sleeping {retry_after}s")
        await asyncio.sleep(retry_after)
        response = await client.request(method, url, **kwargs)
    return response


async def create_client(client: httpx.AsyncClient, org_id: str, payload: dict) -> dict:
    """Create a single NAC client. Returns dict with success status and details."""
    try:
        response = await _request_with_retry(
            client, "POST", f"{BASE_URL}/organizations/{org_id}/nac/clients", json=payload
        )
        response.raise_for_status()
        result = response.json()
        return {"success": True, "id": result.get("id"), "mac": payload["mac"]}
    except Exception as e:
        logger.error(f"Failed to create client {payload.get('mac')}: {e}")
        return {"success": False, "mac": payload.get("mac"), "error": str(e)}


async def update_client(client: httpx.AsyncClient, org_id: str, client_id: str, payload: dict) -> dict:
    """Update a single NAC client. Returns dict with success status and details."""
    try:
        response = await _request_with_retry(
            client, "PUT", f"{BASE_URL}/organizations/{org_id}/nac/clients/{client_id}", json=payload
        )
        response.raise_for_status()
        result = response.json()
        return {"success": True, "id": client_id, "mac": result.get("mac")}
    except Exception as e:
        logger.error(f"Failed to update client {client_id}: {e}")
        return {"success": False, "id": client_id, "error": str(e)}


async def bulk_upload_clients(
    client: httpx.AsyncClient,
    org_id: str,
    csv_content: str,
    update_clients: bool = True,
    create_groups: bool = True,
    debug: bool = False,
) -> dict:
    """Upload a batch of clients via the bulkUpload API (CAM template CSV, base64-encoded).

    Args:
        client: HTTP client
        org_id: Organization ID
        csv_content: Raw CSV text for this batch (CAM template: MAC address, Endpoint device group, Description, IPSK)
        update_clients: Upsert existing clients matched by MAC (default: True)
        create_groups: Auto-create any missing groups named in 'Endpoint device group' (default: True)
        debug: Log the decoded CSV and payload flags before sending

    Returns:
        The bulkUpload API response dict
    """
    base64_csv = base64.b64encode(csv_content.encode()).decode()
    payload = {
        "contents": base64_csv,
        "updateClients": update_clients,
        "createClientGroups": create_groups,
    }

    if debug:
        logger.info(f"CSV Content:\n{csv_content}")
        logger.info(f"Payload: updateClients={update_clients}, createClientGroups={create_groups}")

    response = await _request_with_retry(
        client, "POST", f"{BASE_URL}/organizations/{org_id}/nac/clients/bulkUpload", json=payload
    )
    response.raise_for_status()
    return response.json()


async def bulk_delete_clients(client: httpx.AsyncClient, org_id: str, client_ids: list[str]) -> dict:
    """Delete a batch of NAC clients via bulkDelete. Returns dict with success status and details."""
    try:
        response = await _request_with_retry(
            client, "POST", f"{BASE_URL}/organizations/{org_id}/nac/clients/bulkDelete", json={"clientIds": client_ids}
        )
        response.raise_for_status()
        return {"success": True, "ids": client_ids}
    except Exception as e:
        logger.error(f"Failed to delete clients {client_ids}: {e}")
        return {"success": False, "ids": client_ids, "error": str(e)}


async def run_batch(coro_factory, items: list, semaphore: asyncio.Semaphore) -> list[dict]:
    """Run a batch of operations with concurrency control."""
    async def with_semaphore(item):
        async with semaphore:
            return await coro_factory(item)

    tasks = [with_semaphore(item) for item in items]
    return await asyncio.gather(*tasks)


def print_summary(action: str, total: int, succeeded: int, failed: int, elapsed: float) -> None:
    """Print a summary block for a completed operation."""
    print(f"\n{'=' * 60}")
    print(f"{action} Complete")
    print(f"{'=' * 60}")
    print(f"Total:        {total}")
    print(f"Succeeded:    {succeeded}")
    print(f"Failed:       {failed}")
    print(f"Time elapsed: {elapsed:.1f}s")
    if elapsed > 0:
        print(f"Rate:         {total / elapsed:.1f} ops/sec")
    print(f"{'=' * 60}")


async def run_create(
    client: httpx.AsyncClient,
    org_id: str,
    create_input: str,
    semaphore: asyncio.Semaphore,
    batch_size: int = BULK_BATCH_SIZE,
    update_clients: bool = True,
    create_groups: bool = True,
    fmt: str = "summary",
    debug: bool = False,
) -> int:
    """Create clients. A CSV file path is uploaded whole via bulkUpload (CAM template,
    auto-creating missing groups and upserting by MAC); an inline JSON object creates one
    client via a single POST /nac/clients request."""
    stripped = create_input.strip()
    if not stripped.startswith("{"):
        return await run_bulk_upload(client, org_id, stripped, batch_size, update_clients, create_groups, fmt, debug)

    rows = load_rows(create_input)
    logger.info(f"Parsed {len(rows)} row(s) from input")
    if not rows:
        logger.error("No rows found in input")
        return 1

    all_errors = []
    for i, row in enumerate(rows, start=2):
        all_errors.extend(validate_create_row(row, i))
    if all_errors:
        for error in all_errors[:10]:
            logger.error(f"  {error}")
        return 1

    payloads = [build_create_payload(row) for row in rows]
    start = time.time()
    total_succeeded = total_failed = 0
    for i, chunk in enumerate(chunk_rows(payloads), start=1):
        logger.info(f"Creating chunk {i} ({len(chunk)} clients)...")
        results = await run_batch(lambda p: create_client(client, org_id, p), chunk, semaphore)
        succeeded = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        total_succeeded += len(succeeded)
        total_failed += len(failed)
        for result in failed[:3]:
            logger.error(f"  Failed: {result['mac']} - {result.get('error', 'Unknown error')}")

    print_summary("Create", len(payloads), total_succeeded, total_failed, time.time() - start)
    return 1 if total_failed > 0 else 0


async def run_bulk_upload(
    client: httpx.AsyncClient,
    org_id: str,
    csv_path: str,
    batch_size: int,
    update_clients: bool,
    create_groups: bool,
    fmt: str,
    debug: bool,
) -> int:
    """Upload a CSV file of clients via the bulkUpload API, splitting into batches as needed."""
    path = Path(csv_path)
    if not path.exists():
        raise click.BadParameter(f"File not found: {csv_path}")

    csv_content = path.read_text()
    is_valid, error = validate_bulk_csv(csv_content)
    if not is_valid:
        logger.error(f"Invalid CSV: {error}")
        return 1

    total_rows = len(csv_content.strip().split("\n")) - 1
    if batch_size and total_rows > batch_size:
        batches = split_csv_into_batches(csv_content, batch_size)
        logger.info(f"Splitting {total_rows} clients into {len(batches)} batches of {batch_size}")
    else:
        batches = [csv_content]
        logger.info(f"Uploading {total_rows} client(s) in a single batch")

    start = time.time()
    all_results = []
    total_success = total_failure = 0

    for batch_num, batch_csv in enumerate(batches, 1):
        if len(batches) > 1:
            batch_rows = len(batch_csv.strip().split("\n")) - 1
            logger.info(f"Uploading batch {batch_num}/{len(batches)} ({batch_rows} clients)")

        try:
            result = await bulk_upload_clients(client, org_id, batch_csv, update_clients, create_groups, debug)
        except Exception as e:
            logger.error(f"Batch {batch_num} failed: {e}")
            return 1

        counts = result.get("meta", {}).get("counts", {})
        success = counts.get("success", 0)
        failure = counts.get("failure", 0)
        total_success += success
        total_failure += failure
        all_results.append(result)

        if len(batches) > 1:
            logger.info(f"Batch {batch_num} complete: {success} succeeded, {failure} failed")

    elapsed = time.time() - start
    logger.info(f"Upload completed in {elapsed:.1f}s: {total_success} succeeded, {total_failure} failed")

    if len(batches) == 1:
        output = format_bulk_result(all_results[0], fmt)
    else:
        combined = {
            "meta": {"counts": {"total": total_rows, "success": total_success, "failure": total_failure}},
            "batches": len(batches),
            "results": all_results,
        }
        output = format_bulk_result(combined, fmt)

    print(output)
    return 1 if total_failure > 0 else 0


async def run_update(client: httpx.AsyncClient, org_id: str, update_input: str, semaphore: asyncio.Semaphore) -> int:
    rows = load_rows(update_input)
    logger.info(f"Parsed {len(rows)} row(s) from input")
    if not rows:
        logger.error("No rows found in input")
        return 1

    all_errors = []
    for i, row in enumerate(rows, start=2):
        all_errors.extend(validate_update_row(row, i))
    if all_errors:
        for error in all_errors[:10]:
            logger.error(f"  {error}")
        return 1

    updates = [(row["id"], build_update_payload(row)) for row in rows]
    start = time.time()
    total_succeeded = total_failed = 0
    for i, chunk in enumerate(chunk_rows(updates), start=1):
        logger.info(f"Updating chunk {i} ({len(chunk)} clients)...")
        results = await run_batch(lambda u: update_client(client, org_id, u[0], u[1]), chunk, semaphore)
        succeeded = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        total_succeeded += len(succeeded)
        total_failed += len(failed)
        for result in failed[:3]:
            logger.error(f"  Failed: {result['id']} - {result.get('error', 'Unknown error')}")

    print_summary("Update", len(updates), total_succeeded, total_failed, time.time() - start)
    return 1 if total_failed > 0 else 0


async def run_delete(client: httpx.AsyncClient, org_id: str, ids: list[str], semaphore: asyncio.Semaphore) -> int:
    start = time.time()
    total_succeeded = total_failed = 0
    for i, chunk in enumerate(chunk_rows(ids, DELETE_CHUNK_SIZE), start=1):
        logger.info(f"Deleting chunk {i} ({len(chunk)} clients)...")
        result = await bulk_delete_clients(client, org_id, chunk)
        if result["success"]:
            total_succeeded += len(chunk)
        else:
            total_failed += len(chunk)
            logger.error(f"  Failed: {len(chunk)} clients - {result.get('error', 'Unknown error')}")

    print_summary("Delete", len(ids), total_succeeded, total_failed, time.time() - start)
    return 1 if total_failed > 0 else 0


def parse_filter(value: str) -> tuple[str, str]:
    """Parse a key=value filter string."""
    if "=" not in value:
        raise click.BadParameter(f"Filter must be key=value, got: {value}")
    key, _, val = value.partition("=")
    return (key.strip(), val.strip())


async def run(
    org_id: Optional[str],
    do_search: bool,
    fmt: str,
    filters: list[tuple[str, str]],
    batch_size: int,
    limit: int,
    create_input: Optional[str],
    update_input: Optional[str],
    delete_input: Optional[str],
    delete_id: Optional[str],
    workers: int,
    timeout: int,
    create_groups: bool,
    update_clients: bool,
    create_fmt: str,
    debug: bool,
) -> None:
    """Main dispatch workflow."""
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")
    if not api_key:
        logger.error("MERAKI_DASHBOARD_API_KEY not set")
        sys.exit(1)
    if not org_id:
        logger.error("Organization ID required (--org or MERAKI_ORG_ID)")
        sys.exit(1)

    semaphore = asyncio.Semaphore(workers)
    exit_code = 0

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=httpx.Timeout(float(timeout)),
    ) as client:
        if do_search:
            start = time.time()
            all_clients = await fetch_all_clients(client, org_id, batch_size=batch_size, limit=limit)
            elapsed = time.time() - start
            logger.info(f"Fetched {len(all_clients)} clients in {elapsed:.1f}s")

            filtered = filter_clients(all_clients, filters)
            logger.info(f"After filtering: {len(filtered)} clients")

            output = FORMATTERS[fmt](filtered)
            if output:
                print(output)
        if create_input:
            exit_code |= await run_create(
                client, org_id, create_input, semaphore,
                batch_size=batch_size, update_clients=update_clients, create_groups=create_groups,
                fmt=create_fmt, debug=debug,
            )
        if update_input:
            exit_code |= await run_update(client, org_id, update_input, semaphore)
        if delete_input:
            rows = load_rows(delete_input)
            errors = []
            for i, row in enumerate(rows, start=2):
                errors.extend(validate_delete_row(row, i))
            if errors:
                for error in errors[:10]:
                    logger.error(f"  {error}")
                exit_code = 1
            else:
                exit_code |= await run_delete(client, org_id, [row["id"] for row in rows], semaphore)
        if delete_id:
            result = await bulk_delete_clients(client, org_id, [delete_id])
            if result["success"]:
                print(f"Deleted client {delete_id}")
            else:
                print(f"Failed to delete client {delete_id}: {result.get('error')}")
                exit_code = 1

    if exit_code:
        sys.exit(exit_code)


@click.command()
@click.option("--org", "-o", "org_id", envvar="MERAKI_ORG_ID", help="Organization ID (or set MERAKI_ORG_ID)")
@click.option("--search", "-s", "do_search", is_flag=True, default=False, help="Search/list NAC clients (default action when no other flags are given)")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "jsonl", "yaml", "csv", "table", "summary"]), help="Output format for search (default: json) and for CSV --create results (default: summary)")
@click.option("--filter", "-f", "filters", multiple=True, help="Filter by key=value (repeatable, supports dot notation)")
@click.option("--batch", "batch_size", default=1000, type=int, help="Batch size per API request, and per CSV chunk for bulk --create uploads (default: 1000)")
@click.option("--limit", "limit", default=0, type=int, help="Maximum number of clients to fetch (default: 0 = all)")
@click.option("--create", "create_input", help="CSV file path (CAM template: MAC address, Endpoint device group, Description, IPSK; uploaded via bulkUpload), or a JSON object for a single client (fields: mac, type, owner, description, ipsk, groups)")
@click.option("--create-groups/--no-create-groups", "create_groups", default=True, help="Auto-create any missing groups named in a CSV --create upload (default: true)")
@click.option("--update-clients/--no-update-clients", "update_clients", default=True, help="Upsert existing clients matched by MAC in a CSV --create upload (default: true)")
@click.option("--debug", is_flag=True, default=False, help="Show decoded CSV and payload details for CSV --create uploads")
@click.option("--update", "update_input", help="CSV file path, or a JSON object for a single client, to update (fields: id, type, owner, mac, description, ipsk, groupsAdd, groupsRemove)")
@click.option("--delete", "delete_input", help="CSV file path, or a JSON object for a single client, to delete (field: id)")
@click.option("--delete-id", "delete_id", default=None, help="Delete a single client by ID")
@click.option("--workers", "-w", default=1, type=click.IntRange(1, 10), help="Number of concurrent workers for create/update, 1-10 (default: 1)")
@click.option("--timeout", "-t", default=30, type=int, help="HTTP timeout in seconds (default: 30)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(
    org_id: Optional[str],
    do_search: bool,
    fmt: str,
    filters: tuple[str, ...],
    batch_size: int,
    limit: int,
    create_input: Optional[str],
    create_groups: bool,
    update_clients: bool,
    debug: bool,
    update_input: Optional[str],
    delete_input: Optional[str],
    delete_id: Optional[str],
    workers: int,
    timeout: int,
    verbose: bool,
) -> None:
    """Search, create, update, and delete Cisco Access Manager (CAM) NAC clients.

    Manages NAC clients through the org-scoped
    /organizations/{organizationId}/nac/clients API. With no flags given, the
    default action is to search/export all clients. --update/--delete each
    accept either a path to a CSV file or an inline JSON object for one
    client, and may be combined with each other or with --search in one run.

    --create with a CSV file path uploads the whole file via the bulkUpload
    API using the CAM template format (MAC address, Endpoint device group,
    Description, IPSK), auto-creating missing groups and upserting existing
    clients matched by MAC (both on by default; use --no-create-groups /
    --no-update-clients to disable). --create with an inline JSON object
    creates a single client via one POST request instead.

    \b
    Examples:
      cam-clients.py                          # Export all clients (default batch 1000)
      cam-clients.py --limit 100              # Export first 100 clients
      cam-clients.py --batch 500              # Use 500 clients per API request
      cam-clients.py --filter status=Connected
      cam-clients.py --format csv -f ssid=Guest -f source=Discovered
      cam-clients.py --filter classification.os=iOS
      cam-clients.py --format table -f owner=jsmith -v
      cam-clients.py --create clients.csv
      cam-clients.py --create clients.csv --debug
      cam-clients.py --create clients.csv --no-create-groups --no-update-clients
      cam-clients.py --create clients.csv --format json
      cam-clients.py --create '{"mac": "AA:BB:CC:DD:EE:FF", "description": "Printer"}'
      cam-clients.py -o 123456 --create clients.csv --batch 500 -v
      cam-clients.py --update updates.csv
      cam-clients.py --update '{"id": "627126248111374692", "description": "Reassigned"}'
      cam-clients.py --delete deletes.csv
      cam-clients.py --delete '{"id": "627126248111374692"}'
      cam-clients.py --delete-id 627126248111374692
    """
    if verbose or debug:
        logger.setLevel(logging.INFO)

    # Default to search when no CRUD action is specified (preserves prior no-flag export behavior)
    if not any([create_input, update_input, delete_input, delete_id]):
        do_search = True

    parsed_filters = [parse_filter(f) for f in filters]

    # --format defaults to json for search output; a CSV --create defaults to the
    # friendlier 'summary' breakdown unless the user explicitly chose a format.
    ctx = click.get_current_context()
    fmt_is_default = ctx.get_parameter_source("fmt") == click.core.ParameterSource.DEFAULT
    create_fmt = "summary" if fmt_is_default else fmt

    run_main(
        run(org_id, do_search, fmt, parsed_filters, batch_size, limit, create_input, update_input, delete_input, delete_id,
            workers, timeout, create_groups, update_clients, create_fmt, debug)
    )


if __name__ == "__main__":
    main()
