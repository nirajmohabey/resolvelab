"""OIDC identity boundary, shared request limits and immutable published evidence."""
from alembic import op

revision = "0002_security"
down_revision = "0001_core"


def upgrade():
    ddl = r"""
    CREATE TABLE resolvelab.external_identities (
      issuer text NOT NULL CHECK (issuer LIKE 'https://%'),
      external_subject text NOT NULL CHECK (length(external_subject) BETWEEN 1 AND 300),
      tenant_id uuid NOT NULL,
      actor_id uuid NOT NULL,
      customer_subject text NOT NULL CHECK (length(customer_subject) BETWEEN 1 AND 200),
      active boolean NOT NULL DEFAULT true,
      PRIMARY KEY(issuer, external_subject),
      FOREIGN KEY(tenant_id,actor_id) REFERENCES resolvelab.memberships(tenant_id,actor_id) ON DELETE CASCADE
    );
    REVOKE ALL ON resolvelab.external_identities FROM PUBLIC;
    CREATE FUNCTION resolvelab.resolve_external_identity(p_issuer text,p_subject text)
    RETURNS TABLE(tenant_id uuid,actor_id uuid,customer_subject text)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,resolvelab,pg_temp AS $$
    DECLARE identity_row resolvelab.external_identities%ROWTYPE;
    BEGIN
      SELECT e.* INTO identity_row FROM resolvelab.external_identities e
        WHERE e.issuer=p_issuer AND e.external_subject=p_subject AND e.active;
      IF NOT FOUND THEN RETURN; END IF;
      PERFORM set_config('app.tenant_id',identity_row.tenant_id::text,true);
      IF EXISTS (SELECT 1 FROM resolvelab.memberships m WHERE m.tenant_id=identity_row.tenant_id AND m.actor_id=identity_row.actor_id AND m.active) THEN
        RETURN QUERY SELECT identity_row.tenant_id,identity_row.actor_id,identity_row.customer_subject;
      END IF;
    END $$;
    REVOKE ALL ON FUNCTION resolvelab.resolve_external_identity(text,text) FROM PUBLIC;

    CREATE TABLE resolvelab.request_limits (
      bucket_key text NOT NULL,
      window_start timestamptz NOT NULL,
      hits integer NOT NULL CHECK(hits>0),
      PRIMARY KEY(bucket_key,window_start)
    );
    REVOKE ALL ON resolvelab.request_limits FROM PUBLIC;
    CREATE FUNCTION resolvelab.consume_rate_limit(p_key text,p_quota integer,p_window integer)
    RETURNS TABLE(allowed boolean,retry_after integer)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,resolvelab,pg_temp AS $$
    DECLARE bucket timestamptz; count_hits integer;
    BEGIN
      IF p_key !~ '^[a-f0-9]{64}$' OR p_quota NOT BETWEEN 1 AND 10000 OR p_window NOT BETWEEN 1 AND 3600 THEN
        RAISE EXCEPTION 'invalid rate-limit arguments';
      END IF;
      bucket=to_timestamp(floor(extract(epoch FROM clock_timestamp())/p_window)*p_window);
      DELETE FROM resolvelab.request_limits r WHERE (r.bucket_key,r.window_start) IN
        (SELECT x.bucket_key,x.window_start FROM resolvelab.request_limits x WHERE x.window_start < clock_timestamp()-interval '2 hours' LIMIT 100);
      INSERT INTO resolvelab.request_limits(bucket_key,window_start,hits) VALUES(p_key,bucket,1)
        ON CONFLICT(bucket_key,window_start) DO UPDATE SET hits=resolvelab.request_limits.hits+1 RETURNING hits INTO count_hits;
      RETURN QUERY SELECT count_hits<=p_quota,greatest(1,ceil(extract(epoch FROM bucket+make_interval(secs=>p_window)-clock_timestamp()))::integer);
    END $$;
    REVOKE ALL ON FUNCTION resolvelab.consume_rate_limit(text,integer,integer) FROM PUBLIC;

    CREATE FUNCTION resolvelab.immutable_row() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'immutable_record: create a new version instead of changing retained evidence' USING ERRCODE='23514'; END $$;
    CREATE TRIGGER immutable_model BEFORE UPDATE OR DELETE ON resolvelab.model_versions FOR EACH ROW EXECUTE FUNCTION resolvelab.immutable_row();
    CREATE TRIGGER immutable_release BEFORE UPDATE OR DELETE ON resolvelab.releases FOR EACH ROW EXECUTE FUNCTION resolvelab.immutable_row();
    CREATE TRIGGER immutable_trace BEFORE UPDATE OR DELETE ON resolvelab.trace_events FOR EACH ROW EXECUTE FUNCTION resolvelab.immutable_row();
    CREATE TRIGGER immutable_approval BEFORE UPDATE OR DELETE ON resolvelab.approvals FOR EACH ROW EXECUTE FUNCTION resolvelab.immutable_row();
    CREATE TRIGGER immutable_action BEFORE UPDATE OR DELETE ON resolvelab.actions FOR EACH ROW EXECUTE FUNCTION resolvelab.immutable_row();
    CREATE TRIGGER immutable_idempotency BEFORE UPDATE OR DELETE ON resolvelab.idempotency_keys FOR EACH ROW EXECUTE FUNCTION resolvelab.immutable_row();

    CREATE FUNCTION resolvelab.guard_corpus_version() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.state IN ('published','retired') THEN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable_corpus' USING ERRCODE='23514'; END IF;
        IF (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') OR
           (OLD.state='retired' AND NEW.state<>'retired') OR
           (OLD.state='published' AND NEW.state NOT IN ('published','retired')) THEN
          RAISE EXCEPTION 'immutable_corpus' USING ERRCODE='23514';
        END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER immutable_corpus BEFORE UPDATE OR DELETE ON resolvelab.corpus_versions FOR EACH ROW EXECUTE FUNCTION resolvelab.guard_corpus_version();
    CREATE FUNCTION resolvelab.guard_corpus_content() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE frozen boolean; existing boolean;
    BEGIN
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
    CREATE TRIGGER immutable_documents BEFORE INSERT OR UPDATE OR DELETE ON resolvelab.documents FOR EACH ROW EXECUTE FUNCTION resolvelab.guard_corpus_content();
    CREATE TRIGGER immutable_chunks BEFORE INSERT OR UPDATE OR DELETE ON resolvelab.chunks FOR EACH ROW EXECUTE FUNCTION resolvelab.guard_corpus_content();
    """
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(ddl)


def downgrade():
    raise RuntimeError("Restore a reviewed backup into a disposable database instead of destructive downgrade")
