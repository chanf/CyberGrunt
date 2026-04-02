import unittest
import os
import shutil
import tempfile
import sys
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limbs.core import base

class TestCoreLimbs(unittest.TestCase):

    def setUp(self):
        # Create a temporary workspace for file tests
        self.test_dir = tempfile.mkdtemp()
        self.ctx = {"workspace": self.test_dir}

    def tearDown(self):
        # Clean up the temporary workspace
        shutil.rmtree(self.test_dir)

    def test_write_and_read_file(self):
        """Test basic write and read tool functionality."""
        fpath = "test.txt"
        content = "Hello, CyberGrunt!"
        
        # Write
        write_res = base.tool_write_file({"path": fpath, "content": content}, self.ctx)
        self.assertIn("Written to", write_res)
        
        # Verify file exists on disk
        real_path = os.path.join(self.test_dir, fpath)
        self.assertTrue(os.path.exists(real_path))
        
        # Read
        read_res = base.tool_read_file({"path": fpath}, self.ctx)
        self.assertEqual(read_res, content)

    def test_read_file_not_found(self):
        """Test reading a non-existent file."""
        res = base.tool_read_file({"path": "missing.txt"}, self.ctx)
        self.assertIn("[error] file not found", res)

    def test_edit_file(self):
        """Test edit_file (replace old with new)."""
        fpath = "edit_me.txt"
        base.tool_write_file({"path": fpath, "content": "The quick brown fox"}, self.ctx)
        
        # Replace 'brown' with 'red'
        edit_res = base.tool_edit_file({"path": fpath, "old": "brown", "new": "red"}, self.ctx)
        self.assertIn("Edited", edit_res)
        
        # Verify content
        read_res = base.tool_read_file({"path": fpath}, self.ctx)
        self.assertEqual(read_res, "The quick red fox")

    def test_edit_file_not_found(self):
        """Test editing with a missing 'old' string."""
        fpath = "edit_me.txt"
        base.tool_write_file({"path": fpath, "content": "Original content"}, self.ctx)
        
        res = base.tool_edit_file({"path": fpath, "old": "wrong", "new": "new"}, self.ctx)
        self.assertIn("[error] old string not found", res)

    def test_exec_tool(self):
        """Test shell command execution."""
        # Simple echo
        res = base.tool_exec({"command": "echo 'test'"}, self.ctx)
        self.assertEqual(res, "test")
        
        # Test error output
        res = base.tool_exec({"command": "ls /non_existent_path"}, self.ctx)
        self.assertIn("No such file or directory", res)
        self.assertIn("exit code", res)

    def test_exec_timeout(self):
        """Test command timeout handling."""
        # This might take a second, so keep it short
        res = base.tool_exec({"command": "sleep 2", "timeout": 1}, self.ctx)
        self.assertIn("[error] command timed out", res)

    def test_list_files_empty(self):
        """Test list_files when index.json is missing."""
        res = base.tool_list_files({}, self.ctx)
        self.assertEqual(res, "No files received yet.")

    def test_list_files_invalid_limit_fallback(self):
        """Test list_files falls back when limit is not an integer."""
        files_dir = os.path.join(self.test_dir, "files")
        os.makedirs(files_dir, exist_ok=True)
        index_path = os.path.join(files_dir, "index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump([
                {"type": "file", "filename": "a.txt", "size": 128, "time": "2026-04-02", "path": "files/a.txt"},
                {"type": "file", "filename": "b.txt", "size": 256, "time": "2026-04-02", "path": "files/b.txt"},
            ], f)

        res = base.tool_list_files({"limit": "oops"}, self.ctx)
        self.assertIn("showing 2 most recent", res)
        self.assertIn("a.txt", res)
        self.assertIn("b.txt", res)

    def test_path_traversal_security(self):
        """Test that path traversal attempts are blocked."""
        # Try to read outside workspace
        res = base.tool_read_file({"path": "../config.json"}, self.ctx)
        self.assertIn("[error] Access denied", res)
        
        # Try to write outside workspace
        res = base.tool_write_file({"path": "../../hacker.txt", "content": "pwned"}, self.ctx)
        self.assertIn("[error] Access denied", res)
        
        # Try to edit outside workspace
        res = base.tool_edit_file({"path": "/etc/passwd", "old": "root", "new": "hacker"}, self.ctx)
        self.assertIn("[error] Access denied", res)

if __name__ == '__main__':
    unittest.main()
