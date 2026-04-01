import os
import shutil
import sqlite3
import tempfile
import unittest

from ai_forum.ai_execute_api import AIExecuteService


class TestAIExecuteSandbox(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for d in ("workspace", "limbs", "brain", "tests", "ai_forum", ".git", "venv"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)

        self.audit_db = os.path.join(self.root, "ai_forum", "execution_log.db")
        self.forum_db = os.path.join(self.root, "ai_forum", "forum.db")
        self.service = AIExecuteService(
            project_root=self.root,
            audit_db_path=self.audit_db,
            forum_db_path=self.forum_db,
        )

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.root)

    def test_blocks_path_traversal_write(self):
        res = self.service.execute(
            actor="developer_ai",
            command={
                "action": "write_file",
                "params": {"path": "../../../etc/passwd", "content": "x"},
            },
            source="unit",
        )
        self.assertFalse(res["ok"])
        self.assertIn("blocked", res["error"])

    def test_blocks_git_config_write(self):
        res = self.service.execute(
            actor="developer_ai",
            command={
                "action": "write_file",
                "params": {"path": ".git/config", "content": "x"},
            },
            source="unit",
        )
        self.assertFalse(res["ok"])
        self.assertIn("blocked", res["error"])

    def test_blocks_venv_write(self):
        res = self.service.execute(
            actor="developer_ai",
            command={
                "action": "write_file",
                "params": {"path": "venv/pwn.py", "content": "print(1)"},
            },
            source="unit",
        )
        self.assertFalse(res["ok"])
        self.assertIn("blocked", res["error"])

    def test_allows_controlled_write(self):
        rel = "tests/sandbox_ok.txt"
        res = self.service.execute(
            actor="developer_ai",
            command={
                "action": "write_file",
                "params": {"path": rel, "content": "hello"},
            },
            source="unit",
        )
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.exists(os.path.join(self.root, rel)))

    def test_audit_log_written(self):
        self.service.execute(
            actor="developer_ai",
            command={"action": "check_status", "params": {}},
            source="unit",
        )
        conn = sqlite3.connect(self.audit_db)
        count = conn.execute("SELECT COUNT(*) FROM execution_logs").fetchone()[0]
        conn.close()
        self.assertGreaterEqual(int(count), 1)


if __name__ == "__main__":
    unittest.main()
