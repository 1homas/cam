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
"""Tests for cam-users.py"""

import importlib.util
import json
import sys
from pathlib import Path

import click as click_module
import pytest
from unittest.mock import AsyncMock, MagicMock

# Import module with hyphens in name
spec = importlib.util.spec_from_file_location("cam_users", Path(__file__).parent / "cam-users.py")
cam_users = importlib.util.module_from_spec(spec)
sys.modules["cam_users"] = cam_users
spec.loader.exec_module(cam_users)

parse_csv = cam_users.parse_csv
validate_create_row = cam_users.validate_create_row
validate_update_row = cam_users.validate_update_row
validate_delete_row = cam_users.validate_delete_row
build_create_payload = cam_users.build_create_payload
build_update_payload = cam_users.build_update_payload
chunk_rows = cam_users.chunk_rows
build_search_body = cam_users.build_search_body
flatten_search_results = cam_users.flatten_search_results
parse_next_starting_after = cam_users.parse_next_starting_after
load_rows = cam_users.load_rows
resolve_idp_ids_by_name = cam_users.resolve_idp_ids_by_name


def test_parse_csv():
    """Test CSV parsing."""
    csv_content = """email,displayName,password,sendPassword
alice@example.com,Alice Smith,Pass123,true
bob@example.com,Bob Jones,Pass456,false
"""
    rows = parse_csv(csv_content)
    assert len(rows) == 2
    assert rows[0]["email"] == "alice@example.com"
    assert rows[0]["displayName"] == "Alice Smith"
    assert rows[1]["sendPassword"] == "false"


def test_parse_csv_drops_empty_values_and_rows():
    """Test CSV parsing drops empty values and skips fully empty rows."""
    csv_content = """email,displayName,password
alice@example.com,,Pass123
,,
"""
    rows = parse_csv(csv_content)
    assert len(rows) == 1
    assert "displayName" not in rows[0]


def test_validate_create_row_valid():
    """Test create row validation with valid data."""
    row = {"email": "alice@example.com", "displayName": "Alice Smith"}
    assert validate_create_row(row, 1) == []


def test_validate_create_row_missing_email():
    """Test create row validation with missing email."""
    errors = validate_create_row({"displayName": "Alice"}, 1)
    assert any("email" in err for err in errors)


def test_validate_update_row_valid():
    """Test update row validation with valid data."""
    row = {"id": "1284392014819", "displayName": "Alice Smith"}
    assert validate_update_row(row, 1) == []


def test_validate_update_row_missing_id():
    """Test update row validation with missing id."""
    errors = validate_update_row({"displayName": "Alice"}, 1)
    assert any("id" in err for err in errors)


def test_validate_delete_row_valid():
    """Test delete row validation with valid data."""
    assert validate_delete_row({"id": "1284392014819"}, 1) == []


def test_validate_delete_row_missing_id():
    """Test delete row validation with missing id."""
    errors = validate_delete_row({}, 1)
    assert any("id" in err for err in errors)


def test_build_create_payload_minimal():
    """Test building create payload with only required fields, defaulting accountType."""
    payload = build_create_payload({"email": "alice@example.com"})
    assert payload == {"email": "alice@example.com", "accountType": "802.1X"}


def test_build_create_payload_full():
    """Test building create payload with all optional fields."""
    row = {
        "email": "mdavis@meraki.com",
        "displayName": "Miles Davis",
        "upn": "mdavis@meraki.com",
        "password": "AL0ngEnoughPassword",
        "sendPassword": "true",
        "accountType": "802.1X",
        "groupIds": "1;2;3",
    }
    payload = build_create_payload(row)
    assert payload["email"] == "mdavis@meraki.com"
    assert payload["displayName"] == "Miles Davis"
    assert payload["upn"] == "mdavis@meraki.com"
    assert payload["password"] == "AL0ngEnoughPassword"
    assert payload["sendPassword"] is True
    assert payload["accountType"] == "802.1X"
    assert payload["groupIds"] == ["1", "2", "3"]


def test_build_create_payload_group_ids_from_json_list():
    """Test that a JSON array for groupIds is passed through as-is."""
    payload = build_create_payload({"email": "a@example.com", "groupIds": ["1", "2"]})
    assert payload["groupIds"] == ["1", "2"]


