import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request

from ai_forum.forum_runtime import ForumRuntime
from ai_forum.forum_server import create_server
from ai_forum.forum_store import ForumStore


class _DummyLLM:
    def generate_post(self, open_thread_count):
        return {"title": "dummy", "body": "dummy"}

    def generate_reply(self, thread):
        return "dummy"


class TestForumHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = ForumStore(os.path.join(cls.tmpdir, "forum.db"))
        cls.runtime = ForumRuntime(
            store=cls.store,
            llm_client=_DummyLLM(),
            settings={
                "poster_interval_sec": 120,
                "review_interval_sec": 30,
                "max_open_threads": 20,
            },
        )

        cls.t1 = cls.store.create_thread("帖子A", "内容A")
        cls.store.create_reply(thread_id=cls.t1["id"], body="回帖A", author="reviewer_ai")
        cls.t2 = cls.store.create_thread("帖子B", "内容B")

        cls.server = create_server(cls.runtime, host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.runtime.stop_workers()
        cls.store.close()
        shutil.rmtree(cls.tmpdir)

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_list_threads_endpoint(self):
        with urllib.request.urlopen(self._url("/api/threads?status=all&limit=10"), timeout=3) as resp:
            payload = json.loads(resp.read())
        self.assertIn("threads", payload)
        self.assertGreaterEqual(len(payload["threads"]), 2)

    def test_single_thread_endpoint(self):
        with urllib.request.urlopen(self._url(f"/api/threads/{self.t1['id']}"), timeout=3) as resp:
            payload = json.loads(resp.read())
        self.assertIn("thread", payload)
        self.assertEqual(payload["thread"]["id"], self.t1["id"])
        self.assertEqual(len(payload["thread"]["replies"]), 1)

    def test_sse_event_format(self):
        with urllib.request.urlopen(self._url("/api/events"), timeout=3) as stream:
            line1 = stream.readline().decode("utf-8").strip()
            line2 = stream.readline().decode("utf-8").strip()
            self.assertEqual(line1, "event: connected")
            self.assertTrue(line2.startswith("data: "))

            self.runtime.event_bus.publish("thread_created", {"thread": {"id": 999, "title": "x"}})

            event_line = ""
            data_line = ""
            for _ in range(8):
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


if __name__ == "__main__":
    unittest.main()
