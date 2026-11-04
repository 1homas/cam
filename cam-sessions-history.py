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
Download Cisco Access Manager (CAM) NAC session history.

Fetches GET /organizations/{organizationId}/nac/sessions/history across all
pages (numeric `startingAfter` offset pagination, 429 retry with Retry-After)
and prints the result to stdout, or writes it to --output. The endpoint only
accepts a lookback window of <= 31 days, so "all sessions" is the maximum
31-day window by default.

JSON and YAML wrap the sessions in a payload with organizationId, fetchedAt,
window, and count metadata; CSV, JSONL (default), and table emit session rows
only.

Usage:
    cam-sessions-history.py [--org ORG_ID] [--days N | --t0 TS [--t1 TS]] [-p PAGES]
                            [--format json|jsonl|yaml|csv|table] [-o FILE] [--pretty] [-v]

Options:
    --org ORG_ID        Organization ID (or set MERAKI_ORG_ID)
    --days N            Days of history to fetch (default: 31, the API max)
    --t0 TS             Absolute start (ISO 8601, e.g. 2026-08-01T00:00:00Z). Overrides --days
    --t1 TS             Absolute end (ISO 8601). Bounds the window to [t0, t1]; requires --t0
    -p, --pages PAGES   Pages to fetch, perPage=1000 (default: 0, all). Ignored
                        (forced to 0) whenever --days or --t0/--t1 is given
                        explicitly, since a requested window must be returned
                        in full
    --format FORMAT     json|jsonl|yaml|csv|table (default: jsonl)
    -o, --output FILE   Write output to this file instead of stdout
    --pretty            Pretty-print JSON output
    -v, --verbose       Enable verbose logging

Examples:
    # Fetch all pages for the full 31-day window, as JSONL
    cam-sessions-history.py

    # Cap at the first page (1000 sessions); only applies with no --days/--t0
    cam-sessions-history.py --pages 1

    # Pipe JSONL to jq for the most recent session
    cam-sessions-history.py | tail -1 | jq .

    # Session counts by result
    cam-sessions-history.py | jq -s '[.[].status] | group_by(.) | map({status: .[0], total: length})'

    # Last 7 days as JSON with metadata, pretty-printed, to a file
    cam-sessions-history.py --days 7 --format json --pretty -o ~/Downloads/sessions.json

    # Session rows as CSV for a spreadsheet
    cam-sessions-history.py --format csv > sessions.csv

    # Quick table preview of the last day
    cam-sessions-history.py --days 1 --format table

    # Override the organization, with progress logging
    cam-sessions-history.py --org 123456 --days 14 -v

    # Absolute start (see caveat below)
    cam-sessions-history.py --t0 2026-08-01T00:00:00Z

