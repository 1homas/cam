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
Purge stale Guest clients from Cisco Access Manager (CAM).

Finds disconnected guest clients older than a specified age and deletes them
via the Meraki Dashboard API bulkDelete endpoint.

Matching criteria:
  - SSID contains the --ssid value (case-insensitive)
  - source = "Discovered"
  - status = "Disconnected"
  - lastLogin.timestamp <= now - age

Usage:
    cam-guest-purge.py --ssid Guest [--age SECONDS] [--dry-run]
"""

import asyncio
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

import click
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cam-guest-purge")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"
DEFAULT_AGE = "7d"
AGE_PATTERN = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


def parse_age(value: str) -> int:
    """Parse age string (DDdHHhMMmSSs) or plain seconds into total seconds."""
    if value.isdigit():
        return int(value)
    match = AGE_PATTERN.match(value)
    if not match or not any(match.groups()):
        raise click.BadParameter(f"Invalid age format: {value!r}. Use DDdHHhMMmSSs (e.g. 7d, 2d12h, 30m, 3600)")
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


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
    """Fetch all NAC clients with pagination."""
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

        link_header = response.headers.get("Link", "")
        next_token = parse_next_starting_after(link_header)
        if not next_token:
            break
        params["startingAfter"] = next_token

    return clients


def filter_stale_guests(clients: list[dict], cutoff: datetime, ssid_match: str = "guest") -> list[dict]:
    """Filter clients matching stale guest criteria."""
    ssid_match_lower = ssid_match.lower()
    stale: list[dict] = []
    for c in clients:
        ssid = (c.get("ssid") or "").lower()
        source = c.get("source", "")
        status = c.get("status", "")
        last_login = c.get("lastLogin", {})
        timestamp_str = last_login.get("timestamp", "")

        if ssid_match_lower not in ssid:
            continue
        if source != "Discovered":
            continue
        if status != "Disconnected":
            continue
        if not timestamp_str:
            continue

        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if ts <= cutoff:
            stale.append(c)

    return stale


async def bulk_delete_clients(client: httpx.AsyncClient, org_id: str, client_ids: list[str]) -> bool:
    """Delete clients via bulkDelete endpoint."""
    response = await client.post(
        f"{BASE_URL}/organizations/{org_id}/nac/clients/bulkDelete",
        json={"clientIds": client_ids},
    )

    if response.status_code == 429:
        retry_after = float(response.headers.get("Retry-After", 1))
        logger.warning(f"Rate limited on delete, sleeping {retry_after}s")
        await asyncio.sleep(retry_after)
        response = await client.post(
            f"{BASE_URL}/organizations/{org_id}/nac/clients/bulkDelete",
            json={"clientIds": client_ids},
        )

    if response.status_code == 204:
        return True

    logger.error(f"bulkDelete failed: {response.status_code} {response.text}")
    return False


async def run(age: str, dry_run: bool, ssid: str) -> None:
    """Main purge workflow."""
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")
    org_id = os.getenv("MERAKI_ORG_ID")

    if not api_key:
        logger.error("MERAKI_DASHBOARD_API_KEY not set")
        sys.exit(1)
    if not org_id:
        logger.error("MERAKI_ORG_ID not set")
        sys.exit(1)

    age_seconds = parse_age(age)
    cutoff = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = cutoff.__class__.fromtimestamp(cutoff.timestamp() - age_seconds, tz=timezone.utc)

    logger.info(f"Purging guest clients with lastLogin <= {cutoff.isoformat()}")

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=httpx.Timeout(30.0),
    ) as client:
        start = time.time()
        all_clients = await fetch_all_clients(client, org_id)
        logger.info(f"Total clients fetched: {len(all_clients)}")

        stale = filter_stale_guests(all_clients, cutoff, ssid)
        logger.info(f"Stale guest clients found: {len(stale)}")

        if not stale:
            logger.info("Nothing to purge")
            return

        for c in stale:
            last_login = c.get("lastLogin", {}).get("timestamp", "unknown")
            logger.info(f"  {c['mac']} | ssid={c.get('ssid')} | lastLogin={last_login} | id={c['id']}")

        if dry_run:
            logger.info(f"Dry run: would delete {len(stale)} clients")
            return

        client_ids = [c["id"] for c in stale]
        # bulkDelete in batches of 100
        batch_size = 100
        deleted = 0
        for i in range(0, len(client_ids), batch_size):
            batch = client_ids[i : i + batch_size]
            success = await bulk_delete_clients(client, org_id, batch)
            if success:
                deleted += len(batch)
                logger.info(f"✅ Deleted batch {i // batch_size + 1} ({len(batch)} clients)")
            else:
                logger.error(f"❌ Failed batch {i // batch_size + 1}")

        elapsed = time.time() - start
        logger.info(f"Purge complete: {deleted}/{len(stale)} clients deleted in {elapsed:.1f}s")


@click.command()
@click.option("--age", default=DEFAULT_AGE, type=str, help="Age as DDdHHhMMmSSs or seconds (default: 7d)")
@click.option("--ssid", required=True, type=str, help="SSID name to match (case-insensitive contains)")
@click.option("--dry-run", is_flag=True, default=False, help="List clients without deleting")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(age: str, ssid: str, dry_run: bool, verbose: bool) -> None:
    """Purge stale Guest clients from Cisco Access Manager."""
    if verbose or dry_run:
        logger.setLevel(logging.INFO)
    asyncio.run(run(age, dry_run, ssid))


if __name__ == "__main__":
    main()
