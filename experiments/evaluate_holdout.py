from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from retailops_agent.agent import RetailOpsAgent
from retailops_agent.harness_runner import execute_policy
from retailops_agent.models import ExecutionPolicy


def normalize(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def evaluate(task_root: Path, output: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    artifact_root = output.parent / "artifacts"
    for task_dir in sorted(path for path in task_root.iterdir() if path.is_dir()):
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        expected = json.loads((task_dir / "expected.json").read_text(encoding="utf-8"))
        naive = execute_policy(
            orders_path=task_dir / "orders.csv",
            refunds_path=task_dir / "refunds.csv",
            task_path=task_dir / "task.json",
            output_path=artifact_root / task_dir.name / "naive.json",
            policy=ExecutionPolicy.naive(),
        )
        trace = RetailOpsAgent(initial_policy=ExecutionPolicy.naive()).run(
            str(task["instruction"]),
            task_dir / "orders.csv",
            task_dir / "refunds.csv",
            metric_contract=task.get("metric_contract"),
        )
        actual = {name: normalize(result.data) for name, result in trace.results.items()}
        oracle_ok = actual == expected["results"]
        rows.append(
            {
                "task": task_dir.name,
                "scenario": expected["scenario"],
                "metric_contract": expected.get("metric_contract"),
                "naive_ok": naive["ok"],
                "naive_issues": [issue["code"] for issue in naive["issues"]],
                "repaired_ok": trace.ok,
                "oracle_ok": oracle_ok,
                "repair_attempts": trace.attempts,
                "final_issues": [issue.code for issue in trace.issues],
                "final_policy": trace.policies[-1].to_dict(),
            }
        )
    summary = {
        "benchmark": task_root.name,
        "task_count": len(rows),
        "naive_passed": sum(bool(row["naive_ok"]) for row in rows),
        "repaired_passed": sum(bool(row["repaired_ok"]) for row in rows),
        "oracle_passed": sum(bool(row["oracle_ok"]) for row in rows),
        "rows": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the frozen holdout benchmark.")
    parser.add_argument(
        "--tasks", type=Path, default=Path("benchmarks/retailops/holdout-v1")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("runs/holdout-v1/strategy-comparison.json")
    )
    args = parser.parse_args()
    summary = evaluate(args.tasks, args.output)
    print(
        f"naive={summary['naive_passed']}/{summary['task_count']}, "
        f"repaired={summary['repaired_passed']}/{summary['task_count']}, "
        f"oracle={summary['oracle_passed']}/{summary['task_count']} -> {args.output}"
    )
    return int(
        summary["repaired_passed"] != summary["task_count"]
        or summary["oracle_passed"] != summary["task_count"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
