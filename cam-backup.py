#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "click>=8.0.0",
# ]
# ///
"""
Backup Cisco Access Manager (CAM) data using Meraki Dashboard API.

Fetches NAC data from multiple endpoints and saves JSON files to a
date-stamped backup directory. Supports pagination for large datasets.

Usage:
    cam-backup.py [--org ORG_ID] [--dir DIRECTORY] [-v]

Examples:
    cam-backup.py                    # Backup to ./backups/YYYY-MM-DD/
    cam-backup.py --org 123456       # Override MERAKI_ORG_ID
    cam-backup.py --dir /data        # Backup to /data/backups/YYYY-MM-DD/
    cam-backup.py -v                 # Verbose logging
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import click
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cam-backup")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"))
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"


def get_backup_endpoints() -> list[dict[str, str]]:
    """Return list of CAM API endpoints to back up.

    Returns:
        List of endpoint definitions with name, path, and filename
    """
    return [
        {
            "name": "clients",
            "path": "/organizations/{orgId}/nac/clients",
            "filename": "clients.json",
        },
        {
            "name": "client_groups",
            "path": "/organizations/{orgId}/nac/clients/groups",
            "filename": "client_groups.json",
        },
        {
            "name": "clients_overview",
            "path": "/organizations/{orgId}/nac/clients/overview",
            "filename": "clients_overview.json",
        },
        {
            "name": "authorization_policies",
            "path": "/organizations/{orgId}/nac/authorization/policies",
            "filename": "authorization_policies.json",
        },
        {
            "name": "certificates",
            "path": "/organizations/{orgId}/nac/certificates",
            "filename": "certificates.json",
        },
        {
            "name": "certificates_overview",
            "path": "/organizations/{orgId}/nac/certificates/overview",
            "filename": "certificates_overview.json",
        },
        {
            "name": "certificate_crls",
            "path": "/organizations/{orgId}/nac/certificates/authorities/crls/descriptors",
            "filename": "certificate_crls.json",
        },
        {
            "name": "dictionaries",
            "path": "/organizations/{orgId}/nac/dictionaries",
            "filename": "dictionaries.json",
        },
        {
            "name": "license_usage",
            "path": "/organizations/{orgId}/nac/license/usage",
            "filename": "license_usage.json",
        },
    ]


def create_backup_dir(base_dir: str = ".") -> Path:
    """Create backup directory structure for today's date.

    Args:
        base_dir: Base directory where backups will be stored

    Returns:
        Path to today's backup directory
    """
    today = date.today().strftime("%Y-%m-%d")
    backup_dir = Path(base_dir) / "backups" / today
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def save_json(data: dict[str, Any], filepath: Path) -> None:
    """Save data as JSON to file.

    Args:
        data: Dictionary to save
        filepath: Destination file path
    """
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)


def parse_next_starting_after(link_header: str) -> Optional[str]:
    """Extract startingAfter token from Link header.

    Args:
        link_header: HTTP Link header value

    Returns:
        Next page token or None
    """
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


async def fetch_endpoint(client: httpx.AsyncClient, org_id: str, endpoint: dict[str, str]) -> dict[str, Any]:
    """Fetch data from a single endpoint with pagination support.

    Args:
        client: HTTP client
        org_id: Organization ID
        endpoint: Endpoint definition dict

    Returns:
        Dictionary with status and data/error
    """
    path = endpoint["path"].replace("{orgId}", org_id)
    url = f"{BASE_URL}{path}"
    all_items: list[dict] = []
    # Only use perPage for clients endpoint (large dataset)
    if endpoint["name"] == "clients":
        params: dict = {"perPage": 1000}
    elif endpoint["name"] == "license_usage":
        # startDate is required by this endpoint; default to the trailing 30 days
        now = datetime.now(timezone.utc)
        params = {
            "startDate": (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDate": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    else:
        params = {}
    page_count = 0

    try:
        while True:
            response = await client.get(url, params=params)

            # Handle rate limiting
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 1))
                logger.warning(f"Rate limited on {endpoint['name']}, sleeping {retry_after}s")
                await asyncio.sleep(retry_after)
                continue

            response.raise_for_status()
            data = response.json()

            # Handle different response formats
            if isinstance(data, dict):
                if "items" in data:
                    all_items.extend(data["items"])
                    # Preserve metadata from first page
                    if page_count == 0:
                        result_data = {"items": all_items, "meta": data.get("meta", {})}
                    else:
                        result_data["items"] = all_items
                else:
                    # Non-paginated response (e.g., overview endpoints)
                    return {"status": "success", "data": data, "pages": 1}
            elif isinstance(data, list):
                all_items.extend(data)
                result_data = all_items
            else:
                result_data = data

            page_count += 1
            # Don't print pagination dots since we'll overwrite the line

            # Check for pagination
            link_header = response.headers.get("Link", "")
            next_token = parse_next_starting_after(link_header)
            if not next_token:
                break
            params["startingAfter"] = next_token

        return {"status": "success", "data": result_data, "pages": page_count}

    except httpx.HTTPStatusError as e:
        logger.info(f"HTTP error for {endpoint['name']}: {e.response.status_code}")
        return {"status": "error", "error": str(e), "status_code": e.response.status_code}
    except Exception as e:
        logger.info(f"Error fetching {endpoint['name']}: {e}")
        return {"status": "error", "error": str(e)}


async def backup_all_endpoints(
    client: httpx.AsyncClient, org_id: str, backup_dir: Path, endpoints: list[dict[str, str]]
) -> dict[str, Any]:
    """Fetch and save all endpoints.

    Args:
        client: HTTP client
        org_id: Organization ID
        backup_dir: Directory to save backups
        endpoints: List of endpoint definitions

    Returns:
        Summary statistics
    """
    stats = {"success": 0, "failed": 0, "total": len(endpoints), "errors": []}

    for endpoint in endpoints:
        print(f"{endpoint['name']}: ", end="", flush=True, file=sys.stderr)

        result = await fetch_endpoint(client, org_id, endpoint)

        if result["status"] == "success":
            print("\r✅ " + endpoint["name"], file=sys.stderr)
            filepath = backup_dir / endpoint["filename"]
            save_json(result["data"], filepath)
            stats["success"] += 1
            logger.info(f"Saved {endpoint['name']} to {filepath} ({result.get('pages', 1)} pages)")
        else:
            print("\r❌ " + endpoint["name"], file=sys.stderr)
            stats["failed"] += 1
            stats["errors"].append({"endpoint": endpoint["name"], "error": result.get("error", "Unknown error")})
            logger.info(f"Failed to backup {endpoint['name']}: {result.get('error')}")

    print(file=sys.stderr)  # Final newline
    return stats


async def run(org_id: Optional[str], backup_base_dir: str) -> None:
    """Main backup workflow.

    Args:
        org_id: Organization ID
        backup_base_dir: Base directory for backups
    """
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")

    if not api_key:
        logger.error("MERAKI_DASHBOARD_API_KEY not set")
        sys.exit(1)
    if not org_id:
        logger.error("Organization ID required (--org or MERAKI_ORG_ID)")
        sys.exit(1)

    # Create backup directory
    backup_dir = create_backup_dir(backup_base_dir)
    print(f"Backing up to: {backup_dir}", file=sys.stderr)

    endpoints = get_backup_endpoints()

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=httpx.Timeout(30.0),
    ) as client:
        start = time.time()
        stats = await backup_all_endpoints(client, org_id, backup_dir, endpoints)
        elapsed = time.time() - start

        print(f"\n✅ Backup complete!", file=sys.stderr)
        print(f"   Success: {stats['success']}/{stats['total']}", file=sys.stderr)
        print(f"   Failed: {stats['failed']}/{stats['total']}", file=sys.stderr)
        print(f"   Time: {elapsed:.1f}s", file=sys.stderr)
        print(f"   Location: {backup_dir}", file=sys.stderr)

        if stats["errors"]:
            print("\nErrors:", file=sys.stderr)
            for err in stats["errors"]:
                print(f"  - {err['endpoint']}: {err['error']}", file=sys.stderr)


@click.command()
@click.option("--org", "-o", "org_id", envvar="MERAKI_ORG_ID", help="Organization ID (or set MERAKI_ORG_ID)")
@click.option("--dir", "backup_dir", default=".", help="Base directory for backups (default: current directory)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(org_id: Optional[str], backup_dir: str, verbose: bool) -> None:
    """Backup Cisco Access Manager data to JSON files.

    Creates a date-stamped directory under backups/ and fetches data from
    multiple NAC endpoints. Shows progress with status indicators:
      ✅ Success (HTTP 2xx)
      ❌ Error (HTTP 4xx/5xx)
      . Pagination (additional pages)

    \b
    Examples:
      cam-backup.py                    # Backup to ./backups/YYYY-MM-DD/
      cam-backup.py --org 123456       # Override MERAKI_ORG_ID
      cam-backup.py --dir /data        # Backup to /data/backups/YYYY-MM-DD/
      cam-backup.py -v                 # Verbose logging
    """
    if verbose:
        logger.setLevel(logging.INFO)
    asyncio.run(run(org_id, backup_dir))


if __name__ == "__main__":
    main()
