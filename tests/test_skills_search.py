import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limbs.skills import search


class TestSkillsSearch(unittest.TestCase):
    def setUp(self):
        self.ctx = {"workspace": "/tmp/workspace", "session_key": "test_sid"}
        self.patcher = patch("limbs.hub._extra_config", {"tavily_api_key": "tavily_test_key"})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    @patch("urllib.request.urlopen")
    def test_web_search_tavily_success(self, mock_urlopen):
        """Web search should render answer + result citations."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {
                "answer": "A short synthesized answer.",
                "results": [
                    {
                        "title": "Result 1",
                        "content": "Information about thing.",
                        "url": "http://example.com/1",
                    },
                    {
                        "title": "Result 2",
                        "content": "More data.",
                        "url": "http://example.com/2",
                    },
                ],
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = search.tool_web_search({"query": "What is 7/24 Office?"}, self.ctx)
        self.assertIn("AI Summary: A short synthesized answer.", res)
        self.assertIn("Result 1", res)
        self.assertIn("URL: http://example.com/1", res)
        self.assertIn("Snippet: More data.", res)

    def test_web_search_no_api_key(self):
        """Web search should fail fast without Tavily API key."""
        with patch("limbs.hub._extra_config", {}):
            res = search.tool_web_search({"query": "test"}, self.ctx)
        self.assertIn("[error]", res)
        self.assertIn("not configured", res.lower())

    @patch("urllib.request.urlopen", side_effect=RuntimeError("network down"))
    def test_web_search_upstream_error(self, _mock_urlopen):
        """Web search should return formatted error on provider failure."""
        res = search.tool_web_search({"query": "latest news"}, self.ctx)
        self.assertIn("[error] search failed", res)
        self.assertIn("network down", res)

    def test_web_search_empty_query(self):
        """Web search should reject empty query inputs."""
        self.assertIn("[error] query is required", search.tool_web_search({"query": ""}, self.ctx))
        self.assertIn("[error] query is required", search.tool_web_search({}, self.ctx))

    @patch("limbs.skills.search._call_tavily")
    def test_web_search_empty_results(self, mock_call_tavily):
        """Web search should return fallback text when no rows exist."""
        mock_call_tavily.return_value = {"answer": "", "results": []}
        res = search.tool_web_search({"query": "no data"}, self.ctx)
        self.assertIn("No relevant search results found.", res)

    @patch("limbs.skills.search._call_tavily")
    def test_web_search_invalid_payload(self, mock_call_tavily):
        """Web search should reject malformed provider payloads."""
        mock_call_tavily.return_value = ["not", "a", "dict"]
        res = search.tool_web_search({"query": "invalid payload"}, self.ctx)
        self.assertIn("invalid response payload", res)


if __name__ == "__main__":
    unittest.main()
