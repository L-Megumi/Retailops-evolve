from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Rescore persisted one-shot answers.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = json.loads((args.run_root / "result.json").read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for row in result["rows"]:
        task_id = row["task"]
        answer = json.loads(
            (args.run_root / "one-shot-answers" / task_id / "answer.json").read_text(
                encoding="utf-8"
            )
        )
        expected = json.loads(
            (args.tasks / task_id / "expected.json").read_text(encoding="utf-8")
        )
        updated = dict(row)
        updated["oracle_ok"] = answer["results"] == expected["results"]
        updated["policy_exact"] = row.get("selected_policy") == expected["metric_contract"]
        rows.append(updated)

    summary = {
        "benchmark": result["benchmark"],
        "task_count": len(rows),
        "passed": sum(bool(row["ok"]) for row in rows),
        "oracle_passed": sum(bool(row["oracle_ok"]) for row in rows),
        "policy_exact": sum(bool(row["policy_exact"]) for row in rows),
        "model_calls": result["model_calls"],
        "model_usage": result["model_usage"],
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"one_shot={summary['passed']}/{summary['task_count']}, "
        f"oracle={summary['oracle_passed']}/{summary['task_count']}, "
        f"policy={summary['policy_exact']}/{summary['task_count']}"
    )
    return int(
        summary["passed"] != summary["task_count"]
        or summary["oracle_passed"] != summary["task_count"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