def test_build_update_payload_excludes_id():
    """Test building update payload excludes 'id' field."""
    row = {"id": "1284392014819", "displayName": "Miles Davis", "sendPassword": "false"}
    payload = build_update_payload(row)
    assert "id" not in payload
    assert payload["displayName"] == "Miles Davis"
    assert payload["sendPassword"] is False


def test_build_update_payload_partial():
    """Test building update payload with only some fields set."""
    payload = build_update_payload({"id": "1", "email": "new@example.com"})
    assert payload == {"email": "new@example.com"}


def test_chunk_rows():
    """Test chunking rows into batches."""
    rows = [{"email": f"user{i}@example.com"} for i in range(25)]
    chunks = list(chunk_rows(rows, chunk_size=10))
    assert len(chunks) == 3
    assert len(chunks[0]) == 10
    assert len(chunks[2]) == 5


@pytest.mark.asyncio
async def test_create_user_success():
    """Test creating a single user."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"idpUserId": "1284392014819", "displayName": "Miles Davis"}
    mock_client.request = AsyncMock(return_value=mock_response)

    payload = {"email": "mdavis@meraki.com", "displayName": "Miles Davis"}
    result = await cam_users.create_user(mock_client, "123456", payload)

    assert result["success"] is True
    assert result["idpUserId"] == "1284392014819"
    assert result["email"] == "mdavis@meraki.com"


@pytest.mark.asyncio
async def test_create_user_failure():
    """Test handling user creation failure."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.raise_for_status.side_effect = Exception("400 Bad Request")
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await cam_users.create_user(mock_client, "123456", {"email": "bad@example.com"})

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_update_user_success():
    """Test updating a single user."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"idpUserId": "1284392014819"}
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await cam_users.update_user(mock_client, "123456", "1284392014819", {"displayName": "New Name"})

    assert result["success"] is True
    assert result["id"] == "1284392014819"


@pytest.mark.asyncio
async def test_update_user_failure():
    """Test handling user update failure."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = Exception("404 Not Found")
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await cam_users.update_user(mock_client, "123456", "missing-id", {"displayName": "X"})

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_delete_user_success():
    """Test deleting a single user."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await cam_users.delete_user(mock_client, "123456", "1284392014819")

    assert result["success"] is True
    assert result["id"] == "1284392014819"


@pytest.mark.asyncio
async def test_delete_user_failure():
    """Test handling user deletion failure."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = Exception("404 Not Found")
    mock_client.request = AsyncMock(return_value=mock_response)

    result = await cam_users.delete_user(mock_client, "123456", "missing-id")

    assert result["success"] is False
    assert "error" in result


def test_load_rows_from_csv_file(tmp_path):
    """Test loading rows from a CSV file path."""
    csv_file = tmp_path / "users.csv"
    csv_file.write_text("email,displayName\nalice@example.com,Alice\n")

    rows = load_rows(str(csv_file))

    assert rows == [{"email": "alice@example.com", "displayName": "Alice"}]


def test_load_rows_from_json_block():
    """Test loading a single user from an inline JSON object."""
    rows = load_rows('{"email": "mdavis@meraki.com", "displayName": "Miles Davis"}')

    assert rows == [{"email": "mdavis@meraki.com", "displayName": "Miles Davis"}]


def test_load_rows_from_json_block_drops_empty_and_null_values():
    """Test that JSON null/empty values are dropped like CSV empties."""
    rows = load_rows('{"id": "1", "displayName": "", "email": null}')

    assert rows == [{"id": "1"}]


def test_load_rows_from_json_block_preserves_boolean():
    """Test that JSON booleans survive (not coerced to strings)."""
    rows = load_rows('{"email": "a@example.com", "sendPassword": false}')

    assert rows == [{"email": "a@example.com", "sendPassword": False}]


def test_load_rows_invalid_json_raises():
    """Test that malformed JSON raises a BadParameter error."""
    with pytest.raises(click_module.BadParameter):
        load_rows('{"email": "bad"')


def test_load_rows_json_array_raises():
    """Test that a JSON array (not a single object) raises a BadParameter error."""
    with pytest.raises(click_module.BadParameter):
        load_rows('[{"email": "a@example.com"}]')


