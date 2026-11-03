from __future__ import annotations

import unittest

from retailops_agent.planner import RulePlanner


class RulePlannerIntentTests(unittest.TestCase):
    def setUp(self):
        self.planner = RulePlanner()

    def test_refunded_order_share_selects_rate_only(self):
        plan = self.planner.plan(
            "统计发生退款的订单占比；同一订单出现多行商品时只计为一个订单。"
        )
        self.assertEqual(plan.tools, ("refund_rate",))

    def test_category_net_sales_does_not_select_daily_summary(self):
        plan = self.planner.plan("输出各商品类别扣除退款后的净销售额排行。")
        self.assertEqual(plan.tools, ("category_ranking",))

    def test_combined_paraphrase_selects_rate_and_category(self):
        plan = self.planner.plan("核算退款订单率和类别净销售额排行。")
        self.assertEqual(plan.tools, ("refund_rate", "category_ranking"))

    def test_order_and_refund_dates_select_daily_summary(self):
        plan = self.planner.plan("按订单日期与退款发生日期汇总销售额。")
        self.assertEqual(plan.tools, ("daily_net_sales",))


if __name__ == "__main__":
    unittest.main()
