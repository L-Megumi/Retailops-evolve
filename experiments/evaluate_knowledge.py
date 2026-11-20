"""Reproducible retrieval comparison and optional live-model scenario checks.

Question labels remain separate from the indexed corpus. This seed set is a
development benchmark, not an unseen production-accuracy estimate.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse

from retailops_agent.assistant import RetailOpsAssistant
from retailops_agent.knowledge import FastEmbedEncoder, FastEmbedReranker, KnowledgeStore, is_official_source
from retailops_agent.llm import OpenAICompatibleModel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("knowledge/retailops-policies.json"))
    parser.add_argument("--questions", type=Path, default=Path("benchmarks/knowledge/questions.json"))
    parser.add_argument("--embedding-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--modes", default="bm25,dense,hybrid")
    parser.add_argument("--qdrant-url")
    parser.add_argument("--vector-alias", default="retailops_policy")
    parser.add_argument("--reranker-model")
    parser.add_argument("--with-model", action="store_true")
    parser.add_argument("--public-policy-only", action="store_true",
                        help="validate that model evidence contains only reviewed official public policy summaries")
    parser.add_argument("--ids", help="comma-separated question IDs for live scenario checks")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    questions = json.loads(args.questions.read_text(encoding="utf-8-sig"))
    if args.public_policy_only:
        documents = json.loads(args.corpus.read_text(encoding="utf-8-sig"))["documents"]
        if any(d["source_type"] != "official_public_policy" or not is_official_source(d["platform"], d["source_url"]) for d in documents):
            raise ValueError("public-only evaluation excludes internal documents and unapproved sources")
        if any("retailops" in q.get("platforms", []) for q in questions):
            raise ValueError("public-only evaluation excludes internal knowledge questions")
    modes = args.modes.split(",")
    encoder = FastEmbedEncoder(args.embedding_model) if any(m != "bm25" for m in modes) else None
    from retailops_agent.vector_index import QdrantVectorIndex
    vector = QdrantVectorIndex(args.qdrant_url, alias=args.vector_alias) if args.qdrant_url else None
    store = KnowledgeStore.from_path(args.corpus, encoder=encoder, vector_index=vector,
                                     reranker=FastEmbedReranker(args.reranker_model) if args.reranker_model else None)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "evaluation_set": "seed-development",
              "corpus_hash": store.corpus_hash,
              "questions_hash": hashlib.sha256(args.questions.read_bytes()).hexdigest(),
              "embedding_model": args.embedding_model if encoder else None,
              "public_policy_only": args.public_policy_only,
              "vector_backend": "qdrant" if vector else "memory", "reranker_model": args.reranker_model,
              "retrieval": {}, "scenarios": []}
    for mode in modes:
        rows = []
        for q in questions:
            if not q.get("expected_clause_ids") or not q.get("platforms"):
                continue
            start = time.perf_counter()
            hits = []
            for platform in q["platforms"]:
                scope = "platform" if platform == "suning" else q.get("business_scope")
                hits.extend(store.search(q["query"], platforms=(platform,), business_scope=scope,
                                         as_of=q.get("as_of"), top_k=3, mode=mode))
            found = {hit.clause_id for hit in hits}
            expected = set(q["expected_clause_ids"])
            rows.append({"id": q["id"], "expected": sorted(expected), "retrieved": [h.clause_id for h in hits],
                         "recall_at_3_per_platform": len(expected & found) / len(expected),
                         "all_required_found": expected <= found,
                         "platform_leakage": any(h.platform not in q["platforms"] for h in hits),
                         "elapsed_ms": round((time.perf_counter() - start) * 1000, 3)})
        report["retrieval"][mode] = {
            "case_count": len(rows),
            "mean_recall_at_3_per_platform": sum(r["recall_at_3_per_platform"] for r in rows) / len(rows) if rows else None,
            "all_required_found_count": sum(r["all_required_found"] for r in rows),
            "platform_leakage_count": sum(r["platform_leakage"] for r in rows), "cases": rows,
            "timing_note": "includes first-use model/index loading; not a steady-state latency benchmark"}
    selected = set(args.ids.split(",")) if args.ids else None
    model = OpenAICompatibleModel.deepseek_from_env() if args.with_model else None
    assistant = RetailOpsAssistant(store, model=model, retrieval_mode=modes[-1])
    for q in questions:
        if selected is not None and q["id"] not in selected:
            continue
        start = time.perf_counter()
        result = assistant.run(q["query"], platforms=tuple(q.get("platforms", [])),
                               business_scope=q.get("business_scope"), as_of=q.get("as_of"))
        citations = {c["clause_id"] for c in result.citations}
        actual_platforms = {c["platform"] for c in result.citations}
        report["scenarios"].append({
            "id": q["id"], "category": q["category"], "expected_status": q["expected_status"],
            "status_match": None if not args.with_model and result.status == "retrieved" else result.status == q["expected_status"],
            "expected_citations_present": set(q.get("expected_clause_ids", [])) <= citations,
            "expected_platforms_match": actual_platforms == set(q.get("expected_platforms", [])),
            "answer_support_evaluation": "manual_review_required" if q.get("evaluation_layer") == "answer_support" else "not_scored",
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3), "result": asdict(result)})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"id": q["id"], "status": result.status, "expected": q["expected_status"]}, ensure_ascii=False), flush=True)
        if any(e.get("code") in {"insufficient_balance", "authentication_failed"} for e in result.events):
            report["stopped_reason"] = "provider_account_requires_attention"
            break
    report["scenario_summary"] = {"count": len(report["scenarios"]),
                                  "answer_evaluation_enabled": args.with_model,
                                  "status_cases_scored": sum(r["status_match"] is not None for r in report["scenarios"]),
                                  "status_matches": sum(r["status_match"] is True for r in report["scenarios"]),
                                  "citation_coverage_matches": sum(r["expected_citations_present"] for r in report["scenarios"])}
    if model:
        report["model_usage"] = {"model": model.model, "http_requests": model.request_count, **model.total_usage()}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["scenario_summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
