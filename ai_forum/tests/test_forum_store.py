import os
import shutil
import tempfile
import unittest

from ai_forum.forum_store import ForumStore


class TestForumStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "forum.db")
        self.store = ForumStore(self.db_path)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmpdir)

    def test_create_thread_and_reviewer_reply_status_transition(self):
        thread = self.store.create_thread("开发进展 A", "正在重构模块 A")
        self.assertEqual(thread["status"], "open")
        self.assertEqual(self.store.count_open_threads(), 1)

        reply = self.store.create_reply(thread_id=thread["id"], body="请补充回归测试", author="reviewer_ai")
        self.assertEqual(reply["thread_id"], thread["id"])

        updated = self.store.get_thread(thread["id"])
        self.assertEqual(updated["status"], "replied")
        self.assertEqual(len(updated["replies"]), 1)
        self.assertEqual(updated["replies"][0]["author"], "reviewer_ai")
        self.assertEqual(self.store.count_open_threads(), 0)

    def test_get_oldest_open_thread(self):
        t1 = self.store.create_thread("T1", "B1")
        t2 = self.store.create_thread("T2", "B2")

        oldest = self.store.get_oldest_open_thread()
        self.assertIsNotNone(oldest)
        self.assertEqual(oldest["id"], t1["id"])

        self.store.create_reply(thread_id=t1["id"], body="done", author="reviewer_ai")
        oldest = self.store.get_oldest_open_thread()
        self.assertIsNotNone(oldest)
        self.assertEqual(oldest["id"], t2["id"])


if __name__ == "__main__":
    unittest.main()
