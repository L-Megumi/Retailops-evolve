from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from retailops_agent.agent import RetailOpsAgent
from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.plugin_manager import PluginManager
from retailops_agent.plugin_protocol import PluginSpec
from retailops_agent.report import render_markdown
from retailops_agent.semantic_plugin_router import CapabilitySpec

from tests.test_plugin_manager import (
    BAD_RUNTIME_SOURCE,
    DEDUP_CAPABILITY,
    DEDUP_SOURCE,
    GOOD_SOURCE,
    METRIC_SOURCE,
    dedup_registration,
)


class PluginRuntimeAgentTests(unittest.TestCase):
    def test_agent_routes_executes_validates_and_reports_plugin(self) -> None:
        manager = PluginManager()
        manager.install(
            version="1",
            registration=CodePluginRegistration(
                plugin_id="refund-cap",
                cues=("累计退款上限",),
                source_sha256=hashlib.sha256(GOOD_SOURCE.encode("utf-8")).hexdigest(),
                source_path="plugin.py",
            ),
            capability=CapabilitySpec("refund-cap", "definition", ()),
            source=GOOD_SOURCE,
            activate=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            orders = root / "orders.csv"
            refunds = root / "refunds.csv"
            orders.write_text(
                "order_id,order_date,category,product,quantity,unit_price\n"
                "O1,2026-01-01,A,P,1,100.00\n",
                encoding="utf-8",
            )
            refunds.write_text(
                "refund_id,order_id,refund_date,refund_amount\n"
                "R1,O1,2026-01-02,70.00\n"
                "R2,O1,2026-01-03,60.00\n",
                encoding="utf-8",
            )

            trace = RetailOpsAgent(plugin_manager=manager).run(
                "按累计退款上限生成每日和品类经营汇总", orders, refunds
            )

        self.assertTrue(trace.ok, trace.issues)
        self.assertEqual(trace.plugin["plugin_id"], "refund-cap")
        daily_refunds = sum(
            row["refund_amount"] for row in trace.results["daily_net_sales"].data
        )
        category_refunds = sum(
            row["refund_amount"] for row in trace.results["category_ranking"].data
        )
        self.assertEqual(daily_refunds, Decimal("100.00"))
        self.assertEqual(category_refunds, Decimal("100.00"))
        report = render_markdown(trace)
        self.assertIn("refund-cap@1", report)
        self.assertIn("插件路由：deterministic", report)

    def test_agent_stops_when_matched_plugin_has_no_stable_fallback(self) -> None:
        manager = PluginManager()
        broken = manager.install(
            version="1",
            registration=CodePluginRegistration(
                plugin_id="refund-cap",
                cues=("累计退款上限",),
                source_sha256=hashlib.sha256(
                    BAD_RUNTIME_SOURCE.encode("utf-8")
                ).hexdigest(),
                source_path="plugin.py",
            ),
            capability=CapabilitySpec("refund-cap", "definition", ()),
            source=BAD_RUNTIME_SOURCE,
            activate=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            orders = root / "orders.csv"
            refunds = root / "refunds.csv"
            orders.write_text(
                "order_id,order_date,category,product,quantity,unit_price\n"
                "O1,2026-01-01,A,P,1,100.00\n",
                encoding="utf-8",
            )
            refunds.write_text(
                "refund_id,order_id,refund_date,refund_amount\n"
                "R1,O1,2026-01-02,70.00\n",
                encoding="utf-8",
            )

            trace = RetailOpsAgent(plugin_manager=manager).run(
                "按累计退款上限生成每日汇总", orders, refunds
            )

        self.assertFalse(trace.ok)
        self.assertEqual(trace.issues[0].code, "plugin_execution_failed")
        self.assertFalse(trace.results)
        self.assertEqual(broken.status, "quarantined")

    def test_agent_executes_second_plugin_family(self) -> None:
        manager = PluginManager()
        manager.install(
            version="1",
            registration=dedup_registration(),
            capability=DEDUP_CAPABILITY,
            source=DEDUP_SOURCE,
            spec=PluginSpec.refund_transform(invariants=()),
            activate=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            orders = root / "orders.csv"
            refunds = root / "refunds.csv"
            orders.write_text(
                "order_id,order_date,category,product,quantity,unit_price\n"
                "O1,2026-01-01,A,P,1,100.00\n",
                encoding="utf-8",
            )
            refunds.write_text(
                "refund_id,order_id,refund_date,refund_amount\n"
                "R1,O1,2026-01-02,20.00\n"
                "R2,O1,2026-01-03,30.00\n"
                "R1,O1,2026-01-02,20.00\n",
                encoding="utf-8",
            )

            trace = RetailOpsAgent(plugin_manager=manager).run(
                "Deduplicate refunds by refund_id and generate daily totals",
                orders,
                refunds,
            )

        self.assertTrue(trace.ok, trace.issues)
        self.assertEqual(trace.plugin["plugin_id"], "refund-dedup")
        total = sum(
            row["refund_amount"] for row in trace.results["daily_net_sales"].data
        )
        self.assertEqual(total, Decimal("50.00"))

    def test_agent_merges_metric_tool_result_from_generic_protocol(self) -> None:
        manager = PluginManager()
        manager.install(
            version="1",
            registration=CodePluginRegistration(
                plugin_id="refund-replay-rate",
                cues=("refund replay rate",),
                source_sha256=hashlib.sha256(METRIC_SOURCE.encode("utf-8")).hexdigest(),
                source_path="metric.py",
            ),
            capability=CapabilitySpec(
                "refund-replay-rate",
                "calculate duplicate refund rows divided by total refund rows",
                (),
            ),
            source=METRIC_SOURCE,
            spec=PluginSpec.metric_tool("refund_replay_rate"),
            activate=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            orders = root / "orders.csv"
            refunds = root / "refunds.csv"
            orders.write_text(
                "order_id,order_date,category,product,quantity,unit_price\n"
                "O1,2026-01-01,A,P,1,100.00\n",
                encoding="utf-8",
            )
            refunds.write_text(
                "refund_id,order_id,refund_date,refund_amount\n"
                "R1,O1,2026-01-02,20.00\n"
                "R2,O1,2026-01-03,30.00\n"
                "R1,O1,2026-01-02,20.00\n",
                encoding="utf-8",
            )

            trace = RetailOpsAgent(plugin_manager=manager).run(
                "Calculate the refund replay rate", orders, refunds
            )

        self.assertTrue(trace.ok, trace.issues)
        self.assertIn("refund_replay_rate", trace.results)
        self.assertEqual(
            trace.results["refund_replay_rate"].data["replay_rate_bps"], 3333
        )


if __name__ == "__main__":
    unittest.main()
