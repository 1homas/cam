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
Search, create, update, and delete Meraki IAM (IdP) users.

Manages Meraki dashboard users through the organization-scoped IAM/IdP users
API (/organizations/{organizationId}/iam/users/idps/users and
/organizations/{organizationId}/iam/users/search). This is org-scoped, not
network-scoped, and represents dashboard/IdP end users (displayName, email,
password, sendPassword) rather than the network-scoped 802.1X/Guest/Client
VPN local authentication users (merakiAuthUsers).

Supports parallel operations with configurable concurrency and automatic
batching for create, update, and delete, each driven by a CSV file or an
inline JSON object for a single user. The idps/users resource itself has no
GET endpoint, so update and delete require an `id` for each user (the IdP
user id) obtained via --search or captured from a prior create response.
--search has no default IdP filter (searches across all IdPs); --idp-name
narrows it to one. --groups (POST .../iam/users/idps/groups/search) lists
available IdP groups, filterable the same way, to discover group IDs for
--group-id or the groupIds create field. Delete has no IdP field in its request at all (not
accepted by the API) — deleted users are always removed from the native
"802.1X" local users store regardless of --idp-name. Create accepts an
undocumented `accountType` field (defaults to "802.1X" if omitted) plus
`upn` and `groupIds`, none of which appear in the published Meraki OpenAPI
spec but are accepted by the live API.

The published OpenAPI spec marks no field as required and omits `upn`/
`accountType`/`groupIds` entirely, but the live API enforces its own
minimum for --create: `email`, `upn`, `displayName`, and `password` are
all required (accountType is required too, but this script defaults it
to "802.1X" so it's effectively optional). This script's own pre-flight
validation only checks for `email` — a request missing `upn`/`displayName`/
`password` will pass local validation and fail with a 400 from the API.
Password must be 12+ characters, contain no common words, and include at
least 3 of: uppercase, lowercase, number, symbol.

Usage:
    cam-users.py --org ORG_ID --search [FILTERS] [--format json|yaml|csv|table]
    cam-users.py --org ORG_ID --groups [FILTERS] [--format json|yaml|csv|table]
    cam-users.py --template
    cam-users.py --org ORG_ID --create (users.csv|'{"email": "..."}') [--workers N] [-v]
    cam-users.py --org ORG_ID --update (users.csv|'{"id": "...", ...}') [--workers N] [-v]
    cam-users.py --org ORG_ID --delete (users.csv|'{"id": "..."}'|USER_ID) [--workers N] [-v]

Examples:
    cam-users.py --search
    cam-users.py --search --query miles --format table
    cam-users.py --search --access-type Wireless --status Enabled --limit 50
    cam-users.py --search --group-id 4000 --group-id 4001 --format csv > users.csv
    cam-users.py --groups --format table
    cam-users.py --groups --query finance --idp-name "Azure AD IdP" --format csv
    cam-users.py --groups --idp-name 802.1X --format yaml
    cam-users.py --template > user.json
    cam-users.py --create users.csv
    cam-users.py --create '{"email": "mm@meraki.com", "upn": "mm@meraki.com", "displayName": "Miles Meraki", "password": "ChangeMe123!"}'
    cam-users.py --create '{"email": "mm@meraki.com", "upn": "mm@meraki.com", "displayName": "Miles Meraki", "password": "ChangeMe123!", "groupIds": ["627126248111344153"]}'
    cam-users.py -o 123456 --create users.csv --workers 10 -v
    cam-users.py --update updates.csv
    cam-users.py --delete deletes.csv -w 20
    cam-users.py --delete '{"id": "1284392014819"}'
    cam-users.py --delete 1284392014819
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

logger = logging.getLogger("cam-users")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)


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

BASE_URL = "https://api.meraki.com/api/v1"

CREATE_REQUIRED_COLUMNS = ["email"]
CREATE_OPTIONAL_COLUMNS = ["displayName", "upn", "password", "sendPassword", "accountType", "groupIds"]
DEFAULT_ACCOUNT_TYPE = "802.1X"

CREATE_TEMPLATE = {
    "email": "mm@meraki.com",
    "displayName": "Miles Meraki",
    "upn": "mm@meraki.com",
    "password": "ChangeMe123!",
    "sendPassword": False,
    "accountType": DEFAULT_ACCOUNT_TYPE,
    "groupIds": ["627126248111344153", "627126248111344152"],
}

