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
Search, create, update, and delete Cisco Access Manager (CAM) NAC client groups.

Manages NAC client groups through the organization-scoped Meraki Dashboard API
(/organizations/{organizationId}/nac/clients/groups). With no flags given, the
default action is to search/export all groups in JSON, YAML, CSV, or table
format.

Supports parallel operations with configurable concurrency and automatic
batching for create, update, and delete, each driven by a CSV file or an
inline JSON object for a single group. Update and delete require a group `id`,
obtained via --search or captured from a prior create response. There is no
bulk-delete endpoint for groups, so delete issues one DELETE request per group.

Usage:
    cam-clients-groups.py [--search-query TEXT] [--format json|yaml|csv|table] [-v]
    cam-clients-groups.py --org ORG_ID --create (groups.csv|'{"name": "..."}') [--workers N] [-v]
    cam-clients-groups.py --org ORG_ID --update (groups.csv|'{"id": "...", ...}') [--workers N] [-v]
    cam-clients-groups.py --org ORG_ID --delete (groups.csv|'{"id": "..."}') [--workers N] [-v]
    cam-clients-groups.py --org ORG_ID --delete-id GROUP_ID

Examples:
    cam-clients-groups.py --search
    cam-clients-groups.py --search --search-query Camera --format table
    cam-clients-groups.py --search --sort-key name --sort-order DESC --format csv > groups.csv
    cam-clients-groups.py --create groups.csv
    cam-clients-groups.py --create '{"name": "Cameras", "description": "Security cameras"}'
    cam-clients-groups.py -o 123456 --create groups.csv --workers 10 -v
    cam-clients-groups.py --update updates.csv
    cam-clients-groups.py --update '{"id": "627126248111341608", "description": "Updated"}'
    cam-clients-groups.py --update '{"id": "627126248111341608", "membersAdd": "1;2;3"}'
    cam-clients-groups.py --delete deletes.csv -w 20
    cam-clients-groups.py --delete-id 627126248111341608
"""

import asyncio
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

logger = logging.getLogger("cam-clients-groups")
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


SORT_KEYS = ["name"]
SORT_ORDERS = ["ASC", "DESC"]

SEARCH_CSV_COLUMNS = ["id", "name", "description", "membersCount"]

CREATE_REQUIRED_COLUMNS = ["name"]
CREATE_OPTIONAL_COLUMNS = ["description", "members"]

UPDATE_REQUIRED_COLUMNS = ["id"]
UPDATE_OPTIONAL_COLUMNS = ["name", "description", "membersAdd", "membersRemove"]

DELETE_REQUIRED_COLUMNS = ["id"]


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
    """Load rows from a CSV file path or an inline JSON object for a single group.

    A value starting with '{' is parsed as a single-group JSON object;
    otherwise it is treated as a path to a CSV file.
    """
    stripped = value.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"Invalid JSON: {e}")
        if not isinstance(obj, dict):
            raise click.BadParameter("JSON block must be a single group object")
        row = {k: v for k, v in obj.items() if v not in (None, "")}
        return [row]

    path = Path(stripped)
    if not path.exists():
        raise click.BadParameter(f"File not found: {stripped}")
    return parse_csv(path.read_text())


def validate_create_row(row: dict, row_num: int) -> list[str]:
    """Validate a create row. Returns list of error messages."""
    errors = []
    if not row.get("name"):
        errors.append(f"Row {row_num}: Missing required field 'name'")
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
    """Build a create-group API payload from a CSV row or JSON object."""
    payload = {"name": row["name"]}
    if row.get("description"):
        payload["description"] = row["description"]
    member_ids = _split_ids(row.get("members"))
    if member_ids:
        payload["members"] = [{"value": mid} for mid in member_ids]
    return payload


def build_update_payload(row: dict) -> dict:
    """Build an update-group API payload from a CSV row or JSON object (excludes 'id')."""
    payload = {}
    if row.get("name"):
        payload["name"] = row["name"]
    if row.get("description"):
        payload["description"] = row["description"]
    add_ids = _split_ids(row.get("membersAdd"))
    remove_ids = _split_ids(row.get("membersRemove"))
    if add_ids or remove_ids:
        members = {}
        if add_ids:
            members["addList"] = [{"value": mid} for mid in add_ids]
        if remove_ids:
            members["removeList"] = [{"value": mid} for mid in remove_ids]
        payload["members"] = members
    return payload


def chunk_rows(rows: list, chunk_size: int = 100):
    """Split rows into chunks for batching."""
    for i in range(0, len(rows), chunk_size):
        yield rows[i : i + chunk_size]


def parse_next_starting_after(link_header: str) -> Optional[str]:
    """Extract startingAfter token from a Link header."""
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


def flatten_group(item: dict) -> dict:
    """Flatten a group item's nested members count into a row."""
    members = item.get("members") or {}
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "description": item.get("description"),
        "membersCount": members.get("totalCount", 0) if isinstance(members, dict) else 0,
    }


