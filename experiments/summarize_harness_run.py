from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(run_root: Path, tasks_root: Path, output: Path) -> dict[str, object]:
    result = read_json(run_root / "result.json")
    rows: list[dict[str, object]] = []
    summarized_tasks: set[str] = set()
    for summary_path in sorted((run_root / "runs").glob("**/summary.json")):
        task_summary = read_json(summary_path)
        task_id = str(task_summary["task_id"])
        summarized_tasks.add(task_id)
        base_dir = Path(str(task_summary["base"][0]["run_dir"]))
        selection = next(iter(task_summary["selections"].values()))
        final_dir = Path(str(selection["chosen_run_dir"]))
        base_answer = read_json(base_dir / "answer.json")
        final_answer = read_json(final_dir / "answer.json")
        expected = read_json(tasks_root / task_id / "expected.json")
        base_ok = bool(base_answer["ok"])
        final_ok = bool(final_answer["ok"])
        oracle_ok = final_answer["results"] == expected["results"]
        rows.append(
            {
                "task": task_id,
                "scenario": expected["scenario"],
                "base_ok": base_ok,
                "final_ok": final_ok,
                "oracle_ok": oracle_ok,
                "improved": not base_ok and final_ok,
                "retained_correct_base": base_ok
                and final_ok
                and bool(selection.get("chosen_tree_is_base")),
                "initial_issues": [issue["code"] for issue in base_answer["issues"]],
                "final_issues": [issue["code"] for issue in final_answer["issues"]],
                "base_policy": base_answer["policy"],
                "final_policy": final_answer["policy"],
                "chosen_tree_is_base": bool(selection.get("chosen_tree_is_base")),
            }
        )

    for preflight in result.get("preflight_results", []):
        task_id = str(preflight["task"])
        if task_id in summarized_tasks or not preflight.get("ok"):
            continue
        answer = read_json(Path(str(preflight["run_dir"])) / "answer.json")
        expected = read_json(tasks_root / task_id / "expected.json")
        oracle_ok = answer["results"] == expected["results"]
        rows.append(
            {
                "task": task_id,
                "scenario": expected["scenario"],
                "base_ok": True,
                "final_ok": True,
                "oracle_ok": oracle_ok,
                "improved": False,
                "retained_correct_base": True,
                "initial_issues": [],
                "final_issues": [],
                "base_policy": answer["policy"],
                "final_policy": answer["policy"],
                "chosen_tree_is_base": True,
                "early_stopped": True,
            }
        )

    rows.sort(key=lambda row: row["task"])

    responses = result.get("model_responses", [])
    task_count = len(rows)
    summary = {
        "benchmark": tasks_root.name,
        "task_count": task_count,
        "base_passed": sum(row["base_ok"] for row in rows),
        "final_passed": sum(row["final_ok"] for row in rows),
        "oracle_passed": sum(row["oracle_ok"] for row in rows),
        "improved": sum(row["improved"] for row in rows),
        "retained_correct_base": sum(row["retained_correct_base"] for row in rows),
        "regressed": sum(row["base_ok"] and not row["final_ok"] for row in rows),
        "execution_count": result["execution_count"],
        "preflight_retained": len(result.get("retained_tasks", [])),
        "model_calls": result["model_calls"],
        "model_usage": result.get("model_usage", {}),
        "plain_text_fallbacks": sum(
            not bool(response.get("json_mode")) for response in responses
        ),
        "average_model_calls_per_task": result["model_calls"] / task_count,
        "average_tokens_per_task": result.get("model_usage", {}).get("total_tokens", 0)
        / task_count,
        "rows": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize and externally verify a Harness2 run.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(args.run_root, args.tasks, args.output)
    print(
        f"base={summary['base_passed']}/{summary['task_count']}, "
        f"final={summary['final_passed']}/{summary['task_count']}, "
        f"oracle={summary['oracle_passed']}/{summary['task_count']}, "
        f"improved={summary['improved']}, retained={summary['retained_correct_base']}"
    )
    return int(
        summary["final_passed"] != summary["task_count"]
        or summary["oracle_passed"] != summary["task_count"]
        or summary["regressed"] != 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
