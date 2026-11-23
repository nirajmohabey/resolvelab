"""Signed fixture identities; membership and permissions remain database decisions."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import os
from functools import lru_cache
from uuid import NAMESPACE_URL, UUID, uuid5

import jwt
from sqlalchemy import text

from .db import ROOT, engine, transaction
from .config import auth_mode, environment
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
    return environment() == "local" and auth_mode() == "fixture" and os.environ.get("DEMO_MODE", "false").lower() == "true"


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
    if not demo_enabled():
        raise DomainError(403, "fixture_auth_disabled", "Fixture tokens cannot be issued in this environment.")
    now = datetime.now(timezone.utc)
    return jwt.encode({"iss": "resolvelab", "aud": "resolvelab-api", "sub": principal.actor_id,
                       "tenant_id": principal.tenant_id, "customer_subject": principal.customer_subject,
                       "alias": principal.alias, "iat": now, "exp": now + timedelta(hours=2)},
                      _secret(), algorithm="HS256")


@lru_cache(maxsize=4)
def _jwks_client(url):
    return jwt.PyJWKClient(url, timeout=5, lifespan=300, cache_keys=False, cooldown_duration=30)


def _oidc_claims(token):
    issuer = os.environ.get("OIDC_ISSUER", "")
    audience = os.environ.get("OIDC_AUDIENCE", "")
    url = os.environ.get("OIDC_JWKS_URL", "")
    algorithm = os.environ.get("OIDC_ALGORITHM", "RS256")
    if not issuer.startswith("https://") or not url.startswith("https://") or not audience or algorithm not in {"RS256", "ES256"}:
        raise DomainError(503, "auth_not_configured", "The identity provider is not configured.")
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != algorithm or not isinstance(header.get("kid"), str) or len(header["kid"]) > 200:
            raise jwt.InvalidTokenError("Disallowed signing algorithm or key identifier")
        key = _jwks_client(url).get_signing_key_from_jwt(token)
        return jwt.decode(token, key.key, algorithms=[algorithm], issuer=issuer, audience=audience, leeway=10,
                          options={"require": ["exp", "iat", "sub", "iss", "aud"]})
    except jwt.PyJWKClientConnectionError:
        raise DomainError(503, "identity_provider_unavailable", "The identity signing keys are temporarily unavailable.", True) from None


def authenticate(token):
    if len(token) > 8192:
        raise DomainError(401, "invalid_token", "The bearer token is invalid or expired.")
    try:
        if auth_mode() in {"oidc", "neon"}:
            if auth_mode() == "neon":
                from .neon_auth import claims as neon_claims
                claims = neon_claims(token)
            else:
                claims = _oidc_claims(token)
            subject = claims["sub"]
            if not isinstance(subject, str) or not 1 <= len(subject) <= 300:
                raise ValueError("Invalid external subject")
            # Only verified issuer/subject enters this reviewed, minimal lookup.
            # Tenant, role and customer claims from the token are ignored.
            with engine.connect() as conn:
                mapped = conn.execute(text("SELECT * FROM resolvelab.resolve_external_identity(:issuer,:subject)"),
                                      {"issuer": claims["iss"], "subject": subject}).mappings().first()
            if not mapped:
                raise DomainError(401, "identity_not_registered", "The external identity has no active support membership.")
            principal = Principal(str(mapped["tenant_id"]), str(mapped["actor_id"]), "", mapped["customer_subject"])
            if environment() == "production":
                from .config import deployment_tenants
                if principal.tenant_id not in deployment_tenants():
                    raise DomainError(401, "identity_not_registered", "The identity has no active membership in this deployment.")
            with transaction(principal) as conn:
                return assert_member(conn, principal)
        if not demo_enabled():
            raise DomainError(401, "fixture_auth_disabled", "Fixture authentication is disabled.")
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