UPDATE_REQUIRED_COLUMNS = ["id"]
UPDATE_OPTIONAL_COLUMNS = ["displayName", "email", "password", "sendPassword"]

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
    """Load rows from a CSV file path or an inline JSON object for a single user.

    A value starting with '{' is parsed as a single-user JSON object;
    otherwise it is treated as a path to a CSV file.
    """
    stripped = value.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"Invalid JSON: {e}")
        if not isinstance(obj, dict):
            raise click.BadParameter("JSON block must be a single user object")
        row = {k: v for k, v in obj.items() if v not in (None, "")}
        return [row]

    path = Path(stripped)
    if not path.exists():
        raise click.BadParameter(f"File not found: {stripped}")
    return parse_csv(path.read_text())


def load_delete_rows(value: str) -> list[dict]:
    """Load delete targets from a CSV file path, an inline JSON object, or a bare user ID.

    A value starting with '{' is parsed as a single-user JSON object; a value
    matching an existing file path is parsed as CSV; anything else is treated
    as a literal user ID.
    """
    stripped = value.strip()
    if stripped.startswith("{"):
        return load_rows(stripped)

    path = Path(stripped)
    if path.exists():
        return parse_csv(path.read_text())

    return [{"id": stripped}]


def validate_create_row(row: dict, row_num: int) -> list[str]:
    """Validate a create row. Returns list of error messages."""
    errors = []
    if not row.get("email"):
        errors.append(f"Row {row_num}: Missing required field 'email'")
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


def _to_bool(value) -> bool:
    """Coerce a CSV string or JSON boolean to bool."""
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes")


def build_create_payload(row: dict) -> dict:
    """Build a create-user API payload from a CSV row or JSON object."""
    payload = {"email": row["email"], "accountType": row.get("accountType") or DEFAULT_ACCOUNT_TYPE}
    if row.get("displayName"):
        payload["displayName"] = row["displayName"]
    if row.get("upn"):
        payload["upn"] = row["upn"]
    if row.get("password"):
        payload["password"] = row["password"]
    if row.get("sendPassword") is not None and row.get("sendPassword") != "":
        payload["sendPassword"] = _to_bool(row["sendPassword"])
    if row.get("groupIds"):
        group_ids = row["groupIds"]
        if isinstance(group_ids, str):
            group_ids = [g.strip() for g in group_ids.split(";") if g.strip()]
        payload["groupIds"] = list(group_ids)
    return payload


def build_update_payload(row: dict) -> dict:
    """Build an update-user API payload from a CSV row or JSON object (excludes 'id')."""
    payload = {}
    if row.get("displayName"):
        payload["displayName"] = row["displayName"]
    if row.get("email"):
        payload["email"] = row["email"]
    if row.get("password"):
        payload["password"] = row["password"]
    if row.get("sendPassword") is not None and row.get("sendPassword") != "":
        payload["sendPassword"] = _to_bool(row["sendPassword"])
    return payload


def chunk_rows(rows: list, chunk_size: int = 100):
    """Split rows into chunks for batching."""
    for i in range(0, len(rows), chunk_size):
        yield rows[i : i + chunk_size]


ACCESS_TYPES = ["Client VPN", "IKEv2 Client VPN", "Remote Access", "Self-service portal", "Switch", "Trusted Access", "Wireless", "Wireless 802.1x"]
STATUSES = ["Disabled", "Enabled", "Mixed"]
SORT_KEYS = ["created_at", "updated_at", "username"]
SORT_ORDERS = ["asc", "desc"]

SEARCH_CSV_COLUMNS = ["id", "displayName", "email", "username", "accessTypes", "groups", "idp.name", "idp.type", "upn", "userId", "createdAt", "lastUpdatedAt", "externalId"]
GROUP_CSV_COLUMNS = ["groupId", "name", "memberships", "idp.name", "idp.type", "externalId"]


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


def build_search_body(filters: dict, per_page: int) -> dict:
    """Build the request body for the users search endpoint from CLI filters."""
    body: dict = {"perPage": per_page}
    for key in ("searchQuery", "sortKey", "sortOrder"):
        if filters.get(key):
            body[key] = filters[key]
    for key in ("accessTypes", "groupIds", "idpIds", "statuses", "userIds"):
        if filters.get(key):
            body[key] = list(filters[key])
    return body


