from __future__ import annotations

import argparse
import os
from pathlib import Path

from .agent import RetailOpsAgent
from .llm import OpenAICompatibleModel
from .planner import ModelPlanner, RulePlanner
from .models import ExecutionPolicy
from .plugin_manager import PluginManager
from .report import render_markdown
from .evolution import CapabilityEvolver


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retailops")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run an analysis task")
    run.add_argument("--orders", type=Path, required=True)
    run.add_argument("--refunds", type=Path, required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--output", type=Path)
    run.add_argument("--planner", choices=("rule", "model"), default="rule")
    run.add_argument("--required-result", action="append", default=[], help="explicit output contract; may be repeated")
    run.add_argument("--evolve", action="store_true", help="generate missing metrics with trusted verifiers")
    run.add_argument("--evolution-attempts", type=int, choices=(1, 2, 3), default=2)
    run.add_argument("--evolution-artifacts", type=Path, default=Path("runs/online-evolution"))
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
    serve = sub.add_parser("serve", help="serve durable Agent tasks and SSE trace events")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--data-root", type=Path, default=Path("data"))
    serve.add_argument("--plugin-registry", type=Path, default=Path("plugins/registry.json"))
    serve.add_argument("--state", type=Path, default=Path("state/tasks.sqlite3"))
    serve.add_argument("--allow-evolution", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "serve":
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("RETAILOPS_SERVICE_TOKEN"):
            raise SystemExit("set RETAILOPS_SERVICE_TOKEN before binding a non-loopback address")
        try:
            import uvicorn
            from .service import create_app
        except ImportError as exc:
            raise SystemExit("install the service extra: pip install '.[service]'") from exc
        uvicorn.run(create_app(data_root=args.data_root, manifest=args.plugin_registry,
                               state_path=args.state, allow_evolution=args.allow_evolution),
                    host=args.host, port=args.port)
        return 0
    if args.evolve and not args.plugin_registry:
        raise SystemExit("--evolve requires --plugin-registry")
    model = None
    if args.planner == "model" or args.plugin_semantic_router or args.evolve:
        model = (OpenAICompatibleModel.deepseek_from_env() if os.environ.get("DEEPSEEK_API_KEY")
                 else OpenAICompatibleModel.from_env())
    planner = (
        ModelPlanner(model)
        if args.planner == "model"
        else RulePlanner()
    )
    initial_policy = ExecutionPolicy.naive() if args.initial_policy == "naive" else ExecutionPolicy()
    plugin_manager = None
    if args.plugin_registry:
        semantic_model = (
            model
            if args.plugin_semantic_router or args.evolve
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
        evolver=CapabilityEvolver(model, args.evolution_artifacts, args.evolution_attempts) if args.evolve else None,
    ).run(
        args.task, args.orders, args.refunds, required_results=tuple(args.required_result)
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

