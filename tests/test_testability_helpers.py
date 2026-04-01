import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
E2E_CONFIG = os.path.join(PROJECT_ROOT, "tests", "e2e_config.json")
os.environ["AGENT_CONFIG"] = E2E_CONFIG

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import main as main_mod


class TestTestabilityHelpers(unittest.TestCase):
    def setUp(self):
        with main_mod.EventBus._lock:
            main_mod.EventBus._clients = {}
        with main_mod._RECENT_ERROR_LOCK:
            main_mod._RECENT_ERROR = None

    def test_error_snapshot_contract(self):
        try:
            raise RuntimeError("synthetic crash")
        except RuntimeError as exc:
            main_mod._record_recent_error("unit_test", exc)

        snap = main_mod._get_recent_error()
        self.assertIsInstance(snap, dict)
        self.assertEqual(snap["where"], "unit_test")
        self.assertIn("synthetic crash", snap["message"])
        self.assertIn("RuntimeError", snap["stack"])
        self.assertIn("ts", snap)

    def test_eventbus_stats(self):
        sid = "testability_sid"
        q = main_mod.EventBus.subscribe(sid)
        stats = main_mod.EventBus.stats()
        self.assertGreaterEqual(stats["active_sessions"], 1)
        self.assertGreaterEqual(stats["active_connections"], 1)

        main_mod.EventBus.unsubscribe(sid, q)
        after = main_mod.EventBus.stats()
        self.assertGreaterEqual(after["active_sessions"], 0)
        self.assertGreaterEqual(after["active_connections"], 0)

    def test_health_payload_dependencies_present(self):
        payload = main_mod._build_test_health_payload()
        self.assertTrue(payload["ok"])
        self.assertIn("active_sessions", payload)
        self.assertIn("active_connections", payload)
        self.assertIn("loaded_limbs", payload)
        self.assertIn("recent_error", payload)
        self.assertIn("ts", payload)

        loaded = main_mod._loaded_limb_names()
        self.assertIsInstance(loaded, list)
        self.assertIn("exec", loaded)
        self.assertIn("self_check", loaded)

    def test_html_contains_testability_markers(self):
        html = main_mod.HTML_UI
        markers = [
            'data-testid="chat-input"',
            'data-testid="send-button"',
            'data-testid="chat-stream"',
            'data-testid="system-status-bar"',
            'data-testid="log-stream"',
            "log-event-tool-call",
            "log-event-tool-success",
            "log-event-llm-timeout",
            "log-event-error",
        ]
        for marker in markers:
            self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main()
