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
Delete all NAC clients and groups from Cisco Access Manager (CAM).

Deletes all NAC clients and/or groups from the organization via the
Meraki Dashboard API endpoints.

Usage:
    cam-clients-delete.py [--clients-only | --groups-only] [--dry-run] [--no-loop]
"""

import asyncio
import logging
import os
import sys
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import click
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cam-clients-delete")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"


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

    NOTE: The Meraki API returns meta.totalCount but does not provide Link headers
    for pagination and returns 500 errors when using startingAfter manually.
    This appears to be an API limitation/bug. The script will fetch and delete
    up to 1000 clients per run. Run multiple times to delete all clients.
    """
    clients: list[dict] = []
    params: dict = {"perPage": 1000}
    page = 1

    while True:
        logger.info(f"Fetching client page {page}...")
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

        # Check meta for total count
        if isinstance(data, dict) and 'meta' in data:
            meta = data['meta']
            total_count = meta.get('totalCount', 0)
            if total_count > len(clients):
                logger.warning(f"API shows {total_count} total clients but pagination unavailable (Meraki API limitation)")
                logger.warning(f"Will delete {len(clients)} clients this run - run script again to delete remaining clients")

        # Check for pagination via Link header (currently not provided by Meraki API)
        link_header = response.headers.get("Link", "")
        next_token = parse_next_starting_after(link_header)
        if not next_token:
            break
        params["startingAfter"] = next_token

    return clients


async def fetch_all_groups(client: httpx.AsyncClient, org_id: str) -> list[dict]:
    """Fetch NAC groups (may be limited by API pagination issues)."""
    groups: list[dict] = []
    params: dict = {"perPage": 1000}

    while True:
        response = await client.get(f"{BASE_URL}/organizations/{org_id}/nac/clients/groups", params=params)

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 1))
            logger.warning(f"Rate limited, sleeping {retry_after}s")
            await asyncio.sleep(retry_after)
            continue

        response.raise_for_status()
        data = response.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        groups.extend(items)
        logger.info(f"Fetched {len(items)} groups (total: {len(groups)})")

        link_header = response.headers.get("Link", "")
        next_token = parse_next_starting_after(link_header)
        if not next_token:
            break
        params["startingAfter"] = next_token

    return groups


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


async def delete_group(client: httpx.AsyncClient, org_id: str, group_id: str) -> bool:
    """Delete a single NAC group."""
    response = await client.delete(f"{BASE_URL}/organizations/{org_id}/nac/clients/groups/{group_id}")

    if response.status_code == 429:
        retry_after = float(response.headers.get("Retry-After", 1))
        logger.warning(f"Rate limited on group delete, sleeping {retry_after}s")
        await asyncio.sleep(retry_after)
        response = await client.delete(f"{BASE_URL}/organizations/{org_id}/nac/clients/groups/{group_id}")

    if response.status_code == 204:
        return True

    logger.error(f"Group delete failed: {response.status_code} {response.text}")
    return False


async def run(dry_run: bool, clients_only: bool, groups_only: bool, loop_until_empty: bool) -> None:
    """Main deletion workflow."""
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
        overall_start = time.time()
        iteration = 1
        total_clients_deleted = 0
        total_groups_deleted = 0

        while True:
            if loop_until_empty and iteration > 1:
                logger.info(f"\n{'='*60}")
                logger.info(f"Starting iteration {iteration}")
                logger.info(f"{'='*60}\n")

            start = time.time()

            # Delete clients
            iteration_clients_deleted = 0
            if not groups_only:
                all_clients = await fetch_all_clients(client, org_id)
                logger.info(f"Total clients found: {len(all_clients)}")

                if all_clients:
                    if dry_run:
                        for c in all_clients:
                            logger.info(f"  {c.get('mac', 'N/A')} | name={c.get('name', 'N/A')} | id={c['id']}")
                        logger.info(f"Dry run: would delete {len(all_clients)} clients")
                    else:
                        client_ids = [c["id"] for c in all_clients]
                        batch_size = 1000
                        deleted = 0
                        for i in range(0, len(client_ids), batch_size):
                            batch = client_ids[i : i + batch_size]
                            success = await bulk_delete_clients(client, org_id, batch)
                            if success:
                                deleted += len(batch)
                                logger.info(f"✅ Deleted client batch {i // batch_size + 1} ({len(batch)} clients)")
                            else:
                                logger.error(f"❌ Failed client batch {i // batch_size + 1}")

                        iteration_clients_deleted = deleted
                        total_clients_deleted += deleted
                        logger.info(f"Clients deleted this iteration: {deleted}/{len(all_clients)}")
                else:
                    logger.info("No clients to delete")

            # Delete groups (only on first iteration if looping, since groups should be deleted once)
            iteration_groups_deleted = 0
            if not clients_only and iteration == 1:
                all_groups = await fetch_all_groups(client, org_id)
                logger.info(f"Total groups found: {len(all_groups)}")

                if all_groups:
                    if dry_run:
                        for g in all_groups:
                            logger.info(f"  {g.get('name', 'N/A')} | id={g['id']}")
                        logger.info(f"Dry run: would delete {len(all_groups)} groups")
                    else:
                        deleted = 0
                        for g in all_groups:
                            success = await delete_group(client, org_id, g["id"])
                            if success:
                                deleted += 1
                                logger.info(f"✅ Deleted group: {g.get('name', 'N/A')}")
                            else:
                                logger.error(f"❌ Failed to delete group: {g.get('name', 'N/A')}")

                        iteration_groups_deleted = deleted
                        total_groups_deleted += deleted
                        logger.info(f"Groups deleted: {deleted}/{len(all_groups)}")
                else:
                    logger.info("No groups to delete")

            elapsed = time.time() - start
            logger.info(f"Iteration {iteration} complete in {elapsed:.1f}s")

            # Check if we should continue looping
            if not loop_until_empty or dry_run:
                break

            # If no clients were found or deleted, we're done
            if not groups_only and iteration_clients_deleted == 0:
                logger.info(f"\nNo more clients to delete - stopping")
                break

            iteration += 1

        overall_elapsed = time.time() - overall_start
        if loop_until_empty and not dry_run:
            logger.info(f"\n{'='*60}")
            logger.info(f"All deletions complete after {iteration} iteration(s) in {overall_elapsed:.1f}s")
            logger.info(f"Total clients deleted: {total_clients_deleted}")
            logger.info(f"Total groups deleted: {total_groups_deleted}")
            logger.info(f"{'='*60}")


@click.command()
@click.option("--clients-only", is_flag=True, default=False, help="Delete only clients, not groups")
@click.option("--groups-only", is_flag=True, default=False, help="Delete only groups, not clients")
@click.option("--dry-run", is_flag=True, default=False, help="List items without deleting")
@click.option("--no-loop", "no_loop", is_flag=True, default=False, help="Run only once (default: loops until all clients deleted)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(clients_only: bool, groups_only: bool, dry_run: bool, no_loop: bool, verbose: bool) -> None:
    """Delete all NAC clients and groups from Cisco Access Manager.

    Due to a Meraki API limitation, only 1000 clients can be fetched per request.
    By default, the script loops automatically until all clients are deleted.
    Use --no-loop to run only a single iteration.
    """
    if clients_only and groups_only:
        logger.error("Cannot specify both --clients-only and --groups-only")
        sys.exit(1)

    if verbose or dry_run:
        logger.setLevel(logging.INFO)

    loop_until_empty = not no_loop
    asyncio.run(run(dry_run, clients_only, groups_only, loop_until_empty))


if __name__ == "__main__":
    main()
