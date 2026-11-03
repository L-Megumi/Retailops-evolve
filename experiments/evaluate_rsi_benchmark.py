from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from retailops_agent.data import load_orders, load_refunds
from retailops_agent.models import ExecutionPolicy
from retailops_agent.repair import PolicyRepairer
from retailops_agent.tools import execute_tool
from retailops_agent.validators import validate_results

try:
    from .generate_holdout import calculate_expected
    from .generate_rsi_benchmark import calculate_capability_expected
except ImportError:  # pragma: no cover - direct script execution
    from generate_holdout import calculate_expected
    from generate_rsi_benchmark import calculate_capability_expected


def _normalize(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _execute(
    task_dir: Path,
    tools: list[str],
    policy: ExecutionPolicy,
) -> dict[str, object]:
    orders = load_orders(task_dir / "orders.csv")
    refunds = load_refunds(task_dir / "refunds.csv")
    return {
        name: _normalize(execute_tool(name, orders, refunds, policy).data)
        for name in tools
    }


def _read_csv(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _feedback_repair(
    task_dir: Path,
    tools: list[str],
    contract: dict[str, str],
) -> tuple[dict[str, object], ExecutionPolicy, int]:
    orders = load_orders(task_dir / "orders.csv")
    refunds = load_refunds(task_dir / "refunds.csv")
    policy = ExecutionPolicy.naive()
    repairer = PolicyRepairer()
    results = {}
    attempts = 0
    for attempts in range(1, 4):
        typed_results = {name: execute_tool(name, orders, refunds, policy) for name in tools}
        results = {name: _normalize(result.data) for name, result in typed_results.items()}
        issues = validate_results(orders, refunds, typed_results, contract)
        if not issues:
            break
        repaired, _ = repairer.repair(policy, issues)
        if repaired == policy:
            break
        policy = repaired
    return results, policy, attempts


def evaluate_suite(root: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for task_path in sorted((root / "tasks").glob("*/*/task.json")):
        split = task_path.parents[1].name
        task_dir = task_path.parent
        case_id = task_dir.name
        task = json.loads(task_path.read_text(encoding="utf-8"))
        oracle = json.loads(
            (root / "oracle" / split / f"{case_id}.json").read_text(encoding="utf-8")
        )
        tools = list(task["tools"])
        contract = oracle["metric_contract"]
        expected = oracle["results"]
        naive = _execute(task_dir, tools, ExecutionPolicy.naive())
        default = _execute(task_dir, tools, ExecutionPolicy())
        oracle_policy = _execute(task_dir, tools, ExecutionPolicy(**contract))
        repaired, final_policy, attempts = _feedback_repair(task_dir, tools, contract)
        raw_orders = _read_csv(task_dir / "orders.csv")
        raw_refunds = _read_csv(task_dir / "refunds.csv")
        if oracle.get("capability_contract"):
            recalculated_oracle = calculate_capability_expected(
                raw_orders,
                raw_refunds,
                tools,
                contract,
                oracle["capability_contract"],
            )
        else:
            recalculated_oracle = calculate_expected(
                raw_orders, raw_refunds, tools, contract
            )
        recalculated_oracle = _normalize(recalculated_oracle)
        metadata = task["benchmark_metadata"]
        rows.append(
            {
                "task": case_id,
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
                "naive_ok": naive == expected,
                "default_ok": default == expected,
                "feedback_repair_ok": repaired == expected,
                "representable_oracle_policy_ok": oracle_policy == expected,
                "oracle_ceiling_ok": recalculated_oracle == expected,
                "repair_attempts": attempts,
                "final_policy": final_policy.to_dict(),
            }
        )

    strategies = ("naive_ok", "default_ok", "feedback_repair_ok", "oracle_ceiling_ok")
    by_split: dict[str, dict[str, object]] = {}
    for split in sorted({str(row["split"]) for row in rows}):
        split_rows = [row for row in rows if row["split"] == split]
        by_split[split] = {
            "task_count": len(split_rows),
            **{strategy: sum(bool(row[strategy]) for row in split_rows) for strategy in strategies},
        }
    by_axis: dict[str, dict[str, dict[str, int]]] = {}
    for axis in (
        "case_kind",
        "capability_family",
        "scenario",
        "instruction_style",
        "data_profile",
    ):
        buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            buckets[str(row[axis])].append(row)
        by_axis[axis] = {
            bucket: {
                "task_count": len(bucket_rows),
                **{
                    strategy: sum(bool(row[strategy]) for row in bucket_rows)
                    for strategy in strategies
                },
            }
            for bucket, bucket_rows in sorted(buckets.items())
        }
    return {
        "benchmark": root.name,
        "task_count": len(rows),
        "strategies": {
            strategy: sum(bool(row[strategy]) for row in rows) for strategy in strategies
        },
        "by_split": by_split,
        "by_axis": by_axis,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic baselines on the RSI suite.")
    parser.add_argument(
        "--suite", type=Path, default=Path("benchmarks/retailops/rsi-v1")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/rsi-v1/deterministic-baselines.json"),
    )
    args = parser.parse_args()
    result = evaluate_suite(args.suite)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    scores = result["strategies"]
    print(
        f"tasks={result['task_count']}, naive={scores['naive_ok']}, "
        f"default={scores['default_ok']}, feedback={scores['feedback_repair_ok']}, "
        f"oracle={scores['oracle_ceiling_ok']} -> {args.output}"
    )
    supported_count = result["by_axis"]["case_kind"].get(
        "contract_selection", {"task_count": result["task_count"]}
    )["task_count"]
    return int(
        scores["feedback_repair_ok"] != supported_count
        or scores["oracle_ceiling_ok"] != result["task_count"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