def test_load_rows_missing_file_raises():
    """Test that a nonexistent file path raises a BadParameter error."""
    with pytest.raises(click_module.BadParameter):
        load_rows("/nonexistent/path/users.csv")


def test_load_delete_rows_from_csv_file(tmp_path):
    """Test loading delete rows from a CSV file path."""
    csv_file = tmp_path / "deletes.csv"
    csv_file.write_text("id\n1284392014819\n")

    rows = cam_users.load_delete_rows(str(csv_file))

    assert rows == [{"id": "1284392014819"}]


def test_load_delete_rows_from_json_block():
    """Test loading a single delete target from an inline JSON object."""
    rows = cam_users.load_delete_rows('{"id": "1284392014819"}')

    assert rows == [{"id": "1284392014819"}]


def test_load_delete_rows_from_bare_id():
    """Test that a plain string, neither JSON nor an existing file path, is treated as a literal user ID."""
    rows = cam_users.load_delete_rows("1284392014819")

    assert rows == [{"id": "1284392014819"}]


def test_load_delete_rows_invalid_json_raises():
    """Test that malformed JSON (starting with '{') still raises, rather than falling back to a literal ID."""
    with pytest.raises(click_module.BadParameter):
        cam_users.load_delete_rows('{"id": "bad"')


def test_build_create_payload_json_false_send_password():
    """Test that a JSON boolean False for sendPassword is still included in the payload."""
    payload = build_create_payload({"email": "a@example.com", "sendPassword": False})
    assert payload["sendPassword"] is False


def test_build_create_payload_accountType_override():
    """Test that an explicit accountType overrides the default."""
    payload = build_create_payload({"email": "a@example.com", "accountType": "Guest"})
    assert payload["accountType"] == "Guest"


def test_build_update_payload_json_false_send_password():
    """Test that a JSON boolean False for sendPassword is still included in the update payload."""
    payload = build_update_payload({"id": "1", "sendPassword": False})
    assert payload["sendPassword"] is False


def test_resolve_idp_ids_by_name_match():
    """Test resolving an IdP name to its id, case-insensitively."""
    idps = [{"idpId": "1284392014819", "name": "Azure AD IdP"}, {"idpId": "2", "name": "Okta"}]
    ids = resolve_idp_ids_by_name(idps, ["azure ad idp"])
    assert ids == ["1284392014819"]


def test_resolve_idp_ids_by_name_multiple():
    """Test resolving multiple IdP names in order."""
    idps = [{"idpId": "1", "name": "Azure AD IdP"}, {"idpId": "2", "name": "Okta"}]
    ids = resolve_idp_ids_by_name(idps, ["Okta", "Azure AD IdP"])
    assert ids == ["2", "1"]


def test_resolve_idp_ids_by_name_no_match():
    """Test that an unmatched name is skipped, not raised."""
    idps = [{"idpId": "1", "name": "Azure AD IdP"}]
    ids = resolve_idp_ids_by_name(idps, ["Nonexistent IdP"])
    assert ids == []


def test_resolve_idp_ids_by_name_partial_match():
    """Test that only matched names contribute ids when some names don't match."""
    idps = [{"idpId": "1", "name": "Azure AD IdP"}]
    ids = resolve_idp_ids_by_name(idps, ["Azure AD IdP", "Missing"])
    assert ids == ["1"]


