import os
import sys
import unittest
from unittest.mock import patch


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
        with main_mod._ACTIVE_TASKS_LOCK:
            main_mod._ACTIVE_TASKS = {}

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
        # Ensure limbs are loaded into the registry for this test context
        # We must clear cache and registry to prevent cross-test contamination
        main_mod.limbs_hub.Registry.clear()
        main_mod.limbs_hub._loaded_mtimes.clear()
        main_mod.limbs_hub.init_extra(main_mod.CONF)
        
        payload = main_mod._build_test_health_payload()
        self.assertTrue(payload["ok"])
        self.assertIn("active_sessions", payload)
        self.assertIn("active_connections", payload)
        self.assertIn("loaded_limbs", payload)
        self.assertIn("recent_error", payload)
        self.assertIn("active_tasks", payload)
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
            'data-testid="stop-button"',
            'data-testid="chat-stream"',
            'data-testid="system-status-bar"',
            'data-testid="log-stream"',
            "log-event-thought",
            "log-event-tool-call",
            "log-event-tool-success",
            "log-event-llm-timeout",
            "log-event-tool-quality",
            "log-event-error",
        ]
        for marker in markers:
            self.assertIn(marker, html)

    def test_structured_event_mapping(self):
        event = main_mod._structured_event_from_log("Thought: planning next action", None)
        self.assertEqual(event[0], "thought")
        self.assertEqual(event[1], "planning next action")

        event = main_mod._structured_event_from_log("Action: Calling tool 'write_file' with args {}", None)
        self.assertEqual(event[0], "tool_start")
        self.assertEqual(event[2]["tool"], "write_file")

        event = main_mod._structured_event_from_log("Result: done", "write_file")
        self.assertEqual(event[0], "tool_end")
        self.assertEqual(event[2]["status"], "ok")
        self.assertEqual(event[2]["tool"], "write_file")

    def test_request_task_stop_authorization(self):
        class DummyThread:
            ident = 123

            @staticmethod
            def is_alive():
                return True

        with main_mod._ACTIVE_TASKS_LOCK:
            main_mod._ACTIVE_TASKS["sid_a"] = {
                "thread": DummyThread(),
                "owner_id": "owner_a",
                "stop_requested": False,
            }

        forbidden = main_mod._request_task_stop("sid_a", "intruder")
        self.assertFalse(forbidden["ok"])
        self.assertEqual(forbidden["status_code"], 403)

        with patch.object(main_mod, "_raise_async_exception", return_value=True):
            allowed = main_mod._request_task_stop("sid_a", "owner_a")
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["status_code"], 200)

    def test_run_agent_task_abort_lifecycle(self):
        events = []

        def fake_publish(sid, event_type, content, extra=None):
            events.append((event_type, content, extra))

        with patch.object(main_mod.EventBus, "publish", side_effect=fake_publish):
            with patch.object(main_mod.llm, "chat", side_effect=main_mod.TaskAbortRequested()):
                main_mod.run_agent_task("sid_abort", "stop me")

        event_types = [e[0] for e in events]
        self.assertIn("lifecycle", event_types)
        self.assertIn("done", event_types)
        lifecycle = [e for e in events if e[0] == "lifecycle"][-1]
        self.assertEqual((lifecycle[2] or {}).get("phase"), "aborted")


if __name__ == "__main__":
    unittest.main()
