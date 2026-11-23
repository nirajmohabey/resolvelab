"""Durable fenced reports and transactional queue outbox."""
from alembic import op

revision = "0007_serverless_jobs"
down_revision = "0006_operator_paths"


def upgrade():
    op.execute("""
        CREATE TABLE resolvelab.evaluation_reports (
            tenant_id uuid NOT NULL, evaluation_id uuid NOT NULL,
            report jsonb NOT NULL CHECK(jsonb_typeof(report)='object' AND octet_length(report::text)<=4194304),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(tenant_id,evaluation_id),
            FOREIGN KEY(tenant_id,evaluation_id) REFERENCES resolvelab.evaluation_runs(tenant_id,evaluation_id) ON DELETE CASCADE);
        CREATE TABLE resolvelab.evaluation_outbox (
            tenant_id uuid NOT NULL, evaluation_id uuid NOT NULL,
            dispatch_token uuid, lease_expires_at timestamptz,
            available_at timestamptz NOT NULL DEFAULT now(), delivered_at timestamptz,
            dispatch_attempts integer NOT NULL DEFAULT 0 CHECK(dispatch_attempts>=0),
            last_error_code text,
            PRIMARY KEY(tenant_id,evaluation_id),
            FOREIGN KEY(tenant_id,evaluation_id) REFERENCES resolvelab.evaluation_runs(tenant_id,evaluation_id) ON DELETE CASCADE);
        CREATE INDEX evaluation_outbox_ready ON resolvelab.evaluation_outbox(tenant_id,available_at)
            WHERE delivered_at IS NULL;
        ALTER TABLE resolvelab.evaluation_reports ENABLE ROW LEVEL SECURITY;
        ALTER TABLE resolvelab.evaluation_reports FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON resolvelab.evaluation_reports
            USING(tenant_id=resolvelab.current_tenant()) WITH CHECK(tenant_id=resolvelab.current_tenant());
        ALTER TABLE resolvelab.evaluation_outbox ENABLE ROW LEVEL SECURITY;
        ALTER TABLE resolvelab.evaluation_outbox FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON resolvelab.evaluation_outbox
            USING(tenant_id=resolvelab.current_tenant()) WITH CHECK(tenant_id=resolvelab.current_tenant());
        REVOKE ALL ON resolvelab.evaluation_reports,resolvelab.evaluation_outbox FROM PUBLIC;
    """)


def downgrade():
    raise RuntimeError("Retained reports and queued work require reviewed archival, not destructive downgrade")
