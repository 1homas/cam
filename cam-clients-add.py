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
Bulk add NAC clients to Cisco Access Manager from a base64-encoded CSV file.

Uploads clients via the Meraki Dashboard API bulkUpload endpoint using a
base64-encoded CSV file. Automatically batches large files (>1000 rows).

CSV Format (CAM Template):
  Required column: MAC address
  Optional columns: Endpoint device group, Description
  Template: references/Client template.csv

Usage:
    # Upload from CSV file
    cam-clients-add.py --file clients.csv

    # Upload from base64-encoded string (using input redirection)
    cam-clients-add.py "$(base64 < clients.csv)"

    # Upload from base64-encoded string (using cat)
    cam-clients-add.py "$(cat clients.csv | base64)"

    # Upload with custom options
    cam-clients-add.py --file clients.csv --create-groups --format json

    # Skip updating existing clients
    cam-clients-add.py --file clients.csv --no-update-clients

    # Verbose logging
    cam-clients-add.py --file clients.csv -v
"""

import asyncio
import base64
import csv
import io
import json
import logging
import os
import sys
import time
from pathlib import Path

import click
import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cam-clients-add")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
        )
    )
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"
DEFAULT_BATCH_SIZE = 1000  # Maximum clients per batch


def split_csv_into_batches(csv_content: str, batch_size: int) -> list[str]:
    """Split CSV content into batches of rows.

    Args:
        csv_content: Full CSV content with headers
        batch_size: Maximum number of data rows per batch

    Returns:
        List of CSV strings, each with headers and up to batch_size rows
    """
    lines = csv_content.strip().split("\n")
    if len(lines) <= 1:
        return [csv_content]  # Just headers or empty

    header = lines[0]
    data_rows = lines[1:]

    batches = []
    for i in range(0, len(data_rows), batch_size):
        batch_rows = data_rows[i : i + batch_size]
        batch_csv = header + "\n" + "\n".join(batch_rows)
        batches.append(batch_csv)

    return batches


def validate_csv_content(csv_content: str) -> tuple[bool, str]:
    """Validate CSV content has required headers.

    Accepts CAM template format with 'MAC address' column.

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


async def upload_clients(
    client: httpx.AsyncClient,
    org_id: str,
    base64_csv: str,
    update_clients: bool,
    create_groups: bool,
    debug: bool = False,
) -> dict:
    """Upload clients via bulkUpload API.

    Args:
        client: httpx async client
        org_id: Meraki organization ID
        base64_csv: Base64-encoded CSV content
        update_clients: Whether to update existing clients
        create_groups: Whether to create new client groups
        debug: Show payload details

    Returns:
        API response dict with upload results
    """
    payload = {
        "contents": base64_csv,
        "updateClients": update_clients,
        "createClientGroups": create_groups,
    }

    if debug:
        # Show decoded CSV for debugging
        try:
            decoded_csv = base64.b64decode(base64_csv).decode()
            logger.info(f"CSV Content:\n{decoded_csv}")
        except:
            logger.warning("Could not decode CSV for debug output")
        logger.info(f"Payload: updateClients={update_clients}, createClientGroups={create_groups}")

    response = await client.post(
        f"{BASE_URL}/organizations/{org_id}/nac/clients/bulkUpload",
        json=payload,
    )

    if response.status_code == 429:
        retry_after = float(response.headers.get("Retry-After", 1))
        logger.warning(f"Rate limited, sleeping {retry_after}s")
        await asyncio.sleep(retry_after)
        return await upload_clients(
            client, org_id, base64_csv, update_clients, create_groups
        )

    if response.status_code >= 400:
        error_detail = response.text
        try:
            error_json = response.json()
            error_detail = json.dumps(error_json, indent=2)
        except:
            pass
        logger.error(f"API Error ({response.status_code}): {error_detail}")
        response.raise_for_status()

    return response.json()