async def search_groups(client: httpx.AsyncClient, org_id: str, search_query: Optional[str], group_ids: tuple[str, ...],
                         sort_key: Optional[str], sort_order: Optional[str], per_page: int, limit: int) -> list[dict]:
    """Search/list NAC client groups, following Link header pagination until limit or exhaustion."""
    rows: list[dict] = []
    params: dict = {"perPage": per_page}
    if search_query:
        params["search"] = search_query
    if group_ids:
        params["groupIds[]"] = list(group_ids)
    if sort_key:
        params["sortKey"] = sort_key
    if sort_order:
        params["sortOrder"] = sort_order

    while True:
        response = await _request_with_retry(
            client, "GET", f"{BASE_URL}/organizations/{org_id}/nac/clients/groups", params=params
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") or []
        page_rows = [flatten_group(item) for item in items]

        if limit > 0:
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            page_rows = page_rows[:remaining]

        rows.extend(page_rows)
        logger.info(f"Fetched {len(page_rows)} group(s) (total: {len(rows)})")

        if limit > 0 and len(rows) >= limit:
            break

        starting_after = parse_next_starting_after(response.headers.get("Link", ""))
        if not starting_after or not items:
            break
        params["startingAfter"] = starting_after

    return rows


def format_search_json(rows: list[dict]) -> str:
    return json.dumps(rows, indent=2, default=str)


def format_search_jsonl(rows: list[dict]) -> str:
    """Format rows as JSON Lines (one compact JSON object per line)."""
    return "\n".join(json.dumps(row, default=str) for row in rows)


def format_search_yaml(rows: list[dict]) -> str:
    return yaml.dump(rows, default_flow_style=False, sort_keys=False)


def format_search_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(SEARCH_CSV_COLUMNS)
    for row in rows:
        writer.writerow([row.get(col, "") for col in SEARCH_CSV_COLUMNS])
    return output.getvalue()


def format_search_table(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(SEARCH_CSV_COLUMNS) + " |",
        "| " + " | ".join("---" for _ in SEARCH_CSV_COLUMNS) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "") or "") for col in SEARCH_CSV_COLUMNS) + " |")
    return "\n".join(lines)


