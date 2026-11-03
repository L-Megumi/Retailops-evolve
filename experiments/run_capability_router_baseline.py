from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path

from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.models import ExecutionPolicy
from retailops_agent.strategy_library import (
    ACTION_DESCRIPTIONS,
    StrategyLibrary,
    StrategyRule,
    execute_with_library,
)


def _normalize(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _write_checkpoint(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _summary(
    rows: list[dict[str, object]],
    model: OpenAICompatibleModel,
) -> dict[str, object]:
    return {
        "runs": len(rows),
        "oracle_passed": sum(bool(row["oracle_ok"]) for row in rows),
        "action_exact": sum(bool(row["action_exact"]) for row in rows),
        "errors": sum(bool(row["error"]) for row in rows),
        "model_calls": model.request_count,
        "model_usage": model.total_usage(),
        "persistent_memory": False,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Per-task capability router baseline with the same action space but no memory."
    )
    parser.add_argument(
        "--suite", type=Path, default=Path("benchmarks/retailops/rsi-v2")
    )
    parser.add_argument(
        "--splits",
        nargs="+",
        default=["evolution", "validation", "transfer"],
        choices=["evolution", "validation", "transfer"],
    )
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/rsi-v2/capability-router-one-shot.json"),
    )
    args = parser.parse_args()
    if args.max_attempts < 1:
        parser.error("--max-attempts must be positive")

    model = OpenAICompatibleModel.deepseek_from_env()
    action_text = "\n".join(
        f"- {name}: {description}" for name, description in ACTION_DESCRIPTIONS.items()
    )
    system = (
        "Select exactly one safe retail capability action for the task. Return a JSON object "
        "with string fields action and reason. action must be one listed name. You have no "
        "memory from earlier tasks and must decide independently for this task.\n" + action_text
    )
    rows: list[dict[str, object]] = []
    for split in args.splits:
        for task_dir in sorted(path for path in (args.suite / "tasks" / split).iterdir() if path.is_dir()):
            task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
            if task["benchmark_metadata"].get("case_kind") != "capability_expansion":
                continue
            payload: dict[str, object] = {}
            error = ""
            for attempt in range(1, args.max_attempts + 1):
                try:
                    payload = model.complete_json(
                        system=system,
                        user=json.dumps(
                            {"instruction": task["instruction"], "tools": task["tools"]},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                    error = ""
                    break
                except (OSError, RuntimeError, ValueError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    if attempt < args.max_attempts:
                        time.sleep(min(2**attempt, 8))

            oracle = json.loads(
                (args.suite / "oracle" / split / f"{task_dir.name}.json").read_text(
                    encoding="utf-8"
                )
            )
            action = str(payload.get("action", ""))
            if not error and action not in ACTION_DESCRIPTIONS:
                error = f"invalid action: {action!r}"
            oracle_ok = False
            if not error:
                library = StrategyLibrary(
                    version=1,
                    rules=(
                        StrategyRule(
                            rule_id="one-shot",
                            cue=str(task["instruction"])[:120],
                            action=action,
                            reason="per-task no-memory baseline",
                            source_task=task_dir.name,
                        ),
                    ),
                )
                execution = execute_with_library(
                    task_dir,
                    ExecutionPolicy(**oracle["metric_contract"]),
                    library,
                )
                actual = {
                    name: _normalize(result.data)
                    for name, result in execution.results.items()
                }
                oracle_ok = actual == oracle["results"]
            expected_action = {
                "equal_allocation": "equal_category_allocation",
                "quantity_allocation": "quantity_category_allocation",
                "deduplicate_refunds": "deduplicate_refunds",
                "conditional_time": "hybrid_refund_time",
            }[oracle["scenario"]]
            rows.append(
                {
                    "task": task_dir.name,
                    "split": split,
                    "action": action,
                    "expected_action": expected_action,
                    "action_exact": action == expected_action,
                    "oracle_ok": oracle_ok,
                    "error": error,
                }
            )
            _write_checkpoint(args.output, _summary(rows, model))

    result = _summary(rows, model)
    _write_checkpoint(args.output, result)
    print(
        f"no_memory_router={result['oracle_passed']}/{result['runs']}, "
        f"actions={result['action_exact']}/{result['runs']}, errors={result['errors']}, "
        f"calls={result['model_calls']} -> {args.output}"
    )
    return int(result["oracle_passed"] != result["runs"])


if __name__ == "__main__":
    raise SystemExit(main())