def flatten_search_results(items: list[dict]) -> list[dict]:
    """Flatten nested search results into one row per IdP user."""
    rows = []
    for item in items:
        upn = item.get("upn")
        user_id = item.get("userId")
        for idp_user in item.get("idpUsers", []):
            idp = idp_user.get("idp") or {}
            groups = idp_user.get("groups") or []
            rows.append({
                "id": idp_user.get("id"),
                "displayName": idp_user.get("displayName"),
                "email": idp_user.get("email"),
                "username": idp_user.get("username"),
                "accessTypes": ";".join(idp_user.get("accessTypes") or []),
                "groups": ";".join(g.get("name", "") for g in groups),
                "idp.name": idp.get("name"),
                "idp.type": idp.get("type"),
                "upn": upn,
                "userId": user_id,
                "createdAt": idp_user.get("createdAt"),
                "lastUpdatedAt": idp_user.get("lastUpdatedAt"),
                "externalId": idp_user.get("externalId"),
            })
    return rows


async def search_users(client: httpx.AsyncClient, org_id: str, filters: dict, per_page: int, limit: int) -> list[dict]:
    """Search IAM/IdP users, following Link header pagination until limit or exhaustion."""
    rows: list[dict] = []
    body = build_search_body(filters, per_page)

    while True:
        response = await _request_with_retry(
            client, "POST", f"{BASE_URL}/organizations/{org_id}/iam/users/search", json=body
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") or []
        page_rows = flatten_search_results(items)

        if limit > 0:
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            page_rows = page_rows[:remaining]

        rows.extend(page_rows)
        logger.info(f"Fetched {len(page_rows)} users (total: {len(rows)})")

        if limit > 0 and len(rows) >= limit:
            break

        starting_after = parse_next_starting_after(response.headers.get("Link", ""))
        if not starting_after or not items:
            break
        body["startingAfter"] = starting_after

    return rows


def build_group_search_body(filters: dict, per_page: int) -> dict:
    """Build the request body for the IdP groups search endpoint from CLI filters."""
    body: dict = {"perPage": per_page}
    if filters.get("searchQuery"):
        body["searchQuery"] = filters["searchQuery"]
    if filters.get("idpIds"):
        body["idpIds"] = list(filters["idpIds"])
    return body


def flatten_group_results(items: list[dict]) -> list[dict]:
    """Flatten IdP group search results into one row per group."""
    rows = []
    for item in items:
        idp = item.get("idp") or {}
        rows.append({
            "groupId": item.get("groupId"),
            "name": item.get("name"),
            "memberships": item.get("memberships"),
            "idp.name": idp.get("name"),
            "idp.type": idp.get("type"),
            "externalId": item.get("externalId"),
        })
    return rows


async def search_groups(client: httpx.AsyncClient, org_id: str, filters: dict, per_page: int, limit: int) -> list[dict]:
    """Search IdP groups, following Link header pagination until limit or exhaustion."""
    rows: list[dict] = []
    body = build_group_search_body(filters, per_page)

    while True:
        response = await _request_with_retry(
            client, "POST", f"{BASE_URL}/organizations/{org_id}/iam/users/idps/groups/search", json=body
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") or []
        page_rows = flatten_group_results(items)

        if limit > 0:
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            page_rows = page_rows[:remaining]

        rows.extend(page_rows)
        logger.info(f"Fetched {len(page_rows)} groups (total: {len(rows)})")

        if limit > 0 and len(rows) >= limit:
            break

        starting_after = parse_next_starting_after(response.headers.get("Link", ""))
        if not starting_after or not items:
            break
        body["startingAfter"] = starting_after

    return rows


async def fetch_all_idps(client: httpx.AsyncClient, org_id: str, per_page: int = 50) -> list[dict]:
    """Fetch all identity providers for an organization via idps/search, following Link header pagination."""
    idps: list[dict] = []
    body: dict = {"perPage": per_page}

    while True:
        response = await _request_with_retry(
            client, "POST", f"{BASE_URL}/organizations/{org_id}/iam/users/idps/search", json=body
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") or []
        idps.extend(items)

        starting_after = parse_next_starting_after(response.headers.get("Link", ""))
        if not starting_after or not items:
            break
        body["startingAfter"] = starting_after

    return idps


def resolve_idp_ids_by_name(idps: list[dict], names: list[str]) -> list[str]:
    """Match IdP names (case-insensitive) to their ids. Logs a warning for unmatched names."""
    name_to_id = {idp.get("name", "").lower(): idp.get("idpId") for idp in idps}
    ids = []
    for name in names:
        idp_id = name_to_id.get(name.lower())
        if idp_id:
            ids.append(idp_id)
        else:
            logger.warning(f"No IdP found with name '{name}'")
    return ids


def format_search_json(rows: list[dict]) -> str:
    return json.dumps(rows, indent=2, default=str)


def format_search_jsonl(rows: list[dict]) -> str:
    """Format rows as JSON Lines (one compact JSON object per line)."""
    return "\n".join(json.dumps(row, default=str) for row in rows)


def format_search_yaml(rows: list[dict]) -> str:
    return yaml.dump(rows, default_flow_style=False, sort_keys=False)


def format_search_csv(rows: list[dict], columns: list[str] = SEARCH_CSV_COLUMNS) -> str:
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(col, "") for col in columns])
    return output.getvalue()


def format_search_table(rows: list[dict], columns: list[str] = SEARCH_CSV_COLUMNS) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "") or "") for col in columns) + " |")
    return "\n".join(lines)


