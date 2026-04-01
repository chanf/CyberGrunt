import os
import shutil
import tempfile
import unittest

from ai_forum.forum_store import ForumStore


class TestForumStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = ForumStore(os.path.join(self.tmpdir, "forum.db"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmpdir)

    def test_thread_status_pending_and_resolved(self):
        thread = self.store.create_thread(
            title="开发任务A",
            body="实现接口A并补测试",
            author="developer_ai",
        )
        self.assertEqual(thread["status"], "pending")

        updated = self.store.set_thread_status(
            thread_id=thread["id"],
            status="resolved",
            updated_by="developer_ai",
        )
        self.assertEqual(updated["status"], "resolved")
        self.assertEqual(updated["updated_by"], "developer_ai")

    def test_actionable_rule_by_last_actor(self):
        thread = self.store.create_thread(
            title="需要产品确认验收口径",
            body="请确认这次验收是否包含弱网场景",
            author="developer_ai",
        )

        # 新帖由 developer_ai 发起，reviewer_ai 需要回复，developer_ai 不需要立即自回
        dev_actionable = self.store.list_actionable_threads(author="developer_ai")
        rev_actionable = self.store.list_actionable_threads(author="reviewer_ai")
        self.assertEqual(len(dev_actionable), 0)
        self.assertEqual(len(rev_actionable), 1)
        self.assertEqual(rev_actionable[0]["id"], thread["id"])

        self.store.create_reply(
            thread_id=thread["id"],
            author="reviewer_ai",
            body="验收要包含弱网和超时重试场景。",
        )

        # reviewer 回帖后，developer 需要响应
        dev_actionable = self.store.list_actionable_threads(author="developer_ai")
        rev_actionable = self.store.list_actionable_threads(author="reviewer_ai")
        self.assertEqual(len(dev_actionable), 1)
        self.assertEqual(dev_actionable[0]["id"], thread["id"])
        self.assertEqual(len(rev_actionable), 0)

        self.store.set_thread_status(
            thread_id=thread["id"],
            status="resolved",
            updated_by="developer_ai",
        )

        # 已解决后，双方都不再需要回复
        self.assertEqual(len(self.store.list_actionable_threads(author="developer_ai")), 0)
        self.assertEqual(len(self.store.list_actionable_threads(author="reviewer_ai")), 0)


if __name__ == "__main__":
    unittest.main()
