"""Fence stale corpus snapshots and bound metadata without rewriting history."""
from alembic import op

revision = "0005_corpus_mvcc"
down_revision = "0004_corpus_bounds"


def upgrade():
    ddl = r"""
    ALTER TABLE resolvelab.documents ADD CONSTRAINT document_title_byte_limit
      CHECK(octet_length(title)<=512) NOT VALID;
    ALTER TABLE resolvelab.documents ADD CONSTRAINT document_label_byte_limit
      CHECK(octet_length(source_label)<=256) NOT VALID;
    ALTER TABLE resolvelab.chunks ADD CONSTRAINT chunk_locator_byte_limit
      CHECK(octet_length(locator)<=256) NOT VALID;

    CREATE OR REPLACE FUNCTION resolvelab.guard_corpus_content() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE frozen boolean; existing boolean;
    BEGIN
      IF TG_OP='INSERT' THEN
        PERFORM 1 FROM resolvelab.corpus_versions c WHERE c.tenant_id=NEW.tenant_id AND c.corpus_id=NEW.corpus_id FOR UPDATE;
      ELSIF TG_OP='DELETE' THEN
        PERFORM 1 FROM resolvelab.corpus_versions c WHERE c.tenant_id=OLD.tenant_id AND c.corpus_id=OLD.corpus_id FOR UPDATE;
      ELSE
        PERFORM 1 FROM resolvelab.corpus_versions c
          WHERE (c.tenant_id=OLD.tenant_id AND c.corpus_id=OLD.corpus_id)
             OR (c.tenant_id=NEW.tenant_id AND c.corpus_id=NEW.corpus_id)
          ORDER BY c.tenant_id,c.corpus_id FOR UPDATE;
      END IF;
      -- A lock alone does not refresh a REPEATABLE READ snapshot. A deliberate
      -- no-op draft UPDATE creates an MVCC conflict, causing stale concurrent
      -- writers to abort with 40001 instead of passing an old child-count check.
      -- Canonically ordered parent locks above are held before these writes.
      -- Published/retired parents and retained semantic metadata stay untouched.
      IF TG_OP='INSERT' THEN
        UPDATE resolvelab.corpus_versions c SET state=c.state
          WHERE c.tenant_id=NEW.tenant_id AND c.corpus_id=NEW.corpus_id AND c.state='draft';
      ELSIF TG_OP='DELETE' THEN
        UPDATE resolvelab.corpus_versions c SET state=c.state
          WHERE c.tenant_id=OLD.tenant_id AND c.corpus_id=OLD.corpus_id AND c.state='draft';
      ELSE
        UPDATE resolvelab.corpus_versions c SET state=c.state
          WHERE ((c.tenant_id=OLD.tenant_id AND c.corpus_id=OLD.corpus_id)
              OR (c.tenant_id=NEW.tenant_id AND c.corpus_id=NEW.corpus_id)) AND c.state='draft';
      END IF;
      IF TG_OP IN ('UPDATE','DELETE') THEN
        SELECT c.state IN ('published','retired') INTO frozen FROM resolvelab.corpus_versions c WHERE c.tenant_id=OLD.tenant_id AND c.corpus_id=OLD.corpus_id;
        IF frozen THEN RAISE EXCEPTION 'immutable_policy: publish a new corpus version' USING ERRCODE='23514'; END IF;
      END IF;
      IF TG_OP IN ('INSERT','UPDATE') THEN
        SELECT c.state IN ('published','retired') INTO frozen FROM resolvelab.corpus_versions c WHERE c.tenant_id=NEW.tenant_id AND c.corpus_id=NEW.corpus_id;
        IF frozen THEN
          existing=false;
          IF TG_OP='INSERT' THEN
            IF TG_TABLE_NAME='documents' THEN
              SELECT EXISTS(SELECT 1 FROM resolvelab.documents d WHERE d.tenant_id=NEW.tenant_id AND d.corpus_id=NEW.corpus_id AND d.document_id=NEW.document_id) INTO existing;
            ELSE
              SELECT EXISTS(SELECT 1 FROM resolvelab.chunks c WHERE c.tenant_id=NEW.tenant_id AND c.chunk_id=NEW.chunk_id) INTO existing;
            END IF;
          END IF;
          IF NOT existing THEN RAISE EXCEPTION 'immutable_policy: publish a new corpus version' USING ERRCODE='23514'; END IF;
        END IF;
        RETURN NEW;
      END IF;
      RETURN OLD;
    END $$;

    CREATE OR REPLACE FUNCTION resolvelab.guard_corpus_publication() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE section_count bigint; total_bytes bigint; section_bytes integer; valid_vectors boolean; locator_bytes integer;
    BEGIN
      IF NEW.state<>'published' THEN RETURN NEW; END IF;
      IF TG_OP='UPDATE' AND OLD.state<>'draft' THEN RETURN NEW; END IF;
      IF NEW.embedding_dimension IS DISTINCT FROM 384 OR NEW.embedding_model_revision IS DISTINCT FROM
        'sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41/onnx-quint8-avx2' THEN
        RAISE EXCEPTION 'corpus_embedding_version: publish the pinned 384-dimensional model' USING ERRCODE='23514';
      END IF;
      SELECT count(*),coalesce(sum(b.body_bytes),0),coalesce(max(b.body_bytes),0),coalesce(bool_and(b.valid_vector),true),coalesce(max(b.locator_bytes),0)
        INTO section_count,total_bytes,section_bytes,valid_vectors,locator_bytes
        FROM (SELECT octet_length(c.body) AS body_bytes,octet_length(c.locator) AS locator_bytes,
                     resolvelab.valid_corpus_embedding(c.embedding,c.embedding_dimension) AS valid_vector
              FROM resolvelab.chunks c WHERE c.tenant_id=NEW.tenant_id AND c.corpus_id=NEW.corpus_id LIMIT 65) b;
      IF section_count>64 OR section_bytes>16384 OR total_bytes>262144 THEN
        RAISE EXCEPTION 'corpus_admission_limit: at most 64 sections, 16 KiB per section and 256 KiB total' USING ERRCODE='23514';
      END IF;
      IF locator_bytes>256 OR EXISTS(
        SELECT 1 FROM resolvelab.documents d WHERE d.tenant_id=NEW.tenant_id AND d.corpus_id=NEW.corpus_id
          AND (octet_length(d.title)>512 OR octet_length(d.source_label)>256)) THEN
        RAISE EXCEPTION 'corpus_metadata_limit: title 512 bytes, source label and locator 256 bytes' USING ERRCODE='23514';
      END IF;
      IF NOT valid_vectors THEN
        RAISE EXCEPTION 'corpus_embedding_invalid: every section requires a finite unit 384-vector' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    """
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(ddl)


def downgrade():
    raise RuntimeError("Retain immutable corpus history; restore a reviewed backup into a disposable database")
