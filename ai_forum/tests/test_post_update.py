import argparse
import io
import json
import unittest
from unittest.mock import patch

from ai_forum import post_update as mod


class TestPostUpdate(unittest.TestCase):
    def test_run_test_commands_success(self) -> None:
        evidence, failed = mod._run_test_commands(
            ["./venv/bin/python -c 'print(\"ok\")'"],
            timeout_sec=10.0,
            max_output_lines=3,
        )
        self.assertEqual(len(evidence), 1)
        self.assertIn("PASS", evidence[0])
        self.assertEqual(failed, [])

    def test_run_test_commands_failure(self) -> None:
        evidence, failed = mod._run_test_commands(
            ["./venv/bin/python -c 'import sys; print(\"boom\"); sys.exit(3)'"],
            timeout_sec=10.0,
            max_output_lines=2,
        )
        self.assertEqual(len(evidence), 1)
        self.assertIn("FAIL", evidence[0])
        self.assertIn("code 3", evidence[0])
        self.assertEqual(failed, evidence)

    def test_main_blocks_when_no_test_evidence(self) -> None:
        args = argparse.Namespace(
            thread_id=1,
            summary="done",
            author="developer_ai",
            api_base="http://localhost:8090",
            test=[],
            run_test_cmd=[],
            test_timeout_sec=10.0,
            max_test_output_lines=3,
            allow_no_tests=False,
            changed_file=[],
            note="note",
            details="",
            resolve=False,
        )
        with patch.object(mod, "_parse_args", return_value=args):
            with patch.object(mod, "_post_json") as mock_post:
                with patch("sys.stdout", new_callable=io.StringIO) as out:
                    code = mod.main()
        self.assertEqual(code, 2)
        self.assertEqual(mock_post.call_count, 0)
        payload = json.loads(out.getvalue().strip())
        self.assertFalse(payload["ok"])
        self.assertIn("no test evidence", payload["error"])

    def test_main_posts_when_tests_present(self) -> None:
        args = argparse.Namespace(
            thread_id=2,
            summary="done",
            author="developer_ai",
            api_base="http://localhost:8090",
            test=["manual pass"],
            run_test_cmd=[],
            test_timeout_sec=10.0,
            max_test_output_lines=3,
            allow_no_tests=False,
            changed_file=["a.py"],
            note="note",
            details="",
            resolve=False,
        )
        fake_reply = {"reply": {"id": 88}}
        with patch.object(mod, "_parse_args", return_value=args):
            with patch.object(mod, "_post_json", return_value=fake_reply) as mock_post:
                with patch("sys.stdout", new_callable=io.StringIO) as out:
                    code = mod.main()
        self.assertEqual(code, 0)
        self.assertEqual(mock_post.call_count, 1)
        payload = json.loads(out.getvalue().strip())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reply_id"], 88)


if __name__ == "__main__":
    unittest.main()
