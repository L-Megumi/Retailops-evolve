from __future__ import annotations

import argparse
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from retailops_agent.data import load_orders, load_refunds
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.models import ExecutionPolicy, Refund
from retailops_agent.plugin_sandbox import UnsafePluginError, run_refund_plugin
from retailops_agent.tools import execute_tool


def _normalize(value: object) -> object:
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, str):
        try:
            number = Decimal(value)
        except (InvalidOperation, ValueError):
            return value
        text = format(number, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _strip_fence(source: str) -> str:
    text = source.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline >= 0 else text
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _plugin_inputs(task_dir: Path) -> tuple[dict[str, int], list[dict[str, object]]]:
    orders = load_orders(task_dir / "orders.csv")
    refunds = load_refunds(task_dir / "refunds.csv")
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for order in orders:
        totals[order.order_id] += order.gross_amount
    order_totals_cents = {
        order_id: int(amount * 100) for order_id, amount in totals.items()
    }
    refund_rows = [
        {
            "refund_id": refund.refund_id,
            "order_id": refund.order_id,
            "refund_date": refund.refund_date,
            "refund_cents": int(refund.refund_amount * 100),
        }
        for refund in refunds
    ]
    return order_totals_cents, refund_rows


def evaluate_split(
    suite: Path,
    split: str,
    source: str | None,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for task_dir in sorted(path for path in (suite / "tasks" / split).iterdir() if path.is_dir()):
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        oracle = json.loads(
            (suite / "oracle" / split / f"{task_dir.name}.json").read_text(encoding="utf-8")
        )
        orders = load_orders(task_dir / "orders.csv")
        refunds = load_refunds(task_dir / "refunds.csv")
        error = ""
        if source is not None:
            try:
                totals, refund_rows = _plugin_inputs(task_dir)
                transformed = run_refund_plugin(
                    source,
                    order_totals_cents=totals,
                    refunds=refund_rows,
                )
                refunds = [
                    Refund(
                        refund_id=str(row["refund_id"]),
                        order_id=str(row["order_id"]),
                        refund_date=str(row["refund_date"]),
                        refund_amount=Decimal(int(row["refund_cents"])) / Decimal(100),
                    )
                    for row in transformed
                ]
            except UnsafePluginError as exc:
                error = str(exc)
        actual: dict[str, object] = {}
        if not error:
            policy = ExecutionPolicy(**oracle["metric_contract"])
            actual = {
                name: _normalize(execute_tool(name, orders, refunds, policy).data)
                for name in task["tools"]
            }
        expected = _normalize(oracle["results"])
        ok = not error and actual == expected
        rows.append(
            {
                "task": task_dir.name,
                "ok": ok,
                "error": error,
                "actual": actual if not ok and not error else None,
            }
        )
    return {
        "split": split,
        "passed": sum(bool(row["ok"]) for row in rows),
        "task_count": len(rows),
        "errors": sum(bool(row["error"]) for row in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="First AST-restricted LLM-generated code-operator experiment."
    )
    parser.add_argument(
        "--suite", type=Path, default=Path("benchmarks/retailops/code-evolution-v1")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/code-evolution-v1/attempt-1")
    )
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    baseline = {
        split: evaluate_split(args.suite, split, None)
        for split in ("evolution", "validation")
    }
    source_task = sorted((args.suite / "tasks" / "evolution").iterdir())[0]
    task = json.loads((source_task / "task.json").read_text(encoding="utf-8"))
    model = OpenAICompatibleModel.deepseek_from_env()
    response = model.complete_json(
        system=(
            "Write one small pure Python function for a restricted retail plugin. Return JSON "
            "with string fields code and reason. code must define exactly "
            "def transform_refunds(order_totals_cents, refunds): with no imports, decorators, "
            "annotations, classes, exceptions, comprehensions or helper functions. Inputs are a "
            "dict order_id->gross cents and a list of refund dicts containing refund_id, order_id, "
            "refund_date, refund_cents. Return refund dicts in input order with positive integer "
            "refund_cents. Use only dict/list/int/str/min/max/sum/len/range/enumerate/sorted and "
            "safe dict/list methods get, setdefault, append, copy, items, keys, values. All money "
            "is integer cents. Do not use Markdown fences inside code."
        ),
        user=json.dumps(
            {
                "instruction": task["instruction"],
                "external_judge": "current pipeline over-counts refunds and fails the hidden oracle",
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
        f"baseline={baseline['evolution']['passed']}/{baseline['evolution']['task_count']}, "
        f"candidate_evolution={candidate['evolution']['passed']}/{candidate['evolution']['task_count']}, "
        f"validation={candidate['validation']['passed']}/{candidate['validation']['task_count']}, "
        f"transfer={transfer_text}, calls={model.request_count} -> {args.output}"
    )
    return int(not improved)


if __name__ == "__main__":
    raise SystemExit(main())
