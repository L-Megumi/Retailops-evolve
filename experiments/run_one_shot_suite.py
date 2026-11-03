from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path

from retailops_agent.data import load_orders, load_refunds
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.models import ExecutionPolicy
from retailops_agent.tools import execute_tool


ALLOWED = {
    "refund_rate_denominator": {"order_lines", "distinct_orders"},
    "category_refund_allocation": {"first_category", "proportional"},
    "refund_time_attribution": {"refund_date", "order_date"},
}


def _normalize(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _summarize(
    *,
    suite_name: str,
    splits: list[str],
    repeats: int,
    rows: list[dict[str, object]],
    model_calls: int,
    model_usage: dict[str, int],
) -> dict[str, object]:
    by_split = {
        split: {
            "runs": sum(row["split"] == split for row in rows),
            "oracle_passed": sum(
                row["split"] == split and bool(row["oracle_ok"]) for row in rows
            ),
            "policy_exact": sum(
                row["split"] == split and bool(row["policy_exact"]) for row in rows
            ),
            "errors": sum(row["split"] == split and bool(row["error"]) for row in rows),
        }
        for split in splits
    }
    return {
        "benchmark": suite_name,
        "splits": splits,
        "repeats": repeats,
        "runs": len(rows),
        "oracle_passed": sum(bool(row["oracle_ok"]) for row in rows),
        "policy_exact": sum(bool(row["policy_exact"]) for row in rows),
        "errors": sum(bool(row["error"]) for row in rows),
        "model_calls": model_calls,
        "model_usage": model_usage,
        "by_split": by_split,
        "rows": rows,
    }


def _write_checkpoint(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a no-feedback DeepSeek policy baseline against the isolated RSI suite."
    )
    parser.add_argument(
        "--suite", type=Path, default=Path("benchmarks/retailops/rsi-v1")
    )
    parser.add_argument(
        "--splits",
        nargs="+",
        default=["evolution", "validation", "transfer"],
        choices=["regression", "evolution", "validation", "transfer"],
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--case-kind",
        choices=["all", "contract_selection", "capability_expansion"],
        default="all",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume completed repeat/split/task rows from the output checkpoint",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/rsi-v1/one-shot.json")
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be positive")

    model = OpenAICompatibleModel.deepseek_from_env()
    rows: list[dict[str, object]] = []
    previous_calls = 0
    previous_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if args.resume and args.output.is_file():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        rows = list(previous.get("rows", []))
        previous_calls = int(previous.get("model_calls", 0))
        for key in previous_usage:
            previous_usage[key] = int(previous.get("model_usage", {}).get(key, 0))
    completed = {
        (int(row["repeat"]), str(row["split"]), str(row["task"])) for row in rows
    }
    system = (
        "你是经营分析策略选择器。只能根据用户任务文本选择三个策略字段，"
        "没有执行结果、隐藏合同或校验反馈。返回非空JSON对象，例如："
        '{"refund_rate_denominator":"distinct_orders",'
        '"category_refund_allocation":"proportional",'
        '"refund_time_attribution":"refund_date"}。'
        "refund_rate_denominator只能是order_lines或distinct_orders；"
        "category_refund_allocation只能是first_category或proportional；"
        "refund_time_attribution只能是refund_date或order_date。"
    )
    for repeat in range(1, args.repeats + 1):
        for split in args.splits:
            for task_dir in sorted(path for path in (args.suite / "tasks" / split).iterdir() if path.is_dir()):
                identity = (repeat, split, task_dir.name)
                if identity in completed:
                    continue
                task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
                metadata = task["benchmark_metadata"]
                if (
                    args.case_kind != "all"
                    and metadata.get("case_kind", "contract_selection") != args.case_kind
                ):
                    continue
                payload: dict[str, object] = {}
                request_error = ""
                for attempt in range(1, args.max_attempts + 1):
                    try:
                        payload = model.complete_json(
                            system=system,
                            user=json.dumps(
                                {
                                    "instruction": task["instruction"],
                                    "current_policy": ExecutionPolicy.naive().to_dict(),
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        )
                        request_error = ""
                        break
                    except (OSError, RuntimeError, ValueError) as exc:
                        request_error = f"{type(exc).__name__}: {exc}"
                        if attempt < args.max_attempts:
                            time.sleep(min(2**attempt, 8))
                selected: dict[str, str] = {}
                error = request_error
                if not error:
                    for field, allowed in ALLOWED.items():
                        value = str(payload.get(field, ""))
                        if value not in allowed:
                            error = f"invalid {field}: {value!r}"
                            break
                        selected[field] = value
                oracle = json.loads(
                    (args.suite / "oracle" / split / f"{task_dir.name}.json").read_text(
                        encoding="utf-8"
                    )
                )
                oracle_ok = False
                policy_exact = False
                if not error:
                    orders = load_orders(task_dir / "orders.csv")
                    refunds = load_refunds(task_dir / "refunds.csv")
                    policy = ExecutionPolicy(**selected)
                    actual = {
                        name: _normalize(execute_tool(name, orders, refunds, policy).data)
                        for name in task["tools"]
                    }
                    oracle_ok = actual == oracle["results"]
                    policy_exact = all(
                        selected[field] == oracle["metric_contract"][field]
                        for field in oracle["relevant_contract_fields"]
                    )
                rows.append(
                    {
                        "repeat": repeat,
                        "task": task_dir.name,
                        "split": split,
                        "scenario": oracle["scenario"],
                        "case_kind": metadata.get("case_kind", "contract_selection"),
                        "capability_family": (
                            oracle["scenario"]
                            if metadata.get("case_kind") == "capability_expansion"
                            else "none"
                        ),
                        "instruction_style": metadata["instruction_style"],
                        "data_profile": metadata["data_profile"],
                        "oracle_ok": oracle_ok,
                        "policy_exact": policy_exact,
                        "selected_policy": selected,
                        "error": error,
                    }
                )
                current_usage = model.total_usage()
                total_usage = {
                    key: previous_usage[key] + current_usage[key] for key in previous_usage
                }
                result = _summarize(
                    suite_name=args.suite.name,
                    splits=args.splits,
                    repeats=args.repeats,
                    rows=rows,
                    model_calls=previous_calls + model.request_count,
                    model_usage=total_usage,
                )
                _write_checkpoint(args.output, result)

    current_usage = model.total_usage()
    result = _summarize(
        suite_name=args.suite.name,
        splits=args.splits,
        repeats=args.repeats,
        rows=rows,
        model_calls=previous_calls + model.request_count,
        model_usage={
            key: previous_usage[key] + current_usage[key] for key in previous_usage
        },
    )
    _write_checkpoint(args.output, result)
    print(
        f"one_shot={result['oracle_passed']}/{result['runs']}, "
        f"policy={result['policy_exact']}/{result['runs']}, errors={result['errors']}, "
        f"calls={result['model_calls']} "
        f"-> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
