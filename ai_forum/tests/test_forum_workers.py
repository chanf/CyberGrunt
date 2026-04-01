import os
import shutil
import tempfile
import unittest

from ai_forum.forum_store import ForumStore


class TestForumWorkflowRules(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = ForumStore(os.path.join(self.tmpdir, "forum.db"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmpdir)

    def test_pending_thread_needs_cross_reply(self):
        t = self.store.create_thread(
            title="产品设计提案",
            body="请评估论坛 API 命名一致性",
            author="reviewer_ai",
        )

        # reviewer 发起 -> developer 需要回复
        actionable_dev = self.store.list_actionable_threads("developer_ai")
        actionable_rev = self.store.list_actionable_threads("reviewer_ai")
        self.assertEqual([x["id"] for x in actionable_dev], [t["id"]])
        self.assertEqual(actionable_rev, [])

        # developer 回复后 -> reviewer 需要回复
        self.store.create_reply(t["id"], "我建议统一 /api/threads 路径。", "developer_ai")
        actionable_dev = self.store.list_actionable_threads("developer_ai")
        actionable_rev = self.store.list_actionable_threads("reviewer_ai")
        self.assertEqual(actionable_dev, [])
        self.assertEqual([x["id"] for x in actionable_rev], [t["id"]])

    def test_resolved_thread_exits_actionable_queue(self):
        t = self.store.create_thread(
            title="验收结论确认",
            body="请确认是否通过本轮验收",
            author="developer_ai",
        )
        self.store.create_reply(t["id"], "还需补充 SSE 测试。", "reviewer_ai")
        self.store.set_thread_status(t["id"], "resolved", "reviewer_ai")

        self.assertEqual(self.store.list_actionable_threads("developer_ai"), [])
        self.assertEqual(self.store.list_actionable_threads("reviewer_ai"), [])


if __name__ == "__main__":
    unittest.main()
