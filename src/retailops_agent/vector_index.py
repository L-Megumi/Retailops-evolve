"""Versioned Qdrant snapshots. Publishing is explicit; readers pin a snapshot.

JSON remains the source of truth. An immutable collection binds corpus, embedding
model and embedding-input format; changing metadata can reuse unchanged vectors.
"""
from __future__ import annotations

from datetime import date
import hashlib
import math
import re
import uuid


class QdrantVectorIndex:
    def __init__(self, url="http://127.0.0.1:6333", *, alias="retailops_policy", client=None):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", alias):
            raise ValueError("invalid vector index alias")
        from qdrant_client import QdrantClient
        self.client = client if client is not None else QdrantClient(url=url, timeout=30)
        self.alias = alias
        self.collection = None
        self.model_id = None

    def _name(self, store):
        model = getattr(store.encoder, "model_name", None)
        if not model:
            raise ValueError("persistent indexing requires a named embedding model")
        self.model_id = model
        identity = f"snapshot-v2\nembedding-input-v1\n{model}\n{store.corpus_hash}"
        return self.alias + "_" + hashlib.sha256(identity.encode()).hexdigest()[:24]

    @staticmethod
    def _vector_hash(chunk):
        return hashlib.sha256(chunk.embedding_text.encode()).hexdigest()

    @staticmethod
    def _point_id(chunk_id):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "retailops:knowledge:" + chunk_id))

    def _active(self):
        return next((a.collection_name for a in self.client.get_aliases().aliases
                     if a.alias_name == self.alias), None)

    def _validate(self, name, store):
        from qdrant_client import models as m
        if self.client.count(name, exact=True).count != len(store._chunks):
            raise RuntimeError("vector snapshot is incomplete; rebuild before use")
        # Every point is scoped to the expected corpus and model, not just the count.
        expected = m.Filter(must=[
            m.FieldCondition(key="corpus_hash", match=m.MatchValue(value=store.corpus_hash)),
            m.FieldCondition(key="embedding_model", match=m.MatchValue(value=self.model_id)),
        ])
        if self.client.count(name, count_filter=expected, exact=True).count != len(store._chunks):
            raise RuntimeError("vector snapshot identity mismatch")

    def open(self, store):
        name = self._name(store)
        if not self.client.collection_exists(name):
            raise RuntimeError("vector snapshot missing; run knowledge-index for this corpus and model")
        self._validate(name, store)
        if self._active() != name:
            raise RuntimeError("active vector snapshot does not match corpus; publish or load the matching corpus")
        self.collection = name
        return self

    def publish(self, store):
        from qdrant_client import models as m
        name = self._name(store)
        active = self._active()
        reused = encoded = 0
        exists = self.client.collection_exists(name)
        incomplete = False
        if exists:
            try:
                self._validate(name, store)
            except RuntimeError:
                # Retry an interrupted unpublished build. Never mutate an active
                # snapshot if its integrity check fails.
                if active == name:
                    raise
                incomplete = True
        if not exists or incomplete:
            previous = {}
            if active:
                offset = None
                while True:
                    points, offset = self.client.scroll(active, limit=256, offset=offset, with_vectors=True)
                    for point in points:
                        p = point.payload or {}
                        if p.get("embedding_model") == self.model_id:
                            previous[p.get("vector_hash")] = point.vector
                    if offset is None:
                        break
            vectors = {}
            for chunk in store._chunks:
                key = self._vector_hash(chunk)
                if key in previous:
                    vectors[key] = previous[key]
                    reused += 1
            missing = list({self._vector_hash(c): c.embedding_text for c in store._chunks
                            if self._vector_hash(c) not in vectors}.items())
            for start in range(0, len(missing), 64):
                batch = missing[start:start + 64]
                raw = store.encoder.encode([text for _, text in batch])
                rows = store._normalize_vectors(raw, len(batch))
                vectors.update((key, list(row)) for (key, _), row in zip(batch, rows))
                encoded += len(batch)
            dimension = len(next(iter(vectors.values())))
            if not exists:
                self.client.create_collection(name, vectors_config=m.VectorParams(size=dimension, distance=m.Distance.COSINE))
            # Filtering indexes are built before loading points on the server.
            for field in ("platform", "business_scope", "status", "corpus_hash", "embedding_model"):
                self.client.create_payload_index(name, field_name=field, field_schema=m.PayloadSchemaType.KEYWORD, wait=True)
            for field in ("start_day", "end_day"):
                self.client.create_payload_index(name, field_name=field, field_schema=m.PayloadSchemaType.INTEGER, wait=True)
            self.client.create_payload_index(name, field_name="known_start", field_schema=m.PayloadSchemaType.BOOL, wait=True)
            for start in range(0, len(store._chunks), 128):
                points = []
                for chunk in store._chunks[start:start + 128]:
                    h = chunk.hit
                    payload = {"chunk_id": h.chunk_id, "platform": h.platform,
                               "business_scope": h.business_scope, "status": chunk.status,
                               "start_day": date.fromisoformat(h.effective_from).toordinal() if h.effective_from else 1,
                               "end_day": date.fromisoformat(h.effective_to).toordinal() if h.effective_to else 3652060,
                               "known_start": h.effective_from is not None,
                               "bounded_end": h.effective_to is not None,
                               "corpus_hash": store.corpus_hash, "embedding_model": self.model_id,
                               "point_count": len(store._chunks),
                               "content_hash": h.content_hash, "vector_hash": self._vector_hash(chunk),
                               "doc_id": h.doc_id, "version": h.version, "source_url": h.source_url}
                    points.append(m.PointStruct(id=self._point_id(h.chunk_id),
                                                vector=vectors[self._vector_hash(chunk)], payload=payload))
                self.client.upsert(name, points=points, wait=True)
        self._validate(name, store)
        if active != name:
            changes = []
            if active:
                changes.append(m.DeleteAliasOperation(delete_alias=m.DeleteAlias(alias_name=self.alias)))
            changes.append(m.CreateAliasOperation(create_alias=m.CreateAlias(collection_name=name, alias_name=self.alias)))
            self.client.update_collection_aliases(change_aliases_operations=changes)
        self.collection = name
        return {"collection": name, "alias": self.alias, "previous_collection": active,
                "points": len(store._chunks), "encoded_vectors": encoded, "reused_vectors": reused,
                "existing_snapshot": encoded == 0 and reused == 0, "corpus_hash": store.corpus_hash,
                "embedding_model": self.model_id}

    def activate(self, collection):
        """Rollback only to a snapshot belonging to this index namespace."""
        from qdrant_client import models as m
        if not re.fullmatch(re.escape(self.alias) + r"_[0-9a-f]{24}", collection) or not self.client.collection_exists(collection):
            raise ValueError("unknown snapshot")
        points, _ = self.client.scroll(collection, limit=1, with_payload=True)
        if not points or self.client.count(collection, exact=True).count != points[0].payload.get("point_count"):
            raise ValueError("cannot activate an incomplete snapshot")
        active = self._active()
        operations = [m.DeleteAliasOperation(delete_alias=m.DeleteAlias(alias_name=self.alias))] if active else []
        operations.append(m.CreateAliasOperation(create_alias=m.CreateAlias(collection_name=collection, alias_name=self.alias)))
        self.client.update_collection_aliases(change_aliases_operations=operations)

    def search(self, vector, *, platforms, business_scope, as_of, limit):
        from qdrant_client import models as m
        if self.collection is None:
            raise RuntimeError("vector index is not open")
        when = date.fromisoformat(as_of) if as_of else date.today()
        must = [m.FieldCondition(key="platform", match=m.MatchAny(any=list(platforms))),
                m.FieldCondition(key="start_day", range=m.Range(lte=when.toordinal())),
                m.FieldCondition(key="end_day", range=m.Range(gt=when.toordinal()))]
        if business_scope:
            must.append(m.FieldCondition(key="business_scope", match=m.MatchAny(any=list({business_scope, "platform"}))))
        if as_of:
            must += [m.FieldCondition(key="known_start", match=m.MatchValue(value=True)),
                     m.Filter(should=[m.FieldCondition(key="status", match=m.MatchValue(value="active")),
                                      m.Filter(must=[m.FieldCondition(key="status", match=m.MatchValue(value="superseded")),
                                                     m.FieldCondition(key="bounded_end", match=m.MatchValue(value=True))])])]
        else:
            must.append(m.FieldCondition(key="status", match=m.MatchValue(value="active")))
        points = self.client.query_points(self.collection, query=list(vector), query_filter=m.Filter(must=must),
                                          limit=limit, with_payload=["chunk_id"]).points
        scores = {p.payload["chunk_id"]: float(p.score) for p in points}
        if not all(math.isfinite(v) for v in scores.values()):
            raise RuntimeError("vector database returned invalid scores")
        return scores
