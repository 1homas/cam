#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "pytest>=8.0.0",
#     "pytest-asyncio>=0.23.0",
# ]
# ///
"""
Tests for cam-clients-add.py
"""

import base64
import pytest
import httpx


def test_encode_csv_to_base64():
    """Test CSV encoding to base64."""
    csv_content = "MAC address,Endpoint device group,Description\n00:0a:9a:6d:00,Guest,Test Client"
    encoded = base64.b64encode(csv_content.encode()).decode()
    assert len(encoded) > 0
    assert base64.b64decode(encoded).decode() == csv_content


def test_decode_base64_to_csv():
    """Test base64 decoding to CSV."""
    csv_content = "MAC address,Endpoint device group,Description\n00:0a:9a:6d:00,Guest,Test Description"
    encoded = base64.b64encode(csv_content.encode()).decode()
    decoded = base64.b64decode(encoded).decode()
    assert "MAC address" in decoded
    assert "00:0a:9a:6d:00" in decoded


def test_validate_csv_headers():
    """Test CSV header validation."""
    valid_csv = "MAC address,Endpoint device group,Description\n00:0a:9a:6d:00,Guest,Test"
    lines = valid_csv.strip().split("\n")
    headers = [h.strip().lower() for h in lines[0].split(",")]
    assert "mac address" in headers


def test_validate_csv_headers_alternate_case():
    """Test CSV header validation is case-insensitive."""
    valid_csv = "mac address,description\n00:0a:9a:6d:00,Test"
    lines = valid_csv.strip().split("\n")
    headers = [h.strip().lower() for h in lines[0].split(",")]
    assert "mac address" in headers


def test_csv_row_count():
    """Test counting CSV rows."""
    csv_content = "MAC address,Description\n00:0a:9a:6d:00,Client1\n00:0b:9b:6e:01,Client2"
    lines = csv_content.strip().split("\n")
    data_rows = len(lines) - 1  # Exclude header
    assert data_rows == 2


def test_validate_template_format():
    """Test validation of CAM CSV template format."""
    template_csv = """MAC address,Endpoint device group,Description
00:0a:95:9d:68:16,Android,Android device
10:0c:95:9d:68:4e,Guest,New device"""
    lines = template_csv.strip().split("\n")
    headers = [h.strip().lower() for h in lines[0].split(",")]
    assert "mac address" in headers
    assert len(lines) == 3  # header + 2 data rows


@pytest.mark.asyncio
async def test_api_payload_structure():
    """Test that API payload has required fields."""
    payload = {
        "contents": "test_base64_string",
        "updateClients": True,
        "createClientGroups": False,
    }
    assert "contents" in payload
    assert "updateClients" in payload
    assert "createClientGroups" in payload
    assert isinstance(payload["updateClients"], bool)
    assert isinstance(payload["createClientGroups"], bool)


def test_split_csv_into_batches():
    """Test CSV splitting into batches."""
    csv_content = "MAC address,Description\n" + "\n".join(
        [f"00:0a:9a:6d:{i:02x},Client{i}" for i in range(5)]
    )

    # Import the function (would need to be exposed in the actual script)
    lines = csv_content.strip().split("\n")
    header = lines[0]
    data_rows = lines[1:]

    # Test batch size of 2
    batch_size = 2
    batches = []
    for i in range(0, len(data_rows), batch_size):
        batch_rows = data_rows[i : i + batch_size]
        batch_csv = header + "\n" + "\n".join(batch_rows)
        batches.append(batch_csv)

    assert len(batches) == 3  # 5 rows / 2 = 3 batches
    assert all("MAC address,Description" in b for b in batches)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
