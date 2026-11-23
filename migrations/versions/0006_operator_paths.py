"""Retain audit stages for explicit follow-up and declined proposals."""
from alembic import op

revision = "0006_operator_paths"
down_revision = "0005_corpus_mvcc"


def upgrade():
    op.execute("ALTER TABLE resolvelab.trace_events DROP CONSTRAINT trace_events_stage_check")
    op.execute("""ALTER TABLE resolvelab.trace_events ADD CONSTRAINT trace_events_stage_check
        CHECK(stage IN ('auth','route','retrieve','generate','validate','read','propose','approve','execute','handoff','context_updated','reject'))""")


def downgrade():
    raise RuntimeError("Retained follow-up/decline audit stages cannot be removed without an explicit archival migration.")
