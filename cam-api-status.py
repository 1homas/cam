#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///
"""
Quickly test Cisco Access Manager REST APIs and save responses.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

API_BASE_URL = "https://api.meraki.com/api/v1/organizations"
API_TEST_DIR = Path("api_test")
DEFAULT_TIMEOUT_SECONDS = 30

ENDPOINTS = [
    "/nac/authorization/policies",
    "/nac/certificates",
    "/nac/certificates/authorities/crls",
    "/nac/certificates/authorities/crls/descriptors",
    "/nac/certificates/overview",
    "/nac/clients",
    "/nac/clients/groups",
    "/nac/clients/overview",
    "/nac/dictionaries",
    "/nac/license/usage?startDate=2026-01-01",
    "/nac/sessions/history",
]


def load_env_file(path: Path) -> None:
    """Load key=value pairs from a .env file without overriding existing environment variables.

    Args:
        path (Path): Location of the .env file.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require_env(name: str) -> str:
    """Read a required environment variable or exit with a helpful message.

    Args:
        name (str): Environment variable name to read.

    Returns:
        str: The resolved environment variable value.
    """
    value = os.getenv(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def build_headers(api_key: str) -> Dict[str, str]:
    """Build request headers for Meraki API calls.

    Args:
        api_key (str): Meraki Dashboard API key.

    Returns:
        Dict[str, str]: Headers for GET requests.
    """
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def sanitize_filename(value: str) -> str:
    """Convert a URL path into a filesystem-friendly filename.

    Args:
        value (str): URL path or query string.

    Returns:
        str: Sanitized filename.
    """
    return value.strip("/").replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "_") or "root"


def write_json(filepath: Path, payload: Any) -> None:
    """Write JSON payloads to disk with consistent formatting.

    Args:
        filepath (Path): Output file path.
        payload (Any): JSON-serializable payload.
    """
    filepath.write_text(json.dumps(payload, indent=2, sort_keys=True))


def record_status(statuses: List[Tuple[str, int, str, str]], status_code: int, method: str, url: str) -> None:
    """Store a status summary entry for later printing.

    Args:
        statuses (List[Tuple[str, int, str, str]]): Accumulator for status summaries.
        status_code (int): HTTP status code.
        method (str): HTTP method.
        url (str): Request URL.
    """
    icon = "✅" if 200 <= status_code < 300 else "❌"
    statuses.append((icon, status_code, method, url))


def request_json(
    method: str,
    url: str,
    headers: Dict[str, str],
    statuses: List[Tuple[str, int, str, str]],
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Any] = None,
) -> Optional[Any]:
    """Fetch JSON data from the API and update the status list.

    Args:
        method (str): HTTP method.
        url (str): Request URL.
        headers (Dict[str, str]): HTTP headers.
        statuses (List[Tuple[str, int, str, str]]): Status accumulator list.
        params (Optional[Dict[str, Any]]): Optional query parameters.
        payload (Optional[Any]): Optional JSON payload for write operations.

    Returns:
        Optional[Any]: Parsed JSON payload if successful, otherwise None.
    """
    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=payload,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"Request failed for {url}: {exc}", file=sys.stderr)
        record_status(statuses, 0, method.upper(), url)
        return None
    record_status(statuses, response.status_code, response.request.method, response.url)
    if response.status_code < 200 or response.status_code >= 300:
        print(f"Request failed ({response.status_code}) for {response.url}", file=sys.stderr)
        return None
    try:
        return response.json()
    except ValueError:
        print(f"Non-JSON response for {response.url}", file=sys.stderr)
        return None


def fetch_endpoint(
    base_url: str,
    endpoint: str,
    headers: Dict[str, str],
    statuses: List[Tuple[str, int, str, str]],
    method: str = "GET",
    payload: Optional[Any] = None,
) -> Optional[Any]:
    """Fetch a single endpoint response.

    Args:
        base_url (str): Base API URL.
        endpoint (str): Endpoint path including leading slash.
        headers (Dict[str, str]): HTTP headers.
        statuses (List[Tuple[str, int, str, str]]): Status accumulator list.
        method (str): HTTP method.
        payload (Optional[Any]): Optional JSON payload for write operations.

    Returns:
        Optional[Any]: Parsed JSON payload if successful, otherwise None.
    """
    url = f"{base_url}{endpoint}"
    return request_json(method, url, headers, statuses, payload=payload)


def save_endpoint_output(endpoint: str, payload: Any) -> None:
    """Save endpoint payload to a filename derived from the endpoint path.

    Args:
        endpoint (str): Endpoint path or query string.
        payload (Any): JSON-serializable payload.
    """
    filename = f"{sanitize_filename(endpoint)}.json"
    write_json(API_TEST_DIR / filename, payload)


