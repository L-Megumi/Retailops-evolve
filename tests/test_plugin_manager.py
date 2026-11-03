from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.models import OrderLine, Refund
from retailops_agent.plugin_manager import PluginExecutionError, PluginManager
from retailops_agent.plugin_protocol import PluginSpec
from retailops_agent.semantic_plugin_router import CapabilitySpec


GOOD_SOURCE = """def transform_refunds(order_totals_cents, refunds):
    remaining = {}
    for order_id in order_totals_cents:
        remaining[order_id] = order_totals_cents.get(order_id, 0)
    result = []
    for refund in refunds:
        order_id = refund.get('order_id')
        available = remaining.get(order_id, 0)
        if available <= 0:
            continue
        confirmed = min(refund.get('refund_cents'), available)
        remaining[order_id] = available - confirmed
        out = refund.copy()
        out['refund_cents'] = confirmed
        result.append(out)
    return result
"""

BAD_RUNTIME_SOURCE = """def transform_refunds(order_totals_cents, refunds):
    result = []
    for refund in refunds:
        out = refund.copy()
        out['refund_cents'] = refund.get('refund_cents') * 2
        result.append(out)
    return result
"""

DEDUP_SOURCE = """def transform_refunds(order_totals_cents, refunds):
    seen = {}
    result = []
    for row in refunds:
        refund_id = row.get('refund_id')
        if refund_id not in seen:
            seen[refund_id] = True
            result.append(row.copy())
    return result
"""

METRIC_SOURCE = """def compute_metric(orders, refunds):
    seen = {}
    duplicate_rows = 0
    for row in refunds:
        refund_id = row.get('refund_id')
        if refund_id in seen:
            duplicate_rows += 1
        else:
            seen[refund_id] = True
    total_rows = len(refunds)
    replay_rate_bps = duplicate_rows * 10000 // total_rows if total_rows else 0
    return {'metric': 'refund_replay_rate', 'data': {'total_refund_rows': total_rows, 'duplicate_rows': duplicate_rows, 'replay_rate_bps': replay_rate_bps}, 'evidence': {'definition': 'duplicate rows divided by total refund rows'}}
"""


class FakeModel:
    def __init__(self, decisions: list[bool]):
        self.decisions = list(decisions)
        self.calls = 0

    def complete_json(self, **_kwargs):
        self.calls += 1
        use_plugin = self.decisions.pop(0)
        return {
            "decisions": [
                {
                    "id": "runtime-task",
                    "use_plugin": use_plugin,
                    "reason": "fake semantic decision",
                }
            ]
        }


class FakeMultiModel:
    def __init__(self, selections: list[str | None]):
        self.selections = list(selections)
        self.calls = 0

    def complete_json(self, **_kwargs):
        self.calls += 1
        return {
            "plugin_id": self.selections.pop(0),
            "reason": "fake multi-plugin selection",
        }


def registration(source: str) -> CodePluginRegistration:
    return CodePluginRegistration(
        plugin_id="refund-cap",
        cues=("cumulative refund cap",),
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        source_path="plugin.py",
    )


def dedup_registration() -> CodePluginRegistration:
    return CodePluginRegistration(
        plugin_id="refund-dedup",
        cues=("deduplicate refunds by refund_id",),
        source_sha256=hashlib.sha256(DEDUP_SOURCE.encode("utf-8")).hexdigest(),
        source_path="dedup.py",
    )


CAPABILITY = CapabilitySpec(
    "refund-cap",
    "cap cumulative accepted refunds per order at original gross in event order",
    ("per-event threshold", "aggregate daily cap"),
)
DEDUP_CAPABILITY = CapabilitySpec(
    "refund-dedup",
    "keep only the first occurrence of every duplicate refund_id",
    ("amount caps", "deduplication by order id"),
)
DEDUP_SPEC = PluginSpec.refund_transform(
    order_before=("refund-cap",), invariants=()
)

ORDERS = [
    OrderLine("O1", "2026-01-01", "A", "P", 1, Decimal("100.00"))
]
REFUNDS = [
    Refund("R1", "O1", "2026-01-02", Decimal("70.00")),
    Refund("R2", "O1", "2026-01-03", Decimal("60.00")),
]
DUPLICATE_REFUNDS = [
    Refund("R1", "O1", "2026-01-02", Decimal("20.00")),
    Refund("R2", "O1", "2026-01-03", Decimal("30.00")),
    Refund("R1", "O1", "2026-01-02", Decimal("20.00")),
]


