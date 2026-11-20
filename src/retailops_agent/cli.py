from __future__ import annotations

import argparse
import os
import json
from dataclasses import asdict
from decimal import Decimal
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
    serve.add_argument("--knowledge", type=Path)
    serve.add_argument("--embedding-model", help="optional FastEmbed model; enables dense retrieval")
    serve.add_argument("--retrieval-mode", choices=("bm25", "dense", "hybrid"), default="hybrid")
    ask = sub.add_parser("ask", help="answer scoped policy questions and combined retail analysis")
    ask.add_argument("--task", required=True)
    ask.add_argument("--knowledge", type=Path, default=Path("knowledge/retailops-policies.json"))
    ask.add_argument("--platform", choices=("jd", "suning", "taobao", "tmall", "pinduoduo", "retailops"), action="append", default=[])
    ask.add_argument("--scope", choices=("marketplace", "self_operated", "platform", "internal"))
    ask.add_argument("--as-of", help="policy effective date in YYYY-MM-DD")
    ask.add_argument("--orders", type=Path)
    ask.add_argument("--refunds", type=Path)
    ask.add_argument("--planner", choices=("rule", "model"), default="rule")
    ask.add_argument("--retrieval-mode", choices=("bm25", "dense", "hybrid"), default="hybrid")
    ask.add_argument("--embedding-model", help="e.g. BAAI/bge-small-zh-v1.5; requires the rag extra")
    ask.add_argument("--plugin-registry", type=Path, default=Path("plugins/registry.json"))
    ask.add_argument("--required-result", action="append", default=[])
    ask.add_argument("--evolve", action="store_true")
    ask.add_argument("--output", type=Path)
    review = sub.add_parser('review', help='review shop-scoped after-sales cases with evidence')
    review.add_argument('--casebook', type=Path, required=True)
    review.add_argument('--task', default='核查售后案例并整理证据及待补信息')
    review.add_argument('--case-id', action='append', default=[])
    review.add_argument('--check', action='append', default=[], help='records, amount, freight, materials; unknown names become capability gaps')
    review.add_argument('--knowledge', type=Path, default=Path('knowledge/retailops-policies.json'))
    review.add_argument('--rules', type=Path, default=Path('knowledge/review-rules.json'))
    review.add_argument('--embedding-model')
    review.add_argument('--retrieval-mode', choices=('bm25', 'dense', 'hybrid'), default='hybrid')
    review.add_argument('--planner', choices=('rule', 'model'), default='rule')
    review.add_argument('--max-model-calls', type=int, default=8)
    review.add_argument('--output', type=Path)
    review.add_argument('--report', type=Path, help='write an editable human-readable report')
    for entry in (ask, serve, review):
        entry.add_argument("--qdrant-url", help="attach a published persistent vector snapshot")
        entry.add_argument("--vector-alias", default="retailops_policy")
        entry.add_argument("--reranker-model", help="optional FastEmbed cross-encoder, e.g. BAAI/bge-reranker-base")
    index = sub.add_parser("knowledge-index", help="publish a versioned Qdrant knowledge snapshot")
    index.add_argument("--knowledge", type=Path, default=Path("knowledge/retailops-policies.json"))
    index.add_argument("--embedding-model", default="BAAI/bge-small-zh-v1.5")
    index.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    index.add_argument("--vector-alias", default="retailops_policy")
    index.add_argument("--activate", help="rollback alias to an existing snapshot collection")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == 'review':
        from .case_review import CaseReviewAgent, render_case_review
        from .knowledge import FastEmbedEncoder, FastEmbedReranker, KnowledgeStore
        from .vector_index import QdrantVectorIndex
        encoder = FastEmbedEncoder(args.embedding_model) if args.embedding_model else None
        vector = QdrantVectorIndex(args.qdrant_url, alias=args.vector_alias) if args.qdrant_url else None
        store = KnowledgeStore.from_path(args.knowledge, encoder=encoder, vector_index=vector,
                                        reranker=FastEmbedReranker(args.reranker_model) if args.reranker_model else None)
        model = OpenAICompatibleModel.deepseek_from_env() if args.planner == 'model' else None
        trace = CaseReviewAgent(store, rules_path=args.rules, model=model, retrieval_mode=args.retrieval_mode,
                                max_model_calls=args.max_model_calls).run(
                                    args.task, args.casebook, required_results=tuple(args.check), case_ids=tuple(args.case_id))
        payload = asdict(trace)
        payload['ok'] = trace.ok
        report = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report + '\n', encoding='utf-8')
            print(args.output)
        else:
            print(report)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(render_case_review(trace), encoding='utf-8')
        return 0 if trace.ok else 1
    if args.command == "knowledge-index":
        from .knowledge import FastEmbedEncoder, KnowledgeStore
        from .vector_index import QdrantVectorIndex
        index = QdrantVectorIndex(args.qdrant_url, alias=args.vector_alias)
        if args.activate:
            index.activate(args.activate)
            print(json.dumps({"alias": args.vector_alias, "active": args.activate}))
        else:
            store = KnowledgeStore.from_path(args.knowledge, encoder=FastEmbedEncoder(args.embedding_model))
            print(json.dumps(index.publish(store), ensure_ascii=False, indent=2))
        return 0
    if args.command == "ask":
        from .assistant import RetailOpsAssistant
        from .knowledge import FastEmbedEncoder, FastEmbedReranker, KnowledgeStore
        from .vector_index import QdrantVectorIndex
        if bool(args.orders) != bool(args.refunds):
            raise SystemExit("--orders and --refunds must be supplied together")
        model = OpenAICompatibleModel.deepseek_from_env() if args.planner == "model" or args.evolve else None
        encoder = FastEmbedEncoder(args.embedding_model) if args.embedding_model else None
        vector = QdrantVectorIndex(args.qdrant_url, alias=args.vector_alias) if args.qdrant_url else None
        store = KnowledgeStore.from_path(args.knowledge, encoder=encoder, vector_index=vector,
                                        reranker=FastEmbedReranker(args.reranker_model) if args.reranker_model else None)
        manager = PluginManager.from_manifest(args.plugin_registry) if args.plugin_registry.exists() else None
        if args.evolve and manager is None:
            raise SystemExit("--evolve requires an existing --plugin-registry")
        analysis = RetailOpsAgent(ModelPlanner(model) if args.planner == "model" else None,
                                  plugin_manager=manager,
                                  evolver=CapabilityEvolver(model, Path("runs/online-evolution")) if args.evolve else None)
        result = RetailOpsAssistant(store, analysis_agent=analysis, model=model,
                                    retrieval_mode=args.retrieval_mode).run(
            args.task, args.orders, args.refunds, platforms=tuple(args.platform),
            business_scope=args.scope, as_of=args.as_of, required_results=tuple(args.required_result))
        payload = asdict(result)
        payload["ok"] = result.ok
        report = json.dumps(payload, ensure_ascii=False, indent=2,
                            default=lambda value: str(value) if isinstance(value, Decimal) else value)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report + "\n", encoding="utf-8")
            print(args.output)
        else:
            print(report)
        return 0 if result.ok else 1
    if args.command == "serve":
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("RETAILOPS_SERVICE_TOKEN"):
            raise SystemExit("set RETAILOPS_SERVICE_TOKEN before binding a non-loopback address")
        try:
            import uvicorn
            from .service import create_app
        except ImportError as exc:
            raise SystemExit("install the service extra: pip install '.[service]'") from exc
        uvicorn.run(create_app(data_root=args.data_root, manifest=args.plugin_registry,
                               state_path=args.state, allow_evolution=args.allow_evolution,
                               knowledge_path=args.knowledge, embedding_model=args.embedding_model,
                               retrieval_mode=args.retrieval_mode, qdrant_url=args.qdrant_url,
                               vector_alias=args.vector_alias, reranker_model=args.reranker_model),
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

