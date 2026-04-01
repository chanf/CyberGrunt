import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import central as llm
from limbs import hub as limbs_hub


class TestSQLiteMCPFlow(unittest.TestCase):
    def setUp(self):
        self.test_root = tempfile.mkdtemp()
        self.workspace = os.path.join(self.test_root, "workspace")
        self.sessions = os.path.join(self.test_root, "sessions")
        os.makedirs(self.workspace)
        os.makedirs(self.sessions)

        models_config = {
            "default": "test_provider",
            "providers": {
                "test_provider": {
                    "api_key": "fake_key",
                    "api_base": "http://fake_api.com/v1",
                    "model": "gpt-mock",
                }
            },
        }
        llm.init(models_config, self.workspace, "test_owner", self.sessions)
        limbs_hub.init_extra({})

        self._conn = sqlite3.connect(":memory:")

    def tearDown(self):
        try:
            self._conn.close()
        except Exception:
            pass
        shutil.rmtree(self.test_root)

    @patch("mcp_client.execute")
    @patch("brain.central._call_llm")
    def test_llm_can_create_insert_and_query_via_sqlite_mcp(self, mock_call_llm, mock_mcp_execute):
        def sqlite_mcp_handler(name, args):
            self.assertEqual(name, "sqlite__execute")
            sql = args.get("sql", "")
            cur = self._conn.cursor()
            cur.execute(sql)
            if sql.strip().lower().startswith("select"):
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description or []]
                return json.dumps([dict(zip(cols, row)) for row in rows], ensure_ascii=False)
            self._conn.commit()
            return "ok"

        mock_mcp_execute.side_effect = sqlite_mcp_handler

        mock_call_llm.side_effect = [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "tc_1",
                            "type": "function",
                            "function": {
                                "name": "sqlite__execute",
                                "arguments": json.dumps({"sql": "CREATE TABLE items(id INTEGER, name TEXT)"}),
                            },
                        }],
                    }
                }]
            },
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "tc_2",
                            "type": "function",
                            "function": {
                                "name": "sqlite__execute",
                                "arguments": json.dumps({"sql": "INSERT INTO items(id, name) VALUES (1, 'alpha')"}),
                            },
                        }],
                    }
                }]
            },
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "tc_3",
                            "type": "function",
                            "function": {
                                "name": "sqlite__execute",
                                "arguments": json.dumps({"sql": "SELECT id, name FROM items"}),
                            },
                        }],
                    }
                }]
            },
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "SQL flow completed successfully.",
                    }
                }]
            },
        ]

        reply = llm.chat("请通过 sqlite mcp 完成建表、写入和查询", "session_sqlite_mcp")
        self.assertIn("completed", reply.lower())
        self.assertEqual(mock_mcp_execute.call_count, 3)

        third_call_messages = mock_call_llm.call_args_list[3][0][0]
        tool_messages = [m for m in third_call_messages if m.get("role") == "tool"]
        self.assertTrue(any("alpha" in (m.get("content") or "") for m in tool_messages))


if __name__ == "__main__":
    unittest.main()
