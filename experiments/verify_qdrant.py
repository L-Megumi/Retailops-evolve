"""Exercise version publication against the real server in an isolated namespace."""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import uuid

from retailops_agent.knowledge import FastEmbedEncoder, KnowledgeStore
from retailops_agent.vector_index import QdrantVectorIndex


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    encoder = FastEmbedEncoder()
    original = json.loads(Path("knowledge/retailops-policies.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "corpus.json"
        def load(payload):
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return KnowledgeStore.from_path(path, encoder=encoder)
        store = load(original)
        alias = "retailops_verification_" + uuid.uuid4().hex[:8]
        index = QdrantVectorIndex(args.qdrant_url, alias=alias)
        first = index.publish(store)
        repeated = index.publish(store)
        old_reader = load(original)
        old_reader.vector_index = QdrantVectorIndex(args.qdrant_url, alias=alias).open(old_reader)
        metadata_update = copy.deepcopy(original)
        metadata_update["documents"][0]["version"] += "-verification"
        second = index.publish(load(metadata_update))
        assert second["encoded_vectors"] == 0 and second["reused_vectors"] == first["points"]
        content_update = copy.deepcopy(metadata_update)
        content_update["documents"][0]["clauses"][0]["text"] += "（索引集成验证样本）"
        third = index.publish(load(content_update))
        assert third["encoded_vectors"] == 1 and third["reused_vectors"] == first["points"] - 1
        old_hits = old_reader.search("拼多多质量问题退货运费", platforms=("pinduoduo",), business_scope="platform", mode="hybrid")
        assert old_hits and all(h.platform == "pinduoduo" for h in old_hits)
        index.activate(first["collection"])
        QdrantVectorIndex(args.qdrant_url, alias=alias).open(store)
        rows = []
        for platform in ("jd", "suning", "taobao", "tmall", "pinduoduo"):
            scope = "marketplace" if platform == "jd" else "platform"
            old_reader.search("退货运费", platforms=(platform,), business_scope=scope, mode="hybrid")
            rows.append({"platform": platform, "business_scope": scope, **old_reader.last_search_metadata})
        report = {"server": args.qdrant_url, "initial": first, "repeated": repeated,
                  "metadata_update": second, "one_clause_update": third,
                  "rollback_collection": index._active(), "old_reader_remained_pinned": True,
                  "scoped_queries": rows, "example": [asdict(h) for h in old_hits],
                  "primary_alias_modified": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"idempotent": repeated["existing_snapshot"], "metadata_vectors_encoded": second["encoded_vectors"],
                      "changed_clause_vectors_encoded": third["encoded_vectors"],
                      "changed_clause_vectors_reused": third["reused_vectors"],
                      "rollback": report["rollback_collection"] == first["collection"]}))


if __name__ == "__main__":
    main()
