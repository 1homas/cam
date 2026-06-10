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
List all NAC clients from Cisco Access Manager.

Fetches NAC clients from the Meraki Dashboard API and displays them in
JSON, YAML, CSV, or table format.

Usage:
    cam-clients.py [--format json|yaml|csv|table] [-v]
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

logger = logging.getLogger("cam-clients")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    )
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"

CSV_COLUMNS = [
    "id",
    "mac",
    "status",
    "ssid",
    "type",
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
    """Fetch all NAC clients with pagination support.

    NOTE: The Meraki API has a pagination bug where it may return meta.totalCount
    but doesn't reliably provide Link headers. This fetches as many as possible.
    """
    clients: list[dict] = []
    params: dict = {"perPage": 1000}

    while True:
        response = await client.get(
            f"{BASE_URL}/organizations/{org_id}/nac/clients", params=params
        )

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
        if isinstance(data, dict) and "meta" in data:
            meta = data["meta"]
            total_count = meta.get("totalCount", 0)
            if total_count > len(clients):
                logger.warning(
                    f"⚠️  API reports {total_count} total clients but only {len(clients)} fetched"
                )

        # Check for Link header pagination
        link_header = response.headers.get("Link", "")
        next_token = parse_next_starting_after(link_header)
        if not next_token:
            break
        params["startingAfter"] = next_token

    return clients


def format_json(clients: list[dict]) -> str:
    """Format clients as JSON."""
    return json.dumps(clients, indent=2, default=str)


def format_yaml(clients: list[dict]) -> str:
    """Format clients as YAML."""
    return yaml.dump(clients, default_flow_style=False, sort_keys=False)


def format_csv(clients: list[dict]) -> str:
    """Format clients as CSV."""
    if not clients:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for c in clients:
        writer.writerow(c)
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
        row = [str(c.get(col, "")) for col in CSV_COLUMNS]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


FORMATTERS = {
    "json": format_json,
    "yaml": format_yaml,
    "csv": format_csv,
    "table": format_table,
}


async def run(fmt: str) -> None:
    """Main workflow to list all clients."""
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")
    org_id = os.getenv("MERAKI_ORG_ID")

    if not api_key:
        logger.error("MERAKI_DASHBOARD_API_KEY not set")
        sys.exit(1)
    if not org_id:
        logger.error("MERAKI_ORG_ID not set")
        sys.exit(1)

    async with httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(30.0),
    ) as client:
        start = time.time()
        all_clients = await fetch_all_clients(client, org_id)
        elapsed = time.time() - start
        logger.info(f"Fetched {len(all_clients)} clients in {elapsed:.1f}s")

        formatter = FORMATTERS[fmt]
        output = formatter(all_clients)
        if output:
            print(output)


@click.command()
@click.option(
    "--format",
    "fmt",
    default="json",
    type=click.Choice(["json", "yaml", "csv", "table"]),
    help="Output format (default: json)",
)
@click.option(
    "-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging"
)
def main(fmt: str, verbose: bool) -> None:
    """List all NAC clients from Cisco Access Manager.

    \b
    Examples:
      cam-clients.py                    # List all clients as JSON
      cam-clients.py --format table     # Pretty-printed ASCII table
      cam-clients.py --format csv       # CSV format
      cam-clients.py -v                 # Verbose logging
    """
    if verbose:
        logger.setLevel(logging.INFO)
    asyncio.run(run(fmt))


if __name__ == "__main__":
    main()