SEARCH_FORMATTERS = {
    "json": format_search_json,
    "jsonl": format_search_jsonl,
    "yaml": format_search_yaml,
    "csv": format_search_csv,
    "table": format_search_table,
}

GROUP_FORMATTERS = {
    "json": format_search_json,
    "jsonl": format_search_jsonl,
    "yaml": format_search_yaml,
    "csv": lambda rows: format_search_csv(rows, GROUP_CSV_COLUMNS),
    "table": lambda rows: format_search_table(rows, GROUP_CSV_COLUMNS),
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


async def create_user(client: httpx.AsyncClient, org_id: str, payload: dict) -> dict:
    """Create a single Meraki IAM/IdP user. Returns dict with success status and details."""
    try:
        response = await _request_with_retry(
            client, "POST", f"{BASE_URL}/organizations/{org_id}/iam/users/idps/users", json=payload
        )
        response.raise_for_status()
        result = response.json()
        return {
            "success": True,
            "idpUserId": result.get("idpUserId"),
            "email": payload["email"],
        }
    except Exception as e:
        logger.error(f"Failed to create user {payload.get('email')}: {e}")
        return {"success": False, "email": payload.get("email"), "error": str(e)}


async def update_user(client: httpx.AsyncClient, org_id: str, user_id: str, payload: dict) -> dict:
    """Update a single Meraki IAM/IdP user. Returns dict with success status and details."""
    try:
        response = await _request_with_retry(
            client, "PUT", f"{BASE_URL}/organizations/{org_id}/iam/users/idps/users/{user_id}", json=payload
        )
        response.raise_for_status()
        result = response.json()
        return {"success": True, "id": user_id, "idpUserId": result.get("idpUserId")}
    except Exception as e:
        logger.error(f"Failed to update user {user_id}: {e}")
        return {"success": False, "id": user_id, "error": str(e)}


async def delete_user(client: httpx.AsyncClient, org_id: str, user_id: str) -> dict:
    """Delete a single Meraki IAM/IdP user. Returns dict with success status and details."""
    try:
        response = await _request_with_retry(
            client, "DELETE", f"{BASE_URL}/organizations/{org_id}/iam/users/idps/users/{user_id}"
        )
        response.raise_for_status()
        return {"success": True, "id": user_id}
    except Exception as e:
        logger.error(f"Failed to delete user {user_id}: {e}")
        return {"success": False, "id": user_id, "error": str(e)}


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
        logger.info(f"Creating chunk {i} ({len(chunk)} users)...")
        results = await run_batch(lambda p: create_user(client, org_id, p), chunk, semaphore)
        succeeded = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        total_succeeded += len(succeeded)
        total_failed += len(failed)
        for result in failed[:3]:
            logger.error(f"  Failed: {result['email']} - {result.get('error', 'Unknown error')}")

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
        logger.info(f"Updating chunk {i} ({len(chunk)} users)...")
        results = await run_batch(lambda u: update_user(client, org_id, u[0], u[1]), chunk, semaphore)
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
        logger.info(f"Deleting chunk {i} ({len(chunk)} users)...")
        results = await run_batch(lambda uid: delete_user(client, org_id, uid), chunk, semaphore)
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
    search_filters: dict,
    groups: bool,
    group_filters: dict,
    per_page: int,
    limit: int,
    fmt: str,
    create_input: Optional[str],
    update_input: Optional[str],
    delete_input: Optional[str],
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
        headers={"X-Cisco-Meraki-API-Key": api_key, "Content-Type": "application/json"},
        timeout=httpx.Timeout(float(timeout)),
    ) as client:
        if search:
            idp_names = search_filters.pop("idpNames", None)
            if idp_names:
                idps = await fetch_all_idps(client, org_id)
                resolved_ids = resolve_idp_ids_by_name(idps, list(idp_names))
                search_filters["idpIds"] = list(search_filters.get("idpIds") or []) + resolved_ids
            rows = await search_users(client, org_id, search_filters, per_page, limit)
            logger.info(f"Found {len(rows)} IdP user(s)")
            output = SEARCH_FORMATTERS[fmt](rows)
            if output:
                print(output)
        if groups:
            idp_names = group_filters.pop("idpNames", None)
            if idp_names:
                idps = await fetch_all_idps(client, org_id)
                resolved_ids = resolve_idp_ids_by_name(idps, list(idp_names))
                group_filters["idpIds"] = list(group_filters.get("idpIds") or []) + resolved_ids
            rows = await search_groups(client, org_id, group_filters, per_page, limit)
            logger.info(f"Found {len(rows)} group(s)")
            output = GROUP_FORMATTERS[fmt](rows)
            if output:
                print(output)
        if create_input:
            exit_code |= await run_create(client, org_id, create_input, semaphore)
        if update_input:
            exit_code |= await run_update(client, org_id, update_input, semaphore)
        if delete_input:
            rows = load_delete_rows(delete_input)
            errors = []
            for i, row in enumerate(rows, start=2):
                errors.extend(validate_delete_row(row, i))
            if errors:
                for error in errors[:10]:
                    logger.error(f"  {error}")
                exit_code = 1
            else:
                exit_code |= await run_delete(client, org_id, [row["id"] for row in rows], semaphore)

    if exit_code:
        sys.exit(exit_code)


