"""Shared request budgets and browser policy without trusting forwarded clients."""
from hashlib import sha256
import json
import os

from sqlalchemy import text

from .db import engine
from .schemas import DomainError


def strict_request_json(body: bytes):
    """Bound JSON nesting and reject ambiguous/nonstandard values before routing."""
    value = body.decode("utf-8")
    depth, quoted, escaped = 0, False, False
    for character in value:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("JSON nesting exceeds the request contract")
        elif character in "]}":
            depth -= 1

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = item
        return result

    def invalid_constant(value):
        raise ValueError("Nonfinite JSON constant")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=invalid_constant)


def consume_budget(scope, quota, window=60):
    key = sha256(scope.encode()).hexdigest()
    with engine.begin() as conn:
        result = conn.execute(text("SELECT * FROM resolvelab.consume_rate_limit(:key,:quota,:window)"),
                              {"key": key, "quota": max(1, min(int(quota), 10000)), "window": window}).mappings().one()
    if not result["allowed"]:
        error = DomainError(429, "rate_limited", "The request budget is exhausted. Retry after the stated delay.", True)
        error.retry_after = result["retry_after"]
        raise error


def actor_budget(principal):
    consume_budget(f"actor:{principal.tenant_id}:{principal.actor_id}", os.environ.get("ACTOR_REQUESTS_PER_MINUTE", "300"))


def browser_policy(path):
    from urllib.parse import urlsplit
    from .config import public_auth_config
    scripts = "'self'"
    styles = "'self' 'unsafe-inline' https://fonts.googleapis.com"
    if path in {"/docs", "/redoc"}:
        scripts += " 'unsafe-inline' https://cdn.jsdelivr.net"
        styles += " https://cdn.jsdelivr.net"
    connects="'self'"
    configured=public_auth_config()
    if configured["mode"]=="oidc" and configured["configured"]:
        origins={urlsplit(configured[key]).scheme+"://"+urlsplit(configured[key]).netloc for key in ("token_endpoint","jwks_uri")}
        connects+=" "+" ".join(sorted(origins))
    return (f"default-src 'none'; script-src {scripts}; style-src {styles}; "
            f"font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src {connects}; "
            "base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'")
