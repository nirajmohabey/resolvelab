"""Create reviewed core schema and tenant policies."""
from pathlib import Path
from alembic import op

revision = '0001_core'
down_revision = None

def upgrade():
    ddl = (Path(__file__).resolve().parents[1] / '001_core.sql').read_text(encoding='utf-8')
    ddl = ddl.replace('BEGIN;\nCREATE SCHEMA', 'CREATE SCHEMA').replace('END $$;\nCOMMIT;', 'END $$;')
    # No parameters: PostgreSQL format('%I') must not be parsed as a DBAPI bind.
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(ddl)

def downgrade():
    raise RuntimeError('Destructive downgrade is disabled. Restore a reviewed backup into a disposable database.')
