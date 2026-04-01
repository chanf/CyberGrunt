import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from ai_forum.forum_runtime import ForumRuntime
from ai_forum.forum_store import ForumStore


class TestForumWorkers(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = ForumStore(os.path.join(self.tmpdir, "forum.db"))
        self.llm = MagicMock()
        self.runtime = ForumRuntime(
            store=self.store,
            llm_client=self.llm,
            settings={
                "poster_interval_sec": 120,
                "review_interval_sec": 30,
                "max_open_threads": 2,
            },
        )

    def tearDown(self):
        self.runtime.stop_workers()
        self.store.close()
        shutil.rmtree(self.tmpdir)

    def test_poster_respects_max_open_threads(self):
        self.store.create_thread("现有帖子", "已有待回复帖子")
        self.store.create_thread("现有帖子2", "已有待回复帖子")
        self.runtime.settings["max_open_threads"] = 2

        self.runtime.poster_tick()

        self.llm.generate_post.assert_not_called()
        self.assertEqual(self.store.count_open_threads(), 2)

    def test_reviewer_replies_oldest_open_thread(self):
        t1 = self.store.create_thread("先发", "先发内容")
        t2 = self.store.create_thread("后发", "后发内容")
        self.llm.generate_reply.return_value = "已检查，建议增加边界测试。"

        sub = self.runtime.event_bus.subscribe()
        self.runtime.reviewer_tick()

        updated1 = self.store.get_thread(t1["id"])
        updated2 = self.store.get_thread(t2["id"])
        self.assertEqual(updated1["status"], "replied")
        self.assertEqual(updated2["status"], "open")
        self.assertEqual(len(updated1["replies"]), 1)

        packet = sub.get(timeout=1)
        self.assertEqual(packet["type"], "thread_replied")
        self.runtime.event_bus.unsubscribe(sub)

    def test_reviewer_llm_failure_keeps_thread_open_for_retry(self):
        t1 = self.store.create_thread("需要检查", "等待回帖")
        self.llm.generate_reply.side_effect = RuntimeError("temporary failure")

        self.runtime.reviewer_tick()

        after_fail = self.store.get_thread(t1["id"])
        self.assertEqual(after_fail["status"], "open")
        self.assertEqual(len(after_fail["replies"]), 0)

        self.llm.generate_reply.side_effect = None
        self.llm.generate_reply.return_value = "重试成功，建议执行回归脚本。"
        self.runtime.reviewer_tick()

        after_retry = self.store.get_thread(t1["id"])
        self.assertEqual(after_retry["status"], "replied")
        self.assertEqual(len(after_retry["replies"]), 1)


if __name__ == "__main__":
    unittest.main()
