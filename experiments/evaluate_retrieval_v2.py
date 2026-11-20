"""Source-grouped retrieval comparison with real embeddings and local reranking."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import time

from retailops_agent.knowledge import FastEmbedEncoder, FastEmbedReranker, KnowledgeStore
from retailops_agent.vector_index import QdrantVectorIndex


def percentile(values, p):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * p) - 1)] if values else None


def summarize(rows):
    return {"count": len(rows),
            **{k: statistics.mean(r[k] for r in rows) if rows else None
               for k in ("recall_at_3", "recall_at_5", "mrr_at_5", "ndcg_at_5")},
            "all_required_at_3": sum(r["all_required_at_3"] for r in rows),
            "scope_leakage": sum(r["scope_leakage"] for r in rows),
            "p50_ms": percentile([r["elapsed_ms"] for r in rows], .5),
            "p95_ms": percentile([r["elapsed_ms"] for r in rows], .95)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("knowledge/retailops-policies.json"))
    parser.add_argument("--questions", type=Path, default=Path("benchmarks/knowledge/expanded.json"))
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--vector-alias", default="retailops_policy")
    parser.add_argument("--configs", default="bm25,dense,hybrid,memory_hybrid,hybrid_rerank")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    questions = json.loads(args.questions.read_text(encoding="utf-8-sig"))
    if any(len(q.get("platforms", [])) != 1 for q in questions):
        raise ValueError("this ranking benchmark requires one platform per query; use evaluate_knowledge.py for multi-platform scenarios")
    groups = {}
    for q in questions:
        group = q.get("source_group", q["id"])
        split = q.get("split", "seed-regression")
        if group in groups and groups[group] != split:
            raise ValueError("a source group crosses the question split")
        groups[group] = split
    encoder = FastEmbedEncoder()
    reranker = FastEmbedReranker(threads=4)
    report = {"created_at": datetime.now(timezone.utc).isoformat(),
              "questions_hash": hashlib.sha256(args.questions.read_bytes()).hexdigest(),
              "embedding_model": encoder.model_name, "reranker_model": reranker.model_name,
              "split_policy": "source-grouped questions; all source documents remain in retrieval corpus",
              "latency_policy": "sequential warm requests, local CPU embeddings and reranking; excludes warmup",
              "configs": {}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for config in args.configs.split(","):
        if config not in {"bm25", "dense", "hybrid", "memory_hybrid", "hybrid_rerank"}:
            raise ValueError("unknown configuration")
        mode = "hybrid" if config in {"memory_hybrid", "hybrid_rerank"} else config
        vector = QdrantVectorIndex(args.qdrant_url, alias=args.vector_alias) if config not in {"bm25", "memory_hybrid"} else None
        store = KnowledgeStore.from_path(args.corpus, encoder=encoder if mode != "bm25" else None,
                                         vector_index=vector, reranker=reranker if config == "hybrid_rerank" else None)
        report["corpus_hash"] = store.corpus_hash
        first = questions[0]
        start = time.perf_counter()
        store.search(first["query"], platforms=tuple(first["platforms"]),
                     business_scope=first.get("business_scope"), mode=mode)
        warmup = round((time.perf_counter() - start) * 1000, 3)
        rows = []
        for q in questions:
            expected = set(q.get("expected_chunk_ids", q.get("expected_clause_ids", [])))
            if not expected or not q.get("platforms"):
                continue
            started = time.perf_counter()
            hits = []
            for platform in q["platforms"]:
                scope = "platform" if platform not in {"jd", "retailops"} else q.get("business_scope")
                hits.extend(store.search(q["query"], platforms=(platform,), business_scope=scope,
                                         as_of=q.get("as_of"), top_k=5, mode=mode))
            full_ids = "expected_chunk_ids" in q
            ranked = [h.chunk_id if full_ids else h.clause_id for h in hits]
            ranks = [i for i, key in enumerate(ranked, 1) if key in expected]
            found3, found5 = set(ranked[:3]), set(ranked[:5])
            ideal = sum(1 / math.log2(i + 1) for i in range(1, min(len(expected), 5) + 1))
            dcg = sum(1 / math.log2(i + 1) for i, key in enumerate(ranked[:5], 1) if key in expected)
            rows.append({"id": q["id"], "split": q.get("split", "seed-regression"),
                         "source_group": q.get("source_group"), "retrieved": ranked,
                         "expected": sorted(expected),
                         "recall_at_3": len(expected & found3) / len(expected),
                         "recall_at_5": len(expected & found5) / len(expected),
                         "all_required_at_3": expected <= found3,
                         "mrr_at_5": 1 / min(ranks) if ranks else 0.0,
                         "ndcg_at_5": dcg / ideal,
                         "scope_leakage": any(h.platform not in q["platforms"] or
                                               (q.get("business_scope") and h.platform == "jd" and
                                                h.business_scope not in {q["business_scope"], "platform"}) for h in hits),
                         "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
        report["configs"][config] = {"summary": summarize(rows), "warmup_ms": warmup,
                                      "splits": {s: summarize([r for r in rows if r["split"] == s])
                                                 for s in sorted({r["split"] for r in rows})}, "cases": rows}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"config": config, **summarize(rows)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
