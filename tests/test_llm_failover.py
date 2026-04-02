import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import central as llm


class TestLLMFailover(unittest.TestCase):
    def setUp(self):
        self.test_root = tempfile.mkdtemp()
        self.workspace = os.path.join(self.test_root, "workspace")
        self.sessions = os.path.join(self.test_root, "sessions")
        os.makedirs(self.workspace)
        os.makedirs(self.sessions)

    def tearDown(self):
        shutil.rmtree(self.test_root)

    @patch("brain.central.time.sleep")
    @patch("urllib.request.urlopen")
    def test_failover_to_backup_provider_after_primary_http_400(self, mock_urlopen, mock_sleep):
        models_config = {
            "default": "primary",
            "providers": {
                "primary": {
                    "api_base": "https://primary.example/v1",
                    "api_key": "primary-key",
                    "model": "gpt-primary",
                    "max_attempts": 1,
                },
                "backup": {
                    "api_base": "https://backup.example/v1",
                    "api_key": "backup-key",
                    "model": "gpt-backup",
                    "max_attempts": 1,
                },
            },
            "failover": ["backup"],
        }
        llm.init(models_config, self.workspace, "owner", self.sessions)

        call_urls = []

        def side_effect(req, timeout=120):
            call_urls.append(req.full_url)
            if len(call_urls) == 1:
                raise urllib.error.HTTPError(
                    url=req.full_url,
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(b'{"error":"primary bad request"}'),
                )

            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(
                {"choices": [{"message": {"content": "backup-ok"}}]}
            ).encode("utf-8")
            ctx = MagicMock()
            ctx.__enter__.return_value = mock_resp
            ctx.__exit__.return_value = False
            return ctx

        mock_urlopen.side_effect = side_effect

        response = llm._call_llm([{"role": "user", "content": "hello"}], [])

        self.assertEqual(response["choices"][0]["message"]["content"], "backup-ok")
        self.assertEqual(len(call_urls), 2)
        self.assertIn("primary.example", call_urls[0])
        self.assertIn("backup.example", call_urls[1])
        mock_sleep.assert_not_called()

    @patch("brain.central.time.sleep")
    @patch("urllib.request.urlopen")
    def test_retry_with_backoff_on_network_error(self, mock_urlopen, mock_sleep):
        models_config = {
            "default": "primary",
            "providers": {
                "primary": {
                    "api_base": "https://primary.example/v1",
                    "api_key": "primary-key",
                    "model": "gpt-primary",
                    "max_attempts": 3,
                    "retry": {
                        "base_delay_sec": 0.5,
                        "max_delay_sec": 1.0,
                        "jitter_sec": 0.0,
                    },
                }
            },
        }
        llm.init(models_config, self.workspace, "owner", self.sessions)

        def side_effect(req, timeout=120):
            if mock_urlopen.call_count == 1:
                raise urllib.error.URLError("temporary network issue")
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(
                {"choices": [{"message": {"content": "retry-ok"}}]}
            ).encode("utf-8")
            ctx = MagicMock()
            ctx.__enter__.return_value = mock_resp
            ctx.__exit__.return_value = False
            return ctx

        mock_urlopen.side_effect = side_effect

        response = llm._call_llm([{"role": "user", "content": "hello"}], [])

        self.assertEqual(response["choices"][0]["message"]["content"], "retry-ok")
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once()
        self.assertAlmostEqual(mock_sleep.call_args[0][0], 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
