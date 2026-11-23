"""Verify real database destinations before any automated fixture reset."""
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

DISPOSABLE_DATABASES={"resolvelab_test","resolvelab_evaluation"}
# In libpq these parameters can override the database/host encoded in the URL.
ROUTING_PARAMETERS={"dbname","database","host","hostaddr","port","user","password","service","servicefile"}


def connected_identity(conn):
    row=conn.execute(text("SELECT current_database() AS database,inet_server_addr()::text AS server,inet_server_port() AS port")).mappings().one()
    return dict(row)


def validate_disposable_urls(runtime_url,admin_url,expected_database):
    if expected_database not in DISPOSABLE_DATABASES:
        raise RuntimeError("Automatic fixture resets require an explicitly named disposable database.")
    for value in (runtime_url,admin_url):
        if not value:
            raise RuntimeError("Both disposable runtime and administration database URLs are required.")
        try:parsed=make_url(value)
        except Exception:raise RuntimeError("The disposable database URL is invalid.") from None
        if parsed.drivername!="postgresql+psycopg" or parsed.database!=expected_database:
            raise RuntimeError(f"Automatic fixture resets require the dedicated {expected_database} database.")
        if ROUTING_PARAMETERS.intersection(key.lower() for key in parsed.query):
            raise RuntimeError("Database-routing query overrides are forbidden for disposable database URLs.")
    identities=[]
    for value in (runtime_url,admin_url):
        instance=create_engine(value,poolclass=NullPool,connect_args={"connect_timeout":5})
        try:
            with instance.connect() as conn:identities.append(connected_identity(conn))
        except Exception:
            raise RuntimeError("The disposable database destination could not be verified.") from None
        finally:instance.dispose()
    if any(identity["database"]!=expected_database for identity in identities) or identities[0]!=identities[1]:
        raise RuntimeError("Runtime and administration connections must reach the same verified disposable database.")
    return identities[0]
