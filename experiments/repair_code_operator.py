from __future__ import annotations

import argparse
import json
from pathlib import Path

from retailops_agent.llm import OpenAICompatibleModel

try:
    from .run_code_operator_experiment import _strip_fence, evaluate_split
except ImportError:  # pragma: no cover - direct script execution
    from run_code_operator_experiment import _strip_fence, evaluate_split


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair one rejected code operator using exact static-critic feedback."
    )
    parser.add_argument(
        "--suite", type=Path, default=Path("benchmarks/retailops/code-evolution-v1")
    )
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    previous_source = (args.previous / "candidate.py").read_text(encoding="utf-8")
    previous_result = json.loads(
        (args.previous / "result.json").read_text(encoding="utf-8")
    )
    critic_errors = sorted(
        {
            str(row["error"])
            for row in previous_result["candidate"]["evolution"]["rows"]
            if row.get("error")
        }
    )
    model = OpenAICompatibleModel.deepseek_from_env()
    response = model.complete_json(
        system=(
            "Repair a rejected restricted Python plugin. Return JSON with string fields code and "
            "reason. Keep exactly one function named transform_refunds with arguments "
            "(order_totals_cents, refunds). No imports, decorators, annotations, helper functions, "
            "classes, exceptions or comprehensions. Allowed calls: dict, list, int, str, min, max, "
            "sum, len, range, enumerate, sorted and dict/list methods get, setdefault, append, "
            "copy, items, keys, values. The input schema guarantees refund_cents is an integer, so "
            "do not call type or isinstance. Preserve the correct business logic and change only "
            "what the critic rejected. Do not use Markdown fences inside code."
        ),
        user=json.dumps(
            {
                "previous_code": previous_source,
                "static_critic_errors": critic_errors,
                "goal": (
                    "cap cumulative confirmed refunds per order at order_totals_cents, process in "
                    "input order, clip the boundary row and skip later excess rows"
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    source = _strip_fence(str(response.get("code", "")))
    (args.output / "candidate.py").write_text(source + "\n", encoding="utf-8")
    (args.output / "proposal.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    baseline = {
        split: evaluate_split(args.suite, split, None)
        for split in ("evolution", "validation")
    }
    candidate = {
        split: evaluate_split(args.suite, split, source)
        for split in ("evolution", "validation")
    }
    improved = all(
        candidate[split]["passed"] > baseline[split]["passed"]
        for split in ("evolution", "validation")
    )
    transfer = evaluate_split(args.suite, "transfer", source) if improved else None
    result = {
        "parent": str(args.previous),
        "critic_errors": critic_errors,
        "baseline": baseline,
        "candidate": candidate,
        "improved": improved,
        "transfer": transfer,
        "model_calls": model.request_count,
        "model_usage": model.total_usage(),
        "response_reason": response.get("reason", ""),
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    transfer_text = (
        f"{transfer['passed']}/{transfer['task_count']}" if transfer else "not_opened"
    )
    print(
        f"repaired_evolution={candidate['evolution']['passed']}/{candidate['evolution']['task_count']}, "
        f"validation={candidate['validation']['passed']}/{candidate['validation']['task_count']}, "
        f"transfer={transfer_text}, calls={model.request_count} -> {args.output}"
    )
    return int(not improved)


if __name__ == "__main__":
    raise SystemExit(main())
