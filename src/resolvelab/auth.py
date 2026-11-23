"""Signed fixture identities; membership and permissions remain database decisions."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import NAMESPACE_URL, UUID, uuid5

import jwt
from sqlalchemy import text

from .db import ROOT, transaction
from .schemas import DomainError


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    actor_id: str
    role: str
    customer_subject: str
    alias: str = ""

    def as_dict(self):
        return asdict(self)


def _secret():
    value = os.environ.get("AUTH_SECRET", "")
    if len(value) < 32:
        raise DomainError(503, "auth_not_configured", "AUTH_SECRET must contain at least 32 characters.")
    return value


def demo_enabled():
    return os.environ.get("APP_ENV", "local") == "local" and os.environ.get("DEMO_MODE", "false").lower() == "true"


def assert_member(conn, principal, roles=None):
    member = conn.execute(text("SELECT role FROM resolvelab.memberships WHERE tenant_id=:tenant AND actor_id=:actor AND active"),
                          {"tenant": principal.tenant_id, "actor": principal.actor_id}).mappings().first()
    if member is None:
        raise DomainError(401, "invalid_membership", "The authenticated membership is no longer active.")
    role = member["role"]
    if roles and role not in roles:
        raise DomainError(403, "role_forbidden", "This action requires a different organization role.")
    return replace(principal, role=role)


def fixture_principal(alias):
    if not demo_enabled():
        raise DomainError(404, "not_found", "The requested resource was not found.")
    data = json.loads((ROOT / "fixtures" / "evaluation_cases.json").read_text(encoding="utf-8"))
    actor = next((a for a in data["actors"] if a["alias"] == alias), None)
    if alias == "alpha-admin":
        actor = {"alias": alias, "organization": "alpha", "role": "admin", "customer_subject": "customer-001"}
    if actor is None:
        raise DomainError(404, "not_found", "The requested fixture actor was not found.")
    org = actor["organization"]
    tenant = next(x["tenant_id"] for x in data["organizations"] if x["alias"] == org)
    principal = Principal(tenant, str(uuid5(NAMESPACE_URL, f"resolvelab/{org}/{alias}")), actor["role"], actor["customer_subject"], alias)
    with transaction(principal) as conn:
        return assert_member(conn, principal)


def issue_token(principal):
    now = datetime.now(timezone.utc)
    return jwt.encode({"iss": "resolvelab", "aud": "resolvelab-api", "sub": principal.actor_id,
                       "tenant_id": principal.tenant_id, "customer_subject": principal.customer_subject,
                       "alias": principal.alias, "iat": now, "exp": now + timedelta(hours=2)},
                      _secret(), algorithm="HS256")


def authenticate(token):
    try:
        claims = jwt.decode(token, _secret(), algorithms=["HS256"], audience="resolvelab-api", issuer="resolvelab",
                            options={"require": ["exp", "iat", "sub", "tenant_id", "customer_subject"]})
        subject = claims["customer_subject"]
        if not isinstance(subject, str) or not 1 <= len(subject) <= 200:
            raise ValueError("invalid customer context")
        principal = Principal(str(UUID(str(claims["tenant_id"]))), str(UUID(str(claims["sub"]))), "", subject, claims.get("alias", ""))
        with transaction(principal) as conn:
            return assert_member(conn, principal)
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise DomainError(401, "invalid_token", "The bearer token is invalid or expired.") from None
