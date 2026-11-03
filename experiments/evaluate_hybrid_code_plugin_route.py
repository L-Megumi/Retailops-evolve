from __future__ import annotations

import argparse
import json
from pathlib import Path

from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.semantic_plugin_router import CapabilitySpec, route_items
from retailops_agent.strategy_library import StrategyLibrary

try:
    from .propose_code_plugin_route import _task_instructions
    from .run_code_operator_experiment import evaluate_split
    from .run_rsi_evolution import evaluate_library
except ImportError:  # pragma: no cover - direct script execution
    from propose_code_plugin_route import _task_instructions
    from run_code_operator_experiment import evaluate_split
    from run_rsi_evolution import evaluate_library


CAPABILITY = CapabilitySpec(
    capability_id="order-lifetime-cumulative-refund-cap",
    positive_definition=(
        "For each individual order or sale, accepted refund/return value is accumulated across "
        "multiple events and capped at that order's original gross merchandise value. Events are "
        "processed in input/time order; the event crossing the remaining allowance is partially "
        "accepted and later excess is rejected."
    ),
    exclusions=(
        "a limit on each refund event rather than their per-order cumulative total",
        "store/day/category aggregate caps or presentation-only non-negative clamping",
        "alerts or reports that detect excess without changing accepted amounts",
        "deduplication, status filtering, allocation, discounts, shipping, or chargebacks",
        "any task that does not explicitly entail an order-specific allowance based on original value",
    ),
)


def _score(
    decisions: list[dict[str, object]], expected: dict[str, bool]
) -> dict[str, object]:
    rows = [
        {
            **decision,
            "expected": expected[str(decision["id"])],
            "ok": decision["use_plugin"] == expected[str(decision["id"])],
        }
        for decision in decisions
    ]
    return {
        "passed": sum(bool(row["ok"]) for row in rows),
        "count": len(rows),
        "true_positive": sum(
            bool(row["use_plugin"] and row["expected"]) for row in rows
        ),
        "true_negative": sum(
            bool(not row["use_plugin"] and not row["expected"]) for row in rows
        ),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gate a deterministic-first, batched semantic fallback plugin router."
    )
    parser.add_argument(
        "--code-suite", type=Path, default=Path("benchmarks/retailops/code-evolution-v1")
    )
    parser.add_argument(
        "--legacy-suite", type=Path, default=Path("benchmarks/retailops/rsi-v2")
    )
    parser.add_argument(
        "--semantic-dev-suite",
        type=Path,
        required=True,
        help="Previously opened positive paraphrases used to gate semantic recall.",
    )
    parser.add_argument("--audit-suite", type=Path, required=True)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--strategy-library", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    registration = CodePluginRegistration.read(args.registration)
    plugin_source = args.plugin.read_text(encoding="utf-8")
    development = {
        task_id: instruction
        for split in ("evolution", "validation", "transfer")
        for task_id, instruction in _task_instructions(args.code_suite, split).items()
    }
    semantic_development = {
        f"semantic-dev::{task_id}": instruction
        for task_id, instruction in _task_instructions(
            args.semantic_dev_suite, "transfer"
        ).items()
    }
    legacy = {
        task_id: instruction
        for split in ("regression", "evolution", "validation", "transfer")
        for task_id, instruction in _task_instructions(args.legacy_suite, split).items()
    }
    gate_items = (
        list(development.items())
        + list(semantic_development.items())
        + list(legacy.items())
    )
    expected = {task_id: True for task_id in development}
    expected.update({task_id: True for task_id in semantic_development})
    expected.update({task_id: False for task_id in legacy})
    model = OpenAICompatibleModel.deepseek_from_env()
    gate_decisions = route_items(
        registration, model, CAPABILITY, gate_items, batch_size=args.batch_size
    )
    route_gate = _score(gate_decisions, expected)
    direct_plugin_scores = {
        split: evaluate_split(args.code_suite, split, plugin_source)
        for split in ("evolution", "validation", "transfer")
    }
    direct_plugin_scores["semantic_development"] = evaluate_split(
        args.semantic_dev_suite, "transfer", plugin_source
    )
    strategy_library = StrategyLibrary.read(args.strategy_library)
    legacy_strategy_scores = {
        split: evaluate_library(args.legacy_suite, split, strategy_library).summary()
        for split in ("regression", "evolution", "validation", "transfer")
    }
    deterministic_count = sum(
        row["route_source"] == "deterministic" for row in gate_decisions
    )
    gate_checks = {
        "hybrid_route_full": route_gate["passed"] == route_gate["count"],
        "plugin_code_full": all(
            score["passed"] == score["task_count"]
            for score in direct_plugin_scores.values()
        ),
        "legacy_strategy_full": all(
            score["passed"] == score["task_count"]
            for score in legacy_strategy_scores.values()
        ),
    }
    gate_passed = all(gate_checks.values())
    audit_score = None
    if gate_passed:
        audit_cases = json.loads((args.audit_suite / "cases.json").read_text(encoding="utf-8"))
        audit_items = [(str(case["id"]), str(case["instruction"])) for case in audit_cases]
        audit_expected = {str(case["id"]): bool(case["expected"]) for case in audit_cases}
        audit_decisions = route_items(
            registration, model, CAPABILITY, audit_items, batch_size=args.batch_size
        )
        audit_score = _score(audit_decisions, audit_expected)
    audit_passed = bool(
        audit_score and audit_score["passed"] == audit_score["count"]
    )
    accepted = gate_passed and audit_passed
    result = {
        "accepted": accepted,
        "gate_passed": gate_passed,
        "audit_passed": audit_passed,
        "gate_checks": gate_checks,
        "capability": {
            "capability_id": CAPABILITY.capability_id,
            "positive_definition": CAPABILITY.positive_definition,
            "exclusions": list(CAPABILITY.exclusions),
        },
        "route_gate": route_gate,
        "gate_deterministic_count": deterministic_count,
        "gate_semantic_count": len(gate_items) - deterministic_count,
        "direct_plugin_scores": direct_plugin_scores,
        "legacy_strategy_scores": legacy_strategy_scores,
        "post_gate_audit": audit_score,
        "model_calls": model.request_count,
        "model_usage": model.total_usage(),
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit_text = (
        f"{audit_score['passed']}/{audit_score['count']}" if audit_score else "not_opened"
    )
    print(
        f"hybrid_route_accepted={accepted}, gate={route_gate['passed']}/{route_gate['count']}, "
        f"deterministic={deterministic_count}, semantic={len(gate_items)-deterministic_count}, "
        f"audit={audit_text}, calls={model.request_count} -> {args.output}"
    )
    return int(not accepted)


if __name__ == "__main__":
    raise SystemExit(main())
