"""Verify a newly created restore destination before workflow probes can write."""
import re
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from resolvelab.isolation import ROUTING_PARAMETERS, connected_identity


def check_restore_urls(runtime_url, admin_url, database):
    if not re.fullmatch(r"resolvelab_restore_[0-9a-f]{12}", database):
        raise RuntimeError("The restore target must be a newly owned verification database")
    for value in (runtime_url, admin_url):
        parsed = make_url(value)
        if parsed.drivername != "postgresql+psycopg" or parsed.database != database:
            raise RuntimeError("Restore credentials must name the exact owned database")
        if ROUTING_PARAMETERS.intersection(key.lower() for key in parsed.query):
            raise RuntimeError("Database routing overrides are forbidden for restore verification")


def verify_restore_destination(runtime_url, admin_url, database, source_identity):
    check_restore_urls(runtime_url, admin_url, database)
    identities = []
    for value in (runtime_url, admin_url):
        instance = create_engine(value, poolclass=NullPool, connect_args={"connect_timeout": 5})
        try:
            with instance.connect() as conn:
                identities.append(connected_identity(conn))
        finally:
            instance.dispose()
    if (identities[0] != identities[1] or identities[0]["database"] != database
            or identities[0]["database"] == source_identity["database"]
            or any(identities[0][field] != source_identity[field] for field in ("server", "port"))):
        raise RuntimeError("Restore verification did not reach the exact separate owned destination")
    return identities[0]
