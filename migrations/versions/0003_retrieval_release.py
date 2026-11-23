"""Pin retrieval behavior to an immutable release without changing existing cases."""
from alembic import op

revision="0003_retrieval_release"
down_revision="0002_security"


def upgrade():
    op.execute("ALTER TABLE resolvelab.releases ADD COLUMN retrieval_mode text NOT NULL DEFAULT 'hybrid' CHECK(retrieval_mode IN ('lexical','dense','hybrid'))")
    op.execute("ALTER TABLE resolvelab.releases ADD COLUMN retrieval_version text NOT NULL DEFAULT 'hybrid-rrf-v1'")


def downgrade():
    raise RuntimeError("Retain immutable release history; restore a reviewed backup into a disposable database")
