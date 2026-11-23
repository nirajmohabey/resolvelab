-- ResolveLab proposed core migration. PostgreSQL 17 compatible; no extensions.
-- Run migrations with a schema owner. Runtime uses a different non-owner role
-- with no superuser/BYPASSRLS privileges. Authentication sets app.tenant_id and
-- app.actor_id with SET LOCAL inside EVERY request/worker transaction.
-- RLS here enforces organization boundaries; role and order-ownership checks
-- must also be implemented in the domain service. Do not expose SQL credentials.

BEGIN;
CREATE SCHEMA IF NOT EXISTS resolvelab;

CREATE FUNCTION resolvelab.current_tenant() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.tenant_id', true), '')::uuid
$$;

CREATE TABLE resolvelab.tenants (
  tenant_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE resolvelab.memberships (
  tenant_id uuid NOT NULL REFERENCES resolvelab.tenants(tenant_id),
  actor_id uuid NOT NULL,
  role text NOT NULL CHECK (role IN ('operator','approver','evaluator','admin')),
  active boolean NOT NULL DEFAULT true,
  PRIMARY KEY (tenant_id, actor_id)
);

CREATE TABLE resolvelab.corpus_versions (
  tenant_id uuid NOT NULL REFERENCES resolvelab.tenants(tenant_id),
  corpus_id uuid NOT NULL DEFAULT gen_random_uuid(),
  version_label text NOT NULL,
  content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
  embedding_model_revision text,
  embedding_dimension integer CHECK (embedding_dimension > 0),
  state text NOT NULL CHECK (state IN ('draft','published','retired')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, corpus_id),
  UNIQUE (tenant_id, version_label)
);

CREATE TABLE resolvelab.documents (
  tenant_id uuid NOT NULL,
  corpus_id uuid NOT NULL,
  document_id uuid NOT NULL DEFAULT gen_random_uuid(),
  source_label text NOT NULL,
  title text NOT NULL,
  content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
  effective_at timestamptz NOT NULL,
  access_scope text NOT NULL DEFAULT 'support' CHECK (access_scope IN ('support','admin')),
  PRIMARY KEY (tenant_id, corpus_id, document_id),
  FOREIGN KEY (tenant_id, corpus_id)
    REFERENCES resolvelab.corpus_versions(tenant_id, corpus_id)
);

CREATE TABLE resolvelab.chunks (
  tenant_id uuid NOT NULL,
  corpus_id uuid NOT NULL,
  document_id uuid NOT NULL,
  chunk_id uuid NOT NULL DEFAULT gen_random_uuid(),
  locator text NOT NULL,
  body text NOT NULL,
  embedding real[],
  embedding_dimension integer,
  lexical tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED,
  PRIMARY KEY (tenant_id, chunk_id),
  UNIQUE (tenant_id, corpus_id, document_id, locator),
  FOREIGN KEY (tenant_id, corpus_id, document_id)
    REFERENCES resolvelab.documents(tenant_id, corpus_id, document_id),
  CHECK ((embedding IS NULL AND embedding_dimension IS NULL)
      OR (embedding IS NOT NULL AND embedding_dimension > 0
          AND array_ndims(embedding) = 1
          AND cardinality(embedding) = embedding_dimension))
);
CREATE INDEX chunks_lexical_idx ON resolvelab.chunks USING gin (lexical);
CREATE INDEX chunks_corpus_idx ON resolvelab.chunks (tenant_id, corpus_id);
-- Core release can rank the small filtered embedding arrays in Python.
-- Add optional pgvector storage/index only after choosing a fixed model revision
-- and dimension. Cross-check dimension against corpus metadata in the indexer.

CREATE TABLE resolvelab.model_versions (
  tenant_id uuid NOT NULL REFERENCES resolvelab.tenants(tenant_id),
  model_id uuid NOT NULL DEFAULT gen_random_uuid(),
  version_label text NOT NULL,
  artifact_uri text NOT NULL,
  artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[a-f0-9]{64}$'),
  dataset_sha256 text NOT NULL CHECK (dataset_sha256 ~ '^[a-f0-9]{64}$'),
  split_manifest_sha256 text NOT NULL CHECK (split_manifest_sha256 ~ '^[a-f0-9]{64}$'),
  algorithm text NOT NULL,
  validation_threshold numeric(6,5) NOT NULL CHECK (validation_threshold BETWEEN 0 AND 1),
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metrics) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, model_id),
  UNIQUE (tenant_id, version_label)
);

