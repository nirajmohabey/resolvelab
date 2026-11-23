"""Real JWT signatures, database identity binding and browser request boundaries."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from resolvelab import auth
from resolvelab.config import validate_configuration
from resolvelab.db import admin_engine, transaction
from resolvelab.evaluate import principal, rows
from resolvelab.schemas import DomainError


@pytest.fixture
def identity_provider(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key())) | {"kid": "fixture-key", "alg": "RS256", "use": "sig"}
    client = jwt.PyJWKClient("https://identity.example.test/keys")
    monkeypatch.setattr(client, "fetch_data", lambda: {"keys": [jwk]})
    monkeypatch.setattr(auth, "_jwks_client", lambda url: client)
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_ISSUER", "https://identity.example.test/")
    monkeypatch.setenv("OIDC_JWKS_URL", "https://identity.example.test/keys")
    monkeypatch.setenv("OIDC_AUDIENCE", "resolvelab-api")
    actor = principal_for_mapping()
    with admin_engine.begin() as conn:
        conn.execute(text("INSERT INTO resolvelab.external_identities(issuer,external_subject,tenant_id,actor_id,customer_subject) VALUES(:issuer,'external-user',:tenant,:actor,'customer-001')"),
                     {"issuer": "https://identity.example.test/", "tenant": actor.tenant_id, "actor": actor.actor_id})
    def token(**overrides):
        now = datetime.now(timezone.utc)
        claims = {"iss": "https://identity.example.test/", "aud": "resolvelab-api", "sub": "external-user", "iat": now, "exp": now+timedelta(minutes=5),
                  "tenant_id": "22222222-2222-4222-8222-222222222222", "role": "admin", "customer_subject": "forged-customer"} | overrides
        return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "fixture-key"})
    return token, actor


def principal_for_mapping():
    # Compute fixture context before switching authentication mode to OIDC.
    from resolvelab.auth import Principal
    from resolvelab.seed import fixture_id
    return Principal("11111111-1111-4111-8111-111111111111", fixture_id("alpha", "alpha-operator"), "operator", "customer-001")


def test_oidc_signature_and_server_side_identity_ignore_tenant_claims(identity_provider):
    token, expected = identity_provider
    actual = auth.authenticate(token())
    assert (actual.tenant_id, actual.actor_id, actual.role, actual.customer_subject) == (expected.tenant_id, expected.actor_id, "operator", "customer-001")
    with transaction(actual) as conn:
        with pytest.raises(DBAPIError):
            conn.execute(text("SELECT * FROM resolvelab.external_identities"))


@pytest.mark.parametrize("claims", [{"aud": "wrong-api"}, {"iss": "https://attacker.test/"}, {"sub": "unregistered"}, {"exp": datetime(2020,1,1,tzinfo=timezone.utc)}])
def test_oidc_invalid_or_unregistered_identity_is_denied(identity_provider, claims):
    token, _ = identity_provider
    with pytest.raises(DomainError) as failure:
        auth.authenticate(token(**claims))
    assert failure.value.status == 401


def test_oidc_rejects_symmetric_algorithm_and_inactive_mapping(identity_provider):
    token, actor = identity_provider
    with pytest.raises(DomainError):
        auth.authenticate(jwt.encode({"sub": "external-user"}, "a"*32, algorithm="HS256", headers={"kid":"fixture-key"}))
    with admin_engine.begin() as conn:
        conn.execute(text("UPDATE resolvelab.external_identities SET active=false"))
    with pytest.raises(DomainError) as failure:
        auth.authenticate(token())
    assert failure.value.status == 401


def test_production_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    with pytest.raises(RuntimeError):
        validate_configuration()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.delenv("FIXTURE_CLOCK", raising=False)
    monkeypatch.setenv("AUTH_MODE", "fixture")
    with pytest.raises(RuntimeError, match="OIDC|oidc"):
        validate_configuration()


def test_security_headers_origin_host_and_json_gate(client):
    response = client.get("/")
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "script-src 'self';" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert client.get("/health/live", headers={"Host":"attacker.example"}).status_code == 400
    assert client.post("/v1/demo/session", json={"actor":"alpha-operator"}, headers={"Origin":"https://attacker.example"}).status_code == 403
    assert client.post("/v1/demo/session", content="not-json").status_code == 415
    from resolvelab.evaluate import auth_headers
    assert client.post("/v1/cases", json={"message":" \t\n "}, headers=auth_headers(key="blank-message")).status_code == 422
    allowed = client.options("/v1/cases", headers={"Origin":"http://testserver", "Access-Control-Request-Method":"POST", "Access-Control-Request-Headers":"authorization,idempotency-key"})
    assert allowed.status_code == 204
    assert allowed.headers["Access-Control-Allow-Origin"] == "http://testserver"
    assert "Access-Control-Allow-Credentials" not in allowed.headers


def test_shared_database_rate_limit_returns_retry_after(client, monkeypatch):
    from uuid import uuid4
    from resolvelab.security import consume_budget
    scope = "acceptance-test:"+str(uuid4())
    consume_budget(scope, 1)
    with pytest.raises(DomainError) as limited:
        consume_budget(scope, 1)
    assert limited.value.status == 429 and limited.value.retry_after > 0
    monkeypatch.setenv("LOGIN_REQUESTS_PER_MINUTE", "1")
    # A fresh client address avoids coupling this assertion to previous tests.
    from fastapi.testclient import TestClient
    from resolvelab.api import app
    with TestClient(app, client=("198.51.100.73", 44000)) as isolated:
        assert isolated.post("/v1/demo/session", json={"actor":"alpha-operator"}).status_code == 200
        response = isolated.post("/v1/demo/session", json={"actor":"alpha-operator"})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