def format_response(result: dict, fmt: str) -> str:
    """Format upload result based on output format."""
    if fmt == "json":
        return json.dumps(result, indent=2, default=str)
    elif fmt == "yaml":
        return yaml.dump(result, default_flow_style=False, sort_keys=False)
    elif fmt == "summary":
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

        # For batch results, aggregate items
        if batches > 1:
            batch_results = result.get("results", [])
            all_items = []
            for batch_result in batch_results:
                all_items.extend(batch_result.get("items", []))
            items = all_items

        if items:
            lines.append("\nDetails:")
            # Group items by type
            error_items = [i for i in items if i.get("type") == "error"]
            info_items = [i for i in items if i.get("type") == "info"]

            for item in error_items + info_items:
                item_type = item.get("type", "unknown")
                count = item.get("count", 0)
                if count > 0:
                    lines.append(f"  {item_type.upper()}: {count}")
                    for detail in item.get("details", []):
                        message = detail.get("message", "")
                        rows = detail.get("rows", [])
                        if len(rows) > 10:
                            lines.append(f"    - {message} ({len(rows)} rows)")
                        else:
                            lines.append(f"    - {message} (rows: {rows})")

        return "\n".join(lines)
    return str(result)


async def run(
    base64_csv: str,
    update_clients: bool,
    create_groups: bool,
    fmt: str,
    csv_file: str | None,
    debug: bool = False,
    batch_size: int | None = None,
) -> None:
    """Main workflow to upload clients."""
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")
    org_id = os.getenv("MERAKI_ORG_ID")

    if not api_key:
        logger.error("MERAKI_DASHBOARD_API_KEY not set")
        sys.exit(1)
    if not org_id:
        logger.error("MERAKI_ORG_ID not set")
        sys.exit(1)

    # Handle CSV file input
    if csv_file:
        csv_path = Path(csv_file)
        if not csv_path.exists():
            logger.error(f"CSV file not found: {csv_file}")
            sys.exit(1)

        csv_content = csv_path.read_text()
        is_valid, error = validate_csv_content(csv_content)
        if not is_valid:
            logger.error(f"Invalid CSV: {error}")
            sys.exit(1)

        logger.info(f"Loaded CSV from {csv_file}")
    else:
        # Validate base64 input by attempting to decode
        try:
            csv_content = base64.b64decode(base64_csv).decode()
            is_valid, error = validate_csv_content(csv_content)
            if not is_valid:
                logger.error(f"Invalid CSV: {error}")
                sys.exit(1)
        except Exception as e:
            logger.error(f"Invalid base64 input: {e}")
            sys.exit(1)

    # Count total rows
    lines = csv_content.strip().split("\n")
    total_rows = len(lines) - 1  # Exclude header

    # Split into batches if needed
    if batch_size and total_rows > batch_size:
        batches = split_csv_into_batches(csv_content, batch_size)
        logger.info(f"Splitting {total_rows} clients into {len(batches)} batches of {batch_size}")
    else:
        batches = [csv_content]
        logger.info(f"Uploading {total_rows} client(s) in a single batch")

    async with httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(120.0),
    ) as client:
        all_results = []
        total_success = 0
        total_failure = 0
        start = time.time()

        for batch_num, batch_csv in enumerate(batches, 1):
            batch_lines = batch_csv.strip().split("\n")
            batch_row_count = len(batch_lines) - 1

            if len(batches) > 1:
                logger.info(f"Uploading batch {batch_num}/{len(batches)} ({batch_row_count} clients)")

            batch_base64 = base64.b64encode(batch_csv.encode()).decode()
            result = await upload_clients(
                client, org_id, batch_base64, update_clients, create_groups, debug
            )

            meta = result.get("meta", {})
            counts = meta.get("counts", {})
            success = counts.get("success", 0)
            failure = counts.get("failure", 0)

            total_success += success
            total_failure += failure
            all_results.append(result)

            if len(batches) > 1:
                logger.info(f"Batch {batch_num} complete: {success} succeeded, {failure} failed")

        elapsed = time.time() - start
        logger.info(
            f"Upload completed in {elapsed:.1f}s: "
            f"{total_success} succeeded, {total_failure} failed"
        )

        # Format output
        if len(batches) == 1:
            output = format_response(all_results[0], fmt)
        else:
            # Aggregate results for multiple batches
            combined = {
                "meta": {
                    "counts": {
                        "total": total_rows,
                        "success": total_success,
                        "failure": total_failure,
                    }
                },
                "batches": len(batches),
                "results": all_results,
            }
            output = format_response(combined, fmt)

        print(output)


