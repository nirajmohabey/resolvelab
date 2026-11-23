"""Short, tenant-scoped database transactions using the non-owner runtime role."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

if not os.environ.get("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL must be explicitly configured")
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True,
                       pool_size=int(os.environ.get("DB_POOL_SIZE", "1" if os.environ.get("VERCEL") else "5")), max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "0" if os.environ.get("VERCEL") else "5")),
                       pool_timeout=5, connect_args={"connect_timeout": 5})
admin_engine = create_engine(
    os.environ["ADMIN_DATABASE_URL"], pool_pre_ping=True, connect_args={"connect_timeout": 5},
) if os.environ.get("ADMIN_DATABASE_URL") else None


def primitive(value):
    """Produce JSON-compatible values without losing timezone or money semantics."""
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "_mapping"):
        value = dict(value._mapping)
    if isinstance(value, dict):
        return {str(k): primitive(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [primitive(v) for v in value]
    return value


def rows(result):
    return [primitive(dict(row)) for row in result.mappings()]


@contextmanager
def transaction(principal):
    """SET LOCAL is applied on every pooled transaction and cleared by commit/rollback."""
    def field(name):
        return principal[name] if isinstance(principal, dict) else getattr(principal, name)
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :tenant, true), set_config('app.actor_id', :actor, true)"),
                     {"tenant": str(field("tenant_id")), "actor": str(field("actor_id"))})
        conn.execute(text("SET LOCAL statement_timeout = '10s'"))
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        yield conn