def install(
    manager: PluginManager,
    source: str,
    version: str,
    *,
    activate: bool = True,
):
    return manager.install(
        version=version,
        registration=registration(source),
        capability=CAPABILITY,
        source=source,
        activate=activate,
    )


class PluginManagerTests(unittest.TestCase):
    def test_deterministic_route_executes_and_validates_plugin(self) -> None:
        manager = PluginManager()
        install(manager, GOOD_SOURCE, "1")

        application = manager.apply(
            "Apply a cumulative refund cap and report sales", ORDERS, REFUNDS
        )

        self.assertTrue(application.matched)
        self.assertEqual(application.metadata["route_source"], "deterministic")
        self.assertEqual(
            [refund.refund_amount for refund in application.refunds],
            [Decimal("70.00"), Decimal("30.00")],
        )

    def test_semantic_route_cache_survives_manager_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "route-cache.json"
            first_model = FakeModel([True])
            first = PluginManager(semantic_model=first_model, cache_path=cache_path)
            install(first, GOOD_SOURCE, "1")

            first_application = first.apply(
                "逐单限制多次退货总额不得越过原始货款", ORDERS, REFUNDS
            )
            second_model = FakeModel([])
            second = PluginManager(semantic_model=second_model, cache_path=cache_path)
            install(second, GOOD_SOURCE, "1")
            cached_application = second.apply(
                "逐单限制多次退货总额不得越过原始货款", ORDERS, REFUNDS
            )

            self.assertEqual(first_model.calls, 1)
            self.assertEqual(second_model.calls, 0)
            self.assertFalse(first_application.metadata["cache_hit"])
            self.assertTrue(cached_application.metadata["cache_hit"])
            self.assertEqual(cached_application.metadata["route_source"], "semantic_cache")

    def test_negative_semantic_decision_is_cached(self) -> None:
        model = FakeModel([False])
        manager = PluginManager(semantic_model=model)
        install(manager, GOOD_SOURCE, "1")

        first = manager.apply("统计大额退款告警但不要修改金额", ORDERS, REFUNDS)
        second = manager.apply("统计大额退款告警但不要修改金额", ORDERS, REFUNDS)

        self.assertFalse(first.matched)
        self.assertFalse(second.matched)
        self.assertEqual(model.calls, 1)
        self.assertTrue(second.metadata["cache_hit"])
        self.assertEqual(second.refunds, REFUNDS)

    def test_registry_change_invalidates_semantic_cache(self) -> None:
        model = FakeModel([True, True])
        manager = PluginManager(semantic_model=model)
        install(manager, GOOD_SOURCE, "1")
        task = "逐单限制多次退货总额不得越过原始货款"

        first = manager.apply(task, ORDERS, REFUNDS)
        install(manager, GOOD_SOURCE, "2")
        after_upgrade = manager.apply(task, ORDERS, REFUNDS)

        self.assertEqual(model.calls, 2)
        self.assertFalse(first.metadata["cache_hit"])
        self.assertFalse(after_upgrade.metadata["cache_hit"])
        self.assertEqual(after_upgrade.metadata["plugin_version"], "2")

    def test_two_plugin_families_route_without_interference(self) -> None:
        manager = PluginManager()
        install(manager, GOOD_SOURCE, "1")
        manager.install(
            version="1",
            registration=dedup_registration(),
            capability=DEDUP_CAPABILITY,
            source=DEDUP_SOURCE,
            spec=DEDUP_SPEC,
            activate=True,
        )

        capped = manager.apply(
            "Apply a cumulative refund cap", ORDERS, REFUNDS
        )
        deduplicated = manager.apply(
            "Deduplicate refunds by refund_id", ORDERS, DUPLICATE_REFUNDS
        )

        self.assertEqual(capped.metadata["plugin_id"], "refund-cap")
        self.assertEqual(deduplicated.metadata["plugin_id"], "refund-dedup")
        self.assertEqual(
            [refund.refund_id for refund in deduplicated.refunds], ["R1", "R2"]
        )
        self.assertEqual(
            sum(refund.refund_amount for refund in deduplicated.refunds),
            Decimal("50.00"),
        )

    def test_semantic_router_selects_one_of_two_plugins_and_caches_both_decisions(self) -> None:
        model = FakeMultiModel(["refund-dedup"])
        manager = PluginManager(semantic_model=model)
        install(manager, GOOD_SOURCE, "1")
        manager.install(
            version="1",
            registration=dedup_registration(),
            capability=DEDUP_CAPABILITY,
            source=DEDUP_SOURCE,
            spec=DEDUP_SPEC,
            activate=True,
        )
        task = "相同售后流水号多次出现时只采信最早记录"

        first = manager.apply(task, ORDERS, DUPLICATE_REFUNDS)
        second = manager.apply(task, ORDERS, DUPLICATE_REFUNDS)

        self.assertEqual(first.metadata["plugin_id"], "refund-dedup")
        self.assertEqual(first.metadata["route_source"], "semantic")
        self.assertEqual(second.metadata["route_source"], "semantic_cache")
        self.assertEqual(model.calls, 1)

    def test_unknown_multi_plugin_selection_is_rejected(self) -> None:
        model = FakeMultiModel(["unknown-plugin"])
        manager = PluginManager(semantic_model=model)
        install(manager, GOOD_SOURCE, "1")
        manager.install(
            version="1",
            registration=dedup_registration(),
            capability=DEDUP_CAPABILITY,
            source=DEDUP_SOURCE,
            spec=DEDUP_SPEC,
            activate=True,
        )

        with self.assertRaisesRegex(PluginExecutionError, "invalid plugin ids"):
            manager.resolve("含糊的退款处理要求")

    def test_composes_dedup_before_cumulative_cap(self) -> None:
        manager = PluginManager()
        install(manager, GOOD_SOURCE, "1")
        manager.install(
            version="1",
            registration=dedup_registration(),
            capability=DEDUP_CAPABILITY,
            source=DEDUP_SOURCE,
            spec=DEDUP_SPEC,
            activate=True,
        )
        refunds = [
            Refund("R1", "O1", "2026-01-02", Decimal("70.00")),
            Refund("R1", "O1", "2026-01-02", Decimal("70.00")),
            Refund("R2", "O1", "2026-01-03", Decimal("60.00")),
        ]

        application = manager.apply(
            "Deduplicate refunds by refund_id, then apply a cumulative refund cap",
            ORDERS,
            refunds,
        )

        self.assertEqual(
            [step["plugin_id"] for step in application.metadata["pipeline"]],
            ["refund-dedup", "refund-cap"],
        )
        self.assertEqual(
            [(refund.refund_id, refund.refund_amount) for refund in application.refunds],
            [("R1", Decimal("70.00")), ("R2", Decimal("30.00"))],
        )

    def test_metric_tool_uses_distinct_protocol_and_preserves_refunds(self) -> None:
        manager = PluginManager()
        metric_registration = CodePluginRegistration(
            plugin_id="refund-replay-rate",
            cues=("refund replay rate",),
            source_sha256=hashlib.sha256(METRIC_SOURCE.encode("utf-8")).hexdigest(),
            source_path="metric.py",
        )
        manager.install(
            version="1",
            registration=metric_registration,
            capability=CapabilitySpec(
                "refund-replay-rate",
                "calculate duplicate refund event rows divided by all refund rows",
                (),
            ),
            source=METRIC_SOURCE,
            spec=PluginSpec.metric_tool("refund_replay_rate"),
            activate=True,
        )

        application = manager.apply(
            "Calculate the refund replay rate", ORDERS, DUPLICATE_REFUNDS
        )

        self.assertEqual(application.refunds, DUPLICATE_REFUNDS)
        data = application.results["refund_replay_rate"].data
        self.assertEqual(data["total_refund_rows"], 3)
        self.assertEqual(data["duplicate_rows"], 1)
        self.assertEqual(data["replay_rate_bps"], 3333)

    def test_composed_pipeline_restarts_atomically_after_rollback(self) -> None:
        manager = PluginManager()
        stable = install(manager, GOOD_SOURCE, "1")
        broken = install(manager, BAD_RUNTIME_SOURCE, "2")
        manager.install(
            version="1",
            registration=dedup_registration(),
            capability=DEDUP_CAPABILITY,
            source=DEDUP_SOURCE,
            spec=DEDUP_SPEC,
            activate=True,
        )
        refunds = [
            Refund("R1", "O1", "2026-01-02", Decimal("70.00")),
            Refund("R1", "O1", "2026-01-02", Decimal("70.00")),
            Refund("R2", "O1", "2026-01-03", Decimal("60.00")),
        ]

        application = manager.apply(
            "Deduplicate refunds by refund_id, then apply a cumulative refund cap",
            ORDERS,
            refunds,
        )

        self.assertEqual(broken.status, "quarantined")
        self.assertEqual(stable.status, "active")
        self.assertEqual(application.metadata["status"], "rolled_back")
        self.assertEqual(
            application.metadata["rollbacks"],
            [{"plugin_id": "refund-cap", "from": "2", "to": "1"}],
        )
        self.assertEqual(len(application.metadata["steps"]), 2)
        self.assertEqual(
            sum(refund.refund_amount for refund in application.refunds),
            Decimal("100.00"),
        )

    def test_required_result_coverage_overrides_incomplete_deterministic_match(self) -> None:
        model = FakeMultiModel(["refund-replay-rate"])
        manager = PluginManager(semantic_model=model)
        manager.install(
            version="1",
            registration=dedup_registration(),
            capability=DEDUP_CAPABILITY,
            source=DEDUP_SOURCE,
            spec=DEDUP_SPEC,
            activate=True,
        )
        metric_registration = CodePluginRegistration(
            plugin_id="refund-replay-rate",
            cues=("replay metric output",),
            source_sha256=hashlib.sha256(METRIC_SOURCE.encode("utf-8")).hexdigest(),
            source_path="metric.py",
        )
        manager.install(
            version="1",
            registration=metric_registration,
            capability=CapabilitySpec(
                "refund-replay-rate",
                "calculate replay rows divided by total refund rows",
                (),
            ),
            source=METRIC_SOURCE,
            spec=PluginSpec.metric_tool("refund_replay_rate"),
            activate=True,
        )

        application = manager.apply(
            "Deduplicate refunds by refund_id and report replay share",
            ORDERS,
            DUPLICATE_REFUNDS,
            required_results=("refund_replay_rate",),
        )

        self.assertEqual(application.metadata["route_source"], "coverage_semantic")
        self.assertEqual(
            application.metadata["selected_plugin_ids"], ["refund-replay-rate"]
        )
        self.assertIn("refund_replay_rate", application.results)
        self.assertEqual(model.calls, 1)

    def test_bad_new_version_is_quarantined_and_rolls_back(self) -> None:
        manager = PluginManager()
        stable = install(manager, GOOD_SOURCE, "1")
        broken = install(manager, BAD_RUNTIME_SOURCE, "2")

        application = manager.apply(
            "Apply a cumulative refund cap and report sales", ORDERS, REFUNDS
        )

        self.assertEqual(broken.status, "quarantined")
        self.assertEqual(stable.status, "active")
        self.assertEqual(application.metadata["status"], "rolled_back")
        self.assertEqual(application.metadata["rollback_from"], "2")
        self.assertEqual(application.metadata["plugin_version"], "1")
        self.assertEqual(sum(r.refund_amount for r in application.refunds), Decimal("100.00"))

    def test_failure_without_stable_version_stops_execution(self) -> None:
        manager = PluginManager()
        broken = install(manager, BAD_RUNTIME_SOURCE, "1")

        with self.assertRaises(PluginExecutionError):
            manager.apply("Apply a cumulative refund cap", ORDERS, REFUNDS)

        self.assertEqual(broken.status, "quarantined")

    def test_static_rejection_does_not_replace_active_version(self) -> None:
        manager = PluginManager()
        stable = install(manager, GOOD_SOURCE, "1")
        unsafe_source = "import os\n"
        rejected = install(manager, unsafe_source, "2")

        self.assertEqual(rejected.status, "quarantined")
        self.assertEqual(stable.status, "active")


if __name__ == "__main__":
    unittest.main()
