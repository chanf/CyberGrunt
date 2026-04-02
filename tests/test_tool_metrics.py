import json
import os
import shutil
import tempfile
import time
import unittest

import limbs.hub as hub
from limbs.hub import limb


class TestToolMetrics(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.workspace = os.path.join(self.tmpdir, "workspace")
        os.makedirs(os.path.join(self.workspace, "files"), exist_ok=True)
        self.ctx = {"workspace": self.workspace, "owner_id": "admin"}

        self._old_level = hub.log.level
        hub.log.setLevel(100)

        hub.Registry.clear()
        hub.reset_tool_metrics()

    def tearDown(self):
        hub.Registry.clear()
        hub.reset_tool_metrics()
        hub.log.setLevel(self._old_level)
        shutil.rmtree(self.tmpdir)

    def _metric(self, name):
        rows = hub.get_tool_metrics(limit=200)
        for row in rows:
            if row["tool_name"] == name:
                return row
        self.fail(f"metric not found: {name}")

    def test_success_execution_accumulates_metrics(self):
        @limb("metric_success_tool", "metric success", {})
        def metric_success_tool(args, ctx):
            return "ok"

        for _ in range(3):
            self.assertEqual(hub.execute("metric_success_tool", {}, self.ctx), "ok")

        row = self._metric("metric_success_tool")
        self.assertEqual(row["invoke_count"], 3)
        self.assertEqual(row["success_count"], 3)
        self.assertGreaterEqual(row["avg_duration_ms"], 0.0)
        self.assertTrue(row["last_used_at"])

        hub.flush_tool_metrics()
        metrics_path = os.path.join(self.workspace, "files", "tool_metrics.json")
        self.assertTrue(os.path.exists(metrics_path))
        with open(metrics_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("metric_success_tool", data)

    def test_failure_execution_accumulates_metrics(self):
        @limb("metric_fail_ret", "metric fail ret", {})
        def metric_fail_ret(args, ctx):
            return "[error] failed by return"

        @limb("metric_fail_exc", "metric fail exc", {})
        def metric_fail_exc(args, ctx):
            raise ValueError("boom")

        r1 = hub.execute("metric_fail_ret", {}, self.ctx)
        r2 = hub.execute("metric_fail_exc", {}, self.ctx)

        self.assertIn("[error]", r1)
        self.assertIn("[error]", r2)

        row_ret = self._metric("metric_fail_ret")
        row_exc = self._metric("metric_fail_exc")
        self.assertEqual(row_ret["invoke_count"], 1)
        self.assertEqual(row_ret["success_count"], 0)
        self.assertEqual(row_exc["invoke_count"], 1)
        self.assertEqual(row_exc["success_count"], 0)

    def test_degradation_trigger_at_sixth_call(self):
        @limb("metric_flaky", "metric flaky", {})
        def metric_flaky(args, ctx):
            return "[error] flaky"

        outputs = []
        for _ in range(6):
            outputs.append(hub.execute("metric_flaky", {}, self.ctx))

        row = self._metric("metric_flaky")
        self.assertEqual(row["invoke_count"], 6)
        self.assertTrue(row["experimental"])
        self.assertIn("建议重写或弃用", outputs[-1])

    def test_interceptor_overhead_under_2ms(self):
        @limb("metric_perf", "metric perf", {})
        def metric_perf(args, ctx):
            return "ok"

        # warm-up
        for _ in range(5):
            hub.execute("metric_perf", {}, self.ctx)

        loops = 200
        t0 = time.perf_counter()
        for _ in range(loops):
            hub.execute("metric_perf", {}, self.ctx)
        avg_ms = (time.perf_counter() - t0) * 1000.0 / loops

        self.assertLess(avg_ms, 2.0)


if __name__ == "__main__":
    unittest.main()
