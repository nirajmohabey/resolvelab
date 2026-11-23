"""Permission filtering precedes PostgreSQL lexical and exact dense ranking."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from pathlib import Path

import numpy as np
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41/onnx-quint8-avx2"
EMBEDDING_DIMENSION = 384
RRF_K = 60


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


def retrieve(conn, tenant_id, corpus_id, query: str, role: str, mode: str = "hybrid", limit: int = 4) -> list[dict]:
    if mode not in {"lexical", "dense", "hybrid"}:
        raise ValueError("Unsupported retrieval mode")
    limit = max(1, min(int(limit), 20))
    rows = conn.execute(text("""
        SELECT c.chunk_id, c.document_id, c.corpus_id, c.locator, c.body,
               c.embedding, c.embedding_dimension, d.source_label, d.title,
               v.embedding_model_revision, v.embedding_dimension AS corpus_dimension,
               ts_rank_cd(c.lexical, plainto_tsquery('english', :query)) AS lexical_score
        FROM resolvelab.chunks c
        JOIN resolvelab.documents d USING (tenant_id, corpus_id, document_id)
        JOIN resolvelab.corpus_versions v USING (tenant_id, corpus_id)
        WHERE c.tenant_id = :tenant AND c.corpus_id = :corpus
          AND v.state = 'published'
          AND (d.access_scope = 'support' OR :is_admin)
        ORDER BY c.chunk_id
    """), {"tenant": str(tenant_id), "corpus": str(corpus_id), "query": query,
           "is_admin": role == "admin"}).mappings().all()
    if not rows:
        return []
    candidates = [dict(row) for row in rows]
    lexical = sorted(range(len(candidates)), key=lambda i: (-float(candidates[i]["lexical_score"]), str(candidates[i]["chunk_id"])))
    dense = []
    if mode in {"dense", "hybrid"}:
        query_vector = embed([query])[0]
        for candidate in candidates:
            vector = candidate["embedding"]
            if (vector is None or candidate["embedding_dimension"] != EMBEDDING_DIMENSION
                    or candidate["corpus_dimension"] != EMBEDDING_DIMENSION
                    or candidate["embedding_model_revision"] != EMBEDDING_MODEL or len(vector) != EMBEDDING_DIMENSION):
                raise RuntimeError("Corpus embedding dimension/version mismatch; rebuild a new corpus version")
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
                      | {"score": float(scores[index]), "mode": mode,
                         "embedding_model": EMBEDDING_MODEL if mode != "lexical" else None})
    return result