@click.command()
@click.option("--org", "-o", "org_id", envvar="MERAKI_ORG_ID", help="Organization ID (or set MERAKI_ORG_ID)")
@click.option("--search", "-s", "search", is_flag=True, default=False, help="Search/list IAM/IdP users")
@click.option("--groups", "-g", "groups", is_flag=True, default=False, help="Search/list IdP groups (for use as --group-id values). Filterable with --query, --idp-id, --idp-name")
@click.option("--query", "-q", "search_query", default=None, help="Fuzzy filter by display name, username, and email (--search) or group name (--groups)")
@click.option("--access-type", "-a", "access_types", multiple=True, type=click.Choice(ACCESS_TYPES), help="Filter by access type (repeatable)")
@click.option("--group-id", "-G", "group_ids", multiple=True, help="Filter by IdP group ID (repeatable)")
@click.option("--idp-id", "-i", "idp_ids", multiple=True, help="Filter by identity provider ID (repeatable)")
@click.option("--idp-name", "-I", "idp_names", multiple=True, help="Filter by identity provider name (repeatable, resolved to IdP id via idps/search). Default: none, searches across all IdPs.")
@click.option("--status", "-S", "statuses", multiple=True, type=click.Choice(STATUSES), help="Filter by user status (repeatable)")
@click.option("--user-id", "-u", "user_ids", multiple=True, help="Filter by end user ID (repeatable)")
@click.option("--sort-key", "-k", "sort_key", default=None, type=click.Choice(SORT_KEYS), help="Sort field (default: username)")
@click.option("--sort-order", "-r", "sort_order", default=None, type=click.Choice(SORT_ORDERS), help="Sort order (default: asc)")
@click.option("--per-page", "-p", "per_page", default=10, type=click.IntRange(3, 50), metavar="[3-50]", help="Results per page, 3-50 (default: 10)")
@click.option("--limit", "-l", "limit", default=0, type=int, help="Maximum number of users to fetch (default: 0 = all)")
@click.option("--format", "-f", "fmt", default="json", type=click.Choice(["json", "jsonl", "yaml", "csv", "table"]), help="Search output format (default: json)")
@click.option("--create", "-c", "create_input", help="CSV file path, or a JSON object for a single user, to create. Required by the live API: email, upn, displayName, password (12+ chars, no common words, 3 of upper/lower/number/symbol); accountType defaults to 802.1X if omitted. Optional: sendPassword, groupIds")
@click.option("--template", "-t", "template", is_flag=True, default=False, help="Print a JSON template for --create and exit")
@click.option("--update", "-U", "update_input", help="CSV file path, or a JSON object for a single user, to update (fields: id, displayName, email, password, sendPassword). If set, password must be 12+ chars, no common words, 3 of upper/lower/number/symbol")
@click.option("--delete", "-d", "delete_input", help="CSV file path, a JSON object (field: id), or a bare user ID, to delete")
@click.option("--workers", "-w", default=1, type=click.IntRange(1, 10), help="Number of concurrent workers, 1-10 (default: 1)")
@click.option("--timeout", default=30, type=int, help="HTTP timeout in seconds (default: 30)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(
    org_id: Optional[str],
    search: bool,
    groups: bool,
    search_query: Optional[str],
    access_types: tuple[str, ...],
    group_ids: tuple[str, ...],
    idp_ids: tuple[str, ...],
    idp_names: tuple[str, ...],
    statuses: tuple[str, ...],
    user_ids: tuple[str, ...],
    sort_key: Optional[str],
    sort_order: Optional[str],
    per_page: int,
    limit: int,
    fmt: str,
    create_input: Optional[str],
    template: bool,
    update_input: Optional[str],
    delete_input: Optional[str],
    workers: int,
    timeout: int,
    verbose: bool,
) -> None:
    """Search, create, update, and delete Meraki IAM (IdP) users.

    Manages dashboard/IdP users through the org-scoped
    /organizations/{organizationId}/iam/users/idps/users and .../iam/users/search
    APIs. At least one of --search, --groups, --create, --update,
    --delete, or --template is required; multiple may be
    combined in one run. --create/--update accept either a path to a CSV
    file or an inline JSON object for one user. --delete additionally
    accepts a bare user ID.

    \b
    Examples:
      cam-users.py --search
      cam-users.py --search --query miles --format table
      cam-users.py --search --access-type Wireless --status Enabled --limit 50
      cam-users.py --search --idp-name "Azure AD IdP" --format table
      cam-users.py --search --group-id 4000 --group-id 4001 --format csv > users.csv
      cam-users.py --groups --format table
      cam-users.py --groups --query finance --idp-name "Azure AD IdP" --format csv
      cam-users.py --groups --idp-name 802.1X --format yaml
      cam-users.py --template > user.json
      cam-users.py --create users.csv
      cam-users.py --create '{"email": "mm@meraki.com", "upn": "mm@meraki.com", "displayName": "Miles Meraki", "password": "ChangeMe123!"}'
      cam-users.py -o 123456 --create users.csv --workers 10 -v
      cam-users.py --update updates.csv
      cam-users.py --update '{"id": "1284392014819", "displayName": "Miles Meraki"}'
      cam-users.py --delete deletes.csv -w 20
      cam-users.py --delete '{"id": "1284392014819"}'
      cam-users.py --delete 1284392014819
    """
    if verbose:
        logger.setLevel(logging.INFO)

    if template:
        click.echo(json.dumps(CREATE_TEMPLATE, indent=2))
        sys.exit(0)

    if not any([search, groups, create_input, update_input, delete_input]):
        click.echo("Error: specify at least one of --search, --groups, --create, --update, --delete, --template", err=True)
        sys.exit(1)

    search_filters = {
        "searchQuery": search_query,
        "accessTypes": access_types,
        "groupIds": group_ids,
        "idpIds": idp_ids,
        "idpNames": idp_names,
        "statuses": statuses,
        "userIds": user_ids,
        "sortKey": sort_key,
        "sortOrder": sort_order,
    }

    group_filters = {
        "searchQuery": search_query,
        "idpIds": idp_ids,
        "idpNames": idp_names,
    }

    run_main(
        run(org_id, search, search_filters, groups, group_filters, per_page, limit, fmt, create_input, update_input, delete_input, workers, timeout)
    )


if __name__ == "__main__":
    main()
