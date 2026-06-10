#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest>=8.0.0",
#     "pytest-asyncio>=0.23.0",
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "click>=8.0.0",
#     "pyyaml>=6.0.0",
# ]
# ///
"""Tests for cam-clients.py"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

spec = importlib.util.spec_from_file_location("cam_clients", Path(__file__).parent / "cam-clients.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["cam_clients"] = mod
spec.loader.exec_module(mod)


@pytest.fixture
def sample_clients():
    """Sample client data for testing."""
    return [
        {
            "id": "c1",
            "mac": "00:11:22:33:44:55",
            "status": "Connected",
            "ssid": "Corporate",
            "type": "Device",
        },
        {
            "id": "c2",
            "mac": "AA:BB:CC:DD:EE:FF",
            "status": "Disconnected",
            "ssid": "Guest",
            "type": "Device",
        },
    ]


@pytest.mark.asyncio
async def test_fetch_all_clients_success(sample_clients):
    """Test successful client fetch."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"items": sample_clients}
    mock_response.headers = {}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    result = await mod.fetch_all_clients(mock_client, "org123")
    assert len(result) == 2
    assert result[0]["mac"] == "00:11:22:33:44:55"


@pytest.mark.asyncio
async def test_fetch_all_clients_with_pagination():
    """Test client fetch with pagination."""
    page1 = {"items": [{"id": "c1", "mac": "00:11:22:33:44:55"}]}
    page2 = {"items": [{"id": "c2", "mac": "AA:BB:CC:DD:EE:FF"}]}

    mock_response1 = Mock()
    mock_response1.status_code = 200
    mock_response1.json.return_value = page1
    mock_response1.headers = {"Link": '<https://api.meraki.com?startingAfter=token123>; rel="next"'}

    mock_response2 = Mock()
    mock_response2.status_code = 200
    mock_response2.json.return_value = page2
    mock_response2.headers = {}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[mock_response1, mock_response2])

    result = await mod.fetch_all_clients(mock_client, "org123")
    assert len(result) == 2


def test_parse_next_starting_after():
    """Test Link header parsing."""
    link = '<https://api.meraki.com/api/v1/organizations/123/nac/clients?startingAfter=abc123>; rel="next"'
    assert mod.parse_next_starting_after(link) == "abc123"

    assert mod.parse_next_starting_after("") is None
    assert mod.parse_next_starting_after('<https://example.com>; rel="prev"') is None


def test_format_json(sample_clients):
    """Test JSON formatting."""
    result = mod.format_json(sample_clients)
    parsed = json.loads(result)
    assert len(parsed) == 2
    assert parsed[0]["mac"] == "00:11:22:33:44:55"


def test_format_yaml(sample_clients):
    """Test YAML formatting."""
    result = mod.format_yaml(sample_clients)
    assert "mac: 00:11:22:33:44:55" in result
    assert "status: Connected" in result


def test_format_csv(sample_clients):
    """Test CSV formatting."""
    result = mod.format_csv(sample_clients)
    lines = result.strip().split("\n")
    assert len(lines) == 3  # header + 2 rows
    assert "id,mac,status,ssid,type" in lines[0]


def test_format_table(sample_clients):
    """Test table formatting."""
    result = mod.format_table(sample_clients)
    assert "|" in result
    assert "00:11:22:33:44:55" in result
    assert "Connected" in result


def test_format_empty_list():
    """Test formatting empty client list."""
    assert mod.format_json([]) == "[]"
    assert mod.format_csv([]) == ""
    assert mod.format_table([]) == ""
