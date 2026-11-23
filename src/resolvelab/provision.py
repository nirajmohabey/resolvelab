"""Reviewed runtime grants shared by native and container administration."""
from psycopg import sql

READ_TABLES = ("tenants", "memberships", "corpus_versions", "documents", "chunks", "model_versions", "releases", "active_releases", "fixture_orders", "cases", "trace_events", "proposals", "proposal_evidence", "approvals", "actions", "handoffs", "idempotency_keys", "evaluation_runs")
WRITE_TABLES = ("fixture_orders", "cases", "trace_events", "proposals", "proposal_evidence", "approvals", "actions", "handoffs", "idempotency_keys", "evaluation_runs")


def grant_runtime(conn, role_name):
    role = sql.Identifier(role_name)
    conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA resolvelab FROM {}").format(role))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA resolvelab TO {}").format(role))
    for table in READ_TABLES:
        conn.execute(sql.SQL("GRANT SELECT ON resolvelab.{} TO {}").format(sql.Identifier(table), role))
    for table in WRITE_TABLES:
        conn.execute(sql.SQL("GRANT INSERT, UPDATE ON resolvelab.{} TO {}").format(sql.Identifier(table), role))
    for signature in ("resolve_external_identity(text,text)", "consume_rate_limit(text,integer,integer)"):
        conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION resolvelab." + signature + " TO {}").format(role))
