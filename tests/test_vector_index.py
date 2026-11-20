from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
import warnings

from retailops_agent.knowledge import KnowledgeStore
from retailops_agent.vector_index import QdrantVectorIndex
from test_knowledge import document as fixture_document


def document(*args, **kwargs):
    result = fixture_document(*args, **kwargs)
    result["clauses"] = result["clauses"][:1]
    return result

try:
    from qdrant_client import QdrantClient
except ImportError:
    QdrantClient = None


class Encoder:
    model_name = "test-embedding-v1"
    def __init__(self):
        self.document_calls = []
    def encode(self, texts):
        self.document_calls.extend(texts)
        return [[1.0, 0.25, 0.1] for _ in texts]
    def encode_query(self, text):
        return [1.0, 0.25, 0.1]


@unittest.skipUnless(QdrantClient, "install vector extra for Qdrant tests")
class VectorIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = QdrantClient(":memory:")
        self.addCleanup(self.client.close)
        warnings.filterwarnings("ignore", message="Payload indexes have no effect")

    def store(self, docs, encoder=None):
        path = Path(self.temp.name) / "corpus.json"
        path.write_text(json.dumps({"schema_version": 1, "documents": docs}), encoding="utf-8")
        return KnowledgeStore.from_path(path, encoder=encoder or Encoder())

    def index(self, alias="test_policy"):
        return QdrantVectorIndex(client=self.client, alias=alias)

    def test_persistence_reopen_and_no_document_reembedding(self):
        store = self.store([document()])
        published = self.index().publish(store)
        self.assertEqual(published["encoded_vectors"], 1)
        encoder = Encoder()
        reopened = self.store([document()], encoder)
        reopened.vector_index = self.index().open(reopened)
        hits = reopened.search("退货", platforms=("jd",), mode="dense")
        self.assertEqual([h.chunk_id for h in hits], ["policy::return"])
        self.assertEqual(encoder.document_calls, [])

    def test_disk_storage_survives_client_restart(self):
        path = str(Path(self.temp.name) / "vectors")
        store = self.store([document()])
        client = QdrantClient(path=path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            QdrantVectorIndex(client=client, alias="disk_policy").publish(store)
        client.close()
        client = QdrantClient(path=path)
        try:
            store.vector_index = QdrantVectorIndex(client=client, alias="disk_policy").open(store)
            self.assertEqual(store.search("退货", platforms=("jd",), mode="dense")[0].chunk_id, "policy::return")
        finally:
            client.close()

    def test_metadata_only_update_reuses_vectors_and_pins_old_readers(self):
        doc = document()
        old = self.store([doc])
        index = self.index()
        first = index.publish(old)
        old.vector_index = self.index().open(old)
        newer = copy.deepcopy(doc)
        newer["version"] = "v2"
        new = self.store([newer])
        second = index.publish(new)
        self.assertEqual(second["encoded_vectors"], 0)
        self.assertEqual(second["reused_vectors"], 1)
        self.assertNotEqual(first["collection"], second["collection"])
        self.assertEqual(old.search("退货", platforms=("jd",), mode="dense")[0].version, "v1")
        index.activate(first["collection"])
        self.assertEqual(index._active(), first["collection"])
        with self.assertRaisesRegex(RuntimeError, "active vector snapshot"):
            self.index().open(new)

    def test_changed_text_and_model_require_new_embeddings(self):
        doc = document()
        self.index().publish(self.store([doc]))
        doc["clauses"][0]["text"] += " 新规则。"
        changed = self.index().publish(self.store([doc]))
        self.assertEqual(changed["encoded_vectors"], 1)
        encoder = Encoder()
        encoder.model_name = "test-embedding-v2"
        self.assertEqual(self.index().publish(self.store([doc], encoder))["encoded_vectors"], 1)

    def test_server_filter_matches_memory_for_platform_scope_and_dates(self):
        docs = [document("self"), document("market", scope="marketplace"),
                document("common", scope="platform"), document("sun", platform="suning", scope="platform"),
                document("unknown", start=None), document("future", start="9999-01-01"),
                document("old", start="2020-01-01", end="2024-01-01", status="superseded"),
                document("unbounded", start="2020-01-01", status="superseded"), document("draft", status="draft")]
        store = self.store(docs)
        store.vector_index = self.index()
        store.vector_index.publish(store)
        for platform, scope, when in [("jd", "self_operated", None), ("jd", "marketplace", "2024-03-01"),
                                      ("jd", None, "2023-01-01"), ("suning", "platform", None)]:
            expected = {c.hit.chunk_id for c in store._eligible((platform,), scope, when)}
            hits = store.search("退货", platforms=(platform,), business_scope=scope, as_of=when, top_k=20, mode="dense")
            self.assertEqual({h.chunk_id for h in hits}, expected)

    def test_repeated_publish_is_idempotent_and_missing_snapshot_rejected(self):
        store = self.store([document()])
        index = self.index()
        first = index.publish(store)
        second = index.publish(store)
        self.assertEqual(first["collection"], second["collection"])
        self.assertTrue(second["existing_snapshot"])
        with self.assertRaisesRegex(RuntimeError, "snapshot missing"):
            self.index("other_alias").open(store)

    def test_incomplete_snapshot_never_replaces_active_alias(self):
        store = self.store([document()])
        index = self.index()
        good = index.publish(store)
        changed = self.store([document("second")])
        name = index._name(changed)
        from qdrant_client import models as m
        self.client.create_collection(name, vectors_config=m.VectorParams(size=3, distance=m.Distance.COSINE))
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            index.open(changed)
        self.assertEqual(index._active(), good["collection"])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            index.activate(name)
        recovered = index.publish(changed)
        self.assertEqual(recovered["collection"], name)
        self.assertEqual(index._active(), name)

    def test_server_out_of_scope_results_fail_closed(self):
        store = self.store([document()])
        class BadIndex:
            def search(self, *a, **kw):
                return {"foreign::policy": 1.0}
        store.vector_index = BadIndex()
        with self.assertRaisesRegex(RuntimeError, "out-of-scope"):
            store.search("退货", platforms=("jd",), mode="dense")

    def test_unknown_rollback_collection_is_rejected(self):
        for name in ("unrelated", "../other", "test_policy_" + "a" * 24):
            with self.assertRaises(ValueError):
                self.index().activate(name)


class RerankTests(unittest.TestCase):
    def test_reranker_operates_only_on_scoped_candidates(self):
        calls = []
        class Reranker:
            model_name = "test-reranker"
            def rerank(self, query, texts):
                calls.extend(texts)
                return list(range(len(texts)))
        docs = [document("a"), document("b"), document("foreign", platform="suning", scope="platform")]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "corpus.json"
            path.write_text(json.dumps({"schema_version": 1, "documents": docs}), encoding="utf-8")
            store = KnowledgeStore.from_path(path, reranker=Reranker())
            hits = store.search("退货", platforms=("jd",), mode="bm25", top_k=1)
            self.assertEqual(hits[0].doc_id, "b")
            self.assertIn("rerank", hits[0].channels)
            self.assertEqual(len(calls), 2)
            self.assertEqual(store.last_search_metadata["candidate_count"], 2)
