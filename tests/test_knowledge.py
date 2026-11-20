from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from retailops_agent.knowledge import (
    FastEmbedEncoder,
    KnowledgeStore,
    MAX_QUERY_CHARS,
    SentenceTransformerEncoder,
    tokenize,
)


def document(doc_id="policy", *, platform="jd", scope="self_operated", start="2024-01-01", end=None, status="active", clauses=None):
    return {
        "id": doc_id, "title": "售后处理规范", "platform": platform,
        "business_scope": scope, "source_type": "official_public_policy",
        "source_url": f"https://help.{platform}.com/policy", "version": "v1",
        "retrieved_at": "2026-11-20T10:00:00+08:00", "effective_from": start,
        "effective_to": end, "status": status,
        "clauses": clauses or [
            {"id": "return", "title": "退货材料", "text": "商品退货需要提供订单号与商品照片。", "topics": ["退货", "材料"]},
            {"id": "shipping", "title": "运费核查", "text": "退货运费由售后人员核查原因，保留物流凭证。", "topics": ["运费"]},
            {"id": "invoice", "title": "发票处理", "text": "需要更改发票抬头时联系开票人员。", "topics": ["发票"]},
        ],
    }


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "corpus.json"

    def store(self, documents=None, *, encoder=None, payload=None):
        payload = payload if payload is not None else {"schema_version": 1, "documents": documents or [document()]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return KnowledgeStore.from_path(self.path, encoder=encoder)

    def test_bm25_finds_clause_and_preserves_provenance(self):
        store = self.store()
        before = self.path.read_bytes()
        hits = store.search("发票抬头", platforms=("jd",), business_scope="self_operated", mode="bm25")
        self.assertEqual(hits[0].clause_id, "invoice")
        self.assertEqual(hits[0].channels, ("bm25",))
        self.assertEqual(hits[0].version, "v1")
        self.assertEqual(hits[0].retrieved_at, "2026-11-20T10:00:00+08:00")
        self.assertEqual(len(hits[0].content_hash), 64)
        self.assertEqual(self.path.read_bytes(), before)

    def test_platform_and_business_scope_filter_before_ranking(self):
        docs = [document("self"), document("market", scope="marketplace"), document("common", scope="platform"), document("suning", platform="suning")]
        store = self.store(docs)
        hits = store.search("退货运费", platforms=("jd",), business_scope="self_operated", top_k=20)
        self.assertEqual({hit.doc_id for hit in hits}, {"self", "common"})
        hits = store.search("退货运费", platforms=("jd",), business_scope="marketplace", top_k=20)
        self.assertEqual({hit.doc_id for hit in hits}, {"market", "common"})

    def test_platform_scope_does_not_pull_marketplace_or_self_rules(self):
        store = self.store([document("self"), document("common", scope="platform")])
        self.assertEqual({hit.doc_id for hit in store.search("退货", platforms=("jd",), business_scope="platform")}, {"common"})

    def test_historical_queries_exclude_unknown_effective_dates(self):
        store = self.store([document("known"), document("unknown", start=None)])
        historical = store.search("退货", platforms=("jd",), as_of="2025-03-01", top_k=20)
        self.assertEqual({hit.doc_id for hit in historical}, {"known"})
        current = store.search("退货", platforms=("jd",), top_k=20)
        self.assertEqual({hit.doc_id for hit in current}, {"known", "unknown"})
        self.assertEqual(store.read_document("unknown::return", platforms=("jd",), as_of="2025-03-01"), [])

    def test_effective_interval_is_start_inclusive_end_exclusive(self):
        store = self.store([document("old", start="2024-01-01", end="2025-01-01"), document("new", start="2025-01-01")])
        hits = store.search("退货", platforms=("jd",), as_of="2025-01-01", top_k=20)
        self.assertEqual({hit.doc_id for hit in hits}, {"new"})
        self.assertEqual(store.search("退货", platforms=("jd",), as_of="2023-12-31"), [])

    def test_inactive_and_future_documents_are_not_searchable(self):
        store = self.store([document("draft", status="draft"), document("future", start="9999-01-01")])
        self.assertEqual(store.search("退货", platforms=("jd",)), [])

    def test_historical_version_selection_includes_bounded_superseded_policy(self):
        docs = [
            document("old", start="2024-01-01", end="2025-01-01", status="superseded"),
            document("new", start="2025-01-01"),
            document("unbounded", start="2024-01-01", status="superseded"),
        ]
        store = self.store(docs)
        historical = store.search("退货", platforms=("jd",), as_of="2024-06-01", top_k=20)
        self.assertEqual({hit.doc_id for hit in historical}, {"old"})
        boundary = store.search("退货", platforms=("jd",), as_of="2025-01-01", top_k=20)
        self.assertEqual({hit.doc_id for hit in boundary}, {"new"})
        current = store.search("退货", platforms=("jd",), top_k=20)
        self.assertEqual({hit.doc_id for hit in current}, {"new"})
        self.assertTrue(store.read_document("old::return", platforms=("jd",), as_of="2024-06-01"))
        self.assertEqual(store.read_document("old::return", platforms=("jd",)), [])

    def test_content_kind_is_carried_through_search_and_read(self):
        doc = document()
        doc["content_kind"] = "editorial_summary"
        store = self.store([doc])
        hit = store.search("退货", platforms=("jd",))[0]
        self.assertEqual(hit.content_kind, "editorial_summary")
        self.assertEqual(store.read_document(hit.chunk_id, platforms=("jd",))[0].content_kind, "editorial_summary")

    def test_official_source_domains_cannot_cross_platform_or_use_lookalikes(self):
        for url in ["https://help.suning.com/policy", "https://jd.com.attacker.test/policy", "https://fakejd.com/policy", "https://user@help.jd.com/policy"]:
            doc = document()
            doc["source_url"] = url
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "does not match platform"):
                self.store([doc])

    def test_hybrid_without_encoder_is_reported_as_bm25(self):
        store = self.store()
        hits = store.search("退货", platforms=("jd",), mode="hybrid")
        self.assertTrue(hits)
        self.assertTrue(all(hit.retrieval_mode == "bm25" and hit.channels == ("bm25",) for hit in hits))
        self.assertEqual(store.last_search_metadata["effective_mode"], "bm25")
        self.assertEqual(store.last_search_metadata["fallback_reason"], "encoder_not_configured")
        with self.assertRaises(ValueError):
            store.search("退货", platforms=("jd",), mode="dense")

    def test_query_bounds_and_explicit_platform_requirement(self):
        store = self.store()
        for changes in [
            {"platforms": ()}, {"platforms": ("unknown",)}, {"platforms": "jd"},
            {"query": ""}, {"query": "a" * (MAX_QUERY_CHARS + 1)},
            {"top_k": 0}, {"top_k": 21}, {"top_k": True},
            {"business_scope": "all"}, {"mode": "fake"}, {"as_of": "2025-02-30"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                store.search(**({"query": "退货", "platforms": ("jd",)} | changes))

    def test_neighbour_read_is_in_source_order_and_cannot_cross_scope(self):
        store = self.store([document(), document("market", scope="marketplace"), document("other", platform="suning")])
        hits = store.read_document("policy::shipping", platforms=("jd",), business_scope="self_operated", context=1)
        self.assertEqual([hit.clause_id for hit in hits], ["return", "shipping", "invoice"])
        self.assertTrue(all(hit.channels == ("context",) for hit in hits))
        self.assertEqual(len(store.read_document("policy::shipping", platforms=("jd",), context=0)), 1)
        for target in ["market::shipping", "other::shipping", "missing"]:
            self.assertEqual(store.read_document(target, platforms=("jd",), business_scope="self_operated"), [])
        for context in [-1, 6, True]:
            with self.assertRaises(ValueError):
                store.read_document("policy::shipping", platforms=("jd",), context=context)

    def test_corpus_hash_changes_when_policy_metadata_changes(self):
        first = self.store().corpus_hash
        changed = document()
        changed["version"] = "v2"
        self.assertNotEqual(first, self.store([changed]).corpus_hash)
        self.assertEqual(first, self.store().corpus_hash)

    def test_invalid_corpus_is_rejected(self):
        original = {"schema_version": 1, "documents": [document()]}
        invalid = []
        invalid.append({"schema_version": True, "documents": [document()]})
        invalid.append({"schema_version": 1, "documents": []})
        invalid.append({"schema_version": 1, "documents": [document(), document()]})
        for key, value in [("platform", "unknown"), ("source_type", "unverified"), ("effective_from", "2025-99-01"), ("retrieved_at", "yesterday"), ("source_url", "file:///policy"), ("clauses", [])]:
            payload = copy.deepcopy(original)
            payload["documents"][0][key] = value
            invalid.append(payload)
        duplicate = copy.deepcopy(original)
        duplicate["documents"][0]["clauses"].append(copy.deepcopy(duplicate["documents"][0]["clauses"][0]))
        invalid.append(duplicate)
        interval = copy.deepcopy(original)
        interval["documents"][0]["effective_to"] = "2023-01-01"
        invalid.append(interval)
        missing = copy.deepcopy(original)
        del missing["documents"][0]["source_url"]
        invalid.append(missing)
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.store(payload=payload)

    def test_bad_json_is_a_validation_error(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "UTF-8 JSON"):
            KnowledgeStore.from_path(self.path)

    def test_chinese_and_english_tokenization(self):
        self.assertEqual(tokenize("退款 REFUND_ID"), ["退", "款", "退款", "refund_id"])

    def test_real_rrf_algorithm_with_injected_vector_fixture(self):
        class FixtureEncoder:
            def __init__(self):
                self.calls = []

            def encode(self, texts):
                self.calls.append(list(texts))
                # Explicit fixtures exercise vector plumbing, not model quality.
                return [[0.1, 1.0] if "苹果" in text else [1.0, 0.0] for text in texts]

        clauses = [
            {"id": "a", "title": "苹果", "text": "苹果水果", "topics": []},
            {"id": "b", "title": "香蕉", "text": "香蕉水果", "topics": []},
        ]
        encoder = FixtureEncoder()
        store = self.store([document(clauses=clauses)], encoder=encoder)
        hits = store.search("苹果", platforms=("jd",), mode="hybrid")
        self.assertEqual(hits[0].clause_id, "a")
        self.assertEqual(hits[0].channels, ("bm25", "dense"))
        self.assertAlmostEqual(hits[0].score, 2 / 61)
        self.assertEqual(hits[0].retrieval_mode, "hybrid")
        self.assertEqual(store.last_search_metadata["effective_mode"], "hybrid")
        store.search("苹果", platforms=("jd",), mode="hybrid")
        self.assertEqual([len(call) for call in encoder.calls], [2, 1, 1])

    def test_dense_embeddings_only_receive_eligible_documents(self):
        class RecordingEncoder:
            def __init__(self):
                self.documents = []
                self.queries = []

            def encode(self, texts):
                self.documents.extend(texts)
                return [[1.0, 0.0] for _ in texts]

            def encode_query(self, query):
                self.queries.append(query)
                return [1.0, 0.0]

        encoder = RecordingEncoder()
        hidden = document("hidden", platform="suning")
        hidden["clauses"][0]["text"] = "不应进入编码器的苏宁资料"
        store = self.store([document(), hidden], encoder=encoder)
        hits = store.search("退货", platforms=("jd",), mode="dense")
        self.assertEqual({hit.platform for hit in hits}, {"jd"})
        self.assertFalse(any("苏宁" in text for text in encoder.documents))
        self.assertEqual(encoder.queries, ["退货"])

    def test_invalid_encoder_vectors_fail_without_fake_lexical_fallback(self):
        for bad in [[], [[0.0, 0.0]] * 3, [[float("nan"), 1.0]] * 3, [[1.0], [1.0, 0.0], [1.0]]]:
            with self.subTest(bad=bad):
                encoder = SimpleNamespace(encode=lambda texts, value=bad: value)
                store = self.store(encoder=encoder)
                with self.assertRaises(ValueError):
                    store.search("退货", platforms=("jd",), mode="hybrid")

    def test_search_metadata_is_isolated_between_concurrent_contexts(self):
        store = self.store()
        barrier = threading.Barrier(2)

        def run(mode):
            store.search("退货", platforms=("jd",), mode=mode)
            barrier.wait(timeout=5)
            return store.last_search_metadata["requested_mode"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, "bm25")
            second = pool.submit(run, "hybrid")
            self.assertEqual(first.result(timeout=10), "bm25")
            self.assertEqual(second.result(timeout=10), "hybrid")


class EncoderAdapterTests(unittest.TestCase):
    def test_fastembed_cache_environment_and_explicit_path_precedence(self):
        with patch.dict("os.environ", {"RETAILOPS_EMBEDDING_CACHE": "preferred", "FASTEMBED_CACHE_PATH": "fallback"}):
            self.assertEqual(FastEmbedEncoder().cache_dir, "preferred")
            self.assertEqual(FastEmbedEncoder(cache_dir="explicit").cache_dir, "explicit")

    def test_fastembed_uses_passage_and_query_interfaces_lazily(self):
        calls = []

        class Model:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def passage_embed(self, texts):
                calls.append(("passages", texts))
                return iter([[1.0, 0.0] for _ in texts])

            def query_embed(self, query):
                calls.append(("query", query))
                return iter([[0.0, 1.0]])

        with patch.dict("sys.modules", {"fastembed": SimpleNamespace(TextEmbedding=Model)}):
            encoder = FastEmbedEncoder(cache_dir="model-cache", threads=2)
            self.assertEqual(calls, [])
            self.assertEqual(encoder.encode(["条款"]), [[1.0, 0.0]])
            self.assertEqual(encoder.encode_query("问题"), [0.0, 1.0])
        self.assertEqual([call[0] for call in calls], ["init", "passages", "query"])
        self.assertEqual(calls[0][1]["model_name"], "BAAI/bge-small-zh-v1.5")

    def test_sentence_transformer_query_instruction_is_model_specific(self):
        encoder = SentenceTransformerEncoder()
        self.assertTrue(encoder.query_instruction)
        self.assertIsNone(encoder._model)
        self.assertEqual(SentenceTransformerEncoder("BAAI/bge-m3").query_instruction, "")


if __name__ == "__main__":
    unittest.main()
