from __future__ import annotations

import argparse
import json
from pathlib import Path

from retailops_agent.harness_runner import execute_policy
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.models import ExecutionPolicy


ALLOWED = {
    "refund_rate_denominator": {"order_lines", "distinct_orders"},
    "category_refund_allocation": {"first_category", "proportional"},
    "refund_time_attribution": {"refund_date", "order_date"},
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate one-shot model policy selection without validator feedback."
    )
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    model = OpenAICompatibleModel.deepseek_from_env()
    rows: list[dict[str, object]] = []
    artifact_root = args.output.parent / "one-shot-answers"
    for task_dir in sorted(path for path in args.tasks.iterdir() if path.is_dir()):
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        expected = json.loads((task_dir / "expected.json").read_text(encoding="utf-8"))
        payload = model.complete_json(
            system=(
                "你是经营分析策略选择器。只能根据用户任务文本选择三个策略字段，"
                "没有执行结果或校验反馈。返回非空JSON对象，例如："
                '{"refund_rate_denominator":"distinct_orders",'
                '"category_refund_allocation":"proportional",'
                '"refund_time_attribution":"refund_date"}。'
                "refund_rate_denominator只能是order_lines或distinct_orders；"
                "category_refund_allocation只能是first_category或proportional；"
                "refund_time_attribution只能是refund_date或order_date。"
            ),
            user=json.dumps(
                {
                    "instruction": task["instruction"],
                    "current_policy": ExecutionPolicy.naive().to_dict(),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        selected: dict[str, str] = {}
        error = ""
        for field, allowed in ALLOWED.items():
            value = str(payload.get(field, ""))
            if value not in allowed:
                error = f"invalid {field}: {value!r}"
                break
            selected[field] = value
        if error:
            rows.append(
                {
                    "task": task_dir.name,
                    "scenario": expected["scenario"],
                    "ok": False,
                    "oracle_ok": False,
                    "error": error,
                    "model_response": payload,
                }
            )
            continue
        answer_path = artifact_root / task_dir.name / "answer.json"
        answer = execute_policy(
            orders_path=task_dir / "orders.csv",
            refunds_path=task_dir / "refunds.csv",
            task_path=task_dir / "task.json",
            output_path=answer_path,
            policy=ExecutionPolicy(**selected),
        )
        serialized_answer = json.loads(answer_path.read_text(encoding="utf-8"))
        rows.append(
            {
                "task": task_dir.name,
                "scenario": expected["scenario"],
                "ok": bool(answer["ok"]),
                "oracle_ok": serialized_answer["results"] == expected["results"],
                "selected_policy": selected,
                "expected_policy": expected["metric_contract"],
                "issues": [issue["code"] for issue in answer["issues"]],
                "error": "",
            }
        )

    result = {
        "benchmark": args.tasks.name,
        "task_count": len(rows),
        "passed": sum(bool(row["ok"]) for row in rows),
        "oracle_passed": sum(bool(row["oracle_ok"]) for row in rows),
        "model_calls": model.request_count,
        "model_usage": model.total_usage(),
        "model_responses": model.response_history,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"one_shot={result['passed']}/{result['task_count']}, "
        f"oracle={result['oracle_passed']}/{result['task_count']}, "
        f"calls={result['model_calls']} -> {args.output}"
    )
    return int(result["passed"] != result["task_count"])


if __name__ == "__main__":
    raise SystemExit(main())
