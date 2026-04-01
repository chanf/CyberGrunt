import unittest
import json
import os
import sys
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import central as llm

class TestAzureLLM(unittest.TestCase):

    def setUp(self):
        self.azure_config = {
            "default": "azure_dev",
            "providers": {
                "azure_dev": {
                    "type": "azure",
                    "api_key": "test-key-123",
                    "api_base": "https://my-resource.openai.azure.com/",
                    "deployment_name": "my-gpt-4",
                    "api_version": "2024-02-15-preview"
                }
            }
        }
        llm.init(self.azure_config, "/tmp", "admin", "/tmp/sessions")

    @patch('urllib.request.urlopen')
    def test_azure_request_format(self, mock_urlopen):
        """Verify Azure request URL and Headers are correctly constructed."""
        # Mock successful response
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Azure response"}}]
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        messages = [{"role": "user", "content": "hello"}]
        tool_defs = []
        
        llm._call_llm(messages, tool_defs)
        
        # Capture the request object sent to urlopen
        args, kwargs = mock_urlopen.call_args
        req = args[0]
        
        # 1. Check URL
        expected_url = "https://my-resource.openai.azure.com/openai/deployments/my-gpt-4/chat/completions?api-version=2024-02-15-preview"
        self.assertEqual(req.full_url, expected_url)
        
        # 2. Check Headers
        self.assertEqual(req.get_header("Api-key"), "test-key-123")
        self.assertIsNone(req.get_header("Authorization")) # Should NOT have Bearer token
        
        # 3. Check Body (should NOT have 'model' field for Azure)
        body = json.loads(req.data.decode('utf-8'))
        self.assertNotIn("model", body)
        self.assertEqual(body["messages"], messages)
        self.assertEqual(body["max_tokens"], 4000)  # default safety cap
        self.assertNotIn("tools", body)  # empty tool_defs should not be sent

    @patch('urllib.request.urlopen')
    def test_tools_field_included_when_non_empty(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}}]
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        llm._call_llm(
            [{"role": "user", "content": "hello"}],
            [{
                "type": "function",
                "function": {
                    "name": "dummy_tool",
                    "description": "dummy",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        )
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertIn("tools", body)

if __name__ == '__main__':
    unittest.main()
