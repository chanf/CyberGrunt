import unittest
import os
import sys
from unittest.mock import MagicMock, patch, ANY

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limbs.skills import memory

class TestSkillsMemory(unittest.TestCase):

    def setUp(self):
        self.ctx = {
            "workspace": "/tmp/workspace",
            "session_key": "web_test_user"
        }

    @patch('subprocess.run')
    @patch('os.path.isdir')
    def test_search_memory_all(self, mock_isdir, mock_run):
        """Test general keyword search."""
        mock_isdir.return_value = True
        mock_run.return_value = MagicMock(stdout="file1.md:10:match line", stderr="")
        
        args = {"query": "test keyword", "scope": "all"}
        res = memory.tool_search_memory(args, self.ctx)
        
        # Verify grep arguments
        call_args = mock_run.call_args[0][0]
        self.assertIn("grep", call_args)
        self.assertIn("-r", call_args)
        self.assertIn("test keyword", call_args)
        self.assertIn(os.path.join(self.ctx["workspace"], "memory"), call_args)
        self.assertIn("1 matches", res)

    @patch('subprocess.run')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    def test_search_memory_long(self, mock_exists, mock_isdir, mock_run):
        """Test search specifically in MEMORY.md."""
        mock_isdir.return_value = True
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(stdout="MEMORY.md:5:important fact", stderr="")
        
        args = {"query": "fact", "scope": "long"}
        res = memory.tool_search_memory(args, self.ctx)
        
        call_args = mock_run.call_args[0][0]
        self.assertNotIn("-r", call_args) # Should not be recursive
        self.assertTrue(any("MEMORY.md" in a for a in call_args))
        self.assertIn("1 matches", res)

    @patch('subprocess.run')
    @patch('os.path.isdir')
    def test_search_memory_empty(self, mock_isdir, mock_run):
        """Test search with no matches."""
        mock_isdir.return_value = True
        mock_run.return_value = MagicMock(stdout="", stderr="")
        
        res = memory.tool_search_memory({"query": "ghost"}, self.ctx)
        self.assertIn("No memories found", res)

    @patch('brain.memory.retrieve')
    def test_recall_semantic(self, mock_retrieve):
        """Test semantic recall."""
        mock_retrieve.return_value = "Historical fact: AI is cool."
        
        args = {"query": "history of AI"}
        res = memory.tool_recall(args, self.ctx)
        
        mock_retrieve.assert_called_once_with(
            "history of AI", 
            self.ctx["session_key"], 
            top_k=ANY,
            scope="auto"
        )
        self.assertEqual(res, "Historical fact: AI is cool.")

    @patch('brain.memory.retrieve')
    def test_recall_no_results(self, mock_retrieve):
        """Test recall when nothing is found."""
        mock_retrieve.return_value = None
        res = memory.tool_recall({"query": "something new"}, self.ctx)
        self.assertIn("No relevant memories found", res)

    @patch('brain.memory.retrieve')
    def test_recall_scope_override(self, mock_retrieve):
        """Test recall scope override."""
        mock_retrieve.return_value = "public memory"
        res = memory.tool_recall({"query": "api docs", "scope": "public"}, self.ctx)
        mock_retrieve.assert_called_once_with(
            "api docs",
            self.ctx["session_key"],
            top_k=ANY,
            scope="public"
        )
        self.assertEqual(res, "public memory")

if __name__ == '__main__':
    unittest.main()
