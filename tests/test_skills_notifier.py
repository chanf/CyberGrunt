import unittest
import os
import sys
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limbs.skills import notifier

class TestSkillsNotifier(unittest.TestCase):

    def setUp(self):
        self.ctx = {
            "owner_id": "user123",
            "workspace": "/tmp/workspace"
        }

    @patch('scheduler.add')
    def test_schedule_task(self, mock_add):
        """Test scheduling a task via the limb."""
        mock_add.return_value = "Task created"
        args = {
            "name": "daily_report",
            "message": "Run self_check",
            "cron_expr": "0 9 * * *"
        }
        res = notifier.tool_schedule(args, self.ctx)
        
        mock_add.assert_called_once_with(args)
        self.assertEqual(res, "Task created")

    @patch('scheduler.list_all')
    def test_list_schedules(self, mock_list):
        """Test listing schedules."""
        mock_list.return_value = "1. daily_report"
        res = notifier.tool_list_schedules({}, self.ctx)
        
        mock_list.assert_called_once()
        self.assertEqual(res, "1. daily_report")

    @patch('scheduler.remove')
    def test_remove_schedule(self, mock_remove):
        """Test removing a schedule."""
        mock_remove.return_value = "Deleted"
        res = notifier.tool_remove_schedule({"name": "test"}, self.ctx)
        
        mock_remove.assert_called_once_with("test")
        self.assertEqual(res, "Deleted")

    @patch('messaging.upload_and_send')
    def test_send_image_success(self, mock_send):
        """Test successful image sending."""
        mock_send.return_value = {"code": 0, "msg": "ok"}
        args = {"path": "http://example.com/a.jpg", "caption": "Hello"}
        res = notifier.tool_send_image(args, self.ctx)
        
        mock_send.assert_called_once_with(
            self.ctx["owner_id"], 
            args["path"], 
            args["caption"], 
            self.ctx["workspace"]
        )
        self.assertEqual(res, "Image sent")

    @patch('messaging.upload_and_send')
    def test_send_image_failure(self, mock_send):
        """Test image sending failure."""
        mock_send.return_value = {"code": -1, "msg": "network error"}
        res = notifier.tool_send_image({"path": "err.jpg"}, self.ctx)
        self.assertIn("[error]", res)
        self.assertIn("network error", res)

    @patch('messaging.send_link')
    def test_send_link(self, mock_link):
        """Test rich link sending."""
        mock_link.return_value = {"code": 0}
        args = {
            "title": "Google",
            "desc": "Search engine",
            "link_url": "https://google.com"
        }
        res = notifier.tool_send_link(args, self.ctx)
        
        mock_link.assert_called_once_with(
            self.ctx["owner_id"],
            args["title"],
            args["desc"],
            args["link_url"],
            "" # icon_url default
        )
        self.assertEqual(res, "Link sent")

if __name__ == '__main__':
    unittest.main()