SEARCH_FORMATTERS = {
    "json": format_search_json,
    "jsonl": format_search_jsonl,
    "yaml": format_search_yaml,
    "csv": format_search_csv,
    "table": format_search_table,
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


async def create_group(client: httpx.AsyncClient, org_id: str, payload: dict) -> dict:
    """Create a single NAC client group. Returns dict with success status and details."""
    try:
        response = await _request_with_retry(
            client, "POST", f"{BASE_URL}/organizations/{org_id}/nac/clients/groups", json=payload
        )
        response.raise_for_status()
        result = response.json()
        return {"success": True, "id": result.get("id"), "name": payload["name"]}
    except Exception as e:
        logger.error(f"Failed to create group {payload.get('name')}: {e}")
        return {"success": False, "name": payload.get("name"), "error": str(e)}


async def update_group(client: httpx.AsyncClient, org_id: str, group_id: str, payload: dict) -> dict:
    """Update a single NAC client group. Returns dict with success status and details."""
    try:
        response = await _request_with_retry(
            client, "PUT", f"{BASE_URL}/organizations/{org_id}/nac/clients/groups/{group_id}", json=payload
        )
        response.raise_for_status()
        result = response.json()
        return {"success": True, "id": group_id, "name": result.get("name")}
    except Exception as e:
        logger.error(f"Failed to update group {group_id}: {e}")
        return {"success": False, "id": group_id, "error": str(e)}


async def delete_group(client: httpx.AsyncClient, org_id: str, group_id: str) -> dict:
    """Delete a single NAC client group. Returns dict with success status and details."""
    try:
        response = await _request_with_retry(
            client, "DELETE", f"{BASE_URL}/organizations/{org_id}/nac/clients/groups/{group_id}"
        )
        response.raise_for_status()
        return {"success": True, "id": group_id}
    except Exception as e:
        logger.error(f"Failed to delete group {group_id}: {e}")
        return {"success": False, "id": group_id, "error": str(e)}


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


async def run_create(client: httpx.AsyncClient, org_id: str, create_input: str, semaphore: asyncio.Semaphore) -> int:
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
        logger.info(f"Creating chunk {i} ({len(chunk)} groups)...")
        results = await run_batch(lambda p: create_group(client, org_id, p), chunk, semaphore)
        succeeded = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        total_succeeded += len(succeeded)
        total_failed += len(failed)
        for result in failed[:3]:
            logger.error(f"  Failed: {result['name']} - {result.get('error', 'Unknown error')}")

    print_summary("Create", len(payloads), total_succeeded, total_failed, time.time() - start)
    return 1 if total_failed > 0 else 0


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
        logger.info(f"Updating chunk {i} ({len(chunk)} groups)...")
        results = await run_batch(lambda u: update_group(client, org_id, u[0], u[1]), chunk, semaphore)
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
    for i, chunk in enumerate(chunk_rows(ids), start=1):
        logger.info(f"Deleting chunk {i} ({len(chunk)} groups)...")
        results = await run_batch(lambda gid: delete_group(client, org_id, gid), chunk, semaphore)
        succeeded = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        total_succeeded += len(succeeded)
        total_failed += len(failed)
        for result in failed[:3]:
            logger.error(f"  Failed: {result['id']} - {result.get('error', 'Unknown error')}")

    print_summary("Delete", len(ids), total_succeeded, total_failed, time.time() - start)
    return 1 if total_failed > 0 else 0


async def run(
    org_id: Optional[str],
    search: bool,
    search_query: Optional[str],
    group_ids: tuple[str, ...],
    sort_key: Optional[str],
    sort_order: Optional[str],
    per_page: int,
    limit: int,
    fmt: str,
    create_input: Optional[str],
    update_input: Optional[str],
    delete_input: Optional[str],
    delete_id: Optional[str],
    workers: int,
    timeout: int,
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
        if search:
            rows = await search_groups(client, org_id, search_query, group_ids, sort_key, sort_order, per_page, limit)
            logger.info(f"Found {len(rows)} group(s)")
            output = SEARCH_FORMATTERS[fmt](rows)
            if output:
                print(output)
        if create_input:
            exit_code |= await run_create(client, org_id, create_input, semaphore)
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
            result = await delete_group(client, org_id, delete_id)
            if result["success"]:
                print(f"Deleted group {delete_id}")
            else:
                print(f"Failed to delete group {delete_id}: {result.get('error')}")
                exit_code = 1

    if exit_code:
        sys.exit(exit_code)


@click.command()
@click.option("--org", "-o", "org_id", envvar="MERAKI_ORG_ID", help="Organization ID (or set MERAKI_ORG_ID)")
@click.option("--search", "-s", "search", is_flag=True, default=False, help="Search/list NAC client groups (default action when no other flags are given)")
@click.option("--search-query", "search_query", default=None, help="Fuzzy filter by group name")
@click.option("--group-id", "group_ids", multiple=True, help="Filter by group ID (repeatable)")
@click.option("--sort-key", "sort_key", default=None, type=click.Choice(SORT_KEYS), help="Sort field (default: name)")
@click.option("--sort-order", "sort_order", default=None, type=click.Choice(SORT_ORDERS), help="Sort order (default: ASC)")
@click.option("--per-page", "per_page", default=100, type=click.IntRange(3, 3000), help="Results per page, 3-3000 (default: 100)")
@click.option("--limit", "limit", default=0, type=int, help="Maximum number of groups to fetch (default: 0 = all)")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "jsonl", "yaml", "csv", "table"]), help="Search output format (default: json)")
@click.option("--create", "create_input", help="CSV file path, or a JSON object for a single group, to create (fields: name, description, members)")
@click.option("--update", "update_input", help="CSV file path, or a JSON object for a single group, to update (fields: id, name, description, membersAdd, membersRemove)")
@click.option("--delete", "delete_input", help="CSV file path, or a JSON object for a single group, to delete (field: id)")
@click.option("--delete-id", "delete_id", default=None, help="Delete a single group by ID")
@click.option("--workers", "-w", default=1, type=click.IntRange(1, 10), help="Number of concurrent workers, 1-10 (default: 1)")
@click.option("--timeout", "-t", default=30, type=int, help="HTTP timeout in seconds (default: 30)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(
    org_id: Optional[str],
    search: bool,
    search_query: Optional[str],
    group_ids: tuple[str, ...],
    sort_key: Optional[str],
    sort_order: Optional[str],
    per_page: int,
    limit: int,
    fmt: str,
    create_input: Optional[str],
    update_input: Optional[str],
    delete_input: Optional[str],
    delete_id: Optional[str],
    workers: int,
    timeout: int,
    verbose: bool,
) -> None:
    """Search, create, update, and delete Cisco Access Manager (CAM) NAC client groups.

    Manages NAC client groups through the org-scoped
    /organizations/{organizationId}/nac/clients/groups API. With no flags
    given, the default action is to search/export all groups.
    --create/--update/--delete each accept either a path to a CSV file or an
    inline JSON object for one group, and may be combined with each other or
    with --search in one run.

    \b
    Examples:
      cam-clients-groups.py --search
      cam-clients-groups.py --search --search-query Camera --format table
      cam-clients-groups.py --search --sort-key name --sort-order DESC --format csv > groups.csv
      cam-clients-groups.py --create groups.csv
      cam-clients-groups.py --create '{"name": "Cameras", "description": "Security cameras"}'
      cam-clients-groups.py -o 123456 --create groups.csv --workers 10 -v
      cam-clients-groups.py --update updates.csv
      cam-clients-groups.py --update '{"id": "627126248111341608", "description": "Updated"}'
      cam-clients-groups.py --update '{"id": "627126248111341608", "membersAdd": "1;2;3"}'
      cam-clients-groups.py --delete deletes.csv -w 20
      cam-clients-groups.py --delete '{"id": "627126248111341608"}'
      cam-clients-groups.py --delete-id 627126248111341608
    """
    if verbose:
        logger.setLevel(logging.INFO)

    # Default to search when no CRUD action is specified
    if not any([create_input, update_input, delete_input, delete_id]):
        search = True

    run_main(
        run(org_id, search, search_query, group_ids, sort_key, sort_order, per_page, limit, fmt,
            create_input, update_input, delete_input, delete_id, workers, timeout)
    )


if __name__ == "__main__":
    main()
