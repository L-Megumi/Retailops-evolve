from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from retailops_agent.agent import RetailOpsAgent
from retailops_agent.data import DataLoadError, load_orders
from retailops_agent.planner import ModelPlanner
from retailops_agent.models import ExecutionPolicy


ROOT = Path(__file__).resolve().parents[1]


class RetailOpsAgentTests(unittest.TestCase):
    def test_end_to_end_analysis_passes_validation(self):
        trace = RetailOpsAgent().run(
            "分析每日净销售额、退款率和品类排行",
            ROOT / "data/orders.csv",
            ROOT / "data/refunds.csv",
        )
        self.assertTrue(trace.ok, trace.issues)
        self.assertEqual(
            set(trace.results), {"daily_net_sales", "refund_rate", "category_ranking"}
        )
        self.assertEqual(trace.results["refund_rate"].data["total_orders"], 7)
        self.assertEqual(trace.results["refund_rate"].data["refunded_orders"], 2)

    def test_category_totals_reconcile(self):
        trace = RetailOpsAgent().run(
            "生成品类排行",
            ROOT / "data/orders.csv",
            ROOT / "data/refunds.csv",
        )
        rows = trace.results["category_ranking"].data
        self.assertEqual(sum(row["gross_sales"] for row in rows), Decimal("2374.00"))
        self.assertEqual(sum(row["refund_amount"] for row in rows), Decimal("749.00"))

    def test_missing_columns_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "orders.csv"
            bad.write_text("order_id,quantity\nO1,1\n", encoding="utf-8")
            with self.assertRaises(DataLoadError) as caught:
                load_orders(bad)
            self.assertEqual(caught.exception.issues[0].code, "missing_columns")

    def test_model_planner_rejects_unknown_tools(self):
        class FakeModel:
            def complete_json(self, **_kwargs):
                return {"tools": ["refund_rate", "drop_database"]}

        plan = ModelPlanner(FakeModel()).plan("计算退款率")
        self.assertEqual(plan.tools, ("refund_rate",))

    def test_validator_feedback_repairs_naive_policy(self):
        trace = RetailOpsAgent(initial_policy=ExecutionPolicy.naive()).run(
            "计算退款率并生成品类排行",
            ROOT / "data/orders.csv",
            ROOT / "data/refunds.csv",
        )
        self.assertTrue(trace.ok, trace.issues)
        self.assertEqual(trace.attempts, 2)
        self.assertEqual(trace.policies[0].refund_rate_denominator, "order_lines")
        self.assertEqual(trace.policies[-1].refund_rate_denominator, "distinct_orders")
        repairs = [event for event in trace.events if event["stage"] == "repair"]
        self.assertEqual(len(repairs), 1)
        self.assertEqual(len(repairs[0]["changes"]), 2)


if __name__ == "__main__":
    unittest.main()