CREATE TABLE resolvelab.releases (
  tenant_id uuid NOT NULL,
  release_id uuid NOT NULL DEFAULT gen_random_uuid(),
  model_id uuid NOT NULL,
  corpus_id uuid NOT NULL,
  prompt_version text NOT NULL,
  tool_policy_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, release_id),
  FOREIGN KEY (tenant_id, model_id) REFERENCES resolvelab.model_versions(tenant_id, model_id),
  FOREIGN KEY (tenant_id, corpus_id) REFERENCES resolvelab.corpus_versions(tenant_id, corpus_id)
);
CREATE TABLE resolvelab.active_releases (
  tenant_id uuid PRIMARY KEY REFERENCES resolvelab.tenants(tenant_id),
  release_id uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, release_id) REFERENCES resolvelab.releases(tenant_id, release_id)
);

CREATE TABLE resolvelab.fixture_orders (
  tenant_id uuid NOT NULL REFERENCES resolvelab.tenants(tenant_id),
  order_ref text NOT NULL,
  customer_subject text NOT NULL,
  status text NOT NULL CHECK (status IN ('pending','shipped','cancelled')),
  tracking_summary text NOT NULL,
  cancel_deadline timestamptz NOT NULL,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, order_ref)
);

CREATE TABLE resolvelab.cases (
  tenant_id uuid NOT NULL,
  case_id uuid NOT NULL DEFAULT gen_random_uuid(),
  release_id uuid NOT NULL,
  created_by uuid NOT NULL,
  customer_subject text NOT NULL,
  order_ref text,
  message text NOT NULL CHECK (length(message) BETWEEN 1 AND 6000),
  state text NOT NULL DEFAULT 'new' CHECK (state IN
    ('new','triaged','needs_info','draft_ready','awaiting_approval','executing','resolved','escalated','failed')),
  intent text CHECK (intent IN ('track_order','cancel_request','human_handoff','out_of_scope')),
  routing_score numeric(6,5) CHECK (routing_score BETWEEN 0 AND 1),
  resolution jsonb CHECK (resolution IS NULL OR jsonb_typeof(resolution) = 'object'),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, case_id),
  FOREIGN KEY (tenant_id, release_id) REFERENCES resolvelab.releases(tenant_id, release_id),
  FOREIGN KEY (tenant_id, created_by) REFERENCES resolvelab.memberships(tenant_id, actor_id)
  -- order_ref intentionally is not a foreign key: an unknown reference is a
  -- valid support input. Domain code checks existence, tenant and ownership.
);

CREATE TABLE resolvelab.trace_events (
  tenant_id uuid NOT NULL,
  trace_id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  request_id uuid NOT NULL,
  stage text NOT NULL CHECK (stage IN ('auth','route','retrieve','generate','validate','propose','approve','execute','handoff')),
  outcome text NOT NULL CHECK (outcome IN ('ok','denied','abstained','error')),
  duration_ms numeric(12,3) CHECK (duration_ms >= 0),
  input_tokens integer CHECK (input_tokens >= 0),
  output_tokens integer CHECK (output_tokens >= 0),
  estimated_cost_usd numeric(14,8) CHECK (estimated_cost_usd >= 0),
  cost_basis text,
  details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, trace_id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES resolvelab.cases(tenant_id, case_id)
);
CREATE INDEX trace_case_idx ON resolvelab.trace_events (tenant_id, case_id, created_at);

CREATE TABLE resolvelab.proposals (
  tenant_id uuid NOT NULL,
  proposal_id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  tool_name text NOT NULL CHECK (tool_name = 'cancel_order'),
  order_ref text NOT NULL,
  expected_order_version integer NOT NULL CHECK (expected_order_version > 0),
  expected_case_version integer NOT NULL CHECK (expected_case_version > 0),
  proposal_sha256 text NOT NULL CHECK (proposal_sha256 ~ '^[a-f0-9]{64}$'),
  tool_policy_version text NOT NULL,
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','approved','rejected','expired','executed','failed')),
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, proposal_id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES resolvelab.cases(tenant_id, case_id),
  FOREIGN KEY (tenant_id, order_ref) REFERENCES resolvelab.fixture_orders(tenant_id, order_ref),
  UNIQUE (tenant_id, case_id, proposal_sha256)
);

