import unittest
import os
import sys
import json
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limbs.skills import media

class TestSkillsMedia(unittest.TestCase):

    def setUp(self):
        self.ctx = {"workspace": "/tmp/workspace"}
        # Mock the extra config in hub
        self.patcher = patch('limbs.hub._extra_config', {
            "video_api": {
                "api_key": "test_key",
                "api_base": "https://fake-video.com/v1",
                "model": "v-model"
            }
        })
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    @patch('urllib.request.urlopen')
    def test_generate_video_success(self, mock_urlopen):
        """Test video generation task submission."""
        # Mock successful JSON response
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"id": "task_123"}).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        args = {"prompt": "A cat flying in space", "size": "1024x1024"}
        res = media.tool_generate_video(args, self.ctx)
        
        self.assertIn("task_123", res)
        self.assertIn("submitted", res)

    def test_generate_video_no_config(self):
        """Test behavior when API is not configured."""
        with patch('limbs.hub._extra_config', {}):
            res = media.tool_generate_video({"prompt": "test"}, self.ctx)
            self.assertIn("[error]", res)
            self.assertIn("not configured", res)

if __name__ == '__main__':
    unittest.main()
