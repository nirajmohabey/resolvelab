"""Neon identity proves a subject; reviewed database membership grants authority."""
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt
import pytest
from sqlalchemy import text

from resolvelab import auth, neon_auth
from resolvelab.db import admin_engine
from resolvelab.seed import fixture_id
from resolvelab.schemas import DomainError


def test_neon_never_creates_membership_or_trusts_role_tenant_claims(monkeypatch):
    origin = "https://auth.example.test"
    monkeypatch.setenv("AUTH_MODE","neon")
    monkeypatch.setenv("NEON_AUTH_URL",origin+"/neondb/auth")
    monkeypatch.setenv("NEON_AUTH_JWKS_URL",origin+"/neondb/auth/.well-known/jwks.json")
    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(neon_auth,"_keys",lambda *_: {"review-key":private.public_key()})
    now = datetime.now(timezone.utc)
    token = jwt.encode({"iss":origin,"aud":origin,"sub":"review-user","iat":now,"exp":now+timedelta(minutes=5),
                        "tenant_id":"22222222-2222-4222-8222-222222222222","role":"admin","customer_subject":"forged"},
                       private,algorithm="EdDSA",headers={"kid":"review-key"})
    with pytest.raises(DomainError) as unregistered:
        auth.authenticate(token)
    assert unregistered.value.code == "identity_not_registered"
    with admin_engine.begin() as conn:
        conn.execute(text("""INSERT INTO resolvelab.external_identities
            (issuer,external_subject,tenant_id,actor_id,customer_subject) VALUES(:issuer,'review-user',:tenant,:actor,'customer-001')"""),
            {"issuer":origin,"tenant":"11111111-1111-4111-8111-111111111111","actor":fixture_id("alpha","alpha-operator")})
    actual = auth.authenticate(token)
    assert actual.role == "operator" and actual.tenant_id == "11111111-1111-4111-8111-111111111111"
    assert actual.customer_subject == "customer-001"
    monkeypatch.setenv("APP_ENV","production")
    monkeypatch.setenv("DEPLOYMENT_TENANTS","22222222-2222-4222-8222-222222222222")
    with pytest.raises(DomainError) as excluded:
        auth.authenticate(token)
    assert excluded.value.code == "identity_not_registered"
