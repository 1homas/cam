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
Download all clients from all networks in a Meraki organization.

Fetches every network in the organization, then asynchronously fetches
clients from each network (GET /networks/{networkId}/clients) using the
Meraki Dashboard API. Exports the combined results in CSV (default), JSON,
YAML, or Markdown table format. Supports filtering by any attribute using
--filter key=value (repeatable, dot notation for nested fields).

Usage:
    cam-network-clients.py [--format csv|json|yaml|table] [--filter key=value]... [-v]

Examples:
    cam-network-clients.py                          # All clients, all networks, CSV
    cam-network-clients.py > all-clients.csv        # Download all clients as CSV (default)
    cam-network-clients.py --format json            # Export as JSON
    cam-network-clients.py -n N_123456789            # Single network only
    cam-network-clients.py --concurrency 10          # Fetch 10 networks at once
    cam-network-clients.py --timespan 86400          # Clients seen in the last day
    cam-network-clients.py --filter status=Online
    cam-network-clients.py --filter status=Online -f ssid=Guest  # Online clients on a specific SSID
    cam-network-clients.py --format table -f ssid=Guest -v
    cam-network-clients.py --limit 100 --format table  # Quick preview per network
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

logger = logging.getLogger("cam-network-clients")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"
MAX_TIMESPAN = 2678400  # 31 days, the Meraki API maximum

# (header, field path) pairs. Leads with MAC address / Endpoint device group /
# Description to match the CAM CSV template convention (references/csv/*.csv);
# "Endpoint device group" has no field on network clients, so it's always blank.
CSV_COLUMNS: list[tuple[str, Optional[str]]] = [
    ("MAC address", "mac"),
    ("Endpoint device group", None),
    ("Description", "description"),
    ("_networkId", "_networkId"),
    ("_networkName", "_networkName"),
    ("id", "id"),
    ("ip", "ip"),
    ("ip6", "ip6"),
    ("vlan", "vlan"),
    ("namedVlan", "namedVlan"),
    ("ssid", "ssid"),
    ("switchport", "switchport"),
    ("status", "status"),
    ("os", "os"),
    ("manufacturer", "manufacturer"),
    ("user", "user"),
    ("usage.sent", "usage.sent"),
    ("usage.recv", "usage.recv"),
    ("recentDeviceConnection", "recentDeviceConnection"),
    ("recentDeviceName", "recentDeviceName"),
    ("firstSeen", "firstSeen"),
    ("lastSeen", "lastSeen"),
    ("notes", "notes"),
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


async def fetch_all_networks(client: httpx.AsyncClient, org_id: str, batch_size: int = 1000) -> list[dict]:
    """Fetch all networks in an organization, following Link header pagination.

    Args:
        client: HTTP client
        org_id: Organization ID
        batch_size: Number of networks per API request (default: 1000)

    Returns:
        List of network dictionaries with id, name, and other metadata
    """
    networks: list[dict] = []
    params: dict = {"perPage": batch_size}

    while True:
        response = await client.get(f"{BASE_URL}/organizations/{org_id}/networks", params=params)

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 1))
            logger.warning(f"Rate limited, sleeping {retry_after}s")
            await asyncio.sleep(retry_after)
            continue

        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list):
            items = []

        if not items:
            break

        networks.extend(items)
        logger.info(f"Fetched {len(items)} networks (total: {len(networks)})")

        link_header = response.headers.get("Link", "")
        starting_after = parse_next_starting_after(link_header)
        if not starting_after:
            break
        params["startingAfter"] = starting_after

    return networks


async def fetch_network_clients(
    client: httpx.AsyncClient,
    network_id: str,
    timespan: float = MAX_TIMESPAN,
    batch_size: int = 1000,
    limit: int = 0,
) -> list[dict]:
    """Fetch all clients seen on a single network within the given timespan.

    Args:
        client: HTTP client
        network_id: Network ID
        timespan: Lookback window in seconds (max: 2678400 / 31 days)
        batch_size: Number of clients per API request (default: 1000)
        limit: Maximum number of clients to fetch (0 = no limit)

    Returns:
        List of client dictionaries

    Uses Link header pagination with startingAfter to fetch all clients.
    """
    clients: list[dict] = []
    params: dict = {"perPage": batch_size, "timespan": timespan}

    while True:
        response = await client.get(f"{BASE_URL}/networks/{network_id}/clients", params=params)

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 1))
            logger.warning(f"Rate limited on network {network_id}, sleeping {retry_after}s")
            await asyncio.sleep(retry_after)
            continue

        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list):
            items = []

        if not items:
            break

        if limit > 0:
            remaining = limit - len(clients)
            if remaining <= 0:
                break
            items = items[:remaining]

        clients.extend(items)
        logger.info(f"Network {network_id}: fetched {len(items)} clients (total: {len(clients)})")

        if limit > 0 and len(clients) >= limit:
            break

        link_header = response.headers.get("Link", "")
        starting_after = parse_next_starting_after(link_header)
        if not starting_after:
            break
        params["startingAfter"] = starting_after

    return clients


