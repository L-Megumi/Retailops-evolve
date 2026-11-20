"""Read-only, scoped policy retrieval with optional dense embeddings.

The corpus is authoritative; lexical and vector indexes are derived in memory.
Hybrid retrieval means BM25 + dense cosine retrieval fused with RRF. Without
an encoder it explicitly falls back to BM25, never a simulated vector score.
"""

from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Any, Protocol, Sequence
from urllib.parse import urlparse


PLATFORMS = frozenset({"jd", "suning", "retailops"})
BUSINESS_SCOPES = frozenset({"marketplace", "self_operated", "platform", "internal"})
SOURCE_TYPES = frozenset({"official_public_policy", "project_spec", "demo_sop"})
STATUSES = frozenset({"active", "draft", "inactive", "archived", "superseded"})
MAX_QUERY_CHARS = 4000
MAX_TOP_K = 20
MAX_CONTEXT = 5
MAX_CORPUS_BYTES = 20 * 1024 * 1024
_TOKEN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002ffff]+|[a-z0-9_]+", re.I)


class Encoder(Protocol):
    def encode(self, texts: list[str]) -> Sequence[Sequence[float]]: ...


class FastEmbedEncoder:
    """Optional ONNX-backed embeddings; model loading is deferred until use."""

    def __init__(
        self, model_name: str = "BAAI/bge-small-zh-v1.5", *,
        cache_dir: str | Path | None = None, threads: int = 2,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = (
            str(cache_dir) if cache_dir is not None
            else os.environ.get("RETAILOPS_EMBEDDING_CACHE") or os.environ.get("FASTEMBED_CACHE_PATH")
        )
        self.threads = _bounded_integer(threads, "threads", 1, 64)
        self._model: Any = None
        self._lock = threading.RLock()

    def _load(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise RuntimeError("Dense retrieval requires the optional fastembed package.") from exc
            self._model = TextEmbedding(
                model_name=self.model_name, cache_dir=self.cache_dir, threads=self.threads,
            )
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            return [list(row) for row in self._load().passage_embed(texts)]

    def encode_query(self, query: str) -> list[float]:
        with self._lock:
            rows = list(self._load().query_embed(query))
            if len(rows) != 1:
                raise ValueError("query encoder must return one vector")
            return list(rows[0])


class SentenceTransformerEncoder:
    """Optional local embedding adapter; importing this module needs no ML stack."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        *,
        device: str = "cpu",
        query_instruction: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        # BGE v1.5 uses the retrieval instruction for queries, not documents.
        self.query_instruction = (
            "为这个句子生成表示以用于检索相关文章："
            if query_instruction is None and "bge-" in model_name.lower() and "zh-v1.5" in model_name.lower()
            else (query_instruction or "")
        )
        self._model: Any = None
        self._lock = threading.RLock()

    def encode(self, texts: list[str]) -> Sequence[Sequence[float]]:
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise RuntimeError(
                        "Dense retrieval requires the optional sentence-transformers package."
                    ) from exc
                self._model = SentenceTransformer(self.model_name, device=self.device)
            return self._model.encode(
                texts, normalize_embeddings=True, show_progress_bar=False,
            ).tolist()

    def encode_queries(self, texts: list[str]) -> Sequence[Sequence[float]]:
        return self.encode([self.query_instruction + text for text in texts])


@dataclass(frozen=True)
class KnowledgeHit:
    chunk_id: str
    doc_id: str
    title: str
    text: str
    platform: str
    business_scope: str
    source_type: str
    source_url: str
    version: str
    clause_id: str
    score: float
    channels: tuple[str, ...]
    content_hash: str
    retrieved_at: str = ""
    effective_from: str | None = None
    effective_to: str | None = None
    clause_title: str = ""
    topics: tuple[str, ...] = ()
    retrieval_mode: str = "bm25"
    content_kind: str = "source_text"


@dataclass(frozen=True)
class _Chunk:
    hit: KnowledgeHit
    status: str
    position: int
    tokens: Counter[str]
    embedding_text: str


def tokenize(text: str) -> list[str]:
    """Chinese character unigrams/bigrams plus case-insensitive English words."""
    result: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        token = match.group()
        if token[0].isascii():
            result.append(token)
        else:
            result.extend(token)
            result.extend(token[i:i + 2] for i in range(len(token) - 1))
    return result


def _text(value: Any, name: str, *, max_length: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{name} must be a non-empty string of at most {max_length} characters")
    return value.strip()


def _identifier(value: Any, name: str) -> str:
    value = _text(value, name, max_length=200)
    if "::" in value or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name} contains a reserved separator or control character")
    return value


def _iso_date(value: Any, name: str, *, nullable: bool = False) -> date | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc


def _snapshot_date(value: Any) -> str:
    value = _text(value, "retrieved_at", max_length=64)
    try:
        if len(value) == 10:
            _iso_date(value, "retrieved_at")
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("retrieved_at must be an ISO date or timestamp") from exc
    return value


def _bounded_integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


class KnowledgeStore:
    def __init__(self, chunks: list[_Chunk], corpus_hash: str, encoder: Encoder | None = None) -> None:
        self._chunks = tuple(chunks)
        self._by_id = {chunk.hit.chunk_id: chunk for chunk in chunks}
        self.corpus_hash = corpus_hash
        self.encoder = encoder
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._dimension: int | None = None
        self._vector_lock = threading.RLock()
        self._search_metadata: ContextVar[dict[str, Any] | None] = ContextVar(
            f"knowledge_search_{id(self)}", default=None,
        )

    @classmethod
    def from_path(cls, path: Path, encoder: Encoder | None = None) -> KnowledgeStore:
        path = Path(path)
        with path.open("rb") as handle:
            raw = handle.read(MAX_CORPUS_BYTES + 1)
        if len(raw) > MAX_CORPUS_BYTES:
            raise ValueError("knowledge corpus exceeds size limit")
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("knowledge corpus must be UTF-8 JSON") from exc
        if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError("knowledge corpus schema_version must be 1")
        documents = payload.get("documents")
        if not isinstance(documents, list) or not documents or len(documents) > 5000:
            raise ValueError("documents must contain between 1 and 5000 documents")

        chunks: list[_Chunk] = []
        document_ids: set[str] = set()
        required = {
            "id", "title", "platform", "business_scope", "source_type", "source_url",
            "version", "retrieved_at", "effective_from", "effective_to", "status", "clauses",
        }
        for document in documents:
            if not isinstance(document, dict) or not required.issubset(document):
                raise ValueError("document is missing required fields")
            doc_id = _identifier(document["id"], "document id")
            if doc_id in document_ids:
                raise ValueError(f"duplicate document id: {doc_id}")
            document_ids.add(doc_id)
            title = _text(document["title"], "document title")
            platform = _text(document["platform"], "platform")
            scope = _text(document["business_scope"], "business_scope")
            source_type = _text(document["source_type"], "source_type")
            status = _text(document["status"], "status")
            if platform not in PLATFORMS or scope not in BUSINESS_SCOPES or source_type not in SOURCE_TYPES or status not in STATUSES:
                raise ValueError(f"invalid document scope, source type or status: {doc_id}")
            source_url = _text(document["source_url"], "source_url")
            if source_type == "official_public_policy":
                parsed = urlparse(source_url)
                if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                    raise ValueError("official public policies require an HTTP(S) source URL")
                domain = {"jd": "jd.com", "suning": "suning.com"}.get(platform)
                hostname = parsed.hostname.lower()
                if parsed.username or parsed.password or (domain and hostname != domain and not hostname.endswith("." + domain)):
                    raise ValueError(f"official policy source URL does not match platform {platform}")
            version = _text(document["version"], "version", max_length=200)
            content_kind = _text(document.get("content_kind", "source_text"), "content_kind", max_length=128)
            retrieved_at = _snapshot_date(document["retrieved_at"])
            start = _iso_date(document["effective_from"], "effective_from", nullable=True)
            end = _iso_date(document["effective_to"], "effective_to", nullable=True)
            if start and end and end <= start:
                raise ValueError("effective_to must be later than effective_from")
            clauses = document["clauses"]
            if not isinstance(clauses, list) or not clauses:
                raise ValueError("clauses must be a non-empty list")
            clause_ids: set[str] = set()
            for position, clause in enumerate(clauses):
                if not isinstance(clause, dict) or not {"id", "title", "text", "topics"}.issubset(clause):
                    raise ValueError("clause is missing required fields")
                clause_id = _identifier(clause["id"], "clause id")
                if clause_id in clause_ids:
                    raise ValueError(f"duplicate clause id in {doc_id}: {clause_id}")
                clause_ids.add(clause_id)
                clause_title = _text(clause["title"], "clause title")
                text = _text(clause["text"], "clause text", max_length=100000)
                topics = clause["topics"]
                if not isinstance(topics, list) or len(topics) > 100:
                    raise ValueError("topics must be a list of at most 100 strings")
                topics_tuple = tuple(dict.fromkeys(_text(topic, "topic", max_length=200) for topic in topics))
                embedding_text = "\n".join((title, clause_title, " ".join(topics_tuple), text))
                hit = KnowledgeHit(
                    chunk_id=f"{doc_id}::{clause_id}", doc_id=doc_id, title=title,
                    text=text, platform=platform, business_scope=scope,
                    source_type=source_type, source_url=source_url, version=version,
                    clause_id=clause_id, score=0.0, channels=(),
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    retrieved_at=retrieved_at, effective_from=document["effective_from"],
                    effective_to=document["effective_to"], clause_title=clause_title,
                    topics=topics_tuple,
                    content_kind=content_kind,
                )
                chunks.append(_Chunk(hit, status, position, Counter(tokenize(embedding_text)), embedding_text))
                if len(chunks) > 100000:
                    raise ValueError("knowledge corpus has too many clauses")
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return cls(chunks, hashlib.sha256(canonical.encode("utf-8")).hexdigest(), encoder)

    @property
    def last_search_metadata(self) -> dict[str, Any]:
        """Metadata for this execution context; hits also carry the effective mode."""
        return dict(self._search_metadata.get() or {})

    def resolve_mode(self, mode: str) -> str:
        if not isinstance(mode, str) or mode not in {"bm25", "dense", "hybrid"}:
            raise ValueError("mode must be bm25, dense or hybrid")
        if mode == "dense" and self.encoder is None:
            raise ValueError("dense retrieval requires an encoder")
        return "bm25" if mode == "hybrid" and self.encoder is None else mode

    def _eligible(self, platforms: tuple[str, ...], business_scope: str | None, as_of: str | None) -> list[_Chunk]:
        if not isinstance(platforms, tuple) or not platforms or any(not isinstance(p, str) or p not in PLATFORMS for p in platforms):
            raise ValueError("platforms must be a non-empty tuple of supported platforms")
        if business_scope is not None and (not isinstance(business_scope, str) or business_scope not in BUSINESS_SCOPES):
            raise ValueError("unsupported business_scope")
        when = _iso_date(as_of, "as_of") if as_of is not None else date.today()
        selected: list[_Chunk] = []
        for chunk in self._chunks:
            hit = chunk.hit
            if hit.platform not in platforms:
                continue
            if as_of is None:
                if chunk.status != "active":
                    continue
            elif chunk.status not in {"active", "superseded"} or (chunk.status == "superseded" and hit.effective_to is None):
                continue
            if business_scope is not None and hit.business_scope not in {business_scope, "platform"}:
                continue
            if as_of is not None and hit.effective_from is None:
                continue
            if hit.effective_from and date.fromisoformat(hit.effective_from) > when:
                continue
            if hit.effective_to and date.fromisoformat(hit.effective_to) <= when:
                continue
            selected.append(chunk)
        return selected

    @staticmethod
    def _bm25(query: str, chunks: list[_Chunk]) -> dict[str, float]:
        terms = set(tokenize(query))
        if not terms or not chunks:
            return {}
        n = len(chunks)
        avg_length = sum(sum(c.tokens.values()) for c in chunks) / n or 1.0
        frequency = {term: sum(term in chunk.tokens for chunk in chunks) for term in terms}
        scores: dict[str, float] = {}
        for chunk in chunks:
            length = sum(chunk.tokens.values())
            score = 0.0
            for term in terms:
                count = chunk.tokens.get(term, 0)
                if count:
                    df = frequency[term]
                    idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                    score += idf * count * 2.5 / (count + 1.5 * (0.25 + 0.75 * length / avg_length))
            if score > 0:
                scores[chunk.hit.chunk_id] = score
        return scores

    def _normalize_vectors(self, raw: Any, expected_count: int) -> list[tuple[float, ...]]:
        try:
            rows = list(raw)
            if len(rows) != expected_count:
                raise ValueError("encoder returned the wrong vector count")
            vectors = []
            dimension = self._dimension
            for row in rows:
                vector = tuple(float(value) for value in row)
                if not vector or not all(math.isfinite(value) for value in vector):
                    raise ValueError("encoder returned an empty or non-finite vector")
                if dimension is None:
                    dimension = len(vector)
                if len(vector) != dimension:
                    raise ValueError("encoder dimensions are inconsistent")
                norm = math.sqrt(sum(value * value for value in vector))
                if not math.isfinite(norm) or norm == 0:
                    raise ValueError("encoder returned a zero or invalid vector")
                vectors.append(tuple(value / norm for value in vector))
            self._dimension = dimension
            return vectors
        except (TypeError, OverflowError) as exc:
            raise ValueError("encoder must return a batch of finite numeric vectors") from exc

    def _dense(self, query: str, chunks: list[_Chunk]) -> dict[str, float]:
        if not chunks:
            return {}
        assert self.encoder is not None
        with self._vector_lock:
            missing = [chunk for chunk in chunks if chunk.hit.chunk_id not in self._vectors]
            if missing:
                vectors = self._normalize_vectors(self.encoder.encode([c.embedding_text for c in missing]), len(missing))
                self._vectors.update((c.hit.chunk_id, vector) for c, vector in zip(missing, vectors))
            if callable(getattr(self.encoder, "encode_query", None)):
                query_batch = [self.encoder.encode_query(query)]
            else:
                encode_query = getattr(self.encoder, "encode_queries", self.encoder.encode)
                query_batch = encode_query([query])
            query_vector = self._normalize_vectors(query_batch, 1)[0]
            return {
                chunk.hit.chunk_id: sum(a * b for a, b in zip(query_vector, self._vectors[chunk.hit.chunk_id]))
                for chunk in chunks
            }

    def search(
        self, query: str, *, platforms: tuple[str, ...],
        business_scope: str | None = None, as_of: str | None = None,
        top_k: int = 5, mode: str = "hybrid",
    ) -> list[KnowledgeHit]:
        query = _text(query, "query", max_length=MAX_QUERY_CHARS)
        top_k = _bounded_integer(top_k, "top_k", 1, MAX_TOP_K)
        effective_mode = self.resolve_mode(mode)
        chunks = self._eligible(platforms, business_scope, as_of)
        metadata: dict[str, Any] = {
            "requested_mode": mode, "effective_mode": effective_mode,
            "eligible_chunks": len(chunks), "corpus_hash": self.corpus_hash,
        }
        if effective_mode != mode:
            metadata["fallback_reason"] = "encoder_not_configured"
        self._search_metadata.set(metadata)
        bm25 = self._bm25(query, chunks) if effective_mode in {"bm25", "hybrid"} else {}
        dense = self._dense(query, chunks) if effective_mode in {"dense", "hybrid"} else {}
        channels: dict[str, tuple[str, ...]] = {}
        if effective_mode == "hybrid":
            scores: dict[str, float] = {}
            window = max(20, top_k * 4)
            for channel, channel_scores in (("bm25", bm25), ("dense", dense)):
                ranked = sorted(channel_scores, key=lambda key: (-channel_scores[key], key))[:window]
                for rank, chunk_id in enumerate(ranked, 1):
                    scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank)
                    channels[chunk_id] = channels.get(chunk_id, ()) + (channel,)
        else:
            scores = bm25 if effective_mode == "bm25" else dense
            channels = {key: (effective_mode,) for key in scores}
        selected = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
        return [replace(self._by_id[key].hit, score=scores[key], channels=channels[key], retrieval_mode=effective_mode) for key in selected]

    def read_document(
        self, chunk_id: str, *, platforms: tuple[str, ...],
        business_scope: str | None = None, as_of: str | None = None,
        context: int = 1,
    ) -> list[KnowledgeHit]:
        """Expand an authorized clause to its document neighbours, in source order."""
        chunk_id = _text(chunk_id, "chunk_id", max_length=402)
        context = _bounded_integer(context, "context", 0, MAX_CONTEXT)
        eligible = self._eligible(platforms, business_scope, as_of)
        target = next((chunk for chunk in eligible if chunk.hit.chunk_id == chunk_id), None)
        if target is None:
            # A missing and an out-of-scope handle deliberately share this result.
            return []
        return [
            replace(chunk.hit, channels=("context",), retrieval_mode="read")
            for chunk in eligible
            if chunk.hit.doc_id == target.hit.doc_id and abs(chunk.position - target.position) <= context
        ]
