from __future__ import annotations

import unittest
from pathlib import Path

from retailops_agent.agent import RetailOpsAgent
from retailops_agent.contracts import normalize_metric_contract
from retailops_agent.models import ExecutionPolicy


ROOT = Path(__file__).resolve().parents[1]


class DynamicMetricContractTests(unittest.TestCase):
    def test_base_policy_can_be_correct_for_task_specific_contract(self):
        trace = RetailOpsAgent(initial_policy=ExecutionPolicy.naive()).run(
            "计算退款率并生成品类排行",
            ROOT / "data/orders.csv",
            ROOT / "data/refunds.csv",
            metric_contract={
                "refund_rate_denominator": "order_lines",
                "category_refund_allocation": "first_category",
            },
        )
        self.assertTrue(trace.ok, trace.issues)
        self.assertEqual(trace.attempts, 1)
        self.assertEqual(trace.policies[-1], ExecutionPolicy.naive())

    def test_daily_refunds_can_be_repaired_to_order_date(self):
        trace = RetailOpsAgent(initial_policy=ExecutionPolicy.naive()).run(
            "生成每日净销售额",
            ROOT / "data/orders.csv",
            ROOT / "data/refunds.csv",
            metric_contract={"refund_time_attribution": "order_date"},
        )
        self.assertTrue(trace.ok, trace.issues)
        self.assertEqual(trace.attempts, 2)
        self.assertEqual(trace.policies[-1].refund_time_attribution, "order_date")
        self.assertEqual(
            trace.events[1]["issues"],
            ["refund_time_attribution"],
        )

    def test_invalid_contract_value_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_metric_contract({"refund_time_attribution": "payment_date"})


if __name__ == "__main__":
    unittest.main()
