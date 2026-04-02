import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, mock_open, patch

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

    def test_qa_sniffer_file_not_found(self):
        """Test check_code_complexity handles missing file."""
        res = qa_sniffer.tool_check_code_complexity({"file_path": "/not_exists.py"}, self.ctx)
        self.assertIn("[error] file not found", res)

    def test_qa_sniffer_parse_error(self):
        """Test check_code_complexity handles syntax errors."""
        bad_syntax = "def broken(:\n    pass"
        fpath = os.path.join(self.test_root, "broken.py")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(bad_syntax)
        res = qa_sniffer.tool_check_code_complexity({"file_path": fpath}, self.ctx)
        self.assertIn("[error] parse failed", res)

    @patch("builtins.open", new_callable=mock_open, read_data="Traceback (most recent call last):\nboom")
    @patch("limbs.skills.qa_sniffer.os.listdir", return_value=["agent.log"])
    @patch("limbs.skills.qa_sniffer.os.path.isdir", return_value=True)
    def test_log_anomaly_detector_finds_traceback(self, _mock_isdir, _mock_listdir, _mock_open):
        """Test log_anomaly_detector reports traceback patterns."""
        res = qa_sniffer.tool_log_anomaly_detector({}, self.ctx)
        self.assertIn("Found hidden traceback in agent.log", res)

    @patch("limbs.skills.qa_sniffer.os.path.isdir", return_value=False)
    def test_log_anomaly_detector_missing_dir(self, _mock_isdir):
        """Test log_anomaly_detector when log dir is absent."""
        res = qa_sniffer.tool_log_anomaly_detector({}, self.ctx)
        self.assertEqual("No log directory found.", res)

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

    @patch('subprocess.run')
    def test_git_log_invalid_limit_fallback(self, mock_run):
        """Test git_log sanitizes invalid limit value."""
        from limbs.skills import git_helper
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        res = git_helper.git_log({"limit": "bad"}, self.ctx)
        self.assertEqual(res["count"], 0)
        called_cmd = mock_run.call_args[0][0]
        self.assertIn("-10", called_cmd)

    def test_git_add_empty_files_rejected(self):
        """Test git_add rejects empty file list."""
        from limbs.skills import git_helper
        res = git_helper.git_add({"files": " , "}, self.ctx)
        self.assertFalse(res["success"])
        self.assertIn("No valid files specified", res["error"])

    def test_list_test_modules_sorted(self):
        """Test list_test_modules output is sorted."""
        res = test_runner.list_test_modules({}, self.ctx)
        self.assertEqual(res["modules"], sorted(res["modules"]))

if __name__ == '__main__':
    unittest.main()
