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
"""Tests for cam-sessions-history.py"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from click.testing import CliRunner

spec = importlib.util.spec_from_file_location("cam_sessions_history", Path(__file__).parent / "cam-sessions-history.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["cam_sessions_history"] = mod
spec.loader.exec_module(mod)

SAMPLE_SESSIONS = [
    {
        "sessionId": "034fb765-eb67-4e25-aacb-53bf3547d689",
        "ts": "2026-05-23T00:34:28.563Z",
        "status": "Success",
        "user": {"id": "9c8ecd37da13"},
        "client": {"id": "9c:8e:cd:37:da:13", "url": "/Demo/n/1o3zXbYb/manage/clients"},
        "authentication": {"protocol": "MAB"},
        "ssid": {"name": ""},
        "classification": {"type": "IP Camera"},
        "details": {"ruleDetails": "Cameras"},
        "counts": {"events": {"total": 1}},
        "device": {"url": "/Demo/n/1o3zXbYb/manage/nodes/new_list/115335421839104/summary"},
    },
    {
        "sessionId": "9d1b2c3e-0000-4e25-aacb-53bf3547d690",
        "ts": "2026-05-23T01:00:00.000Z",
        "status": "Authentication Failed",
        "user": {"id": "jsmith@example.com"},
        "client": {"id": "AA:BB:CC:DD:EE:01", "url": "/Demo/n/1o3zXbYb/manage/clients"},
        "authentication": {"protocol": "EAP-TLS"},
        "ssid": {"name": "Corp-WiFi"},
        "classification": {"type": "Laptop"},
        "details": {"ruleDetails": "TLS_Entra_Employees"},
        "counts": {"events": {"total": 3}},
        "device": {"url": "/Demo/n/1o3zXbYb/manage/nodes/new_list/115335421839105/summary"},
    },
]


def page(items, status_code=200, headers=None):
    """Build a mocked sessions/history page response."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"items": items, "meta": {}}
    response.headers = headers or {}
    return response


class TestBuildParams:
    def test_days_becomes_timespan_seconds(self):
        assert mod.build_params(days=7, t0=None, t1=None) == {"timespan": 604800}

    def test_timespan_capped_at_api_max(self):
        assert mod.build_params(days=90, t0=None, t1=None) == {"timespan": mod.MAX_TIMESPAN}

    def test_t0_replaces_timespan(self):
        params = mod.build_params(days=31, t0="2026-08-01T00:00:00Z", t1=None)
        assert params == {"t0": "2026-08-01T00:00:00Z"}

    def test_t0_and_t1_bound_the_window(self):
        params = mod.build_params(days=31, t0="2026-08-01T00:00:00Z", t1="2026-08-05T00:00:00Z")
        assert params == {"t0": "2026-08-01T00:00:00Z", "t1": "2026-08-05T00:00:00Z"}

    def test_t1_without_t0_raises(self):
        with pytest.raises(ValueError):
            mod.build_params(days=31, t0=None, t1="2026-08-05T00:00:00Z")