@pytest.mark.asyncio
async def test_fetch_all_idps_single_page():
    """Test fetching IdPs with a single page of results."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Link": ""}
    mock_response.json.return_value = {"items": [{"idpId": "1", "name": "Azure AD IdP", "type": "Azure AD"}]}
    mock_client.request = AsyncMock(return_value=mock_response)

    idps = await cam_users.fetch_all_idps(mock_client, "123456")

    assert len(idps) == 1
    assert idps[0]["name"] == "Azure AD IdP"


def test_build_search_body_minimal():
    """Test building search body with no filters."""
    body = build_search_body({}, per_page=10)
    assert body == {"perPage": 10}


def test_build_search_body_with_filters():
    """Test building search body with all filter types."""
    filters = {
        "searchQuery": "miles",
        "sortKey": "username",
        "sortOrder": "asc",
        "accessTypes": ("Wireless",),
        "groupIds": ("4000", "4001"),
        "idpIds": ("3000",),
        "statuses": ("Enabled",),
        "userIds": ("2000",),
    }
    body = build_search_body(filters, per_page=5)
    assert body["perPage"] == 5
    assert body["searchQuery"] == "miles"
    assert body["sortKey"] == "username"
    assert body["sortOrder"] == "asc"
    assert body["accessTypes"] == ["Wireless"]
    assert body["groupIds"] == ["4000", "4001"]
    assert body["idpIds"] == ["3000"]
    assert body["statuses"] == ["Enabled"]
    assert body["userIds"] == ["2000"]


def test_build_search_body_omits_empty_filters():
    """Test that empty/falsy filter values are omitted from the body."""
    filters = {"searchQuery": "", "accessTypes": (), "sortKey": None}
    body = build_search_body(filters, per_page=10)
    assert body == {"perPage": 10}


def test_format_search_jsonl():
    """Test that jsonl format emits one compact JSON object per line."""
    rows = [{"id": "1", "email": "a@example.com"}, {"id": "2", "email": "b@example.com"}]
    result = cam_users.format_search_jsonl(rows)
    lines = result.strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["email"] == "a@example.com"


def test_format_search_jsonl_empty():
    """Test that jsonl format of an empty list is an empty string."""
    assert cam_users.format_search_jsonl([]) == ""


def test_flatten_search_results():
    """Test flattening nested search results into rows."""
    items = [
        {
            "upn": "mdavis@meraki.com",
            "userId": "1284392014819",
            "idpUsers": [
                {
                    "id": "1284392014819",
                    "displayName": "Miles Davis",
                    "email": "mdavis@meraki.com",
                    "username": "mdavis",
                    "accessTypes": ["Wireless", "Client VPN"],
                    "groups": [{"id": "1", "name": "Group name"}],
                    "idp": {"id": "1", "name": "Azure AD IdP", "type": "Azure AD"},
                    "createdAt": "2018-02-11T00:00:00Z",
                    "lastUpdatedAt": "2018-05-12T00:00:00Z",
                    "externalId": "external-Id",
                }
            ],
        }
    ]
    rows = flatten_search_results(items)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "1284392014819"
    assert row["displayName"] == "Miles Davis"
    assert row["accessTypes"] == "Wireless;Client VPN"
    assert row["groups"] == "Group name"
    assert row["idp.name"] == "Azure AD IdP"
    assert row["upn"] == "mdavis@meraki.com"
    assert row["userId"] == "1284392014819"


def test_flatten_search_results_multiple_idp_users():
    """Test flattening a result with multiple idpUsers under one upn."""
    items = [
        {
            "upn": "shared@meraki.com",
            "userId": "1",
            "idpUsers": [
                {"id": "a", "displayName": "A", "accessTypes": [], "groups": []},
                {"id": "b", "displayName": "B", "accessTypes": [], "groups": []},
            ],
        }
    ]
    rows = flatten_search_results(items)
    assert len(rows) == 2
    assert {r["id"] for r in rows} == {"a", "b"}


def test_flatten_search_results_empty():
    """Test flattening an empty result set."""
    assert flatten_search_results([]) == []


def test_parse_next_starting_after():
    """Test extracting startingAfter token from a Link header."""
    link = '<https://api.meraki.com/api/v1/organizations/123/iam/users/search?startingAfter=abc123>; rel="next"'
    assert parse_next_starting_after(link) == "abc123"


def test_parse_next_starting_after_missing():
    """Test Link header with no next rel returns None."""
    link = '<https://api.meraki.com/api/v1/organizations/123/iam/users/search?startingAfter=abc123>; rel="prev"'
    assert parse_next_starting_after(link) is None


def test_parse_next_starting_after_empty():
    """Test empty Link header returns None."""
    assert parse_next_starting_after("") is None


@pytest.mark.asyncio
async def test_search_users_single_page():
    """Test searching users with a single page of results."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Link": ""}
    mock_response.json.return_value = {
        "items": [
            {
                "upn": "mdavis@meraki.com",
                "userId": "1284392014819",
                "idpUsers": [{"id": "1284392014819", "displayName": "Miles Davis", "accessTypes": [], "groups": []}],
            }
        ],
        "meta": {"counts": {"items": {"total": 1, "remaining": 0}}},
    }
    mock_client.request = AsyncMock(return_value=mock_response)

    rows = await cam_users.search_users(mock_client, "123456", {}, per_page=10, limit=0)

    assert len(rows) == 1
    assert rows[0]["id"] == "1284392014819"


