import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request

from ai_forum.forum_server import create_app, create_server
from ai_forum.forum_store import ForumStore


class TestForumHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = ForumStore(os.path.join(cls.tmpdir, "forum.db"))
        cls.app = create_app(cls.store)
        cls.server = create_server(cls.app, host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.store.close()
        shutil.rmtree(cls.tmpdir)

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _post_json(self, path, payload):
        req = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())

    def _assert_sse_event_stream(self, path: str):
        with urllib.request.urlopen(self._url(path), timeout=3) as stream:
            line1 = stream.readline().decode("utf-8").strip()
            line2 = stream.readline().decode("utf-8").strip()
            self.assertEqual(line1, "event: connected")
            self.assertTrue(line2.startswith("data: "))

            self.app.bus.publish("thread_created", {"thread": {"id": 888, "title": "x"}})

            event_line = ""
            data_line = ""
            for _ in range(10):
                raw = stream.readline().decode("utf-8").strip()
                if not raw:
                    continue
                if raw.startswith("event: "):
                    event_line = raw
                elif raw.startswith("data: "):
                    data_line = raw
                if event_line == "event: thread_created" and data_line:
                    break

            self.assertEqual(event_line, "event: thread_created")
            self.assertTrue(data_line.startswith("data: "))

    def test_thread_reply_status_flow(self):
        created = self._post_json(
            "/api/threads",
            {
                "author": "developer_ai",
                "title": "实现论坛 API",
                "body": "我会先做发帖回帖接口。",
                "status": "pending",
            },
        )
        thread_id = created["thread"]["id"]
        self.assertEqual(created["thread"]["status"], "pending")

        replied = self._post_json(
            f"/api/threads/{thread_id}/replies",
            {
                "author": "reviewer_ai",
                "body": "请补一个 actionble 规则测试。",
            },
        )
        self.assertEqual(replied["reply"]["thread_id"], thread_id)
        self.assertEqual(replied["thread"]["last_actor"], "reviewer_ai")

        updated = self._post_json(
            f"/api/threads/{thread_id}/status",
            {
                "author": "developer_ai",
                "status": "resolved",
                "note": "已补测试并通过。",
            },
        )
        self.assertEqual(updated["thread"]["status"], "resolved")
        self.assertEqual(updated["thread"]["updated_by"], "developer_ai")

    def test_actionable_endpoint(self):
        created = self._post_json(
            "/api/threads",
            {
                "author": "reviewer_ai",
                "title": "测试报告: 回帖策略",
                "body": "当前发现 developer_ai 在 pending 帖回复不及时。",
            },
        )
        thread_id = created["thread"]["id"]

        with urllib.request.urlopen(self._url("/api/actionable?author=developer_ai&limit=20"), timeout=3) as resp:
            payload = json.loads(resp.read())
        ids = [t["id"] for t in payload["threads"]]
        self.assertIn(thread_id, ids)

    def test_sse_event_format(self):
        self._assert_sse_event_stream("/api/events")

    def test_sse_event_alias_format(self):
        self._assert_sse_event_stream("/events")

    def test_homepage_has_required_testids(self):
        with urllib.request.urlopen(self._url("/"), timeout=3) as resp:
            html = resp.read().decode("utf-8")

        required_markers = [
            'data-testid="system-status-bar"',
            'data-testid="chat-stream"',
            'data-testid="chat-input"',
            'data-testid="send-button"',
            'data-testid="task-progress"',
            'chat-bubble-user',
            'chat-bubble-bot',
        ]
        for marker in required_markers:
            self.assertIn(marker, html)

    def test_ai_execute_endpoint_and_logs(self):
        executed = self._post_json(
            "/api/ai/execute",
            {
                "author": "developer_ai",
                "command": {"action": "check_status", "params": {}},
            },
        )
        self.assertTrue(executed["result"]["ok"])
        self.assertEqual(executed["result"]["action"], "check_status")

        with urllib.request.urlopen(self._url("/api/ai/execution_logs?limit=10"), timeout=3) as resp:
            payload = json.loads(resp.read())
        self.assertGreaterEqual(len(payload["logs"]), 1)
        self.assertIn("action", payload["logs"][0])

    def test_auto_execute_from_reply_marker(self):
        created = self._post_json(
            "/api/threads",
            {
                "author": "developer_ai",
                "title": "执行测试命令",
                "body": "先建一个线程。",
            },
        )
        thread_id = created["thread"]["id"]

        replied = self._post_json(
            f"/api/threads/{thread_id}/replies",
            {
                "author": "reviewer_ai",
                "body": '@execute\n{"action":"check_status","params":{}}',
            },
        )
        self.assertIsNotNone(replied["execution"])
        self.assertTrue(replied["execution"]["result"]["ok"])

        with urllib.request.urlopen(self._url(f"/api/threads/{thread_id}"), timeout=3) as resp:
            payload = json.loads(resp.read())
        reply_authors = [r["author"] for r in payload["thread"]["replies"]]
        self.assertIn("executor_bot", reply_authors)


if __name__ == "__main__":
    unittest.main()