class TestFetchSessionsHistory:
    @pytest.mark.asyncio
    async def test_single_page(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=page(SAMPLE_SESSIONS))

        result = await mod.fetch_sessions_history(mock_client, "org123", days=7, per_page=1000)
        assert result == SAMPLE_SESSIONS

    @pytest.mark.asyncio
    async def test_sends_timespan_and_per_page(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=page(SAMPLE_SESSIONS))

        await mod.fetch_sessions_history(mock_client, "org123", days=7, per_page=500)

        params = mock_client.get.call_args.kwargs["params"]
        assert params["timespan"] == 604800
        assert params["perPage"] == 500
        assert params["startingAfter"] == 0

    @pytest.mark.asyncio
    async def test_offset_pagination(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[page(SAMPLE_SESSIONS), page(SAMPLE_SESSIONS[:1])]
        )

        result = await mod.fetch_sessions_history(mock_client, "org123", days=7, per_page=2)
        assert len(result) == 3

        offsets = [call.kwargs["params"]["startingAfter"] for call in mock_client.get.call_args_list]
        assert offsets == [0, 2]

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[page(SAMPLE_SESSIONS), page([])])

        result = await mod.fetch_sessions_history(mock_client, "org123", days=7, per_page=2)
        assert len(result) == 2
        assert mock_client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_rate_limit_retry(self):
        rate_limited = page([], status_code=429, headers={"Retry-After": "0.01"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_limited, page(SAMPLE_SESSIONS)])

        result = await mod.fetch_sessions_history(mock_client, "org123", days=7, per_page=1000)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_max_pages_safety_limit(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=page(SAMPLE_SESSIONS))

        result = await mod.fetch_sessions_history(
            mock_client, "org123", days=7, per_page=2, max_pages=3
        )
        assert mock_client.get.await_count == 3
        assert len(result) == 6

    @pytest.mark.asyncio
    async def test_bare_list_response_is_accepted(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = SAMPLE_SESSIONS
        response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        result = await mod.fetch_sessions_history(mock_client, "org123", days=7, per_page=1000)
        assert result == SAMPLE_SESSIONS


class TestBuildPayload:
    def test_payload_shape(self):
        payload = mod.build_payload("org123", SAMPLE_SESSIONS, days=7, t0=None, t1=None)
        assert payload["organizationId"] == "org123"
        assert payload["count"] == 2
        assert payload["sessions"] == SAMPLE_SESSIONS
        assert payload["window"] == {"days": 7, "t0": None, "t1": None}
        assert payload["fetchedAt"].endswith("Z")

    def test_absolute_window_clears_days(self):
        payload = mod.build_payload("org123", [], days=31, t0="2026-08-01T00:00:00Z", t1=None)
        assert payload["window"] == {"days": None, "t0": "2026-08-01T00:00:00Z", "t1": None}


class TestFormatters:
    def test_json_wraps_payload_with_metadata(self):
        payload = mod.build_payload("org123", SAMPLE_SESSIONS, days=7, t0=None, t1=None)
        parsed = json.loads(mod.format_json(payload))
        assert parsed["count"] == 2
        assert len(parsed["sessions"]) == 2

    def test_json_pretty_is_indented(self):
        payload = mod.build_payload("org123", SAMPLE_SESSIONS, days=7, t0=None, t1=None)
        assert "\n" in mod.format_json(payload, pretty=True)
        assert "\n" not in mod.format_json(payload, pretty=False)

    def test_yaml_wraps_payload_with_metadata(self):
        payload = mod.build_payload("org123", SAMPLE_SESSIONS, days=7, t0=None, t1=None)
        parsed = yaml.safe_load(mod.format_yaml(payload))
        assert parsed["count"] == 2
        assert len(parsed["sessions"]) == 2

    def test_jsonl_emits_one_session_per_line(self):
        payload = mod.build_payload("org123", SAMPLE_SESSIONS, days=7, t0=None, t1=None)
        lines = mod.format_jsonl(payload).splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["sessionId"] == SAMPLE_SESSIONS[0]["sessionId"]

    def test_csv_has_header_and_flattened_rows(self):
        payload = mod.build_payload("org123", SAMPLE_SESSIONS, days=7, t0=None, t1=None)
        lines = mod.format_csv(payload).strip().split("\n")
        assert len(lines) == 3  # header + 2 rows
        assert lines[0].startswith("ts,sessionId,status,")
        assert "MAB" in lines[1]

    def test_csv_handles_no_sessions(self):
        payload = mod.build_payload("org123", [], days=7, t0=None, t1=None)
        assert mod.format_csv(payload) == ""

    def test_table_is_markdown_with_csv_columns(self):
        payload = mod.build_payload("org123", SAMPLE_SESSIONS, days=7, t0=None, t1=None)
        lines = mod.format_table(payload).strip().split("\n")
        assert len(lines) == 4  # header + separator + 2 rows
        assert lines[1].startswith("| ---")

        # csv.writer emits \r\n line endings; strip it before comparing headers
        csv_headers = mod.format_csv(payload).strip().split("\n")[0].strip("\r").split(",")
        table_headers = [h.strip() for h in lines[0].strip("|").split("|")]
        assert table_headers == csv_headers

    def test_table_handles_no_sessions(self):
        payload = mod.build_payload("org123", [], days=7, t0=None, t1=None)
        assert mod.format_table(payload) == ""


class TestCli:
    def _patch_fetch(self, monkeypatch, captured):
        async def fake_fetch(client, org_id, days=31, t0=None, t1=None, per_page=1000, max_pages=250):
            captured.update(org_id=org_id, days=days, t0=t0, t1=t1, per_page=per_page, max_pages=max_pages)
            return SAMPLE_SESSIONS

        monkeypatch.setattr(mod, "fetch_sessions_history", fake_fetch)

    def test_pages_defaults_to_zero(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        captured = {}
        self._patch_fetch(monkeypatch, captured)

        result = CliRunner().invoke(mod.main, [])
        assert result.exit_code == 0
        assert captured["max_pages"] == 0

    def test_explicit_days_ignores_pages_limit(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        captured = {}
        self._patch_fetch(monkeypatch, captured)

        result = CliRunner().invoke(mod.main, ["--days", "7", "--pages", "3"])
        assert result.exit_code == 0
        assert captured["max_pages"] == 0

    def test_explicit_t0_ignores_pages_limit(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        captured = {}
        self._patch_fetch(monkeypatch, captured)

        result = CliRunner().invoke(
            mod.main, ["--t0", "2026-08-01T00:00:00Z", "--pages", "3"]
        )
        assert result.exit_code == 0
        assert captured["max_pages"] == 0

    def test_pages_zero_fetches_all(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        captured = {}
        self._patch_fetch(monkeypatch, captured)

        result = CliRunner().invoke(mod.main, ["--pages", "0"])
        assert result.exit_code == 0
        assert captured["max_pages"] == 0

    def test_pages_option_passes_through(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        captured = {}
        self._patch_fetch(monkeypatch, captured)

        result = CliRunner().invoke(mod.main, ["-p", "5"])
        assert result.exit_code == 0
        assert captured["max_pages"] == 5

    def test_pages_negative_errors(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        result = CliRunner().invoke(mod.main, ["--pages", "-1"])
        assert result.exit_code != 0
        assert "--pages" in result.output

    def test_t1_requires_t0(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        result = CliRunner().invoke(mod.main, ["--t1", "2026-08-05T00:00:00Z"])
        assert result.exit_code != 0
        assert "--t1 requires --t0" in result.output

    def test_days_capped_at_api_max(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        captured = {}
        self._patch_fetch(monkeypatch, captured)

        result = CliRunner().invoke(mod.main, ["--days", "90"])
        assert result.exit_code == 0
        assert captured["days"] == mod.MAX_LOOKBACK_DAYS

    def test_org_flag_overrides_env(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "env_org")
        captured = {}
        self._patch_fetch(monkeypatch, captured)

        result = CliRunner().invoke(mod.main, ["--org", "flag_org"])
        assert result.exit_code == 0
        assert captured["org_id"] == "flag_org"

    def test_errors_without_api_key(self, monkeypatch):
        monkeypatch.delenv("MERAKI_DASHBOARD_API_KEY", raising=False)
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        result = CliRunner().invoke(mod.main, [])
        assert result.exit_code != 0

    def test_errors_without_org_id(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.delenv("MERAKI_ORG_ID", raising=False)
        result = CliRunner().invoke(mod.main, [])
        assert result.exit_code != 0

    def test_default_format_is_jsonl(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        self._patch_fetch(monkeypatch, {})

        result = CliRunner().invoke(mod.main, [])
        assert result.exit_code == 0
        lines = result.output.strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)

    def test_prints_json_payload_to_stdout(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        self._patch_fetch(monkeypatch, {})

        result = CliRunner().invoke(mod.main, ["--format", "json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["count"] == 2
        assert parsed["organizationId"] == "org123"

    def test_writes_output_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        self._patch_fetch(monkeypatch, {})
        target = tmp_path / "nested" / "sessions.json"

        result = CliRunner().invoke(mod.main, ["--format", "json", "-o", str(target)])
        assert result.exit_code == 0
        assert json.loads(target.read_text())["count"] == 2

    def test_csv_format_writes_rows(self, monkeypatch):
        monkeypatch.setenv("MERAKI_DASHBOARD_API_KEY", "key123")
        monkeypatch.setenv("MERAKI_ORG_ID", "org123")
        self._patch_fetch(monkeypatch, {})

        result = CliRunner().invoke(mod.main, ["--format", "csv"])
        assert result.exit_code == 0
        assert result.output.strip().split("\n")[0].startswith("ts,sessionId,status,")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
