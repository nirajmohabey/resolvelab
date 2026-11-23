"""Bound corpus admission and serialize publication with draft content changes.

Existing published/retired evidence is retained exactly. NOT VALID checks enforce
new writes; every draft publication is checked and retrieval rejects an oversized
historical corpus without silently truncating its evidence.
"""
from alembic import op

revision = "0004_corpus_bounds"
down_revision = "0003_retrieval_release"


def upgrade():
    ddl = r"""
    CREATE FUNCTION resolvelab.valid_corpus_embedding(p_vector real[], p_dimension integer)
    RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
      SELECT coalesce(
        p_dimension=384 AND array_ndims(p_vector)=1 AND cardinality(p_vector)=384
        AND NOT EXISTS(SELECT 1 FROM unnest(p_vector) x WHERE x IS NULL OR x::text IN ('NaN','Infinity','-Infinity'))
        AND abs(sqrt((SELECT sum(x::double precision*x::double precision) FROM unnest(p_vector) x))-1.0)<=0.001,
        false)
    $$;
    ALTER TABLE resolvelab.chunks ADD CONSTRAINT chunk_body_byte_limit
      CHECK(octet_length(body)<=16384) NOT VALID;
    ALTER TABLE resolvelab.chunks ADD CONSTRAINT chunk_finite_unit_embedding
      CHECK(resolvelab.valid_corpus_embedding(embedding,embedding_dimension)) NOT VALID;

    -- Every content mutation locks the parent first. Publication's UPDATE holds
    -- that same row lock, so it cannot validate concurrently with a draft insert.
    -- Keep the existing conflict/no-op insertion behavior used by idempotent seed.
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

    CREATE FUNCTION resolvelab.guard_chunk_admission() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE corpus_state text; section_count bigint; total_bytes bigint;
    BEGIN
      SELECT c.state INTO corpus_state FROM resolvelab.corpus_versions c
        WHERE c.tenant_id=NEW.tenant_id AND c.corpus_id=NEW.corpus_id FOR UPDATE;
      -- Historical versions are immutable via the existing content trigger;
      -- an ON CONFLICT DO NOTHING replay must remain possible.
      IF corpus_state IS DISTINCT FROM 'draft' THEN RETURN NEW; END IF;
      SELECT count(*),coalesce(sum(b.body_bytes),0) INTO section_count,total_bytes
        FROM (SELECT octet_length(c.body) AS body_bytes FROM resolvelab.chunks c
              WHERE c.tenant_id=NEW.tenant_id AND c.corpus_id=NEW.corpus_id AND c.chunk_id<>NEW.chunk_id
              LIMIT 65) b;
      IF section_count+1>64 OR total_bytes+octet_length(NEW.body)>262144 THEN
        RAISE EXCEPTION 'corpus_admission_limit: at most 64 sections and 256 KiB total' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    -- PostgreSQL runs same-kind triggers alphabetically: immutable_chunks first
    -- locks both old/new parents in canonical order for a draft cross-corpus move.
    CREATE TRIGGER zz_corpus_chunk_admission BEFORE INSERT OR UPDATE ON resolvelab.chunks
      FOR EACH ROW EXECUTE FUNCTION resolvelab.guard_chunk_admission();

    CREATE FUNCTION resolvelab.guard_corpus_publication() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE section_count bigint; total_bytes bigint; section_bytes integer; valid_vectors boolean;
    BEGIN
      IF NEW.state<>'published' THEN RETURN NEW; END IF;
      IF TG_OP='UPDATE' AND OLD.state<>'draft' THEN RETURN NEW; END IF;
      IF NEW.embedding_dimension IS DISTINCT FROM 384 OR NEW.embedding_model_revision IS DISTINCT FROM
        'sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41/onnx-quint8-avx2' THEN
        RAISE EXCEPTION 'corpus_embedding_version: publish the pinned 384-dimensional model' USING ERRCODE='23514';
      END IF;
      SELECT count(*),coalesce(sum(b.body_bytes),0),coalesce(max(b.body_bytes),0),coalesce(bool_and(b.valid_vector),true)
        INTO section_count,total_bytes,section_bytes,valid_vectors
        FROM (SELECT octet_length(c.body) AS body_bytes,
                     resolvelab.valid_corpus_embedding(c.embedding,c.embedding_dimension) AS valid_vector
              FROM resolvelab.chunks c WHERE c.tenant_id=NEW.tenant_id AND c.corpus_id=NEW.corpus_id LIMIT 65) b;
      IF section_count>64 OR section_bytes>16384 OR total_bytes>262144 THEN
        RAISE EXCEPTION 'corpus_admission_limit: at most 64 sections, 16 KiB per section and 256 KiB total' USING ERRCODE='23514';
      END IF;
      IF NOT valid_vectors THEN
        RAISE EXCEPTION 'corpus_embedding_invalid: every section requires a finite unit 384-vector' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER corpus_publication_admission BEFORE INSERT OR UPDATE ON resolvelab.corpus_versions
      FOR EACH ROW EXECUTE FUNCTION resolvelab.guard_corpus_publication();
    """
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(ddl)


def downgrade():
    raise RuntimeError("Retain immutable corpus history; restore a reviewed backup into a disposable database")
