#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "click>=8.0.0",
#     "pyyaml>=6.0.0",
#     "pytest>=7.0.0",
#     "pytest-asyncio>=0.23.0",
# ]
# ///
"""Tests for cam-network-clients.py"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

spec = importlib.util.spec_from_file_location("cam_network_clients", Path(__file__).parent / "cam-network-clients.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["cam_network_clients"] = mod
spec.loader.exec_module(mod)

SAMPLE_CLIENTS = [
    {
        "id": "k74272e",
        "mac": "AA:BB:CC:DD:EE:01",
        "ip": "10.0.1.10",
        "description": "John's laptop",
        "status": "Online",
        "ssid": "Corp-WiFi",
        "os": "macOS",
        "manufacturer": "Apple",
        "usage": {"sent": 100.0, "recv": 200.0},
        "_networkId": "N_1",
        "_networkName": "HQ",
    },
    {
        "id": "k74272f",
        "mac": "AA:BB:CC:DD:EE:02",
        "ip": "10.0.2.20",
        "description": "Guest phone",
        "status": "Offline",
        "ssid": "Guest-WiFi",
        "os": "Android",
        "manufacturer": "Samsung",
        "usage": {"sent": 10.0, "recv": 20.0},
        "_networkId": "N_1",
        "_networkName": "HQ",
    },
    {
        "id": "k742730",
        "mac": "AA:BB:CC:DD:EE:03",
        "ip": "10.0.1.30",
        "description": "Alice's workstation",
        "status": "Online",
        "ssid": "Corp-WiFi",
        "os": "Windows",
        "manufacturer": "Dell",
        "usage": {"sent": 300.0, "recv": 400.0},
        "_networkId": "N_2",
        "_networkName": "Branch",
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
        assert parsed[0]["_networkId"] == "N_1"


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
        assert "_networkId" in lines[0]

    def test_leading_columns_are_mac_group_description(self):
        result = mod.format_csv(SAMPLE_CLIENTS)
        header = result.strip().split("\n")[0]
        assert header.startswith("MAC address,Endpoint device group,Description,")

    def test_endpoint_device_group_column_is_blank(self):
        result = mod.format_csv(SAMPLE_CLIENTS)
        rows = result.strip().split("\n")[1:]
        for row in rows:
            fields = row.split(",")
            assert fields[1] == ""

    def test_csv_flattens_nested_fields(self):
        result = mod.format_csv(SAMPLE_CLIENTS)
        assert "100.0" in result

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

    def test_leading_columns_are_mac_group_description(self):
        result = mod.format_table(SAMPLE_CLIENTS)
        header = result.strip().split("\n")[0]
        assert header.startswith("| MAC address | Endpoint device group | Description |")

    def test_table_has_same_columns_as_csv(self):
        csv_result = mod.format_csv(SAMPLE_CLIENTS)
        table_result = mod.format_table(SAMPLE_CLIENTS)
        csv_headers = csv_result.strip().split("\n")[0].split(",")
        table_header_line = table_result.strip().split("\n")[0]
        table_headers = [h.strip() for h in table_header_line.strip("|").split("|")]
        assert len(table_headers) == len(csv_headers)

    def test_table_handles_empty_list(self):
        result = mod.format_table([])
        assert result == ""


class TestFilterClients:
    def test_filter_by_top_level_field(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("status", "Online")])
        assert len(filtered) == 2
        assert all(c["status"] == "Online" for c in filtered)

    def test_filter_case_insensitive_contains(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("ssid", "guest")])
        assert len(filtered) == 1
        assert filtered[0]["ssid"] == "Guest-WiFi"

    def test_filter_by_network(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("_networkId", "N_2")])
        assert len(filtered) == 1
        assert filtered[0]["description"] == "Alice's workstation"

    def test_no_filters_returns_all(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [])
        assert len(filtered) == 3

    def test_multiple_filters_intersect(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("status", "Online"), ("_networkId", "N_1")])
        assert len(filtered) == 1
        assert filtered[0]["mac"] == "AA:BB:CC:DD:EE:01"

    def test_filter_nested_field_dot_notation(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("usage.sent", "300")])
        assert len(filtered) == 1
        assert filtered[0]["id"] == "k742730"

    def test_filter_nonexistent_field_returns_empty(self):
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("nonexistent", "value")])
        assert len(filtered) == 0


class TestFetchAllNetworks:
    @pytest.mark.asyncio
    async def test_single_page(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": "N_1", "name": "HQ"}, {"id": "N_2", "name": "Branch"}]
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await mod.fetch_all_networks(mock_client, "org123")
        assert len(result) == 2
        assert result[0]["id"] == "N_1"

    @pytest.mark.asyncio
    async def test_pagination(self):
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = [{"id": "N_1", "name": "HQ"}]
        page1.headers = {"Link": '<https://api.meraki.com/api/v1/organizations/org123/networks?startingAfter=abc>; rel="next"'}

        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = [{"id": "N_2", "name": "Branch"}]
        page2.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[page1, page2])

        result = await mod.fetch_all_networks(mock_client, "org123")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_rate_limit_retry(self):
        rate_response = MagicMock()
        rate_response.status_code = 429
        rate_response.headers = {"Retry-After": "0.01"}

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = [{"id": "N_1", "name": "HQ"}]
        ok_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_response, ok_response])

        result = await mod.fetch_all_networks(mock_client, "org123")
        assert len(result) == 1


class TestFetchNetworkClients:
    @pytest.mark.asyncio
    async def test_single_page(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_CLIENTS
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await mod.fetch_network_clients(mock_client, "N_1")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_batch_size_parameter(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_CLIENTS
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await mod.fetch_network_clients(mock_client, "N_1", batch_size=500)

        call_args = mock_client.get.call_args
        assert call_args.kwargs["params"]["perPage"] == 500

    @pytest.mark.asyncio
    async def test_limit_parameter(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_CLIENTS * 100  # 300 items
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await mod.fetch_network_clients(mock_client, "N_1", limit=10)
        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_pagination(self):
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = SAMPLE_CLIENTS[:2]
        page1.headers = {"Link": '<https://api.meraki.com/api/v1/networks/N_1/clients?startingAfter=abc>; rel="next"'}

        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = SAMPLE_CLIENTS[2:]
        page2.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[page1, page2])

        result = await mod.fetch_network_clients(mock_client, "N_1")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_rate_limit_retry(self):
        rate_response = MagicMock()
        rate_response.status_code = 429
        rate_response.headers = {"Retry-After": "0.01"}

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = SAMPLE_CLIENTS
        ok_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_response, ok_response])

        result = await mod.fetch_network_clients(mock_client, "N_1")
        assert len(result) == 3


class TestFetchNetworkClientsBounded:
    @pytest.mark.asyncio
    async def test_tags_clients_with_network_context(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": "1", "mac": "AA:BB:CC:DD:EE:01"}]
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        semaphore = asyncio.Semaphore(1)
        result = await mod.fetch_network_clients_bounded(
            semaphore, mock_client, {"id": "N_1", "name": "HQ"}, mod.MAX_TIMESPAN, 1000, 0
        )
        assert len(result) == 1
        assert result[0]["_networkId"] == "N_1"
        assert result[0]["_networkName"] == "HQ"

    @pytest.mark.asyncio
    async def test_swallows_errors_for_single_network(self):
        request = httpx.Request("GET", "https://api.meraki.com/api/v1/networks/N_1/clients")
        error_response = httpx.Response(400, request=request)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("Bad Request", request=request, response=error_response)
        )

        semaphore = asyncio.Semaphore(1)
        result = await mod.fetch_network_clients_bounded(
            semaphore, mock_client, {"id": "N_1", "name": "HQ"}, mod.MAX_TIMESPAN, 1000, 0
        )
        assert result == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
