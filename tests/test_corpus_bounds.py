"""Actual PostgreSQL admission/locking checks and bounded retrieval regressions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from resolvelab.db import admin_engine, transaction
from resolvelab.retrieval import (
    CorpusAdmissionError, EMBEDDING_MODEL, MAX_CORPUS_BYTES,
    MAX_CORPUS_SECTIONS, MAX_SECTION_BYTES, retrieve,
)

UNIT = [1.0] + [0.0] * 383


def draft(conn, actor):
    ids = {"t": actor.tenant_id, "c": str(uuid4()), "d": str(uuid4())}
    conn.execute(text("""INSERT INTO resolvelab.corpus_versions
        (tenant_id,corpus_id,version_label,content_sha256,embedding_model_revision,embedding_dimension,state)
        VALUES(:t,:c,:label,repeat('0',64),:model,384,'draft')"""),
        {**ids, "label": "bounds-" + ids["c"], "model": EMBEDDING_MODEL})
    conn.execute(text("""INSERT INTO resolvelab.documents
        (tenant_id,corpus_id,document_id,source_label,title,content_sha256,effective_at,access_scope)
        VALUES(:t,:c,:d,'bounds-fixture','Bounds fixture',repeat('0',64),now(),'support')"""), ids)
    return ids


def add(conn, ids, *, body="Policy evidence", vector=UNIT, dimension=384, chunk=None, locator=None):
    chunk = chunk or str(uuid4())
    conn.execute(text("""INSERT INTO resolvelab.chunks
        (tenant_id,corpus_id,document_id,chunk_id,locator,body,embedding,embedding_dimension)
        VALUES(:t,:c,:d,:chunk,:locator,:body,:vector,:dimension)"""),
        {**ids, "chunk": chunk, "locator": locator or "Section " + chunk, "body": body,
         "vector": vector, "dimension": dimension})
    return chunk


def publish(conn, ids):
    conn.execute(text("UPDATE resolvelab.corpus_versions SET state='published' WHERE tenant_id=:t AND corpus_id=:c"), ids)


@pytest.mark.parametrize("vector,dimension", [
    ([float("nan")] + [0.0] * 383, 384),
    ([float("inf")] + [0.0] * 383, 384),
    ([-float("inf")] + [0.0] * 383, 384),
    ([0.0] * 384, 384),
    ([10.0] + [0.0] * 383, 384),
    ([None] + [0.0] * 383, 384),
    (None, None),
    ([1.0] + [0.0] * 382, 383),
])
def test_database_rejects_nonfinite_nonunit_missing_or_wrong_vectors(actor, vector, dimension):
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        with pytest.raises(IntegrityError, match="chunk_finite_unit_embedding"):
            with conn.begin_nested():
                add(conn, ids, vector=vector, dimension=dimension)
        assert conn.execute(text("SELECT count(*) FROM resolvelab.chunks WHERE tenant_id=:t AND corpus_id=:c"), ids).scalar_one() == 0


def test_section_limit_uses_utf8_bytes_and_accepts_boundary(actor):
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        add(conn, ids, body="é" * (MAX_SECTION_BYTES // 2))
        with pytest.raises(IntegrityError, match="chunk_body_byte_limit"):
            with conn.begin_nested():
                add(conn, ids, body="é" * (MAX_SECTION_BYTES // 2) + "x")
        publish(conn, ids)


@pytest.mark.parametrize("field,byte_limit,constraint", [
    ("title", 512, "document_title_byte_limit"),
    ("source_label", 256, "document_label_byte_limit"),
])
def test_document_metadata_limits_use_bytes(actor, field, byte_limit, constraint):
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        sql = text("UPDATE resolvelab.documents SET " + field + "=:value WHERE tenant_id=:t AND corpus_id=:c AND document_id=:d")
        conn.execute(sql, {**ids, "value": "é" * (byte_limit // 2)})
        with pytest.raises(IntegrityError, match=constraint):
            with conn.begin_nested():
                conn.execute(sql, {**ids, "value": "é" * (byte_limit // 2) + "x"})
        add(conn, ids)
        publish(conn, ids)


def test_locator_limit_uses_bytes(actor):
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        add(conn, ids, locator="é" * 128)
        with pytest.raises(IntegrityError, match="chunk_locator_byte_limit"):
            with conn.begin_nested():
                add(conn, ids, locator="é" * 128 + "x")
        publish(conn, ids)


def test_total_bytes_boundary_and_one_byte_overflow(actor):
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        for _ in range(MAX_CORPUS_BYTES // MAX_SECTION_BYTES):
            add(conn, ids, body="x" * MAX_SECTION_BYTES)
        with pytest.raises(IntegrityError, match="corpus_admission_limit"):
            with conn.begin_nested():
                add(conn, ids, body="x")
        publish(conn, ids)


def test_count_budget_includes_admin_sections_and_no_silent_topk_truncation(actor):
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        for _ in range(32):
            add(conn, ids)
        admin_doc = {**ids, "d": str(uuid4())}
        conn.execute(text("""INSERT INTO resolvelab.documents
            (tenant_id,corpus_id,document_id,source_label,title,content_sha256,effective_at,access_scope)
            VALUES(:t,:c,:d,'admin-bounds','Admin bounds',repeat('0',64),now(),'admin')"""), admin_doc)
        for _ in range(32):
            add(conn, admin_doc, body="ADMIN-BOUNDS-CANARY")
        with pytest.raises(IntegrityError, match="corpus_admission_limit"):
            with conn.begin_nested():
                add(conn, admin_doc)
        publish(conn, ids)
    with transaction(actor) as conn:
        result = retrieve(conn, ids["t"], ids["c"], "policy", "operator", mode="lexical", limit=20)
    assert len(result) == 20
    assert all("ADMIN-BOUNDS-CANARY" not in item["body"] for item in result)


@pytest.mark.parametrize("assignment", ["embedding_dimension=383", "embedding_model_revision='unreviewed-model'"])
def test_publication_requires_exact_embedding_metadata(actor, assignment):
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        add(conn, ids)
        # Assignment comes only from this fixed test parametrization.
        conn.execute(text("UPDATE resolvelab.corpus_versions SET " + assignment + " WHERE tenant_id=:t AND corpus_id=:c"), ids)
        with pytest.raises(IntegrityError, match="corpus_embedding_version"):
            with conn.begin_nested():
                publish(conn, ids)
        assert conn.execute(text("SELECT state FROM resolvelab.corpus_versions WHERE tenant_id=:t AND corpus_id=:c"), ids).scalar_one() == "draft"


def test_published_and_retired_content_remains_immutable_and_seed_can_replay(actor):
    from resolvelab.seed import seed
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        chunk = add(conn, ids)
        publish(conn, ids)
        for statement in [
            "UPDATE resolvelab.chunks SET body='changed' WHERE tenant_id=:t AND corpus_id=:c",
            "DELETE FROM resolvelab.chunks WHERE tenant_id=:t AND corpus_id=:c",
            "UPDATE resolvelab.documents SET title='changed' WHERE tenant_id=:t AND corpus_id=:c",
        ]:
            with pytest.raises(IntegrityError, match="immutable_policy"):
                with conn.begin_nested():
                    conn.execute(text(statement), ids)
        conn.execute(text("UPDATE resolvelab.corpus_versions SET state='retired' WHERE tenant_id=:t AND corpus_id=:c"), ids)
        with pytest.raises(IntegrityError, match="immutable_policy"):
            with conn.begin_nested():
                add(conn, ids)
        assert conn.execute(text("SELECT body FROM resolvelab.chunks WHERE tenant_id=:t AND chunk_id=:ch"), {**ids, "ch": chunk}).scalar_one() == "Policy evidence"
    seed(reset=False, write_manifest=False)


def background(action, ready, state):
    try:
        with admin_engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout='5s'"))
            state["pid"] = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
            ready.set()
            action(conn)
        return "committed"
    except DBAPIError as error:
        return str(getattr(error.orig, "sqlstate", "")) + ":" + str(error.orig)


def await_blocked(ready, state, blocker):
    assert ready.wait(3), "Independent database connection did not start"
    deadline = time.monotonic() + 3
    with admin_engine.connect() as observer:
        while time.monotonic() < deadline:
            blockers = observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": state["pid"]}).scalar_one()
            if blocker in blockers:
                return
            time.sleep(.01)
    raise AssertionError("Expected actual PostgreSQL parent-row contention")


def test_concurrent_draft_inserts_cannot_both_pass_last_section_slot(actor):
    with admin_engine.begin() as setup:
        ids = draft(setup, actor)
        for _ in range(MAX_CORPUS_SECTIONS - 1):
            add(setup, ids)
    with admin_engine.connect() as first, ThreadPoolExecutor(max_workers=1) as pool:
        tx = first.begin()
        try:
            blocker = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
            add(first, ids)
            ready, state = Event(), {}
            future = pool.submit(background, lambda conn: add(conn, ids), ready, state)
            await_blocked(ready, state, blocker)
            tx.commit()
            assert "corpus_admission_limit" in future.result(timeout=6)
        finally:
            if tx.is_active:
                tx.rollback()
    with admin_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM resolvelab.chunks WHERE tenant_id=:t AND corpus_id=:c"), ids).scalar_one() == 64
        publish(conn, ids)


def test_publication_waits_for_draft_insert_then_validates_committed_content(actor):
    with admin_engine.begin() as setup:
        ids = draft(setup, actor)
        for _ in range(63):
            add(setup, ids)
    with admin_engine.connect() as writer, ThreadPoolExecutor(max_workers=1) as pool:
        tx = writer.begin()
        try:
            blocker = writer.execute(text("SELECT pg_backend_pid()")).scalar_one()
            add(writer, ids)
            ready, state = Event(), {}
            future = pool.submit(background, lambda conn: publish(conn, ids), ready, state)
            await_blocked(ready, state, blocker)
            tx.commit()
            assert future.result(timeout=6) == "committed"
        finally:
            if tx.is_active:
                tx.rollback()
    with transaction(actor) as conn:
        assert len(retrieve(conn, ids["t"], ids["c"], "policy", "operator", mode="lexical", limit=4)) == 4


def test_repeatable_read_stale_writer_serializes_instead_of_overfilling(actor):
    with admin_engine.begin() as setup:
        ids = draft(setup, actor)
        for _ in range(63):
            add(setup, ids)
    ready, attempt, state = Event(), Event(), {}

    def stale_writer():
        try:
            with admin_engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
                with conn.begin():
                    conn.execute(text("SET LOCAL lock_timeout='5s'"))
                    state["pid"] = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
                    assert conn.execute(text("SELECT count(*) FROM resolvelab.chunks WHERE tenant_id=:t AND corpus_id=:c"), ids).scalar_one() == 63
                    ready.set()
                    assert attempt.wait(3)
                    add(conn, ids)
            return "committed"
        except DBAPIError as error:
            return str(getattr(error.orig, "sqlstate", ""))

    with admin_engine.connect() as first, ThreadPoolExecutor(max_workers=1) as pool:
        tx = first.begin()
        try:
            blocker = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
            future = pool.submit(stale_writer)
            assert ready.wait(3)
            add(first, ids)
            attempt.set()
            await_blocked(ready, state, blocker)
            tx.commit()
            assert future.result(timeout=6) == "40001"
        finally:
            attempt.set()
            if tx.is_active:
                tx.rollback()
    with admin_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM resolvelab.chunks WHERE tenant_id=:t AND corpus_id=:c"), ids).scalar_one() == 64
        publish(conn, ids)


def test_insert_waiting_for_publication_cannot_modify_frozen_corpus(actor):
    with admin_engine.begin() as setup:
        ids = draft(setup, actor)
        add(setup, ids)
    with admin_engine.connect() as publisher, ThreadPoolExecutor(max_workers=1) as pool:
        tx = publisher.begin()
        try:
            blocker = publisher.execute(text("SELECT pg_backend_pid()")).scalar_one()
            publish(publisher, ids)
            ready, state = Event(), {}
            future = pool.submit(background, lambda conn: add(conn, ids), ready, state)
            await_blocked(ready, state, blocker)
            tx.commit()
            assert "immutable_policy" in future.result(timeout=6)
        finally:
            if tx.is_active:
                tx.rollback()
    with admin_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM resolvelab.chunks WHERE tenant_id=:t AND corpus_id=:c"), ids).scalar_one() == 1


def test_historical_oversized_fixture_is_rejected_before_body_materialization(actor):
    # Controlled administrator fault injection affects this disposable fixture
    # only. It represents retained pre-migration evidence, which is not rewritten.
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        conn.exec_driver_sql("ALTER TABLE resolvelab.chunks DISABLE TRIGGER zz_corpus_chunk_admission")
        for _ in range(65):
            add(conn, ids)
        conn.exec_driver_sql("ALTER TABLE resolvelab.chunks ENABLE TRIGGER zz_corpus_chunk_admission")
        with pytest.raises(IntegrityError, match="corpus_admission_limit"):
            with conn.begin_nested():
                publish(conn, ids)
        conn.exec_driver_sql("ALTER TABLE resolvelab.corpus_versions DISABLE TRIGGER corpus_publication_admission")
        publish(conn, ids)
        conn.exec_driver_sql("ALTER TABLE resolvelab.corpus_versions ENABLE TRIGGER corpus_publication_admission")
    with transaction(actor) as conn:
        with pytest.raises(CorpusAdmissionError, match="admission limits"):
            retrieve(conn, ids["t"], ids["c"], "policy", "operator", mode="dense", limit=1)


def test_retained_wrong_shape_vector_fails_admission_without_inference(actor, monkeypatch):
    from resolvelab import retrieval
    monkeypatch.setattr(retrieval, "embed", lambda _: pytest.fail("Malformed retained vectors must fail before inference"))
    with admin_engine.begin() as conn:
        ids = draft(conn, actor)
        # Reconstruct one pre-0004 row in this disposable fixture, then restore
        # the NOT VALID check in the same transaction. Existing evidence remains
        # readable to an administrator but cannot enter serving inference.
        conn.exec_driver_sql("ALTER TABLE resolvelab.chunks DROP CONSTRAINT chunk_finite_unit_embedding")
        add(conn, ids, vector=[1.0] + [0.0] * 511, dimension=512)
        conn.exec_driver_sql("""ALTER TABLE resolvelab.chunks ADD CONSTRAINT chunk_finite_unit_embedding
            CHECK(resolvelab.valid_corpus_embedding(embedding,embedding_dimension)) NOT VALID""")
        with pytest.raises(IntegrityError, match="corpus_embedding_invalid"):
            with conn.begin_nested():
                publish(conn, ids)
        conn.exec_driver_sql("ALTER TABLE resolvelab.corpus_versions DISABLE TRIGGER corpus_publication_admission")
        publish(conn, ids)
        conn.exec_driver_sql("ALTER TABLE resolvelab.corpus_versions ENABLE TRIGGER corpus_publication_admission")
    with transaction(actor) as conn:
        with pytest.raises(CorpusAdmissionError, match="dimension/version mismatch"):
            retrieve(conn, ids["t"], ids["c"], "policy", "operator", mode="dense")
    with admin_engine.connect() as conn:
        assert conn.execute(text("SELECT cardinality(embedding) FROM resolvelab.chunks WHERE tenant_id=:t AND corpus_id=:c"), ids).scalar_one() == 512


@pytest.mark.parametrize("invalid_vector", [[float("nan")] + [0.] * 383, [float("inf")] + [0.] * 383, [10.] + [0.] * 383])
def test_serving_defense_rejects_invalid_vectors_before_ranking(invalid_vector, monkeypatch):
    from resolvelab import retrieval
    monkeypatch.setattr(retrieval, "embed", lambda _: pytest.fail("Invalid corpus must fail before model inference"))
    row = {"chunk_id": "section", "document_id": "document", "corpus_id": "corpus", "locator": "policy",
           "body": "Policy", "embedding": invalid_vector, "embedding_dimension": 384,
           "embedding_model_revision": EMBEDDING_MODEL, "corpus_dimension": 384,
           "source_label": "policy", "title": "Policy", "lexical_score": 1.}

    class Result:
        def mappings(self): return self
        def one_or_none(self):
            return {"embedding_model_revision": EMBEDDING_MODEL, "embedding_dimension": 384,
                    "section_count": 1, "section_bytes": 6, "total_bytes": 6,
                    "title_bytes": 6, "source_label_bytes": 6, "locator_bytes": 6}
        def all(self): return [row]

    class Connection:
        def execute(self, statement, parameters):
            assert parameters["candidate_limit"] == 65
            assert "LIMIT :candidate_limit" in str(statement)
            return Result()

    with pytest.raises(CorpusAdmissionError, match="finite unit vector"):
        retrieve(Connection(), "tenant", "corpus", "query", "operator", mode="dense")
