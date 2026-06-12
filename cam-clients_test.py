"""Tests for cam-clients-export.py"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

spec = importlib.util.spec_from_file_location("cam_clients_export", Path(__file__).parent / "cam-clients-export.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["cam_clients_export"] = mod
spec.loader.exec_module(mod)

SAMPLE_CLIENTS = [
    {
        "id": "1",
        "type": "corporate",
        "owner": "jsmith",
        "mac": "AA:BB:CC:DD:EE:01",
        "description": "John's laptop",
        "status": "Connected",
        "ssid": "Corp-WiFi",
        "source": "Provisioned",
        "ipAddress": "10.0.1.10",
        "lastLogin": {"timestamp": "2026-05-20T10:00:00Z", "location": "Building A"},
        "firstLogin": {"timestamp": "2026-01-15T08:00:00Z", "location": "Building A"},
        "classification": {"type": "Laptop", "manufacturer": "Apple", "model": "MacBook Pro", "os": "macOS"},
    },
    {
        "id": "2",
        "type": "BYOD",
        "owner": "guest1",
        "mac": "AA:BB:CC:DD:EE:02",
        "description": "Guest phone",
        "status": "Disconnected",
        "ssid": "Guest-WiFi",
        "source": "Discovered",
        "ipAddress": "10.0.2.20",
        "lastLogin": {"timestamp": "2026-05-10T14:30:00Z", "location": "Lobby"},
        "firstLogin": {"timestamp": "2026-05-10T14:00:00Z", "location": "Lobby"},
        "classification": {"type": "Phone", "manufacturer": "Samsung", "model": "Galaxy S24", "os": "Android"},
    },
    {
        "id": "3",
        "type": "corporate",
        "owner": "alee",
        "mac": "AA:BB:CC:DD:EE:03",
        "description": "Alice's workstation",
        "status": "Connected",
        "ssid": "Corp-WiFi",
        "source": "Provisioned",
        "ipAddress": "10.0.1.30",
        "lastLogin": {"timestamp": "2026-05-25T09:00:00Z", "location": "Building B"},
        "firstLogin": {"timestamp": "2025-11-01T07:00:00Z", "location": "Building B"},
        "classification": {"type": "Workstation", "manufacturer": "Dell", "model": "OptiPlex", "os": "Windows"},
    },
]


class TestFormatJson:
    def test_outputs_valid_json(self):
        result = mod.format_json(SAMPLE_CLIENTS)
        parsed = json.loads(result)
        assert len(parsed) == 3

    def test_json_contains_all_fields(self):
        result = mod.format_json(SAMPLE_CLIENTS)
        parsed = json.loads(result)
        assert parsed[0]["mac"] == "AA:BB:CC:DD:EE:01"
        assert parsed[0]["owner"] == "jsmith"


class TestFormatYaml:
    def test_outputs_valid_yaml(self):
        import yaml

        result = mod.format_yaml(SAMPLE_CLIENTS)
        parsed = yaml.safe_load(result)
        assert len(parsed) == 3

    def test_yaml_contains_fields(self):
        import yaml

        result = mod.format_yaml(SAMPLE_CLIENTS)
        parsed = yaml.safe_load(result)
        assert parsed[1]["mac"] == "AA:BB:CC:DD:EE:02"


class TestFormatCsv:
    def test_outputs_csv_with_headers(self):
        result = mod.format_csv(SAMPLE_CLIENTS)
        lines = result.strip().split("\n")
        assert len(lines) == 4  # header + 3 rows
        assert "mac" in lines[0]

    def test_csv_flattens_nested_fields(self):
        result = mod.format_csv(SAMPLE_CLIENTS)
        assert "2026-05-20T10:00:00Z" in result

    def test_csv_handles_empty_list(self):
        result = mod.format_csv([])
        assert result == ""


class TestFormatTable:
    def test_outputs_markdown_table(self):
        result = mod.format_table(SAMPLE_CLIENTS)
        lines = result.strip().split("\n")
        assert len(lines) == 5  # header + separator + 3 rows
        assert lines[1].startswith("| ---")
        assert "AA:BB:CC:DD:EE:01" in result

    def test_table_has_same_columns_as_csv(self):
        csv_result = mod.format_csv(SAMPLE_CLIENTS)
        table_result = mod.format_table(SAMPLE_CLIENTS)
        csv_headers = csv_result.strip().split("\n")[0].split(",")
        table_header_line = table_result.strip().split("\n")[0]
        table_headers = [h.strip() for h in table_header_line.strip("|").split("|")]
        assert len(table_headers) == len(csv_headers)

    def test_table_contains_nested_fields(self):
        result = mod.format_table(SAMPLE_CLIENTS)
        assert "2026-05-20T10:00:00Z" in result
        assert "Building A" in result
        assert "macOS" in result

    def test_table_handles_empty_list(self):
        result = mod.format_table([])
        assert result == ""


class TestFilterClients:
    def test_filter_by_top_level_field(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("status", "Connected")])
        assert len(filtered) == 2
        assert all(c["status"] == "Connected" for c in filtered)

    def test_filter_case_insensitive_startswith(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("ssid", "guest")])
        assert len(filtered) == 1
        assert filtered[0]["ssid"] == "Guest-WiFi"

    def test_filter_by_source(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("source", "Discovered")])
        assert len(filtered) == 1

    def test_filter_by_owner(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("owner", "jsmith")])
        assert len(filtered) == 1

    def test_no_filters_returns_all(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [])
        assert len(filtered) == 3

    def test_multiple_filters_intersect(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("status", "Connected"), ("source", "Provisioned")])
        assert len(filtered) == 2

    def test_filter_nested_field_dot_notation(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("classification.os", "mac")])
        assert len(filtered) == 1
        assert filtered[0]["owner"] == "jsmith"

    def test_filter_nested_field_manufacturer(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("classification.manufacturer", "apple")])
        assert len(filtered) == 1
        assert filtered[0]["mac"] == "AA:BB:CC:DD:EE:01"

    def test_filter_nonexistent_field_returns_empty(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("nonexistent", "value")])
        assert len(filtered) == 0

    def test_filter_partial_startswith(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("description", "John")])
        assert len(filtered) == 1
        assert filtered[0]["owner"] == "jsmith"


class TestFetchAllClients:
    @pytest.mark.asyncio
    async def test_single_page(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": SAMPLE_CLIENTS}
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await mod.fetch_all_clients(mock_client, "org123")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_pagination(self):
        page1_response = MagicMock()
        page1_response.status_code = 200
        page1_response.json.return_value = {"items": SAMPLE_CLIENTS[:2]}
        page1_response.headers = {"Link": '<https://api.meraki.com/api/v1/organizations/org123/nac/clients?startingAfter=abc>; rel="next"'}

        page2_response = MagicMock()
        page2_response.status_code = 200
        page2_response.json.return_value = {"items": SAMPLE_CLIENTS[2:]}
        page2_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[page1_response, page2_response])

        result = await mod.fetch_all_clients(mock_client, "org123")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_rate_limit_retry(self):
        rate_response = MagicMock()
        rate_response.status_code = 429
        rate_response.headers = {"Retry-After": "0.01"}

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {"items": SAMPLE_CLIENTS}
        ok_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_response, ok_response])

        result = await mod.fetch_all_clients(mock_client, "org123")
        assert len(result) == 3
