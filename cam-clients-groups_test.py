#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "click>=8.0.0",
#     "pyyaml>=6.0.0",
#     "pytest>=8.0.0",
#     "pytest-asyncio>=0.23.0",
# ]
# ///
"""Tests for cam-clients-groups.py"""

import importlib.util
import json
import sys
from pathlib import Path

import click as click_module
import pytest
from unittest.mock import AsyncMock, MagicMock

spec = importlib.util.spec_from_file_location("cam_clients_groups", Path(__file__).parent / "cam-clients-groups.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["cam_clients_groups"] = mod
spec.loader.exec_module(mod)


def test_parse_csv():
    csv_content = "name,description\nCameras,Security cameras\nGuests,Guest devices\n"
    rows = mod.parse_csv(csv_content)
    assert len(rows) == 2
    assert rows[0]["name"] == "Cameras"
    assert rows[0]["description"] == "Security cameras"


def test_parse_csv_drops_empty_values_and_rows():
    csv_content = "name,description\nCameras,\n,\n"
    rows = mod.parse_csv(csv_content)
    assert len(rows) == 1
    assert "description" not in rows[0]


def test_load_rows_from_csv_file(tmp_path):
    csv_file = tmp_path / "groups.csv"
    csv_file.write_text("name,description\nCameras,Security cameras\n")
    rows = mod.load_rows(str(csv_file))
    assert rows == [{"name": "Cameras", "description": "Security cameras"}]


def test_load_rows_from_json_block():
    rows = mod.load_rows('{"name": "Cameras", "description": "Security cameras"}')
    assert rows == [{"name": "Cameras", "description": "Security cameras"}]


def test_load_rows_drops_empty_and_null_values():
    rows = mod.load_rows('{"id": "1", "description": "", "name": null}')
    assert rows == [{"id": "1"}]


def test_load_rows_invalid_json_raises():
    with pytest.raises(click_module.BadParameter):
        mod.load_rows('{"name": "bad"')


def test_load_rows_json_array_raises():
    with pytest.raises(click_module.BadParameter):
        mod.load_rows('[{"name": "Cameras"}]')


def test_load_rows_missing_file_raises():
    with pytest.raises(click_module.BadParameter):
        mod.load_rows("/nonexistent/path/groups.csv")


def test_validate_create_row_valid():
    assert mod.validate_create_row({"name": "Cameras"}, 1) == []


def test_validate_create_row_missing_name():
    errors = mod.validate_create_row({"description": "x"}, 1)
    assert any("name" in e for e in errors)


def test_validate_update_row_valid():
    assert mod.validate_update_row({"id": "1"}, 1) == []


def test_validate_update_row_missing_id():
    errors = mod.validate_update_row({"name": "x"}, 1)
    assert any("id" in e for e in errors)


def test_validate_delete_row_valid():
    assert mod.validate_delete_row({"id": "1"}, 1) == []


def test_validate_delete_row_missing_id():
    errors = mod.validate_delete_row({}, 1)
    assert any("id" in e for e in errors)


def test_build_create_payload_minimal():
    payload = mod.build_create_payload({"name": "Cameras"})
    assert payload == {"name": "Cameras"}


def test_build_create_payload_full():
    row = {"name": "Cameras", "description": "Security cameras", "members": "1;2;3"}
    payload = mod.build_create_payload(row)
    assert payload["name"] == "Cameras"
    assert payload["description"] == "Security cameras"
    assert payload["members"] == [{"value": "1"}, {"value": "2"}, {"value": "3"}]


def test_build_update_payload_excludes_id():
    payload = mod.build_update_payload({"id": "1", "description": "New"})
    assert "id" not in payload
    assert payload["description"] == "New"


def test_build_update_payload_members_add_remove():
    row = {"id": "1", "membersAdd": "1;2", "membersRemove": "3"}
    payload = mod.build_update_payload(row)
    assert payload["members"]["addList"] == [{"value": "1"}, {"value": "2"}]
    assert payload["members"]["removeList"] == [{"value": "3"}]


def test_build_update_payload_partial():
    payload = mod.build_update_payload({"id": "1", "name": "Renamed"})
    assert payload == {"name": "Renamed"}


def test_chunk_rows():
    rows = [{"name": f"group{i}"} for i in range(25)]
    chunks = list(mod.chunk_rows(rows, chunk_size=10))
    assert len(chunks) == 3
    assert len(chunks[0]) == 10
    assert len(chunks[2]) == 5


def test_format_search_jsonl():
    rows = [{"id": "1", "name": "Cameras"}, {"id": "2", "name": "Guests"}]
    result = mod.format_search_jsonl(rows)
    lines = result.strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["name"] == "Cameras"


def test_format_search_jsonl_empty():
    assert mod.format_search_jsonl([]) == ""


def test_flatten_group():
    item = {"id": "1", "name": "Cameras", "description": "Security", "members": {"totalCount": 3}}
    row = mod.flatten_group(item)
    assert row == {"id": "1", "name": "Cameras", "description": "Security", "membersCount": 3}


def test_flatten_group_no_members():
    item = {"id": "1", "name": "Empty"}
    row = mod.flatten_group(item)
    assert row["membersCount"] == 0


def test_parse_next_starting_after():
    link = '<https://api.meraki.com/api/v1/organizations/123/nac/clients/groups?startingAfter=abc123>; rel="next"'
    assert mod.parse_next_starting_after(link) == "abc123"


def test_parse_next_starting_after_missing():
    link = '<https://api.meraki.com/api/v1/organizations/123/nac/clients/groups?startingAfter=abc123>; rel="prev"'
    assert mod.parse_next_starting_after(link) is None


def test_parse_next_starting_after_empty():
    assert mod.parse_next_starting_after("") is None


@pytest.mark.asyncio
async def test_search_groups_single_page():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Link": ""}
    mock_response.json.return_value = {
        "items": [{"id": "1", "name": "Cameras", "description": "Security", "members": {"totalCount": 3}}]
    }
    mock_client.request = AsyncMock(return_value=mock_response)

    rows = await mod.search_groups(mock_client, "123456", None, (), None, None, per_page=100, limit=0)

    assert len(rows) == 1
    assert rows[0]["name"] == "Cameras"
    assert rows[0]["membersCount"] == 3


@pytest.mark.asyncio
async def test_search_groups_respects_limit():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Link": '<...?startingAfter=next>; rel="next"'}
    mock_response.json.return_value = {
        "items": [
            {"id": "1", "name": "A", "members": {"totalCount": 0}},
            {"id": "2", "name": "B", "members": {"totalCount": 0}},
        ]
    }
    mock_client.request = AsyncMock(return_value=mock_response)

    rows = await mod.search_groups(mock_client, "123456", None, (), None, None, per_page=100, limit=1)

    assert len(rows) == 1


@pytest.mark.asyncio
async def test_create_group_success():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"id": "627126248111341608"}
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await mod.create_group(mock_client, "123456", {"name": "Cameras"})

    assert result["success"] is True
    assert result["id"] == "627126248111341608"
    assert result["name"] == "Cameras"


@pytest.mark.asyncio
async def test_create_group_failure():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.raise_for_status.side_effect = Exception("400 Bad Request")
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await mod.create_group(mock_client, "123456", {"name": "Cameras"})

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_update_group_success():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"name": "Cameras"}
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await mod.update_group(mock_client, "123456", "1", {"description": "New"})

    assert result["success"] is True
    assert result["id"] == "1"


@pytest.mark.asyncio
async def test_update_group_failure():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = Exception("404 Not Found")
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await mod.update_group(mock_client, "123456", "missing-id", {"description": "New"})

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_delete_group_success():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await mod.delete_group(mock_client, "123456", "1")

    assert result["success"] is True
    assert result["id"] == "1"


@pytest.mark.asyncio
async def test_delete_group_failure():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = Exception("404 Not Found")
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await mod.delete_group(mock_client, "123456", "missing-id")

    assert result["success"] is False
    assert "error" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
