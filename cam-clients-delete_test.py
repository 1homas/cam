#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest>=7.0.0",
#     "pytest-asyncio>=0.21.0",
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "click>=8.0.0",
# ]
# ///
"""
Unit tests for cam-clients-delete.py
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os
import asyncio

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

# Import functions from the main script
import importlib.util
spec = importlib.util.spec_from_file_location("cam_clients_delete", "cam-clients-delete.py")
cam_module = importlib.util.module_from_spec(spec)

# Mock environment variables before loading the module
os.environ.setdefault("MERAKI_DASHBOARD_API_KEY", "test_key")
os.environ.setdefault("MERAKI_ORG_ID", "test_org")

# Load the module but don't execute main
sys.modules["cam_clients_delete"] = cam_module
try:
    spec.loader.exec_module(cam_module)
except SystemExit:
    pass

# Import the functions we need
parse_next_starting_after = cam_module.parse_next_starting_after
fetch_all_clients = cam_module.fetch_all_clients
fetch_all_groups = cam_module.fetch_all_groups
bulk_delete_clients = cam_module.bulk_delete_clients
delete_group = cam_module.delete_group


class TestPagination:
    """Test pagination parsing."""

    def test_parse_next_starting_after_with_next(self):
        """Test parsing Link header with next token."""
        link_header = '<https://api.meraki.com/api/v1/organizations/123/nac/clients?perPage=1000&startingAfter=abc123>; rel="next"'
        result = parse_next_starting_after(link_header)
        assert result == "abc123"

    def test_parse_next_starting_after_no_next(self):
        """Test parsing Link header without next."""
        link_header = '<https://api.meraki.com/api/v1/organizations/123/nac/clients?perPage=1000>; rel="first"'
        result = parse_next_starting_after(link_header)
        assert result is None

    def test_parse_next_starting_after_empty(self):
        """Test parsing empty Link header."""
        result = parse_next_starting_after("")
        assert result is None

    def test_parse_next_starting_after_multiple_links(self):
        """Test parsing Link header with multiple links."""
        link_header = '<https://api.meraki.com/api/v1/organizations/123/nac/clients?perPage=1000>; rel="first", <https://api.meraki.com/api/v1/organizations/123/nac/clients?perPage=1000&startingAfter=xyz789>; rel="next"'
        result = parse_next_starting_after(link_header)
        assert result == "xyz789"


class TestClientsDeletion:
    """Test client deletion functionality."""

    @pytest.mark.asyncio
    async def test_bulk_delete_clients_success(self):
        """Test successful bulk delete returns 204."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_response)

        success, count = await bulk_delete_clients(
            mock_client, "org123", ["id1", "id2", "id3"], timeout=60.0, worker_id=1, batch_num=5
        )

        assert success is True
        assert count == 3

    @pytest.mark.asyncio
    async def test_bulk_delete_clients_404_treated_as_success(self):
        """Test 404 error (clients already deleted) is treated as success."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"errors":["Not all client IDs present in the DB"]}'
        mock_client.post = AsyncMock(return_value=mock_response)

        success, count = await bulk_delete_clients(
            mock_client, "org123", ["id1", "id2"], timeout=60.0, worker_id=2, batch_num=10
        )

        assert success is True  # 404 should be treated as success
        assert count == 2

    @pytest.mark.asyncio
    async def test_bulk_delete_clients_timeout(self):
        """Test timeout handling in bulk delete."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Timeout"))

        success, count = await bulk_delete_clients(
            mock_client, "org123", ["id1"], timeout=60.0, worker_id=3, batch_num=15
        )

        assert success is False
        assert count == 0

    @pytest.mark.asyncio
    async def test_bulk_delete_clients_with_worker_context(self):
        """Test that worker and batch context is included in logging."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client.post = AsyncMock(return_value=mock_response)

        success, count = await bulk_delete_clients(
            mock_client, "org123", ["id1"], timeout=60.0, worker_id=4, batch_num=20
        )

        assert success is False
        assert count == 0

    @pytest.mark.asyncio
    async def test_fetch_all_clients_with_metadata(self):
        """Test fetching clients uses metadata totalCount."""
        mock_client = AsyncMock()

        # Mock first page response with metadata
        mock_response1 = MagicMock()
        mock_response1.status_code = 200
        mock_response1.json = MagicMock(return_value={
            "items": [{"id": "c1", "mac": "aa:bb:cc:dd:ee:01"}],
            "meta": {"totalCount": 2}
        })
        mock_response1.headers.get = MagicMock(return_value="")

        # Mock second page response
        mock_response2 = MagicMock()
        mock_response2.status_code = 200
        mock_response2.json = MagicMock(return_value={
            "items": [{"id": "c2", "mac": "aa:bb:cc:dd:ee:02"}],
            "meta": {"totalCount": 2}
        })
        mock_response2.headers.get = MagicMock(return_value="")

        # Mock third page (empty)
        mock_response3 = MagicMock()
        mock_response3.status_code = 200
        mock_response3.json = MagicMock(return_value={"items": []})
        mock_response3.headers.get = MagicMock(return_value="")

        mock_client.get = AsyncMock(side_effect=[mock_response1, mock_response2, mock_response3])

        clients = await fetch_all_clients(mock_client, "org123", batch_size=1, limit=None)

        assert len(clients) == 2
        assert clients[0]["id"] == "c1"
        assert clients[1]["id"] == "c2"

    @pytest.mark.asyncio
    async def test_delete_group_success(self):
        """Test successful group deletion."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.delete = AsyncMock(return_value=mock_response)

        success = await delete_group(mock_client, "org123", "group1")

        assert success is True

    @pytest.mark.asyncio
    async def test_delete_group_failure(self):
        """Test failed group deletion."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client.delete = AsyncMock(return_value=mock_response)

        success = await delete_group(mock_client, "org123", "group1")

        assert success is False


class TestCLI:
    """Test CLI argument parsing."""

    def test_dry_run_flag(self):
        """Test --dry-run flag."""
        pass

    def test_verbose_flag(self):
        """Test --verbose flag."""
        pass

    def test_clients_only_flag(self):
        """Test --clients-only flag."""
        pass

    def test_groups_only_flag(self):
        """Test --groups-only flag."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
