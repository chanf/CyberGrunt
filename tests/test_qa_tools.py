import unittest
import os
import sys
import tempfile
import shutil
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limbs.skills import qa_sniffer
from limbs.skills import code_reviewer
from limbs.skills import test_runner

class TestQATools(unittest.TestCase):

    def setUp(self):
        self.test_root = tempfile.mkdtemp()
        self.ctx = {"workspace": self.test_root}

    def tearDown(self):
        shutil.rmtree(self.test_root)

    def test_qa_sniffer_complexity(self):
        """Test check_code_complexity finds issues."""
        bad_code = "def too_long():\n    print('line')"
        fpath = os.path.join(self.test_root, "bad.py")
        with open(fpath, "w") as f: f.write(bad_code)
        
        res = qa_sniffer.tool_check_code_complexity({"file_path": fpath}, self.ctx)
        self.assertIn("Total issues found: 2", res)

    def test_code_reviewer_static_check(self):
        """Test code_reviewer dict structure."""
        code = "def no_doc():\n    pass"
        fpath = os.path.join(self.test_root, "no_doc.py")
        with open(fpath, "w") as f: f.write(code)
        
        res = code_reviewer.check_file_issues({"file_path": fpath}, self.ctx)
        self.assertIsInstance(res, dict)
        self.assertIn("issues", res)

    def test_test_runner_list(self):
        """Test listing test modules."""
        res = test_runner.list_test_modules({}, self.ctx)
        self.assertIsInstance(res, dict)
        self.assertIn("modules", res)

    @patch('subprocess.run')
    def test_git_helper_status(self, mock_run):
        """Test git_status dict structure."""
        from limbs.skills import git_helper
        mock_run.return_value = MagicMock(stdout="M main.py", returncode=0)
        res = git_helper.git_status({}, self.ctx)
        self.assertIsInstance(res, dict)
        self.assertIn("summary", res)

if __name__ == '__main__':
    unittest.main()
