import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from collections import namedtuple
from unittest.mock import patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limbs.skills import self_repair


class _FakeProc:
    def __init__(self, poll_value):
        self._poll_value = poll_value

    def poll(self):
        return self._poll_value


class _FakeServer:
    def __init__(self, proc):
        self.transport = "stdio"
        self._proc = proc
        self._tools = []


class TestSelfRepairLoop(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.workspace = os.path.join(self.root, "workspace")
        self.test_reports = os.path.join(self.root, "test_reports")
        os.makedirs(self.workspace, exist_ok=True)
        os.makedirs(self.test_reports, exist_ok=True)
        self.ctx = {
            "workspace": self.workspace,
            "owner_id": "admin",
            "session_key": "unit_test",
        }

    def tearDown(self):
        shutil.rmtree(self.root)

    @patch("limbs.skills.self_repair.hub.reload_mcp")
    @patch("limbs.skills.self_repair.shutil.disk_usage")
    def test_triggers_disk_cleanup_and_mcp_reconnect(self, mock_disk_usage, mock_reload_mcp):
        stale_log = os.path.join(self.test_reports, "old.log")
        with open(stale_log, "w", encoding="utf-8") as f:
            f.write("x" * 2048)
        old_ts = time.time() - 7200
        os.utime(stale_log, (old_ts, old_ts))

        usage = namedtuple("usage", ["total", "used", "free"])
        mock_disk_usage.side_effect = [
            usage(10 * 1024**3, 9900 * 1024**2, 100 * 1024**2),   # before cleanup
            usage(10 * 1024**3, 9800 * 1024**2, 200 * 1024**2),   # after cleanup
        ]
        mock_reload_mcp.return_value = ({"sqlite-memory"}, set(), 1)

        with patch("mcp_client._servers", {"sqlite-memory": _FakeServer(_FakeProc(1))}):
            report = self_repair.tool_self_repair_loop(
                {"disk_free_mb_threshold": 512, "cleanup_limit_mb": 64},
                self.ctx,
            )

        self.assertFalse(os.path.exists(stale_log))
        self.assertIn("Disk cleanup triggered", report)
        self.assertIn("MCP reconnect attempted", report)
        mock_reload_mcp.assert_called_once()

    @patch("limbs.skills.self_repair.hub.reload_mcp")
    @patch("limbs.skills.self_repair.shutil.disk_usage")
    def test_skips_repairs_when_system_is_healthy(self, mock_disk_usage, mock_reload_mcp):
        usage = namedtuple("usage", ["total", "used", "free"])
        mock_disk_usage.return_value = usage(10 * 1024**3, 4 * 1024**3, 6 * 1024**3)

        with patch("mcp_client._servers", {}):
            report = self_repair.tool_self_repair_loop({}, self.ctx)

        self.assertIn("Disk cleanup skipped", report)
        self.assertIn("MCP reconnect skipped", report)
        mock_reload_mcp.assert_not_called()

    @patch("limbs.skills.self_repair.shutil.disk_usage")
    def test_writes_repair_history_record(self, mock_disk_usage):
        usage = namedtuple("usage", ["total", "used", "free"])
        mock_disk_usage.return_value = usage(10 * 1024**3, 4 * 1024**3, 6 * 1024**3)

        with patch("mcp_client._servers", {}):
            report = self_repair.tool_self_repair_loop({}, self.ctx)

        history_path = os.path.join(self.workspace, "files", "self_repair_history.jsonl")
        self.assertTrue(os.path.exists(history_path))
        self.assertIn("History recorded", report)

        with open(history_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
        self.assertEqual(len(lines), 1)

        rec = json.loads(lines[0])
        self.assertIn("ts", rec)
        self.assertIn("trigger_reasons", rec)
        self.assertIn("actions", rec)
        self.assertIn("disk_cleanup", rec["actions"])
        self.assertIn("mcp_reconnect", rec["actions"])

    @patch("limbs.skills.self_repair.shutil.disk_usage")
    def test_reads_repair_history_summary(self, mock_disk_usage):
        usage = namedtuple("usage", ["total", "used", "free"])
        mock_disk_usage.return_value = usage(10 * 1024**3, 4 * 1024**3, 6 * 1024**3)

        with patch("mcp_client._servers", {}):
            self_repair.tool_self_repair_loop({}, self.ctx)
            self_repair.tool_self_repair_loop({}, self.ctx)

        summary = self_repair.tool_self_repair_history({"limit": 1}, self.ctx)
        self.assertIn("Self-repair history", summary)
        self.assertIn("reasons=", summary)


if __name__ == "__main__":
    unittest.main()
