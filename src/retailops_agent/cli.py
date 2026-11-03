from __future__ import annotations

import argparse
from pathlib import Path

from .agent import RetailOpsAgent
from .llm import OpenAICompatibleModel
from .planner import ModelPlanner, RulePlanner
from .models import ExecutionPolicy
from .plugin_manager import PluginManager
from .report import render_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retailops")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run an analysis task")
    run.add_argument("--orders", type=Path, required=True)
    run.add_argument("--refunds", type=Path, required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--output", type=Path)
    run.add_argument("--planner", choices=("rule", "model"), default="rule")
    run.add_argument(
        "--initial-policy",
        choices=("verified", "naive"),
        default="verified",
        help="use naive to demonstrate validator-driven repair",
    )
    run.add_argument(
        "--plugin-registry",
        type=Path,
        help="load a versioned code-plugin registry manifest",
    )
    run.add_argument(
        "--plugin-semantic-router",
        action="store_true",
        help="use DeepSeek as a semantic fallback for unmatched plugin routes",
    )
    run.add_argument(
        "--plugin-route-cache",
        type=Path,
        help="persistent semantic-route cache (defaults next to the registry)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    planner = (
        ModelPlanner(OpenAICompatibleModel.from_env())
        if args.planner == "model"
        else RulePlanner()
    )
    initial_policy = ExecutionPolicy.naive() if args.initial_policy == "naive" else ExecutionPolicy()
    plugin_manager = None
    if args.plugin_registry:
        semantic_model = (
            OpenAICompatibleModel.deepseek_from_env()
            if args.plugin_semantic_router
            else None
        )
        cache_path = args.plugin_route_cache or args.plugin_registry.with_name(
            "route-cache.json"
        )
        plugin_manager = PluginManager.from_manifest(
            args.plugin_registry,
            semantic_model=semantic_model,
            cache_path=cache_path,
        )
    trace = RetailOpsAgent(
        planner,
        initial_policy=initial_policy,
        plugin_manager=plugin_manager,
    ).run(
        args.task, args.orders, args.refunds
    )
    report = render_markdown(trace)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(args.output)
    else:
        print(report)
    return 0 if trace.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

