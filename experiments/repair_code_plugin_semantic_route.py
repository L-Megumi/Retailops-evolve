from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.strategy_library import StrategyLibrary

try:
    from .propose_code_plugin_route import _route_evaluation, _task_instructions
    from .run_rsi_evolution import evaluate_library
except ImportError:  # pragma: no cover - direct script execution
    from propose_code_plugin_route import _route_evaluation, _task_instructions
    from run_rsi_evolution import evaluate_library


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair a literal plugin route with safe all-token keyword groups."
    )
    parser.add_argument(
        "--code-suite", type=Path, default=Path("benchmarks/retailops/code-evolution-v1")
    )
    parser.add_argument("--audit-suite", type=Path, required=True)
    parser.add_argument(
        "--legacy-suite", type=Path, default=Path("benchmarks/retailops/rsi-v2")
    )
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--strategy-library", type=Path, required=True)
    parser.add_argument("--previous-registration", type=Path, required=True)
    parser.add_argument("--previous-result", type=Path, required=True)
    parser.add_argument("--previous-semantic-registration", type=Path)
    parser.add_argument("--previous-semantic-result", type=Path)
    parser.add_argument(
        "--previous-invalid-proposal",
        type=Path,
        help="Rejected proposal to repair without treating it as a valid registration.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    if bool(args.previous_semantic_registration) != bool(args.previous_semantic_result):
        parser.error("semantic registration and result must be provided together")
    previous = CodePluginRegistration.read(
        args.previous_semantic_registration or args.previous_registration
    )
    result_path = args.previous_semantic_result or args.previous_result
    previous_result = json.loads(result_path.read_text(encoding="utf-8"))
    previous_transfer = (
        previous_result["development_scores"]["transfer"]
        if args.previous_semantic_result
        else previous_result["transfer"]
    )
    missed_tasks = {
        str(row["task"])
        for row in previous_transfer["rows"]
        if not row["ok"]
    }
    transfer_instructions = _task_instructions(args.code_suite, "transfer")
    missed_instructions = [
        instruction
        for task_id, instruction in transfer_instructions.items()
        if task_id in missed_tasks
    ]
    invalid_feedback = None
    if args.previous_invalid_proposal:
        invalid_proposal = json.loads(
            args.previous_invalid_proposal.read_text(encoding="utf-8")
        )
        invalid_feedback = {
            "rejected_keyword_groups": invalid_proposal.get("keyword_groups"),
            "exact_schema_error": (
                "every keyword group must contain 2-4 tokens; group 7 contained 5 tokens"
            ),
            "repair_instruction": (
                "Preserve the useful semantics, but return a complete replacement with at most "
                "8 groups and exactly 2-4 tokens in every group."
            ),
        }
    model = OpenAICompatibleModel.deepseek_from_env()
    response = model.complete_json(
        system=(
            "Repair a conservative plugin router using structured all-token keyword groups. "
            "Return JSON with fields keyword_groups and reason. keyword_groups is an array of 1-8 "
            "arrays; every inner array contains 2-4 literal tokens, and all tokens in one group "
            "must occur for that group to match. Create semantic conjunctions for order-level "
            "cumulative refund/return caps in Chinese and English. Avoid task ids, regex, and broad "
            "single-token rules. Do not repeat the existing literal cues. Anticipate paraphrases "
            "about preventing refunds from exceeding original order value, remaining refundable "
            "balance, or net value going below zero."
        ),
        user=json.dumps(
            {
                "existing_literal_cues": list(previous.cues),
                "existing_keyword_groups": [list(group) for group in previous.keyword_groups],
                "missed_development_instructions": missed_instructions,
                "previous_result": (
                    "previous semantic groups still missed the listed development instructions; "
                    "revise the complete group set by removing unnecessarily exact modal words "
                    "and covering equivalents such as 上限/天花板 and 不能/不得"
                    if args.previous_semantic_result
                    else "literal router passed training and legacy regression but matched 1/4 development transfer tasks"
                ),
                "invalid_proposal_feedback": invalid_feedback,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    (args.output / "proposal.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    raw_groups = response.get("keyword_groups")
    if not isinstance(raw_groups, list) or any(not isinstance(group, list) for group in raw_groups):
        error = "model response did not contain keyword_groups as a list of lists"
        (args.output / "result.json").write_text(
            json.dumps({"accepted": False, "error": error}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(f"semantic_route_accepted=False, error={error} -> {args.output}")
        return 1

    try:
        registration = CodePluginRegistration(
            plugin_id=previous.plugin_id,
            cues=previous.cues,
            source_sha256=hashlib.sha256(args.plugin.read_bytes()).hexdigest(),
            source_path=str(args.plugin),
            keyword_groups=tuple(
                tuple(str(token) for token in group) for group in raw_groups
            ),
            reason=str(response.get("reason", "")),
        )
    except ValueError as exc:
        result = {
            "accepted": False,
            "error": str(exc),
            "model_calls": model.request_count,
            "model_usage": model.total_usage(),
        }
        (args.output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"semantic_route_accepted=False, error={exc} -> {args.output}")
        return 1
    registration.write(args.output / "registration.json")
    plugin_source = args.plugin.read_text(encoding="utf-8")
    development_scores = {
        split: _route_evaluation(args.code_suite, split, registration, plugin_source)
        for split in ("evolution", "validation", "transfer")
    }
    legacy_instructions = {
        task_id: instruction
        for split in ("regression", "evolution", "validation", "transfer")
        for task_id, instruction in _task_instructions(args.legacy_suite, split).items()
    }
    collisions = sorted(
        task_id
        for task_id, instruction in legacy_instructions.items()
        if registration.matches(instruction)
    )
    strategy_library = StrategyLibrary.read(args.strategy_library)
    legacy_scores = {
        split: evaluate_library(args.legacy_suite, split, strategy_library).summary()
        for split in ("regression", "evolution", "validation", "transfer")
    }
    gate_checks = {
        "development_code_full": all(
            score["passed"] == score["task_count"]
            for score in development_scores.values()
        ),
        "legacy_route_zero_collision": not collisions,
        "legacy_strategy_full": all(
            score["passed"] == score["task_count"] for score in legacy_scores.values()
        ),
    }
    accepted = all(gate_checks.values())
    audit = (
        _route_evaluation(args.audit_suite, "transfer", registration, plugin_source)
        if accepted
        else None
    )
    result = {
        "accepted": accepted,
        "gate_checks": gate_checks,
        "registration": registration.to_dict(),
        "development_scores": development_scores,
        "legacy_collisions": collisions,
        "legacy_scores": legacy_scores,
        "post_repair_audit": audit,
        "model_calls": model.request_count,
        "model_usage": model.total_usage(),
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit_text = f"{audit['passed']}/{audit['task_count']}" if audit else "not_opened"
    print(
        f"semantic_route_accepted={accepted}, development_transfer="
        f"{development_scores['transfer']['passed']}/4, collisions={len(collisions)}, "
        f"audit={audit_text}, calls={model.request_count} -> {args.output}"
    )
    return int(not accepted)


if __name__ == "__main__":
    raise SystemExit(main())