CREATE TABLE resolvelab.proposal_evidence (
  tenant_id uuid NOT NULL,
  proposal_id uuid NOT NULL,
  chunk_id uuid NOT NULL,
  PRIMARY KEY (tenant_id, proposal_id, chunk_id),
  FOREIGN KEY (tenant_id, proposal_id) REFERENCES resolvelab.proposals(tenant_id, proposal_id),
  FOREIGN KEY (tenant_id, chunk_id) REFERENCES resolvelab.chunks(tenant_id, chunk_id)
);

CREATE TABLE resolvelab.approvals (
  tenant_id uuid NOT NULL,
  proposal_id uuid NOT NULL,
  actor_id uuid NOT NULL,
  approved_sha256 text NOT NULL CHECK (approved_sha256 ~ '^[a-f0-9]{64}$'),
  approved_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, proposal_id),
  FOREIGN KEY (tenant_id, proposal_id) REFERENCES resolvelab.proposals(tenant_id, proposal_id),
  FOREIGN KEY (tenant_id, actor_id) REFERENCES resolvelab.memberships(tenant_id, actor_id)
  -- Application verifies active approver/admin role and exact proposal digest.
);

CREATE TABLE resolvelab.actions (
  tenant_id uuid NOT NULL,
  action_id uuid NOT NULL DEFAULT gen_random_uuid(),
  proposal_id uuid NOT NULL,
  status text NOT NULL CHECK (status IN ('succeeded','failed','unknown')),
  connector text NOT NULL DEFAULT 'local_fixture',
  connector_idempotency_key text NOT NULL,
  attempts integer NOT NULL DEFAULT 1 CHECK (attempts > 0),
  result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, action_id),
  UNIQUE (tenant_id, proposal_id),
  UNIQUE (tenant_id, connector, connector_idempotency_key),
  FOREIGN KEY (tenant_id, proposal_id) REFERENCES resolvelab.approvals(tenant_id, proposal_id)
);

CREATE TABLE resolvelab.handoffs (
  tenant_id uuid NOT NULL,
  case_id uuid NOT NULL,
  reason_code text NOT NULL,
  queue_name text NOT NULL DEFAULT 'support_review',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, case_id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES resolvelab.cases(tenant_id, case_id)
);

CREATE TABLE resolvelab.idempotency_keys (
  tenant_id uuid NOT NULL,
  actor_id uuid NOT NULL,
  operation text NOT NULL,
  idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
  request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
  response_status integer NOT NULL CHECK (response_status BETWEEN 200 AND 599),
  response_body jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key),
  FOREIGN KEY (tenant_id, actor_id) REFERENCES resolvelab.memberships(tenant_id, actor_id)
);

CREATE TABLE resolvelab.evaluation_runs (
  tenant_id uuid NOT NULL,
  evaluation_id uuid NOT NULL DEFAULT gen_random_uuid(),
  release_id uuid NOT NULL,
  created_by uuid NOT NULL,
  dataset_sha256 text NOT NULL CHECK (dataset_sha256 ~ '^[a-f0-9]{64}$'),
  mode text NOT NULL CHECK (mode IN ('deterministic_invariants','live_model')),
  state text NOT NULL CHECK (state IN ('queued','running','completed','failed')),
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metrics) = 'object'),
  case_results_uri text,
  environment jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, evaluation_id),
  FOREIGN KEY (tenant_id, release_id) REFERENCES resolvelab.releases(tenant_id, release_id),
  FOREIGN KEY (tenant_id, created_by) REFERENCES resolvelab.memberships(tenant_id, actor_id)
);

DO $$
DECLARE table_name text;
BEGIN
  FOR table_name IN SELECT t.tablename FROM pg_tables t WHERE t.schemaname = 'resolvelab'
  LOOP
    EXECUTE format('ALTER TABLE resolvelab.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE resolvelab.%I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON resolvelab.%I USING (tenant_id = resolvelab.current_tenant()) WITH CHECK (tenant_id = resolvelab.current_tenant())', table_name);
  END LOOP;
END $$;
COMMIT;

-- Tenant context must be re-established for each pooled-connection transaction.
-- Runtime privileges are provisioned separately: grant schema usage and only
-- the table operations needed by the API/worker; do not grant DDL or TRUNCATE.
-- State transitions, immutable published artifacts, ownership, and approvals
-- need service-level guards and integration tests beyond these constraints.
