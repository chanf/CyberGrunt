import os
import sys
import unittest
from unittest.mock import patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limbs.skills import tool_health


class TestSkillsToolHealth(unittest.TestCase):
    def setUp(self):
        self.ctx = {"workspace": "/tmp/workspace", "owner_id": "user123"}

    @patch("limbs.skills.tool_health.hub.get_tool_health_report", return_value="1) read_file 0.99")
    def test_tool_health_report_success(self, mock_get):
        res = tool_health.tool_health_report({}, self.ctx)
        mock_get.assert_called_once()
        self.assertEqual(res, "1) read_file 0.99")

    @patch("limbs.skills.tool_health.hub.get_tool_health_report", side_effect=RuntimeError("hub unavailable"))
    def test_tool_health_report_error(self, _mock_get):
        res = tool_health.tool_health_report({}, self.ctx)
        self.assertIn("[error] failed to get tool health report", res)


if __name__ == "__main__":
    unittest.main()
