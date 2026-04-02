import os
import sys
import unittest
from unittest.mock import patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.memory import manager


class _SearchResult:
    def __init__(self, records):
        self._records = records

    def limit(self, _count):
        return self

    def to_list(self):
        return list(self._records)


class _FakeTable:
    def __init__(self, records):
        self._records = records

    def search(self, _vec):
        return _SearchResult(self._records)


class TestMemoryNamespace(unittest.TestCase):
    def setUp(self):
        self._old_enabled = manager._enabled
        self._old_table = manager._table
        self._old_config = manager._config
        manager._enabled = True
        manager._config = {"retrieve_top_k": 5}

    def tearDown(self):
        manager._enabled = self._old_enabled
        manager._table = self._old_table
        manager._config = self._old_config

    def test_infer_role_scope(self):
        self.assertEqual(manager._infer_role_scope("web_developer_ai"), "dev")
        self.assertEqual(manager._infer_role_scope("web_IronGate"), "qa")
        self.assertEqual(manager._infer_role_scope("web_shadow"), "ops")
        self.assertEqual(manager._infer_role_scope("random-user"), "context")

    @patch("brain.memory.manager._embed", return_value=[[0.1, 0.2]])
    def test_retrieve_filters_unreadable_scopes(self, _mock_embed):
        manager._table = _FakeTable([
            {"id": "seed", "fact": "System initialized", "session_key": "init"},
            {"id": "p1", "fact": "Resolved API baseline", "session_key": "public::web_irongate"},
            {"id": "d1", "fact": "Refactor plan", "session_key": "dev::web_developer_ai"},
            {"id": "q1", "fact": "Hidden QA note", "session_key": "qa::web_irongate"},
            {"id": "c1", "fact": "Cross-team handoff", "session_key": "context::web_shadow"},
            {"id": "l1", "fact": "Legacy own", "session_key": "web_developer_ai"},
            {"id": "l2", "fact": "Legacy other", "session_key": "web_irongate"},
        ])

        output = manager.retrieve("memory query", "web_developer_ai", top_k=10)
        self.assertIn("[public] Resolved API baseline", output)
        self.assertIn("[dev] Refactor plan", output)
        self.assertIn("[context] Cross-team handoff", output)
        self.assertIn("[context] Legacy own", output)
        self.assertNotIn("Hidden QA note", output)
        self.assertNotIn("Legacy other", output)

    @patch("brain.memory.manager._embed", return_value=[[0.1]])
    def test_retrieve_scope_override_denied(self, _mock_embed):
        manager._table = _FakeTable([
            {"id": "q1", "fact": "Hidden QA note", "session_key": "qa::web_irongate"},
        ])
        output = manager.retrieve("memory query", "web_developer_ai", top_k=10, scope="qa")
        self.assertEqual(output, "")

    def test_public_write_requires_explicit_gate(self):
        msgs = [
            {"role": "user", "content": "Need memory update."},
            {"role": "assistant", "content": "Acknowledged."},
        ]
        with patch("brain.memory.manager.threading.Thread") as mock_thread:
            manager.compress_async(msgs, "web_developer_ai", scope="public", allow_public_write=False)
        mock_thread.assert_not_called()

    def test_cross_role_write_is_blocked(self):
        msgs = [
            {"role": "user", "content": "Need memory update."},
            {"role": "assistant", "content": "Acknowledged."},
        ]
        with patch("brain.memory.manager.threading.Thread") as mock_thread:
            manager.compress_async(msgs, "web_developer_ai", scope="qa")
        mock_thread.assert_not_called()

    def test_default_dev_scope_starts_background_worker(self):
        msgs = [
            {"role": "user", "content": "Need memory update."},
            {"role": "assistant", "content": "Acknowledged."},
        ]
        with patch("brain.memory.manager.threading.Thread") as mock_thread:
            manager.compress_async(msgs, "web_developer_ai")
            mock_thread.assert_called_once()
            worker_args = mock_thread.call_args.kwargs.get("args", ())
            self.assertEqual(worker_args[1], "web_developer_ai")
            self.assertEqual(worker_args[2], "dev")
            mock_thread.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
