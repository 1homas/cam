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
Export CAM clients from Cisco Access Manager.

Fetches NAC clients from the Meraki Dashboard API and exports them in
JSON, YAML, CSV, or table format. Supports filtering by any attribute
using --filter key=value (repeatable, dot notation for nested fields).

Usage:
    cam-clients-export.py [--format json|yaml|csv|table] [--filter key=value]... [-v]
"""

import asyncio
import csv
import io
import json
import logging
import os
import sys
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import click
import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cam-client-export")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"

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


async def fetch_all_clients(client: httpx.AsyncClient, org_id: str) -> list[dict]:
    """Fetch NAC clients (limited to 1000 due to Meraki API bug).

    NOTE: The Meraki API has a pagination bug where it returns meta.totalCount
    but doesn't provide Link headers and returns 500 errors with startingAfter.
    This limits exports to 1000 clients maximum.

    For full client deletion (not export), use cam-clients-delete.py --loop instead.
    """
    clients: list[dict] = []
    params: dict = {"perPage": 1000}

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
        clients.extend(items)
        logger.info(f"Fetched {len(items)} clients (total: {len(clients)})")

        # Warn if API shows more items than returned
        if isinstance(data, dict) and 'meta' in data:
            meta = data['meta']
            total_count = meta.get('totalCount', 0)
            if total_count > len(clients):
                logger.warning(f"⚠️  API reports {total_count} total clients but only {len(clients)} exported (Meraki API limitation)")
                logger.warning(f"⚠️  Use cam-clients-delete.py --loop for full deletion of all clients")

        # Check for Link header pagination (not provided by Meraki API)
        link_header = response.headers.get("Link", "")
        next_token = parse_next_starting_after(link_header)
        if not next_token:
            break
        params["startingAfter"] = next_token

    return clients


def filter_clients(clients: list[dict], filters: list[tuple[str, str]]) -> list[dict]:
    """Filter clients by arbitrary key=value pairs. Supports dot notation for nested fields.

    Uses case-insensitive startswith matching.
    """
    result = clients
    for key, value in filters:
        value_lower = value.lower()
        result = [c for c in result if _get_nested(c, key).lower().startswith(value_lower)]
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
    "yaml": format_yaml,
    "csv": format_csv,
    "table": format_table,
}


async def run(fmt: str, filters: list[tuple[str, str]]) -> None:
    """Main export workflow."""
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")
    org_id = os.getenv("MERAKI_ORG_ID")

    if not api_key:
        logger.error("MERAKI_DASHBOARD_API_KEY not set")
        sys.exit(1)
    if not org_id:
        logger.error("MERAKI_ORG_ID not set")
        sys.exit(1)

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=httpx.Timeout(30.0),
    ) as client:
        start = time.time()
        all_clients = await fetch_all_clients(client, org_id)
        elapsed = time.time() - start
        logger.info(f"Fetched {len(all_clients)} clients in {elapsed:.1f}s")

        filtered = filter_clients(all_clients, filters)
        logger.info(f"After filtering: {len(filtered)} clients")

        formatter = FORMATTERS[fmt]
        output = formatter(filtered)
        if output:
            print(output)


def parse_filter(value: str) -> tuple[str, str]:
    """Parse a key=value filter string."""
    if "=" not in value:
        raise click.BadParameter(f"Filter must be key=value, got: {value}")
    key, _, val = value.partition("=")
    return (key.strip(), val.strip())


@click.command()
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "yaml", "csv", "table"]), help="Output format (default: json)")
@click.option("--filter", "-f", "filters", multiple=True, help="Filter by key=value (repeatable, supports dot notation)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(fmt: str, filters: tuple[str, ...], verbose: bool) -> None:
    """Export CAM clients from Cisco Access Manager.

    NOTE: Due to a Meraki API bug, only the first 1000 clients can be exported.
    For deletion of all clients, use cam-clients-delete.py --loop instead.

    \b
    Examples:
      cam-clients-export.py                          # Export first 1000 clients as JSON
      cam-clients-export.py --filter status=Connected
      cam-clients-export.py --format csv -f ssid=Guest -f source=Discovered
      cam-clients-export.py --filter classification.os=iOS
      cam-clients-export.py --format table -f owner=jsmith -v
    """
    if verbose:
        logger.setLevel(logging.INFO)
    parsed_filters = [parse_filter(f) for f in filters]
    asyncio.run(run(fmt, parsed_filters))


if __name__ == "__main__":
    main()
