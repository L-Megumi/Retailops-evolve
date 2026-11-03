from __future__ import annotations

import argparse
import json
from pathlib import Path

from retailops_agent.agent import RetailOpsAgent
from retailops_agent.harness_runner import execute_policy
from retailops_agent.models import ExecutionPolicy


def evaluate(task_root: Path, output: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for task_dir in sorted(path for path in task_root.iterdir() if path.is_dir()):
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        naive_path = output.parent / "artifacts" / task_dir.name / "naive.json"
        naive = execute_policy(
            orders_path=task_dir / "orders.csv",
            refunds_path=task_dir / "refunds.csv",
            task_path=task_dir / "task.json",
            output_path=naive_path,
            policy=ExecutionPolicy.naive(),
        )
        repaired = RetailOpsAgent(initial_policy=ExecutionPolicy.naive()).run(
            str(task["instruction"]),
            task_dir / "orders.csv",
            task_dir / "refunds.csv",
        )
        rows.append(
            {
                "task": task_dir.name,
                "naive_ok": naive["ok"],
                "naive_issues": [issue["code"] for issue in naive["issues"]],
                "repaired_ok": repaired.ok,
                "repair_attempts": repaired.attempts,
                "final_issues": [issue.code for issue in repaired.issues],
                "final_policy": repaired.policies[-1].to_dict(),
            }
        )
    summary = {
        "task_count": len(rows),
        "naive_passed": sum(bool(row["naive_ok"]) for row in rows),
        "repaired_passed": sum(bool(row["repaired_ok"]) for row in rows),
        "rows": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tasks", type=Path, default=Path("benchmarks/retailops/tasks")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("runs/evaluation/strategy-comparison.json")
    )
    args = parser.parse_args()
    summary = evaluate(args.tasks, args.output)
    print(
        f"naive={summary['naive_passed']}/{summary['task_count']}, "
        f"repaired={summary['repaired_passed']}/{summary['task_count']} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

