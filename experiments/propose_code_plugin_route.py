from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.plugin_sandbox import validate_plugin_source
from retailops_agent.strategy_library import StrategyLibrary

try:
    from .run_code_operator_experiment import evaluate_split
    from .run_rsi_evolution import evaluate_library
except ImportError:  # pragma: no cover - direct script execution
    from run_code_operator_experiment import evaluate_split
    from run_rsi_evolution import evaluate_library


def _task_instructions(suite: Path, split: str) -> dict[str, str]:
    instructions: dict[str, str] = {}
    for task_path in sorted((suite / "tasks" / split).glob("*/task.json")):
        task = json.loads(task_path.read_text(encoding="utf-8"))
        instructions[task_path.parent.name] = str(task["instruction"])
    return instructions


def _route_evaluation(
    suite: Path,
    split: str,
    registration: CodePluginRegistration,
    plugin_source: str,
) -> dict[str, object]:
    direct = evaluate_split(suite, split, plugin_source)
    instructions = _task_instructions(suite, split)
    rows: list[dict[str, object]] = []
    direct_by_task = {str(row["task"]): row for row in direct["rows"]}
    for task_id, instruction in instructions.items():
        matched = registration.matches(instruction)
        direct_row = direct_by_task[task_id]
        rows.append(
            {
                "task": task_id,
                "matched": matched,
                "ok": matched and bool(direct_row["ok"]),
                "plugin_error": direct_row["error"] if matched else "",
            }
        )
    return {
        "split": split,
        "passed": sum(bool(row["ok"]) for row in rows),
        "task_count": len(rows),
        "matched": sum(bool(row["matched"]) for row in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Propose and gate a persistent literal-cue route for a frozen code plugin."
    )
    parser.add_argument(
        "--code-suite", type=Path, default=Path("benchmarks/retailops/code-evolution-v1")
    )
    parser.add_argument(
        "--legacy-suite", type=Path, default=Path("benchmarks/retailops/rsi-v2")
    )
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--strategy-library", type=Path, required=True)
    parser.add_argument(
        "--previous-proposal",
        type=Path,
        help="repair a rejected cue proposal without exposing transfer results",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    plugin_source = args.plugin.read_text(encoding="utf-8")
    validate_plugin_source(plugin_source)
    source_sha256 = hashlib.sha256(args.plugin.read_bytes()).hexdigest()
    train_instructions = {
        split: list(_task_instructions(args.code_suite, split).values())
        for split in ("evolution", "validation")
    }
    model = OpenAICompatibleModel.deepseek_from_env()
    previous_proposal = None
    if args.previous_proposal:
        previous_proposal = json.loads(args.previous_proposal.read_text(encoding="utf-8"))
    repair_instruction = (
        " This is a repair of a proposal rejected for having too many cues. Return at most 12 "
        "cues by merging redundant process phrases, retain coverage of all training variants, and "
        "include the exact English cue cumulative refund cap."
        if previous_proposal
        else ""
    )
    response = model.complete_json(
        system=(
            "Create a conservative persistent router for a verified retail code plugin. Return "
            "JSON with fields cues (an array of 1-12 literal substring strings) and reason. A task "
            "matches when any cue occurs case-insensitively in its instruction. Cues should cover "
            "all provided Chinese wording variants and anticipate a concise English paraphrase, "
            "but must be specific to order-level cumulative refund caps. Avoid broad words such as "
            "退款, 订单, 汇总, category, daily when used alone. Do not include regex or task ids."
            + repair_instruction
        ),
        user=json.dumps(
            {
                "plugin_semantics": (
                    "cap cumulative confirmed refunds per order at order gross; consume in file "
                    "order, clip the boundary refund and discard later excess rows"
                ),
                "evolution_instructions": train_instructions["evolution"],
                "validation_instructions": train_instructions["validation"],
                "previous_rejected_proposal": previous_proposal,
                "previous_rejection": (
                    "a plugin registration must contain 1-12 cues"
                    if previous_proposal
                    else ""
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    raw_cues = response.get("cues")
    (args.output / "proposal.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not isinstance(raw_cues, list):
        error = "model response did not contain a cues list"
        (args.output / "result.json").write_text(
            json.dumps({"accepted": False, "error": error}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(f"route_accepted=False, error={error} -> {args.output}")
        return 1
    try:
        registration = CodePluginRegistration(
            plugin_id="cumulative-refund-cap-v1",
            cues=tuple(str(cue) for cue in raw_cues),
            source_sha256=source_sha256,
            source_path=str(args.plugin),
            reason=str(response.get("reason", "")),
        )
    except ValueError as exc:
        result = {
            "accepted": False,
            "error": f"{type(exc).__name__}: {exc}",
            "cue_count": len(raw_cues),
            "model_calls": model.request_count,
            "model_usage": model.total_usage(),
        }
        (args.output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"route_accepted=False, cues={len(raw_cues)}, error={exc} -> {args.output}"
        )
        return 1
    registration.write(args.output / "registration.json")

    code_train = {
        split: _route_evaluation(args.code_suite, split, registration, plugin_source)
        for split in ("evolution", "validation")
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
        "plugin_evolution_full": code_train["evolution"]["passed"]
        == code_train["evolution"]["task_count"],
        "plugin_validation_full": code_train["validation"]["passed"]
        == code_train["validation"]["task_count"],
        "legacy_route_zero_collision": not collisions,
        "legacy_strategy_full": all(
            score["passed"] == score["task_count"] for score in legacy_scores.values()
        ),
    }
    accepted = all(gate_checks.values())
    transfer = (
        _route_evaluation(args.code_suite, "transfer", registration, plugin_source)
        if accepted
        else None
    )
    result = {
        "accepted": accepted,
        "gate_checks": gate_checks,
        "registration": registration.to_dict(),
        "code_train": code_train,
        "legacy_collisions": collisions,
        "legacy_scores": legacy_scores,
        "transfer": transfer,
        "model_calls": model.request_count,
        "model_usage": model.total_usage(),
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    transfer_text = (
        f"{transfer['passed']}/{transfer['task_count']}" if transfer else "not_opened"
    )
    print(
        f"route_accepted={accepted}, evolution={code_train['evolution']['passed']}/4, "
        f"validation={code_train['validation']['passed']}/4, collisions={len(collisions)}, "
        f"transfer={transfer_text}, calls={model.request_count} -> {args.output}"
    )
    return int(not accepted)


if __name__ == "__main__":
    raise SystemExit(main())
