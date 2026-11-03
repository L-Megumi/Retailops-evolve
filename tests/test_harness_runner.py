from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from retailops_agent.harness_runner import execute_policy
from retailops_agent.models import ExecutionPolicy


ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "benchmarks/retailops/tasks/refund-metrics-1"


class HarnessRunnerTests(unittest.TestCase):
    def run_policy(self, policy: ExecutionPolicy) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "answer.json"
            payload = execute_policy(
                orders_path=TASK / "orders.csv",
                refunds_path=TASK / "refunds.csv",
                task_path=TASK / "task.json",
                output_path=output,
                policy=policy,
            )
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ok"], payload["ok"])
            return payload

    def test_naive_policy_produces_actionable_issues(self):
        payload = self.run_policy(ExecutionPolicy.naive())
        self.assertFalse(payload["ok"])
        self.assertEqual(
            {issue["code"] for issue in payload["issues"]},
            {"refund_rate_definition", "category_refund_allocation"},
        )

    def test_verified_policy_passes(self):
        payload = self.run_policy(ExecutionPolicy())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["issues"], [])

    def test_task_contract_drives_daily_attribution_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "task.json"
            task.write_text(
                json.dumps(
                    {
                        "instruction": "生成每日净销售额，退款回溯原订单日期。",
                        "tools": ["daily_net_sales"],
                        "metric_contract": {"refund_time_attribution": "order_date"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            payload = execute_policy(
                orders_path=ROOT / "data/orders.csv",
                refunds_path=ROOT / "data/refunds.csv",
                task_path=task,
                output_path=root / "answer.json",
                policy=ExecutionPolicy.naive(),
            )
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["issues"][0]["code"], "refund_time_attribution")
            self.assertEqual(payload["issues"][0]["expected"], "order_date")


if __name__ == "__main__":
    unittest.main()
