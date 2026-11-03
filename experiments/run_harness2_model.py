from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness2.core.proposer import Proposer
from harness2.core.schedule import mode_token, run_domain
from harness2.core.spec import resolve_channel_model
from harness2.core.spec import ChannelSpec

from retailops_agent.harness2_adapter import DeepSeekRetailOpsProposer, RetailOpsHarnessAdapter
from retailops_agent.llm import OpenAICompatibleModel


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one budgeted Harness2 experiment with a real model editor."
    )
    parser.add_argument("--channel", choices=("deepseek", "opencode", "claude_cli"), required=True)
    parser.add_argument("--model")
    parser.add_argument("--mode", choices=("parallel", "sequential"), default="parallel")
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("runs/harness2-model"))
    parser.add_argument(
        "--tasks", type=Path, default=Path("benchmarks/retailops/tasks")
    )
    parser.add_argument(
        "--only-task",
        help="run exactly one task id from --tasks (useful for a low-cost smoke run)",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="disable the default base-policy preflight and early stop",
    )
    args = parser.parse_args()
    if args.k < 1:
        parser.error("--k must be positive")

    project_root = Path(__file__).resolve().parents[1]
    if args.channel == "deepseek":
        direct_model = OpenAICompatibleModel.deepseek_from_env()
        channel = ChannelSpec("opencode", f"deepseek/{direct_model.model}")
    else:
        if not args.model:
            parser.error("--model is required for opencode and claude_cli")
        direct_model = None
        channel = resolve_channel_model(args.channel, args.model)
    adapter = RetailOpsHarnessAdapter(
        args.output,
        args.tasks,
        project_root,
        channel=channel,
    )
    proposer = (
        DeepSeekRetailOpsProposer(channel, direct_model, audit_dir=args.output / "audit")
        if direct_model is not None
        else Proposer(channel, audit_dir=args.output / "audit")
    )
    tasks = adapter.tasks()
    if args.only_task:
        if args.only_task not in tasks:
            parser.error(
                f"unknown --only-task {args.only_task!r}; available tasks: {', '.join(tasks)}"
            )
        tasks = [args.only_task]
    preflight_results: list[dict[str, object]] = []
    improvement_tasks = list(tasks)
    retained_tasks: list[str] = []
    if not args.no_preflight:
        improvement_tasks = []
        for task_id in tasks:
            run_dir = adapter.execute(task_id, None, f"preflight/{task_id}")
            score = adapter.score(run_dir, task_id) or {"pass": False}
            passed = bool(score.get("pass"))
            preflight_results.append(
                {
                    "task": task_id,
                    "ok": passed,
                    "run_dir": str(run_dir),
                    "issue_count": score.get("issue_count"),
                }
            )
            if passed:
                retained_tasks.append(task_id)
            else:
                improvement_tasks.append(task_id)
    mode = mode_token(args.mode)
    if improvement_tasks:
        result = run_domain(
            "retail-analytics",
            improvement_tasks,
            args.k,
            adapter=adapter,
            prop=proposer,
            runs_root=adapter.spec.runs_root,
            library_root=args.output / "library" / f"k{args.k}",
            modes=(mode,),
        )
    else:
        result = {
            "domain": "retail-analytics",
            "n_tasks": 0,
            "seed": None,
            "done": 0,
            "failed": 0,
            "tasks": [],
        }
    result["harness_done"] = result["done"]
    result["harness_task_count"] = result["n_tasks"]
    result["done"] += len(retained_tasks)
    result["n_tasks"] = len(tasks)
    result["execution_count"] = adapter.execution_count
    result["proposer_calls"] = len(proposer._calls)
    result["model_calls"] = (
        direct_model.request_count if direct_model is not None else len(proposer._calls)
    )
    if direct_model is not None:
        result["model_usage"] = direct_model.total_usage()
        result["model_responses"] = direct_model.response_history
    result["mode"] = args.mode
    result["tasks"] = tasks
    result["improvement_tasks"] = improvement_tasks
    result["retained_tasks"] = retained_tasks
    result["preflight_results"] = preflight_results
    report = args.output / "result.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report)
    return int(result["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