async def fetch_network_clients_bounded(
    semaphore: asyncio.Semaphore,
    client: httpx.AsyncClient,
    network: dict,
    timespan: float,
    batch_size: int,
    limit: int,
) -> list[dict]:
    """Fetch clients for one network, bounded by a concurrency semaphore.

    Tags each client with _networkId/_networkName and swallows per-network
    errors so one bad network doesn't abort the whole download.
    """
    network_id = network["id"]
    network_name = network.get("name", "")

    async with semaphore:
        try:
            clients = await fetch_network_clients(client, network_id, timespan, batch_size, limit)
        except httpx.HTTPStatusError as e:
            logger.warning(f"Skipping network {network_id} ({network_name}): HTTP {e.response.status_code}")
            return []
        except Exception as e:
            logger.warning(f"Skipping network {network_id} ({network_name}): {e}")
            return []

    for c in clients:
        c["_networkId"] = network_id
        c["_networkName"] = network_name

    logger.info(f"Network {network_id} ({network_name}): {len(clients)} client(s)")
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
    """Resolve a dotted key path like 'usage.sent' from a nested dict."""
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
    writer.writerow([header for header, _ in CSV_COLUMNS])
    for c in clients:
        row = [_get_nested(c, field) if field else "" for _, field in CSV_COLUMNS]
        writer.writerow(row)
    return output.getvalue()


def format_table(clients: list[dict]) -> str:
    """Format clients as a Markdown table."""
    if not clients:
        return ""
    headers = [header for header, _ in CSV_COLUMNS]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for c in clients:
        row = [_get_nested(c, field) if field else "" for _, field in CSV_COLUMNS]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


FORMATTERS = {
    "csv": format_csv,
    "json": format_json,
    "yaml": format_yaml,
    "table": format_table,
}


async def run(
    network_id: Optional[str],
    fmt: str,
    filters: list[tuple[str, str]],
    timespan: float,
    batch_size: int,
    limit: int,
    concurrency: int,
) -> None:
    """Main download workflow."""
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

        if network_id:
            networks = [{"id": network_id, "name": network_id}]
        else:
            logger.info(f"Fetching all networks in organization: {org_id}")
            networks = await fetch_all_networks(client, org_id)
        logger.info(f"Found {len(networks)} network(s)")

        semaphore = asyncio.Semaphore(concurrency)
        tasks = [
            fetch_network_clients_bounded(semaphore, client, network, timespan, batch_size, limit)
            for network in networks
        ]
        results = await asyncio.gather(*tasks)
        all_clients = [c for clients in results for c in clients]

        elapsed = time.time() - start
        logger.info(f"Fetched {len(all_clients)} clients from {len(networks)} network(s) in {elapsed:.1f}s")

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
@click.option("--network", "-n", "network_id", default=None, help="Network ID (optional - limits download to a single network)")
@click.option("--format", "fmt", default="csv", type=click.Choice(["csv", "json", "yaml", "table"]), help="Output format (default: csv)")
@click.option("--filter", "-f", "filters", multiple=True, help="Filter by key=value (repeatable, supports dot notation)")
@click.option("--timespan", "timespan", default=MAX_TIMESPAN, type=float, help=f"Lookback window in seconds (default: {MAX_TIMESPAN} / 31 days, the API max)")
@click.option("--batch", "batch_size", default=1000, type=int, help="Batch size per API request (default: 1000, max: 5000)")
@click.option("--limit", "limit", default=0, type=int, help="Maximum number of clients to fetch per network (default: 0 = all)")
@click.option("--concurrency", "concurrency", default=5, type=int, help="Number of networks to fetch concurrently (default: 5)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(
    network_id: Optional[str],
    fmt: str,
    filters: tuple[str, ...],
    timespan: float,
    batch_size: int,
    limit: int,
    concurrency: int,
    verbose: bool,
) -> None:
    """Download all clients from all networks in a Meraki organization.

    By default, asynchronously fetches clients from every network in
    MERAKI_ORG_ID (bounded by --concurrency) and combines them into a
    single export. Use --network to limit the download to one network.
    """
    if batch_size < 3 or batch_size > 5000:
        logger.error("--batch must be between 3 and 5000")
        sys.exit(1)
    if timespan <= 0 or timespan > MAX_TIMESPAN:
        logger.error(f"--timespan must be between 1 and {MAX_TIMESPAN}")
        sys.exit(1)
    if concurrency < 1:
        logger.error("--concurrency must be at least 1")
        sys.exit(1)

    if verbose:
        logger.setLevel(logging.INFO)
    parsed_filters = [parse_filter(f) for f in filters]
    asyncio.run(run(network_id, fmt, parsed_filters, timespan, batch_size, limit, concurrency))


if __name__ == "__main__":
    main()