def fetch_paged_clients(
    base_url: str,
    headers: Dict[str, str],
    statuses: List[Tuple[str, int, str, str]],
) -> None:
    """Fetch /nac/clients by page, using meta.totalCount to determine pagination.

    Args:
        base_url (str): Base API URL.
        headers (Dict[str, str]): HTTP headers.
        statuses (List[Tuple[str, int, str, str]]): Status accumulator list.
    """
    page = 1
    items: List[Dict[str, Any]] = []
    total_count = None
    while True:
        endpoint = f"/nac/clients?page={page}"
        payload = fetch_endpoint(base_url, endpoint, headers, statuses)
        if payload is None:
            break
        save_endpoint_output(endpoint, payload)
        meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
        if total_count is None:
            # Use meta.totalCount on the first page to drive pagination.
            total_count = meta.get("totalCount")
        page_items = payload.get("items", []) if isinstance(payload, dict) else []
        if isinstance(page_items, list):
            items.extend(page_items)
        if total_count is None or not isinstance(total_count, int):
            break
        if len(items) >= total_count:
            break
        page += 1


def fetch_dictionary_attributes(
    base_url: str,
    headers: Dict[str, str],
    statuses: List[Tuple[str, int, str, str]],
    dictionaries_payload: Any,
) -> None:
    """Fetch dictionary attributes for each dictionaryId found.

    Args:
        base_url (str): Base API URL.
        headers (Dict[str, str]): HTTP headers.
        statuses (List[Tuple[str, int, str, str]]): Status accumulator list.
        dictionaries_payload (Any): Response from /nac/dictionaries.
    """
    if not isinstance(dictionaries_payload, list):
        return
    for dictionary in dictionaries_payload:
        dictionary_id = dictionary.get("dictionaryId")
        if not dictionary_id:
            continue
        endpoint = f"/nac/dictionaries/{dictionary_id}/attributes"
        payload = fetch_endpoint(base_url, endpoint, headers, statuses)
        if payload is not None:
            save_endpoint_output(endpoint, payload)


def fetch_session_details(
    base_url: str,
    headers: Dict[str, str],
    statuses: List[Tuple[str, int, str, str]],
    sessions_payload: Any,
) -> None:
    """Fetch session details for each sessionId found in the sessions history payload.

    Args:
        base_url (str): Base API URL.
        headers (Dict[str, str]): HTTP headers.
        statuses (List[Tuple[str, int, str, str]]): Status accumulator list.
        sessions_payload (Any): Response from /nac/sessions/history.
    """
    if not isinstance(sessions_payload, dict):
        return
    items = sessions_payload.get("items")
    if not isinstance(items, list):
        return
    for session in items:
        session_id = session.get("id") or session.get("sessionId")
        if not session_id:
            continue
        endpoint = f"/nac/sessions/{session_id}/details"
        payload = fetch_endpoint(base_url, endpoint, headers, statuses)
        if payload is not None:
            save_endpoint_output(endpoint, payload)


def print_statuses(statuses: Iterable[Tuple[str, int, str, str]]) -> None:
    """Print status lines in the requested summary format.

    Args:
        statuses (Iterable[Tuple[str, int, str, str]]): Status summary entries.
    """
    for icon, status_code, method, url in statuses:
        print(f"{icon} {status_code} {method} {url}")


def main() -> None:
    """Run the API test workflow and save responses to disk."""
    load_env_file(Path(".env"))
    meraki_org_id = require_env("MERAKI_ORG_ID")
    api_key = require_env("MERAKI_DASHBOARD_API_KEY")

    API_TEST_DIR.mkdir(parents=True, exist_ok=True)
    base_url = f"{API_BASE_URL}/{meraki_org_id}"
    headers = build_headers(api_key)
    statuses: List[Tuple[str, int, str, str]] = []

    dictionaries_payload = None
    sessions_payload = None

    for endpoint in ENDPOINTS:
        payload = fetch_endpoint(base_url, endpoint, headers, statuses)
        if payload is None:
            continue
        save_endpoint_output(endpoint, payload)
        if endpoint == "/nac/dictionaries":
            dictionaries_payload = payload
        if endpoint == "/nac/sessions/history":
            sessions_payload = payload

    fetch_paged_clients(base_url, headers, statuses)
    if dictionaries_payload is not None:
        fetch_dictionary_attributes(base_url, headers, statuses, dictionaries_payload)
    if sessions_payload is not None:
        fetch_session_details(base_url, headers, statuses, sessions_payload)

    print_statuses(statuses)


if __name__ == "__main__":
    main()
