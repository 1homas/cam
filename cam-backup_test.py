#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "click>=8.0.0",
# ]
# ///
"""Tests for cam-backup.py"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import the module using importlib to handle hyphenated name
import importlib.util

spec = importlib.util.spec_from_file_location("cam_backup", "cam-backup.py")
cam_backup = importlib.util.module_from_spec(spec)
sys.modules["cam_backup"] = cam_backup
spec.loader.exec_module(cam_backup)


class TestCamBackup(unittest.TestCase):
    """Test cases for CAM backup script"""

    def setUp(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.org_id = "test-org-123"
        self.api_key = "test-api-key"

        # Mock environment variables
        os.environ["MERAKI_ORG_ID"] = self.org_id
        os.environ["MERAKI_DASHBOARD_API_KEY"] = self.api_key

    def tearDown(self):
        """Clean up test fixtures"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.environ.pop("MERAKI_ORG_ID", None)
        os.environ.pop("MERAKI_DASHBOARD_API_KEY", None)

    def test_create_backup_directory_structure(self):
        """Test that backup directory structure is created correctly"""
        from cam_backup import create_backup_dir

        backup_dir = create_backup_dir(self.test_dir)
        today = date.today().strftime("%Y-%m-%d")
        expected = Path(self.test_dir) / "backups" / today

        self.assertEqual(backup_dir, expected)
        self.assertTrue(backup_dir.exists())
        self.assertTrue(backup_dir.is_dir())

    def test_get_endpoints(self):
        """Test that all required endpoints are returned"""
        from cam_backup import get_backup_endpoints

        endpoints = get_backup_endpoints()

        # Should contain multiple endpoints
        self.assertGreater(len(endpoints), 0)

        # Check for critical endpoints
        endpoint_names = [ep["name"] for ep in endpoints]
        self.assertIn("clients", endpoint_names)
        self.assertIn("authorization_policies", endpoint_names)
        self.assertIn("client_groups", endpoint_names)

    def test_save_json_to_file(self):
        """Test saving JSON data to a file"""
        from cam_backup import save_json

        test_data = {"test": "data", "items": [1, 2, 3]}
        test_file = Path(self.test_dir) / "test.json"

        save_json(test_data, test_file)

        self.assertTrue(test_file.exists())
        with open(test_file) as f:
            loaded = json.load(f)
        self.assertEqual(loaded, test_data)

    def test_fetch_endpoint_success(self):
        """Test successful API fetch"""
        from cam_backup import fetch_endpoint
        import asyncio
        from unittest.mock import AsyncMock

        # Mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": [{"id": "1"}], "meta": {"totalCount": 1}}
        mock_response.headers = {}

        mock_http_client = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        endpoint = {
            "name": "test",
            "path": "/organizations/{orgId}/nac/clients",
            "filename": "clients.json",
        }

        result = asyncio.run(fetch_endpoint(mock_http_client, self.org_id, endpoint))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["items"], [{"id": "1"}])
        self.assertEqual(result["data"]["meta"]["totalCount"], 1)

    def test_fetch_endpoint_with_pagination(self):
        """Test API fetch with pagination"""
        from cam_backup import fetch_endpoint
        import asyncio
        from unittest.mock import AsyncMock

        # Mock first response
        mock_response1 = MagicMock()
        mock_response1.status_code = 200
        mock_response1.json.return_value = {"items": [{"id": "1"}], "meta": {"totalCount": 2}}
        mock_response1.headers = {"Link": '<https://api.meraki.com?startingAfter=abc>; rel="next"'}

        # Mock second response
        mock_response2 = MagicMock()
        mock_response2.status_code = 200
        mock_response2.json.return_value = {"items": [{"id": "2"}]}
        mock_response2.headers = {}

        mock_http_client = MagicMock()
        mock_http_client.get = AsyncMock(side_effect=[mock_response1, mock_response2])

        endpoint = {
            "name": "test",
            "path": "/organizations/{orgId}/nac/clients",
            "filename": "clients.json",
        }

        result = asyncio.run(fetch_endpoint(mock_http_client, self.org_id, endpoint))

        # Should have combined both pages
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["data"]["items"]), 2)
        self.assertEqual(result["pages"], 2)

    def test_fetch_endpoint_license_usage_sends_required_date_params(self):
        """license/usage requires startDate, so it must be sent to avoid a 400"""
        from cam_backup import fetch_endpoint
        import asyncio
        from unittest.mock import AsyncMock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"organizationId": self.org_id}
        mock_response.headers = {}

        mock_http_client = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        endpoint = {
            "name": "license_usage",
            "path": "/organizations/{orgId}/nac/license/usage",
            "filename": "license_usage.json",
        }

        result = asyncio.run(fetch_endpoint(mock_http_client, self.org_id, endpoint))

        self.assertEqual(result["status"], "success")
        called_params = mock_http_client.get.call_args.kwargs["params"]
        self.assertIn("startDate", called_params)


class TestOrgOption(unittest.TestCase):
    """Test cases for the -o/--org CLI option"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.environ["MERAKI_DASHBOARD_API_KEY"] = "test-api-key"
        os.environ["MERAKI_ORG_ID"] = "env-org-456"

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.environ.pop("MERAKI_ORG_ID", None)
        os.environ.pop("MERAKI_DASHBOARD_API_KEY", None)

    def test_dash_o_flag_overrides_env(self):
        from click.testing import CliRunner

        captured = {}

        async def fake_run(org_id, backup_base_dir):
            captured["org_id"] = org_id

        with patch.object(cam_backup, "run", fake_run):
            result = CliRunner().invoke(cam_backup.main, ["-o", "flag-org-123", "--dir", self.test_dir])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(captured["org_id"], "flag-org-123")

    def test_falls_back_to_env_var(self):
        from click.testing import CliRunner

        captured = {}

        async def fake_run(org_id, backup_base_dir):
            captured["org_id"] = org_id

        with patch.object(cam_backup, "run", fake_run):
            result = CliRunner().invoke(cam_backup.main, ["--dir", self.test_dir])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(captured["org_id"], "env-org-456")

    def test_errors_without_org_id(self):
        from click.testing import CliRunner

        os.environ.pop("MERAKI_ORG_ID", None)
        result = CliRunner().invoke(cam_backup.main, ["--dir", self.test_dir])

        self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