Caveat: --t1 is a valid, intentional option — an optional upper bound on the
--t0 lookback window. The problem is on Meraki's side: the live API does not
honor t0/t1 at all. It silently ignores both and falls back to its own short
default window (~1 hour), returning only the most recent handful of sessions
regardless of the requested range. Confirmed against the live API
(2026-10-20): --t0 set 25 days back returned the same 12 sessions as a bare
call with no params, while --days 31 (timespan) correctly returned 792
sessions spanning the full 31-day range. Use --days (timespan) for reliable
results until Meraki fixes t0/t1 handling on this endpoint.
"""

import asyncio
import csv
import io
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cam-sessions-history")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)

BASE_URL = "https://api.meraki.com/api/v1"
MAX_LOOKBACK_DAYS = 31
MAX_TIMESPAN = 2678400  # 31 days in seconds, the Meraki API maximum
DEFAULT_PER_PAGE = 1000  # Meraki max for sessions/history

# (header, dotted field path) pairs for CSV/table output.
CSV_COLUMNS: list[tuple[str, str]] = [
    ("ts", "ts"),
    ("sessionId", "sessionId"),
    ("status", "status"),
    ("user.id", "user.id"),
    ("client.id", "client.id"),
    ("authentication.protocol", "authentication.protocol"),
    ("ssid.name", "ssid.name"),
    ("classification.type", "classification.type"),
    ("details.ruleDetails", "details.ruleDetails"),
    ("counts.events.total", "counts.events.total"),
    ("client.url", "client.url"),
    ("device.url", "device.url"),
]


def build_params(days: int, t0: Optional[str], t1: Optional[str]) -> dict:
    """Build the time-window query parameters for sessions/history.

    `t0`/`t1` define an absolute range; otherwise `days` becomes a `timespan`
    in seconds, capped at the API maximum. Never send both — Meraki returns
    400 if `t0` and `timespan` are combined.
    """
    if t1 and not t0:
        raise ValueError("t1 requires t0 (t1 bounds the [t0, t1] window)")

    if t0:
        params = {"t0": t0}
        if t1:
            params["t1"] = t1
        return params

    return {"timespan": min(days * 86400, MAX_TIMESPAN)}


async def fetch_sessions_history(
    client: httpx.AsyncClient,
    org_id: str,
    days: int = MAX_LOOKBACK_DAYS,
    t0: Optional[str] = None,
    t1: Optional[str] = None,
    per_page: int = DEFAULT_PER_PAGE,
    max_pages: Optional[int] = None,
) -> list[dict]:
    """Fetch all session history pages for the organization.

    Meraki's `Link` header for this endpoint advances via a numeric
    `startingAfter` row offset (0, 1000, 2000, ...), not an opaque cursor, so
    the offset is driven directly with `perPage`. Stops on a short or empty
    page. Retries on HTTP 429 using `Retry-After`. `max_pages` of `None` or
    `0` fetches every page.
    """
    params = build_params(days, t0, t1)
    params["perPage"] = per_page

    url = f"{BASE_URL}/organizations/{org_id}/nac/sessions/history"
    sessions: list[dict] = []
    offset = 0
    pages = 0

    while True:
        response = await client.get(url, params={**params, "startingAfter": offset})

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 1))
            logger.warning(f"Rate limited, sleeping {retry_after}s")
            await asyncio.sleep(retry_after)
            continue

        response.raise_for_status()
        payload = response.json()
        items = payload if isinstance(payload, list) else (payload.get("items") or [])

        sessions.extend(items)
        pages += 1
        logger.info(f"Fetched {len(items)} sessions at offset {offset} (total: {len(sessions)})")

        if len(items) < per_page:
            break

        if max_pages and pages >= max_pages:
            logger.warning(f"Pagination hit max_pages limit ({max_pages}); remaining pages not fetched")
            break

        offset += len(items)

    return sessions


def build_payload(
    org_id: str, sessions: list[dict], days: int, t0: Optional[str], t1: Optional[str]
) -> dict:
    """Wrap sessions with organization, fetch time, and requested window metadata."""
    return {
        "organizationId": org_id,
        "fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {"days": None if t0 else days, "t0": t0, "t1": t1},
        "count": len(sessions),
        "sessions": sessions,
    }


def _get_nested(obj: dict, dotted_key: str) -> str:
    """Resolve a dotted key path like 'authentication.protocol' from a nested dict."""
    current = obj
    for part in dotted_key.split("."):
        if isinstance(current, dict):
            current = current.get(part, "")
        else:
            return ""
    return str(current) if current not in (None, "") else ""


def format_json(payload: dict, pretty: bool = False) -> str:
    """Format the full payload (metadata + sessions) as JSON."""
    return json.dumps(payload, indent=2 if pretty else None, default=str)


def format_yaml(payload: dict, pretty: bool = False) -> str:
    """Format the full payload (metadata + sessions) as YAML."""
    return yaml.dump(payload, default_flow_style=False, sort_keys=False)


def format_jsonl(payload: dict, pretty: bool = False) -> str:
    """Format sessions as JSON Lines (one compact session per line)."""
    return "\n".join(json.dumps(s, default=str) for s in payload["sessions"])


def format_csv(payload: dict, pretty: bool = False) -> str:
    """Format sessions as CSV with flattened nested fields."""
    if not payload["sessions"]:
        return ""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([header for header, _ in CSV_COLUMNS])
    for session in payload["sessions"]:
        writer.writerow([_get_nested(session, field) for _, field in CSV_COLUMNS])
    return output.getvalue()


def format_table(payload: dict, pretty: bool = False) -> str:
    """Format sessions as a Markdown table with the same columns as CSV."""
    if not payload["sessions"]:
        return ""
    headers = [header for header, _ in CSV_COLUMNS]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for session in payload["sessions"]:
        lines.append("| " + " | ".join(_get_nested(session, field) for _, field in CSV_COLUMNS) + " |")
    return "\n".join(lines)


FORMATTERS = {
    "json": format_json,
    "jsonl": format_jsonl,
    "yaml": format_yaml,
    "csv": format_csv,
    "table": format_table,
}


async def run(
    org_id: Optional[str],
    days: int,
    t0: Optional[str],
    t1: Optional[str],
    pages: int,
    fmt: str,
    output: Optional[Path],
    pretty: bool,
) -> None:
    """Fetch session history and write it to a file or stdout."""
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")

    if not api_key:
        logger.error("MERAKI_DASHBOARD_API_KEY not set")
        sys.exit(1)
    if not org_id:
        logger.error("Organization ID required (--org or MERAKI_ORG_ID)")
        sys.exit(1)

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=httpx.Timeout(60.0),
    ) as client:
        if t0:
            logger.info(f"Fetching sessions from t0={t0}" + (f" to t1={t1}" if t1 else " to now"))
        else:
            logger.info(f"Fetching sessions for the last {days} day(s)")
        sessions = await fetch_sessions_history(client, org_id, days=days, t0=t0, t1=t1, max_pages=pages)

    logger.info(f"Fetched {len(sessions)} sessions from the API")

    payload = build_payload(org_id, sessions, days, t0, t1)
    text = FORMATTERS[fmt](payload, pretty)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text if text.endswith("\n") else text + "\n")
        logger.info(f"Wrote {len(sessions)} sessions to {output}")
    elif text:
        click.echo(text)


def _examples_from_docstring() -> str:
    """Slice the Examples: block out of the module docstring for reuse as the --help epilog.

    Each example (comment + command) is prefixed with Click's "\\b" marker so
    its line breaks survive Click's paragraph rewrapping.
    """
    lines = __doc__.splitlines()
    start = lines.index("Examples:")
    end = next(i for i in range(start + 1, len(lines)) if lines[i] and not lines[i][0].isspace())
    body = [line.strip() for line in lines[start + 1 : end] if line.strip()]
    blocks = ["\b\n" + "\n".join(body[i : i + 2]) for i in range(0, len(body), 2)]
    return "Examples:\n\n" + "\n\n".join(blocks)


@click.command(epilog=_examples_from_docstring())
@click.option("--org", "org_id", envvar="MERAKI_ORG_ID", help="Organization ID (or set MERAKI_ORG_ID)")
@click.option("--days", default=MAX_LOOKBACK_DAYS, type=int, help=f"Days of history to fetch (default: {MAX_LOOKBACK_DAYS}, the API max)")
@click.option("--t0", default=None, help="Absolute start (ISO 8601, e.g. 2026-08-01T00:00:00Z). Overrides --days")
@click.option("--t1", default=None, help="Absolute end (ISO 8601). Bounds the window to [t0, t1]; requires --t0")
@click.option("-p", "--pages", default=0, type=int, help="Pages to fetch (default: 0, all; ignored if --days or --t0/--t1 is given explicitly)")
@click.option("--format", "fmt", default="jsonl", type=click.Choice(["json", "jsonl", "yaml", "csv", "table"]), help="Output format (default: jsonl)")
@click.option("--output", "-o", "output", type=click.Path(path_type=Path), default=None, help="Write output to this file instead of stdout")
@click.option("--pretty", is_flag=True, default=False, help="Pretty-print JSON output")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging")
def main(
    org_id: Optional[str],
    days: int,
    t0: Optional[str],
    t1: Optional[str],
    pages: int,
    fmt: str,
    output: Optional[Path],
    pretty: bool,
    verbose: bool,
) -> None:
    """Download CAM NAC session history as JSON, YAML, CSV, JSONL, or a Markdown table."""
    if verbose:
        logger.setLevel(logging.INFO)

    if t1 and not t0:
        raise click.UsageError("--t1 requires --t0")
    if days < 1:
        raise click.UsageError("--days must be at least 1")
    if days > MAX_LOOKBACK_DAYS:
        logger.warning(f"--days {days} exceeds API max; capping at {MAX_LOOKBACK_DAYS}")
        days = MAX_LOOKBACK_DAYS
    if pages < 0:
        raise click.UsageError("--pages must be 0 (all) or a positive integer")

    days_explicit = click.get_current_context().get_parameter_source("days") == click.core.ParameterSource.COMMANDLINE
    if pages and (days_explicit or t0):
        logger.warning("--days or --t0/--t1 given explicitly; ignoring --pages limit to fetch the full window")
        pages = 0

    asyncio.run(run(org_id, days, t0, t1, pages, fmt, output, pretty))


if __name__ == "__main__":
    main()
