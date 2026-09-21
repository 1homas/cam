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
"""Tests for cam-clients.py"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click as click_module
import pytest

spec = importlib.util.spec_from_file_location("cam_clients", Path(__file__).parent / "cam-clients.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["cam_clients"] = mod
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


class TestFormatJsonl:
    def test_outputs_one_json_object_per_line(self):
        result = mod.format_jsonl(SAMPLE_CLIENTS)
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert all(json.loads(line) for line in lines)

    def test_jsonl_contains_fields(self):
        result = mod.format_jsonl(SAMPLE_CLIENTS)
        lines = result.strip().split("\n")
        parsed = json.loads(lines[0])
        assert parsed["mac"] == "AA:BB:CC:DD:EE:01"

    def test_jsonl_handles_empty_list(self):
        assert mod.format_jsonl([]) == ""


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
        # With contains matching, we need to be more specific
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("type", "corporate")])
        assert len(filtered) == 2
        assert all(c["type"] == "corporate" for c in filtered)

    def test_filter_case_insensitive_contains(self):
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

    def test_filter_substring_contains(self):
        # Test that "laptop" matches "John's laptop" (substring, not just prefix)
        filtered = mod.filter_clients(SAMPLE_CLIENTS, [("description", "laptop")])
        assert len(filtered) == 1
        assert filtered[0]["owner"] == "jsmith"

    def test_filter_prefix_contains(self):
        # Test that prefix matching still works
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

        result = await mod.fetch_all_clients(mock_client, "org123", batch_size=1000, limit=0)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_batch_size_parameter(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": SAMPLE_CLIENTS}
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await mod.fetch_all_clients(mock_client, "org123", batch_size=500, limit=0)

        # Verify perPage parameter was set correctly
        call_args = mock_client.get.call_args
        assert call_args.kwargs["params"]["perPage"] == 500

    @pytest.mark.asyncio
    async def test_limit_parameter(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": SAMPLE_CLIENTS * 100}  # 300 items
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await mod.fetch_all_clients(mock_client, "org123", batch_size=1000, limit=10)
        assert len(result) == 10

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

        result = await mod.fetch_all_clients(mock_client, "org123", batch_size=1000, limit=0)
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

        result = await mod.fetch_all_clients(mock_client, "org123", batch_size=1000, limit=0)
        assert len(result) == 3


class TestLoadRows:
    def test_load_rows_from_csv_file(self, tmp_path):
        csv_file = tmp_path / "clients.csv"
        csv_file.write_text("mac,description\nAA:BB:CC:DD:EE:FF,Printer\n")
        rows = mod.load_rows(str(csv_file))
        assert rows == [{"mac": "AA:BB:CC:DD:EE:FF", "description": "Printer"}]

    def test_load_rows_from_json_block(self):
        rows = mod.load_rows('{"mac": "AA:BB:CC:DD:EE:FF", "description": "Printer"}')
        assert rows == [{"mac": "AA:BB:CC:DD:EE:FF", "description": "Printer"}]

    def test_load_rows_drops_empty_and_null_values(self):
        rows = mod.load_rows('{"id": "1", "description": "", "mac": null}')
        assert rows == [{"id": "1"}]

    def test_load_rows_invalid_json_raises(self):
        with pytest.raises(click_module.BadParameter):
            mod.load_rows('{"mac": "bad"')

    def test_load_rows_json_array_raises(self):
        with pytest.raises(click_module.BadParameter):
            mod.load_rows('[{"mac": "AA:BB:CC:DD:EE:FF"}]')

    def test_load_rows_missing_file_raises(self):
        with pytest.raises(click_module.BadParameter):
            mod.load_rows("/nonexistent/path/clients.csv")


class TestValidateRows:
    def test_validate_create_row_valid(self):
        assert mod.validate_create_row({"mac": "AA:BB:CC:DD:EE:FF"}, 1) == []

    def test_validate_create_row_missing_mac(self):
        errors = mod.validate_create_row({"description": "Printer"}, 1)
        assert any("mac" in e for e in errors)

    def test_validate_update_row_valid(self):
        assert mod.validate_update_row({"id": "1"}, 1) == []

    def test_validate_update_row_missing_id(self):
        errors = mod.validate_update_row({"description": "x"}, 1)
        assert any("id" in e for e in errors)

    def test_validate_delete_row_valid(self):
        assert mod.validate_delete_row({"id": "1"}, 1) == []

    def test_validate_delete_row_missing_id(self):
        errors = mod.validate_delete_row({}, 1)
        assert any("id" in e for e in errors)


class TestBuildPayloads:
    def test_build_create_payload_minimal(self):
        payload = mod.build_create_payload({"mac": "AA:BB:CC:DD:EE:FF"})
        assert payload == {"mac": "AA:BB:CC:DD:EE:FF"}

    def test_build_create_payload_full(self):
        row = {
            "mac": "AA:BB:CC:DD:EE:FF",
            "type": "corporate",
            "owner": "jsmith",
            "description": "Printer",
            "ipsk": "s3cretpass",
            "groups": "100;200",
        }
        payload = mod.build_create_payload(row)
        assert payload["mac"] == "AA:BB:CC:DD:EE:FF"
        assert payload["type"] == "corporate"
        assert payload["owner"] == "jsmith"
        assert payload["description"] == "Printer"
        assert payload["ipsk"] == "s3cretpass"
        assert payload["groups"] == [{"value": "100"}, {"value": "200"}]

    def test_build_update_payload_excludes_id(self):
        payload = mod.build_update_payload({"id": "1", "description": "New"})
        assert "id" not in payload
        assert payload["description"] == "New"

    def test_build_update_payload_groups_add_remove(self):
        row = {"id": "1", "groupsAdd": "100;200", "groupsRemove": "300"}
        payload = mod.build_update_payload(row)
        assert payload["groups"]["addList"] == [{"value": "100"}, {"value": "200"}]
        assert payload["groups"]["removeList"] == [{"value": "300"}]

    def test_build_update_payload_partial(self):
        payload = mod.build_update_payload({"id": "1", "mac": "AA:BB:CC:DD:EE:FF"})
        assert payload == {"mac": "AA:BB:CC:DD:EE:FF"}


class TestChunkRows:
    def test_chunk_rows(self):
        rows = [{"mac": f"AA:{i:02d}"} for i in range(25)]
        chunks = list(mod.chunk_rows(rows, chunk_size=10))
        assert len(chunks) == 3
        assert len(chunks[0]) == 10
        assert len(chunks[2]) == 5


class TestCreateClient:
    @pytest.mark.asyncio
    async def test_create_client_success(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "627126248111374692"}
        mock_client.request = AsyncMock(return_value=mock_response)

        result = await mod.create_client(mock_client, "123456", {"mac": "AA:BB:CC:DD:EE:FF"})

        assert result["success"] is True
        assert result["id"] == "627126248111374692"
        assert result["mac"] == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_create_client_failure(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.raise_for_status.side_effect = Exception("400 Bad Request")
        mock_client.request = AsyncMock(return_value=mock_response)

        result = await mod.create_client(mock_client, "123456", {"mac": "AA:BB:CC:DD:EE:FF"})

        assert result["success"] is False
        assert "error" in result


class TestUpdateClient:
    @pytest.mark.asyncio
    async def test_update_client_success(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"mac": "AA:BB:CC:DD:EE:FF"}
        mock_client.request = AsyncMock(return_value=mock_response)

        result = await mod.update_client(mock_client, "123456", "1", {"description": "New"})

        assert result["success"] is True
        assert result["id"] == "1"

    @pytest.mark.asyncio
    async def test_update_client_failure(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")
        mock_client.request = AsyncMock(return_value=mock_response)

        result = await mod.update_client(mock_client, "123456", "missing-id", {"description": "New"})

        assert result["success"] is False
        assert "error" in result


class TestBulkDeleteClients:
    @pytest.mark.asyncio
    async def test_bulk_delete_success(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.request = AsyncMock(return_value=mock_response)

        result = await mod.bulk_delete_clients(mock_client, "123456", ["1", "2"])

        assert result["success"] is True
        assert result["ids"] == ["1", "2"]

    @pytest.mark.asyncio
    async def test_bulk_delete_failure(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.raise_for_status.side_effect = Exception("400 Bad Request")
        mock_client.request = AsyncMock(return_value=mock_response)

        result = await mod.bulk_delete_clients(mock_client, "123456", ["1"])

        assert result["success"] is False
        assert "error" in result


class TestValidateBulkCsv:
    def test_valid_csv(self):
        content = "MAC address,Endpoint device group,Description\nAA:BB:CC:DD:EE:01,Cameras,Lobby\n"
        is_valid, error = mod.validate_bulk_csv(content)
        assert is_valid is True
        assert error == ""

    def test_valid_csv_case_insensitive_header(self):
        content = "mac address,description\nAA:BB:CC:DD:EE:01,Lobby\n"
        is_valid, _ = mod.validate_bulk_csv(content)
        assert is_valid is True

    def test_missing_mac_column(self):
        content = "Description\nLobby\n"
        is_valid, error = mod.validate_bulk_csv(content)
        assert is_valid is False
        assert "MAC address" in error

    def test_header_only_csv(self):
        content = "MAC address,Description\n"
        is_valid, error = mod.validate_bulk_csv(content)
        assert is_valid is False


class TestSplitCsvIntoBatches:
    def test_splits_into_multiple_batches(self):
        rows = "\n".join(f"AA:BB:CC:DD:EE:{i:02d},Group" for i in range(5))
        content = f"MAC address,Endpoint device group\n{rows}"
        batches = mod.split_csv_into_batches(content, batch_size=2)
        assert len(batches) == 3
        for batch in batches:
            assert batch.startswith("MAC address,Endpoint device group")

    def test_single_batch_when_under_size(self):
        content = "MAC address,Description\nAA:BB:CC:DD:EE:01,Lobby\n"
        batches = mod.split_csv_into_batches(content, batch_size=1000)
        assert len(batches) == 1

    def test_header_only_returns_single_batch(self):
        content = "MAC address,Description"
        batches = mod.split_csv_into_batches(content, batch_size=10)
        assert batches == [content]


class TestFormatBulkResult:
    def test_json_format(self):
        result = {"meta": {"counts": {"total": 1, "success": 1, "failure": 0}}}
        output = mod.format_bulk_result(result, "json")
        assert json.loads(output) == result

    def test_yaml_format(self):
        import yaml

        result = {"meta": {"counts": {"total": 1, "success": 1, "failure": 0}}}
        output = mod.format_bulk_result(result, "yaml")
        assert yaml.safe_load(output) == result

    def test_summary_format(self):
        result = {"meta": {"counts": {"total": 2, "success": 1, "failure": 1}}}
        output = mod.format_bulk_result(result, "summary")
        assert "Total:   2" in output
        assert "Success: 1" in output
        assert "Failed:  1" in output

    def test_unknown_format_falls_back_to_summary(self):
        result = {"meta": {"counts": {"total": 1, "success": 1, "failure": 0}}}
        output = mod.format_bulk_result(result, "table")
        assert "Upload Summary:" in output

    def test_summary_includes_error_details(self):
        result = {
            "meta": {"counts": {"total": 1, "success": 0, "failure": 1}},
            "items": [{"type": "error", "count": 1, "details": [{"message": "Invalid MAC", "rows": [2]}]}],
        }
        output = mod.format_bulk_result(result, "summary")
        assert "ERROR: 1" in output
        assert "Invalid MAC" in output


class TestBulkUploadClients:
    @pytest.mark.asyncio
    async def test_success_sends_expected_payload(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"meta": {"counts": {"total": 1, "success": 1, "failure": 0}}}
        mock_client.request = AsyncMock(return_value=mock_response)

        csv_content = "MAC address,Description\nAA:BB:CC:DD:EE:01,Lobby\n"
        result = await mod.bulk_upload_clients(mock_client, "123456", csv_content)

        assert result["meta"]["counts"]["success"] == 1
        call = mock_client.request.call_args
        assert call.args == ("POST", f"{mod.BASE_URL}/organizations/123456/nac/clients/bulkUpload")
        payload = call.kwargs["json"]
        assert payload["updateClients"] is True
        assert payload["createClientGroups"] is True

    @pytest.mark.asyncio
    async def test_respects_disabled_flags(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_client.request = AsyncMock(return_value=mock_response)

        await mod.bulk_upload_clients(mock_client, "123456", "MAC address\nAA:BB:CC:DD:EE:01\n", update_clients=False, create_groups=False)

        payload = mock_client.request.call_args.kwargs["json"]
        assert payload["updateClients"] is False
        assert payload["createClientGroups"] is False

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.raise_for_status.side_effect = Exception("400 Bad Request")
        mock_client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(Exception):
            await mod.bulk_upload_clients(mock_client, "123456", "MAC address\nAA:BB:CC:DD:EE:01\n")


class TestRunCreateRouting:
    @pytest.mark.asyncio
    async def test_csv_path_routes_to_bulk_upload(self, tmp_path):
        csv_file = tmp_path / "clients.csv"
        csv_file.write_text("MAC address,Description\nAA:BB:CC:DD:EE:01,Lobby\n")

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"meta": {"counts": {"total": 1, "success": 1, "failure": 0}}}
        mock_client.request = AsyncMock(return_value=mock_response)

        sem = __import__("asyncio").Semaphore(1)
        code = await mod.run_create(mock_client, "123456", str(csv_file), sem)

        assert code == 0
        call = mock_client.request.call_args
        assert "bulkUpload" in call.args[1]

    @pytest.mark.asyncio
    async def test_json_object_routes_to_single_create(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "1"}
        mock_client.request = AsyncMock(return_value=mock_response)

        sem = __import__("asyncio").Semaphore(1)
        code = await mod.run_create(mock_client, "123456", '{"mac": "AA:BB:CC:DD:EE:FF"}', sem)

        assert code == 0
        call = mock_client.request.call_args
        assert call.args == ("POST", f"{mod.BASE_URL}/organizations/123456/nac/clients")

    @pytest.mark.asyncio
    async def test_missing_csv_file_raises(self):
        sem = __import__("asyncio").Semaphore(1)
        with pytest.raises(click_module.BadParameter):
            await mod.run_create(MagicMock(), "123456", "/nonexistent/clients.csv", sem)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
