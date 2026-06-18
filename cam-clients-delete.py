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
Meraki Dashboard API endpoints. Uses parallel workers for fast deletion.

Usage:
    cam-clients-delete.py [OPTIONS]
    cam-clients-delete.py --workers 8 --timeout 120
    cam-clients-delete.py --dry-run
"""

import asyncio
import logging
import os
import signal
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


async def fetch_all_clients(client: httpx.AsyncClient, org_id: str, batch_size: int = 1000, limit: Optional[int] = None) -> list[dict]:
    """Fetch all NAC clients using numeric index pagination.

    Uses startingAfter as a numeric index (not client ID or MAC address).

    Args:
        client: HTTP client
        org_id: Organization ID
        batch_size: Number of clients to fetch per API request (default: 1000, max: 1000)
        limit: Maximum total clients to fetch (default: None = fetch all)
    """
    clients: list[dict] = []
    params: dict = {"perPage": min(batch_size, 1000)}  # API max is 1000
    starting_index = 0
    page = 1

    while True:
        # Check if we've hit the limit
        if limit and len(clients) >= limit:
            logger.info(f"Reached limit of {limit} clients")
            break

        logger.info(f"Fetching client page {page}...")
        logger.debug(f"Request params: {params}")
        response = await client.get(f"{BASE_URL}/organizations/{org_id}/nac/clients", params=params)

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 1))
            logger.warning(f"Rate limited, sleeping {retry_after}s")
            await asyncio.sleep(retry_after)
            continue

        response.raise_for_status()
        data = response.json()
        logger.debug(f"Response data keys: {data.keys() if isinstance(data, dict) else 'not a dict'}")
        if isinstance(data, dict) and 'meta' in data:
            logger.debug(f"Response meta: {data['meta']}")

        items = data.get("items", data) if isinstance(data, dict) else data

        # Apply limit if specified
        if limit:
            remaining = limit - len(clients)
            items = items[:remaining]

        clients.extend(items)
        logger.info(f"Fetched {len(items)} clients (total: {len(clients)})")

        # Check meta for total count
        if isinstance(data, dict) and 'meta' in data:
            meta = data['meta']
            total_count = meta.get('totalCount', 0)
            if len(clients) >= total_count:
                logger.info(f"Fetched all {total_count} clients")
                break

        # Check if we got fewer items than requested (last page)
        if len(items) < batch_size:
            logger.info("Fetched last page (partial batch)")
            break

        # Check if hit limit
        if limit and len(clients) >= limit:
            break

        # Pagination: use numeric index for startingAfter
        starting_index += len(items)
        params["startingAfter"] = str(starting_index)
        page += 1

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


async def bulk_delete_clients(client: httpx.AsyncClient, org_id: str, client_ids: list[str], timeout: float = 60.0, worker_id: int = 0, batch_num: int = 0) -> tuple[bool, int]:
    """Delete clients via bulkDelete endpoint.

    Args:
        client: HTTP client
        org_id: Organization ID
        client_ids: List of client IDs to delete
        timeout: Request timeout in seconds (default: 60)
        worker_id: Worker identifier for logging (default: 0)
        batch_num: Batch number for logging (default: 0)

    Returns:
        Tuple of (success, count) where count is number of clients deleted
    """
    context = f"Worker {worker_id}, Batch {batch_num}: " if worker_id or batch_num else ""
    logger.debug(f"{context}bulkDelete: Sending {len(client_ids)} client IDs (first 3: {client_ids[:3]})")

    try:
        response = await client.post(
            f"{BASE_URL}/organizations/{org_id}/nac/clients/bulkDelete",
            json={"clientIds": client_ids},
            timeout=timeout,
        )

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 1))
            logger.warning(f"Rate limited on delete, sleeping {retry_after}s")
            await asyncio.sleep(retry_after)
            response = await client.post(
                f"{BASE_URL}/organizations/{org_id}/nac/clients/bulkDelete",
                json={"clientIds": client_ids},
                timeout=timeout,
            )

        if response.status_code == 204:
            return True, len(client_ids)

        if response.status_code == 404:
            logger.warning(f"{context}bulkDelete: Some clients already deleted ({len(client_ids)} IDs)")
            return True, len(client_ids)

        logger.error(f"{context}bulkDelete failed: {response.status_code} {response.text}")
        logger.debug(f"{context}bulkDelete failed IDs sample: {client_ids[:5]}")
        return False, 0

    except httpx.TimeoutException as e:
        logger.error(f"{context}bulkDelete timeout after {timeout}s: {e}")
        return False, 0
    except Exception as e:
        logger.error(f"{context}bulkDelete exception: {type(e).__name__}: {e}")
        return False, 0


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


async def fetch_worker(
    deletion_queue: asyncio.Queue,
    client: httpx.AsyncClient,
    org_id: str,
    batch_size: int,
    limit: Optional[int],
    fetch_stats: dict,
    queue_client_count: dict,
) -> None:
    """Single worker that fetches client pages and queues them for deletion.

    Uses startingAfter as a numeric index for pagination.

    Args:
        deletion_queue: Queue to put client IDs for deletion
        client: HTTP client
        org_id: Organization ID
        batch_size: Number of clients to fetch per API request (max: 1000)
        limit: Maximum total clients to fetch
        fetch_stats: Shared dictionary to track fetch progress
        queue_client_count: Shared dictionary to track clients in queue
    """
    page_num = 1
    total_count = None
    starting_index = 0
    params: dict = {"perPage": batch_size}

    while True:
        # Check if we've hit the limit
        if limit and fetch_stats['fetched'] >= limit:
            logger.info(f"Reached limit of {limit} clients")
            break

        try:
            logger.debug(f"Fetching page {page_num} with params: {params}")
            response = await client.get(f"{BASE_URL}/organizations/{org_id}/nac/clients", params=params)

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 1))
                logger.warning(f"Rate limited, sleeping {retry_after}s")
                await asyncio.sleep(retry_after)
                continue

            response.raise_for_status()
            data = response.json()
            items = data.get("items", data) if isinstance(data, dict) else data

            logger.debug(f"Page {page_num}: Received {len(items) if items else 0} items")

            if not items:
                logger.info("No more clients to fetch (empty items)")
                break

            # Get total count on first page
            if page_num == 1 and isinstance(data, dict) and 'meta' in data:
                total_count = data['meta'].get('totalCount', 0)
                logger.info(f"Total clients reported by API: {total_count}")

            # Apply limit if specified
            if limit:
                remaining = limit - fetch_stats['fetched']
                items = items[:remaining]

            client_ids = [c["id"] for c in items]

            # Wait if queue is full (this will block until space is available)
            if deletion_queue.full():
                logger.info(f"Queue full ({queue_client_count['count']} clients queued), fetch worker waiting...")

            await deletion_queue.put(client_ids)
            fetch_stats['fetched'] += len(client_ids)
            queue_client_count['count'] += len(client_ids)

            logger.info(f"Fetched {fetch_stats['fetched']} clients, {queue_client_count['count']} queued")

            # Check if we've fetched all based on totalCount
            if total_count and fetch_stats['fetched'] >= total_count:
                logger.info("Fetched all available clients (based on totalCount)")
                break

            # Pagination: use numeric index for startingAfter
            starting_index += len(items)
            params["startingAfter"] = str(starting_index)
            page_num += 1

        except Exception as e:
            logger.error(f"Exception on page {page_num}: {e}")
            break

    logger.info(f"Fetch complete - fetched {fetch_stats['fetched']} total clients")


async def deletion_worker(
    worker_id: int,
    queue: asyncio.Queue,
    client: httpx.AsyncClient,
    org_id: str,
    timeout: float,
    deleted_count: dict,
    batch_counter: dict,
    queue_client_count: dict,
    batch_size: int = 1000,
) -> None:
    """Worker that processes deletion tasks from the queue.

    Args:
        worker_id: Worker identifier for logging
        queue: Queue containing client IDs to delete
        client: HTTP client
        org_id: Organization ID
        timeout: Request timeout in seconds
        deleted_count: Shared dictionary to track deletion progress
        batch_counter: Shared dictionary to track batch numbers
        queue_client_count: Shared dictionary to track clients in queue
        batch_size: Number of clients per deletion batch
    """
    while True:
        client_ids = await queue.get()
        if client_ids is None:  # Poison pill to stop worker
            queue.task_done()
            break

        # Decrement queue count immediately when items are taken from queue
        queue_client_count['count'] -= len(client_ids)

        logger.debug(f"Delete worker {worker_id}: Received {len(client_ids)} client IDs (sample: {client_ids[:3] if client_ids else 'empty'})")

        # Process in deletion batches
        for i in range(0, len(client_ids), batch_size):
            batch = client_ids[i : i + batch_size]
            # Get unique batch number from shared counter
            batch_num = batch_counter['count']
            batch_counter['count'] += 1

            logger.info(f"🔄 Delete worker {worker_id}: Starting batch {batch_num} ({len(batch)} clients)")

            try:
                success, count = await bulk_delete_clients(client, org_id, batch, timeout, worker_id, batch_num)
                if success:
                    deleted_count['count'] += count
                    logger.info(f"✅ Delete worker {worker_id}: Deleted batch {batch_num} ({count} clients)")
                else:
                    # Re-queue failed batch for retry
                    queue_client_count['count'] += len(batch)
                    await queue.put(batch)
                    logger.warning(f"🔁 Delete worker {worker_id}: Re-queued batch {batch_num} ({len(batch)} clients), {queue_client_count['count']} queued")
            except asyncio.CancelledError:
                # Re-queue on cancellation
                queue_client_count['count'] += len(batch)
                await queue.put(batch)
                logger.warning(f"⚠️ Delete worker {worker_id}: Cancelled batch {batch_num}, re-queued")
                raise
            except Exception as e:
                # Re-queue on exception
                queue_client_count['count'] += len(batch)
                await queue.put(batch)
                error_msg = str(e) if str(e) else type(e).__name__
                logger.warning(f"🔁 Delete worker {worker_id}: Exception in batch {batch_num}: {error_msg}, re-queued, {queue_client_count['count']} queued")

        queue.task_done()


async def delete_clients_parallel(
    client: httpx.AsyncClient,
    org_id: str,
    batch_size: int,
    limit: Optional[int],
    timeout: float,
    workers: int,
) -> tuple[int, int]:
    """Delete clients using 1 fetch worker and N delete workers.

    One worker fetches all pages sequentially, while other workers delete concurrently.

    Args:
        client: HTTP client
        org_id: Organization ID
        batch_size: Number of clients to fetch per API request
        limit: Maximum total clients to fetch
        timeout: Request timeout in seconds
        workers: Total number of workers (1 fetch + N-1 delete)

    Returns:
        Tuple of (clients_fetched, clients_deleted)
    """
    # Set max queue size to hold ~10000 clients worth of batches
    # This prevents memory issues with large datasets
    max_clients_in_queue = 10000
    queue_maxsize = max(1, max_clients_in_queue // batch_size)
    logger.info(f"Queue maxsize: {queue_maxsize} batches (~{queue_maxsize * batch_size} clients max)")

    deletion_queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
    fetch_stats = {'fetched': 0}
    deleted_count = {'count': 0}
    batch_counter = {'count': 1}  # Start batch numbering at 1
    queue_client_count = {'count': 0}  # Track total clients in queue

    # Allocate workers: 1 for fetching, rest for deletion
    num_delete_workers = max(1, workers - 1)
    logger.info(f"Starting pipeline: 1 fetch worker, {num_delete_workers} delete workers")

    # Start single fetch worker
    fetch_task = asyncio.create_task(
        fetch_worker(deletion_queue, client, org_id, batch_size, limit, fetch_stats, queue_client_count)
    )

    # Start deletion workers
    delete_tasks = [
        asyncio.create_task(deletion_worker(i + 1, deletion_queue, client, org_id, timeout, deleted_count, batch_counter, queue_client_count))
        for i in range(num_delete_workers)
    ]

    try:
        # Wait for fetch to complete
        await fetch_task
        logger.info(f"Fetch worker stopped: {fetch_stats['fetched']} clients queued for deletion")

        # Wait for deletion queue to be empty (all deletions complete)
        await deletion_queue.join()
        logger.info(f"All deletions complete: {deleted_count['count']} clients deleted")

    except asyncio.CancelledError:
        logger.warning("\n⚠️  Cancellation requested (Ctrl+C)")
        # Cancel fetch worker if still running
        if not fetch_task.done():
            fetch_task.cancel()
            try:
                await fetch_task
            except asyncio.CancelledError:
                pass

        # Cancel all delete workers
        for task in delete_tasks:
            if not task.done():
                task.cancel()

        logger.info(f"Shutting down gracefully: {deleted_count['count']}/{fetch_stats['fetched']} clients deleted")
        raise

    finally:
        # Stop delete workers with poison pills
        for _ in range(num_delete_workers):
            try:
                deletion_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

        # Wait for delete workers to finish with timeout
        try:
            await asyncio.wait_for(asyncio.gather(*delete_tasks, return_exceptions=True), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Delete workers did not stop within 5s")

        logger.info("All delete workers stopped")

    return fetch_stats['fetched'], deleted_count['count']


async def run(dry_run: bool, clients_only: bool, groups_only: bool, batch_size: int, limit: Optional[int], timeout: float, workers: int) -> None:
    """Main deletion workflow using continuous fetch-and-delete pipeline."""
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")
    org_id = os.getenv("MERAKI_ORG_ID")

    if not api_key:
        logger.error("MERAKI_DASHBOARD_API_KEY not set")
        sys.exit(1)
    if not org_id:
        logger.error("MERAKI_ORG_ID not set")
        sys.exit(1)

    try:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(30.0),
        ) as client:
            overall_start = time.time()
            total_clients_deleted = 0
            total_groups_deleted = 0

            # Delete clients using continuous pipeline
            if not groups_only:
                if dry_run:
                    # For dry run, fetch all clients first
                    all_clients = await fetch_all_clients(client, org_id, batch_size, limit)
                    logger.info(f"Total clients found: {len(all_clients)}")
                    if all_clients:
                        for c in all_clients:
                            logger.info(f"  {c.get('mac', 'N/A')} | name={c.get('name', 'N/A')} | id={c['id']}")
                        logger.info(f"Dry run: would delete {len(all_clients)} clients")
                    else:
                        logger.info("No clients to delete")
                else:
                    # Use parallel fetch-and-delete pipeline (continuous until all fetched)
                    logger.info(f"Starting continuous pipeline with {workers} workers...")
                    fetched, deleted = await delete_clients_parallel(client, org_id, batch_size, limit, timeout, workers)

                    total_clients_deleted = deleted
                    logger.info(f"Pipeline complete: {deleted}/{fetched} clients deleted")

            # Delete groups
            if not clients_only:
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

                        total_groups_deleted = deleted
                        logger.info(f"Groups deleted: {deleted}/{len(all_groups)}")
                else:
                    logger.info("No groups to delete")

            overall_elapsed = time.time() - overall_start
            logger.info(f"\n{'='*60}")
            logger.info(f"All deletions complete in {overall_elapsed:.1f}s")
            logger.info(f"Total clients deleted: {total_clients_deleted}")
            logger.info(f"Total groups deleted: {total_groups_deleted}")
            logger.info(f"{'='*60}")

    except asyncio.CancelledError:
        logger.warning("\n⚠️  Interrupted by user (Ctrl+C)")
        logger.info("Exiting gracefully...")
        sys.exit(130)  # Standard exit code for SIGINT
    except KeyboardInterrupt:
        logger.warning("\n⚠️  Interrupted by user (Ctrl+C)")
        logger.info("Exiting gracefully...")
        sys.exit(130)


@click.command()
@click.option("--clients-only", is_flag=True, default=False, help="Delete only clients, not groups")
@click.option("--groups-only", is_flag=True, default=False, help="Delete only groups, not clients")
@click.option("--dry-run", is_flag=True, default=False, help="List items without deleting")
@click.option("--batch", "batch_size", type=int, default=1000, help="Number of clients to fetch per API request (default: 1000, max: 1000)")
@click.option("--limit", type=int, default=None, help="Maximum total clients to fetch (default: no limit)")
@click.option("--timeout", type=float, default=60.0, help="Timeout in seconds for bulk delete requests (default: 60)")
@click.option("--workers", type=int, default=4, help="Number of parallel workers (1 fetch + N-1 delete, default: 4)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging")
def main(clients_only: bool, groups_only: bool, dry_run: bool, batch_size: int, limit: Optional[int], timeout: float, workers: int, verbose: bool, debug: bool) -> None:
    """Delete all NAC clients and groups from Cisco Access Manager.

    Uses a continuous pipeline with 1 fetch worker and N-1 delete workers.
    The fetch worker sequentially fetches all pages while delete workers
    process them concurrently for maximum efficiency.

    Press Ctrl+C to stop gracefully.
    """
    if clients_only and groups_only:
        logger.error("Cannot specify both --clients-only and --groups-only")
        sys.exit(1)

    if batch_size < 1 or batch_size > 1000:
        logger.error("--batch must be between 1 and 1000")
        sys.exit(1)

    if limit is not None and limit < 1:
        logger.error("--limit must be at least 1")
        sys.exit(1)

    if timeout <= 0:
        logger.error("--timeout must be greater than 0")
        sys.exit(1)

    if workers < 1:
        logger.error("--workers must be at least 1")
        sys.exit(1)

    if debug:
        logger.setLevel(logging.DEBUG)
    elif verbose or dry_run:
        logger.setLevel(logging.INFO)

    # Setup signal handler for graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler(sig, frame):
        logger.warning("\n⚠️  Received interrupt signal, shutting down gracefully...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        loop.run_until_complete(run(dry_run, clients_only, groups_only, batch_size, limit, timeout, workers))
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