@pytest.mark.asyncio
async def test_search_users_respects_limit():
    """Test that search stops paginating once the limit is reached."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Link": '<...?startingAfter=next>; rel="next"'}
    mock_response.json.return_value = {
        "items": [
            {"upn": "a@meraki.com", "userId": "1", "idpUsers": [{"id": "a", "accessTypes": [], "groups": []}]},
            {"upn": "b@meraki.com", "userId": "2", "idpUsers": [{"id": "b", "accessTypes": [], "groups": []}]},
        ],
    }
    mock_client.request = AsyncMock(return_value=mock_response)

    rows = await cam_users.search_users(mock_client, "123456", {}, per_page=10, limit=1)

    assert len(rows) == 1


def test_flatten_group_results():
    """Test flattening group search results into display rows."""
    items = [
        {
            "groupId": "627126248111344205",
            "name": "Finance",
            "memberships": 13,
            "externalId": "039a63c5-b8ec-468c-825f-0d680240f17f",
            "idp": {"id": "627126248111346509", "name": "pseudoco", "type": "Azure AD"},
        }
    ]
    rows = cam_users.flatten_group_results(items)
    assert rows == [
        {
            "groupId": "627126248111344205",
            "name": "Finance",
            "memberships": 13,
            "idp.name": "pseudoco",
            "idp.type": "Azure AD",
            "externalId": "039a63c5-b8ec-468c-825f-0d680240f17f",
        }
    ]


def test_build_group_search_body_minimal():
    """Test building group search body with no filters."""
    body = cam_users.build_group_search_body({}, per_page=10)
    assert body == {"perPage": 10}


def test_build_group_search_body_with_filters():
    """Test building group search body with searchQuery and idpIds."""
    filters = {"searchQuery": "finance", "idpIds": ("3000",)}
    body = cam_users.build_group_search_body(filters, per_page=5)
    assert body == {"perPage": 5, "searchQuery": "finance", "idpIds": ["3000"]}


@pytest.mark.asyncio
async def test_search_groups_single_page():
    """Test searching groups with a single page of results."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Link": ""}
    mock_response.json.return_value = {
        "items": [
            {
                "groupId": "1",
                "name": "Finance",
                "memberships": 13,
                "externalId": "ext-1",
                "idp": {"id": "1", "name": "pseudoco", "type": "Azure AD"},
            }
        ],
    }
    mock_client.request = AsyncMock(return_value=mock_response)

    rows = await cam_users.search_groups(mock_client, "123456", {}, per_page=10, limit=0)

    assert len(rows) == 1
    assert rows[0]["groupId"] == "1"
    assert rows[0]["name"] == "Finance"


@pytest.mark.asyncio
async def test_search_groups_respects_limit():
    """Test that group search stops paginating once the limit is reached."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Link": '<...?startingAfter=next>; rel="next"'}
    mock_response.json.return_value = {
        "items": [
            {"groupId": "1", "name": "A", "memberships": 1, "externalId": "e1", "idp": {}},
            {"groupId": "2", "name": "B", "memberships": 2, "externalId": "e2", "idp": {}},
        ],
    }
    mock_client.request = AsyncMock(return_value=mock_response)

    rows = await cam_users.search_groups(mock_client, "123456", {}, per_page=10, limit=1)

    assert len(rows) == 1


def test_format_search_csv_with_custom_columns():
    """Test that format_search_csv accepts a custom column list for groups."""
    rows = [{"groupId": "1", "name": "Finance"}]
    output = cam_users.format_search_csv(rows, columns=cam_users.GROUP_CSV_COLUMNS)
    assert "groupId" in output.splitlines()[0]
    assert "Finance" in output


def test_format_search_table_with_custom_columns():
    """Test that format_search_table accepts a custom column list for groups."""
    rows = [{"groupId": "1", "name": "Finance"}]
    output = cam_users.format_search_table(rows, columns=cam_users.GROUP_CSV_COLUMNS)
    assert "groupId" in output
    assert "Finance" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
