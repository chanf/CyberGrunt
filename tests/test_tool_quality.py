import os
import shutil
import tempfile
import unittest

from brain import tool_quality as tq


class TestToolQuality(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.workspace = os.path.join(self.tmpdir, "workspace")
        os.makedirs(self.workspace, exist_ok=True)
        tq.close()
        tq.init(self.workspace)

    def tearDown(self):
        tq.close()
        shutil.rmtree(self.tmpdir)

    def test_marks_experimental_when_success_rate_low(self):
        for _ in range(5):
            tq.record_call("unstable_tool", ok=False, blocked=False, error="boom")
        status = tq.get_tool_status("unstable_tool")
        self.assertEqual(status["calls"], 5)
        self.assertAlmostEqual(status["success_rate"], 0.0)
        self.assertTrue(status["experimental"])

    def test_recovers_from_experimental_when_success_rate_improves(self):
        for _ in range(5):
            tq.record_call("flaky_tool", ok=False, blocked=False, error="fail")
        for _ in range(5):
            tq.record_call("flaky_tool", ok=True, blocked=False)

        status = tq.get_tool_status("flaky_tool")
        self.assertEqual(status["calls"], 10)
        self.assertAlmostEqual(status["success_rate"], 0.5)
        self.assertTrue(status["experimental"])

        for _ in range(5):
            tq.record_call("flaky_tool", ok=True, blocked=False)
        status = tq.get_tool_status("flaky_tool")
        self.assertAlmostEqual(status["success_rate"], 10 / 15, places=3)
        self.assertFalse(status["experimental"])

    def test_list_tools_contains_report_data(self):
        tq.record_call("a_tool", ok=True)
        tq.record_call("b_tool", ok=False, blocked=True, error="denied")
        rows = tq.list_tools(limit=10)
        names = {row["tool_name"] for row in rows}
        self.assertIn("a_tool", names)
        self.assertIn("b_tool", names)


if __name__ == "__main__":
    unittest.main()