@click.command()
@click.argument("base64_csv", required=False)
@click.option(
    "--file",
    "csv_file",
    type=click.Path(exists=False),
    help="Path to CSV file to upload (will be base64 encoded)",
)
@click.option(
    "--update-clients",
    is_flag=True,
    default=True,
    help="Update existing clients with new data (default: true)",
)
@click.option(
    "--no-update-clients",
    "update_clients",
    is_flag=True,
    default=False,
    flag_value=False,
    help="Do not update existing clients",
)
@click.option(
    "--create-groups",
    is_flag=True,
    default=False,
    help="Create new client groups if specified in CSV",
)
@click.option(
    "--format",
    "fmt",
    default="summary",
    type=click.Choice(["json", "yaml", "summary"]),
    help="Output format (default: summary)",
)
@click.option(
    "-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging"
)
@click.option(
    "--debug", is_flag=True, default=False, help="Show debug details (CSV content, payload)"
)
@click.option(
    "--batch-size",
    type=int,
    default=None,
    help=f"Split large CSVs into batches (default: {DEFAULT_BATCH_SIZE} for files with >1000 rows)",
)
def main(
    base64_csv: str | None,
    csv_file: str | None,
    update_clients: bool,
    create_groups: bool,
    fmt: str,
    verbose: bool,
    debug: bool,
    batch_size: int | None,
) -> None:
    """Bulk add NAC clients from a base64-encoded CSV file.

    Provide either a base64-encoded CSV string as an argument OR use --file
    to specify a CSV file path. Automatically batches large files (>1000 rows).

    \b
    CSV Format (CAM Template):
      Required column: MAC address
      Optional columns: Endpoint device group, Description
      Template: references/Client template.csv

    \b
    Examples:
      # Upload from CSV file
      cam-clients-add.py --file clients.csv

      # Upload from base64-encoded string (using input redirection)
      cam-clients-add.py "$(base64 < clients.csv)"

      # Upload from base64-encoded string (using cat)
      cam-clients-add.py "$(cat clients.csv | base64)"

      # Upload with custom options
      cam-clients-add.py --file clients.csv --create-groups --format json

      # Skip updating existing clients
      cam-clients-add.py --file clients.csv --no-update-clients

      # Verbose logging
      cam-clients-add.py --file clients.csv -v
    """
    if verbose or debug:
        logger.setLevel(logging.INFO)

    if not base64_csv and not csv_file:
        click.echo("Error: Must provide either BASE64_CSV argument or --file option")
        click.echo("Run with --help for usage information")
        sys.exit(1)

    if base64_csv and csv_file:
        click.echo("Error: Cannot provide both BASE64_CSV argument and --file option")
        sys.exit(1)

    # Auto-enable batching for large files if not specified
    if batch_size is None and csv_file:
        csv_path = Path(csv_file)
        if csv_path.exists():
            line_count = sum(1 for _ in csv_path.open())
            if line_count > DEFAULT_BATCH_SIZE + 1:  # +1 for header
                batch_size = DEFAULT_BATCH_SIZE
                logger.info(f"Auto-enabling batching (file has {line_count - 1} rows)")

    asyncio.run(run(base64_csv or "", update_clients, create_groups, fmt, csv_file, debug, batch_size))


if __name__ == "__main__":
    main()
