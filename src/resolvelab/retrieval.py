"""Permission filtering precedes PostgreSQL lexical and exact dense ranking."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import os
import re
from pathlib import Path

import numpy as np
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41/onnx-quint8-avx2"
EMBEDDING_DIMENSION = 384
RRF_K = 60
MAX_CORPUS_SECTIONS = 64
MAX_SECTION_BYTES = 16 * 1024
MAX_CORPUS_BYTES = 256 * 1024
MAX_TITLE_BYTES = 512
MAX_SOURCE_LABEL_BYTES = 256
MAX_LOCATOR_BYTES = 256
EMBEDDING_NORM_TOLERANCE = 1e-3


class CorpusAdmissionError(RuntimeError):
    """The pinned corpus cannot safely be ranked; publish a corrected version."""


def _validate_embedding(vector, dimension):
    if vector is None or dimension != EMBEDDING_DIMENSION:
        raise CorpusAdmissionError("Corpus embedding dimension/version mismatch; rebuild a new corpus version")
    try:
        value = np.asarray(vector, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise CorpusAdmissionError("Corpus embedding must be a finite unit vector") from error
    if (value.shape != (EMBEDDING_DIMENSION,) or not np.isfinite(value).all()
            or abs(float(np.linalg.norm(value)) - 1.0) > EMBEDDING_NORM_TOLERANCE):
        raise CorpusAdmissionError("Corpus embedding must be a finite unit vector")


def _validate_corpus_size(section_count, section_bytes, total_bytes):
    if (section_count > MAX_CORPUS_SECTIONS or section_bytes > MAX_SECTION_BYTES
            or total_bytes > MAX_CORPUS_BYTES):
        raise CorpusAdmissionError("Corpus exceeds the published section/byte admission limits")


def _validate_metadata_size(title_bytes, source_label_bytes, locator_bytes):
    if (title_bytes > MAX_TITLE_BYTES or source_label_bytes > MAX_SOURCE_LABEL_BYTES
            or locator_bytes > MAX_LOCATOR_BYTES):
        raise CorpusAdmissionError("Corpus exceeds the published metadata byte admission limits")


@lru_cache(maxsize=1)
def _encoder():
    import onnxruntime as ort
    from tokenizers import Tokenizer
    directory = Path(os.environ.get("EMBEDDING_DIRECTORY", ROOT / "artifacts/embeddings"))
    if not (directory / "model.onnx").exists():
        raise RuntimeError("Embedding artifact missing. Run python scripts/fetch_dataset.py during setup.")
    for filename,expected in {
        "model.onnx":"b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21",
        "tokenizer.json":"be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037"}.items():
        if hashlib.sha256((directory/filename).read_bytes()).hexdigest()!=expected:
            raise RuntimeError("Pinned embedding artifact checksum mismatch")
    options = ort.SessionOptions()
    options.intra_op_num_threads = int(os.environ.get("EMBEDDING_THREADS", "2"))
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(directory / "model.onnx"), options, providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=256)
    tokenizer.enable_padding()
    return session, tokenizer


def embed(texts: list[str]) -> np.ndarray:
    """MiniLM transformer, attention-mask mean pooling, unit normalization."""
    if not texts:
        return np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)
    session, tokenizer = _encoder()
    encoded = tokenizer.encode_batch(texts)
    available = {"input_ids": np.asarray([e.ids for e in encoded], dtype=np.int64),
                 "attention_mask": np.asarray([e.attention_mask for e in encoded], dtype=np.int64),
                 "token_type_ids": np.asarray([e.type_ids for e in encoded], dtype=np.int64)}
    inputs = {item.name: available[item.name] for item in session.get_inputs()}
    hidden = session.run(None, inputs)[0]
    mask = available["attention_mask"][..., None].astype(np.float32)
    vectors = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return vectors.astype(np.float32)


def embed_consistent(texts: list[str]) -> np.ndarray:
    """Identical one-example quantization/tokenization in training and serving.

    Dynamic quantization can depend on other examples/padding in a batch. V4
    routing uses this explicit per-example transform for both fit and inference.
    Historical releases retain embed() and their recorded behavior.
    """
    return np.vstack([embed([value])[0] for value in texts]) if texts else np.empty((0,EMBEDDING_DIMENSION),dtype=np.float32)


def retrieve(conn, tenant_id, corpus_id, query: str, role: str, mode: str = "hybrid", limit: int = 4, lexical_mode: str = "all") -> list[dict]:
    if mode not in {"lexical", "dense", "hybrid"}:
        raise ValueError("Unsupported retrieval mode")
    if lexical_mode not in {"all","any"}:raise ValueError("Unsupported lexical query semantics")
    limit = max(1, min(int(limit), 20))
    lexical_query=" OR ".join(re.findall(r"\w+",query)[:128]) if lexical_mode=="any" else query
    # Measure bounded, tenant-scoped metadata before materializing any body or
    # vector. Count all access scopes: admin sections share the corpus budget.
    # Existing immutable historical corpora are not rewritten by the migration;
    # an out-of-policy historical version must fail admission, not lose evidence.
    admission = conn.execute(text("""
        SELECT v.embedding_model_revision, v.embedding_dimension,
               a.section_count, a.section_bytes, a.total_bytes,
               a.title_bytes, a.source_label_bytes, a.locator_bytes
        FROM resolvelab.corpus_versions v
        CROSS JOIN LATERAL (
            SELECT count(*) AS section_count,
                   coalesce(max(b.body_bytes), 0) AS section_bytes,
                   coalesce(sum(b.body_bytes), 0) AS total_bytes,
                   coalesce(max(b.title_bytes), 0) AS title_bytes,
                   coalesce(max(b.source_label_bytes), 0) AS source_label_bytes,
                   coalesce(max(b.locator_bytes), 0) AS locator_bytes
            FROM (SELECT octet_length(c.body) AS body_bytes,
                         octet_length(d.title) AS title_bytes,
                         octet_length(d.source_label) AS source_label_bytes,
                         octet_length(c.locator) AS locator_bytes
                  FROM resolvelab.chunks c
                  JOIN resolvelab.documents d USING (tenant_id, corpus_id, document_id)
                  WHERE c.tenant_id=v.tenant_id AND c.corpus_id=v.corpus_id
                  LIMIT :candidate_limit) b
        ) a
        WHERE v.tenant_id=:tenant AND v.corpus_id=:corpus AND v.state='published'
    """), {"tenant": str(tenant_id), "corpus": str(corpus_id),
           "candidate_limit": MAX_CORPUS_SECTIONS + 1}).mappings().one_or_none()
    if admission is None:
        return []
    _validate_corpus_size(admission["section_count"], admission["section_bytes"], admission["total_bytes"])
    _validate_metadata_size(admission["title_bytes"], admission["source_label_bytes"], admission["locator_bytes"])
    if (admission["embedding_model_revision"] != EMBEDDING_MODEL
            or admission["embedding_dimension"] != EMBEDDING_DIMENSION):
        raise CorpusAdmissionError("Corpus embedding dimension/version mismatch; rebuild a new corpus version")
    rows = conn.execute(text("""
        SELECT c.chunk_id, c.document_id, c.corpus_id, c.locator, c.body,
               CASE WHEN c.embedding_dimension=384 AND cardinality(c.embedding)=384
                         AND array_ndims(c.embedding)=1 THEN c.embedding
                    ELSE NULL END AS embedding,
               c.embedding_dimension, d.source_label, d.title,
               v.embedding_model_revision, v.embedding_dimension AS corpus_dimension,
               CASE WHEN :lexical_any THEN ts_rank_cd(c.lexical,websearch_to_tsquery('english',:lexical_query))
                    ELSE ts_rank_cd(c.lexical,plainto_tsquery('english',:query)) END AS lexical_score
        FROM resolvelab.chunks c
        JOIN resolvelab.documents d USING (tenant_id, corpus_id, document_id)
        JOIN resolvelab.corpus_versions v USING (tenant_id, corpus_id)
        WHERE c.tenant_id = :tenant AND c.corpus_id = :corpus
          AND v.state = 'published'
          AND (d.access_scope = 'support' OR :is_admin)
        ORDER BY c.chunk_id
        LIMIT :candidate_limit
    """), {"tenant": str(tenant_id), "corpus": str(corpus_id), "query": query,
           "is_admin": role == "admin","lexical_any":lexical_mode=="any","lexical_query":lexical_query,
           "candidate_limit": MAX_CORPUS_SECTIONS + 1}).mappings().all()
    if not rows:
        return []
    candidates = [dict(row) for row in rows]
    body_sizes = [len(row["body"].encode("utf-8")) for row in candidates]
    _validate_corpus_size(len(candidates), max(body_sizes), sum(body_sizes))
    for candidate in candidates:
        _validate_metadata_size(*(len(candidate[field].encode("utf-8")) for field in ("title", "source_label", "locator")))
        if (candidate["corpus_dimension"] != EMBEDDING_DIMENSION
                or candidate["embedding_model_revision"] != EMBEDDING_MODEL):
            raise CorpusAdmissionError("Corpus embedding dimension/version mismatch; rebuild a new corpus version")
        _validate_embedding(candidate["embedding"], candidate["embedding_dimension"])
    lexical = sorted(range(len(candidates)), key=lambda i: (-float(candidates[i]["lexical_score"]), str(candidates[i]["chunk_id"])))
    if lexical_mode=="any":lexical=[i for i in lexical if float(candidates[i]["lexical_score"])>0]
    dense = []
    if mode in {"dense", "hybrid"}:
        query_vector = embed([query])[0]
        _validate_embedding(query_vector, EMBEDDING_DIMENSION)
        for candidate in candidates:
            vector = candidate["embedding"]
            candidate["dense_score"] = float(np.dot(np.asarray(vector, dtype=np.float32), query_vector))
        dense = sorted(range(len(candidates)), key=lambda i: (-candidates[i]["dense_score"], str(candidates[i]["chunk_id"])))
    if mode == "lexical":
        ranking = lexical
        scores = {index: float(candidates[index]["lexical_score"]) for index in ranking}
    elif mode == "dense":
        ranking = dense
        scores = {index: candidates[index]["dense_score"] for index in ranking}
    else:
        scores = {i: 0.0 for i in range(len(candidates))}
        for ranked in (lexical, dense):
            for rank, index in enumerate(ranked, 1):
                scores[index] += 1.0 / (RRF_K + rank)
        ranking = sorted(scores, key=lambda i: (-scores[i], str(candidates[i]["chunk_id"])))
    result = []
    for index in ranking[:limit]:
        row = candidates[index]
        result.append({key: str(row[key]) if key.endswith("_id") else row[key]
                       for key in ("chunk_id", "document_id", "corpus_id", "locator", "body", "source_label", "title")}
                      | {"score": float(scores[index]), "mode": mode,"lexical_mode":lexical_mode,
                         "embedding_model": EMBEDDING_MODEL if mode != "lexical" else None})
    return result
