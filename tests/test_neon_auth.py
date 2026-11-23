"""Real Ed25519 signatures, bounded JWKS and same-origin auth proxy contracts."""
import asyncio
from datetime import datetime, timedelta, timezone
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import jwt
import pytest

from resolvelab import neon_auth
from resolvelab.schemas import DomainError

BASE = "https://auth.example.test/neondb/auth"
ORIGIN = "https://auth.example.test"


@pytest.fixture
def neon(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("AUTH_MODE", "neon")
    monkeypatch.setenv("NEON_AUTH_URL", BASE)
    monkeypatch.setenv("NEON_AUTH_JWKS_URL", BASE + "/.well-known/jwks.json")
    private = Ed25519PrivateKey.generate()
    key = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(private.public_key())) | {"kid":"review-key","alg":"EdDSA"}
    calls = []
    original = httpx.Client

    def fetch(request):
        calls.append(request)
        assert str(request.url) == BASE + "/.well-known/jwks.json"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, json={"keys":[key]})

    monkeypatch.setattr(neon_auth.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(fetch), **kw))
    neon_auth._cache.clear()
    neon_auth._attempts.clear()

    def token(**overrides):
        now = datetime.now(timezone.utc)
        return jwt.encode({"sub":"review-user","iss":ORIGIN,"aud":ORIGIN,"iat":now,
                           "exp":now+timedelta(minutes=5),"role":"admin","tenant_id":"forged"} | overrides,
                          private, algorithm="EdDSA", headers={"kid":"review-key"})
    yield token, calls
    neon_auth._cache.clear()
    neon_auth._attempts.clear()


def test_neon_requires_real_signature_exact_issuer_audience_and_cached_keys(neon):
    token, calls = neon
    assert neon_auth.claims(token())["sub"] == "review-user"
    assert neon_auth.claims(token())["sub"] == "review-user"
    assert len(calls) == 1
    for changes in ({"aud":"other"}, {"aud":[ORIGIN,"other"]}, {"iss":BASE}, {"exp":datetime(2000,1,1,tzinfo=timezone.utc)}):
        with pytest.raises(DomainError) as failure:
            neon_auth.claims(token(**changes))
        assert failure.value.status == 401


def test_neon_rejects_algorithm_confusion_before_jwks_request(neon):
    _, calls = neon
    bad = jwt.encode({"sub":"review-user"}, "fixture-key-" * 5, algorithm="HS256", headers={"kid":"review-key"})
    with pytest.raises(DomainError) as failure:
        neon_auth.claims(bad)
    assert failure.value.status == 401 and not calls


def test_neon_unknown_key_refresh_is_bounded(neon):
    token, calls = neon
    neon_auth.claims(token())
    private = Ed25519PrivateKey.generate()
    for index in range(10):
        bad = jwt.encode({"sub":"nobody"}, private, algorithm="EdDSA", headers={"kid":"unknown-"+str(index)})
        with pytest.raises(DomainError):
            neon_auth.claims(bad)
    assert len(calls) == 1


@pytest.mark.parametrize("location", ["/\\attacker.example/", "//attacker.example/", "/%2fattacker.example/", "https://attacker.example/", "javascript:alert(1)"])
def test_reset_redirect_cannot_escape_reviewed_app_origins(location):
    with pytest.raises(ValueError):
        neon_auth._safe_location(location,BASE,["https://app.example.test"])


def test_reset_redirect_preserves_only_reviewed_local_or_app_targets():
    origins = ["https://app.example.test"]
    assert neon_auth._safe_location(BASE+"/verify-email?token=fixture",BASE,origins) == "/auth/verify-email?token=fixture"
    assert neon_auth._safe_location("https://app.example.test/?reset_password=true&token=fixture",BASE,origins).startswith("https://app.example.test/")


def test_jwks_failure_cooldown_prevents_repeated_upstream_fetch(neon, monkeypatch):
    token, calls = neon
    # The existing fixture factory is restored after this test by monkeypatch.
    class Failure:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def stream(self,*args,**kwargs):
            calls.append("outage")
            raise httpx.ConnectError("synthetic key outage")
    monkeypatch.setattr(neon_auth.httpx,"Client",Failure)
    for _ in range(5):
        with pytest.raises(DomainError) as failure:
            neon_auth.claims(token())
        assert failure.value.status == 503
    assert calls == ["outage"]


@pytest.mark.parametrize("path", ["sign-up/email", "email-otp/send-verification-otp", "email-otp/verify-email",
                                 "email-otp/request-password-reset", "email-otp/reset-password"])
def test_only_reviewed_account_operations_are_available_to_the_proxy(path, neon, monkeypatch):
    from resolvelab.api import app
    from resolvelab import security
    monkeypatch.setattr(security,"consume_budget",lambda *args: None)
    monkeypatch.setenv("ALLOWED_HOSTS","app.example.test")
    monkeypatch.setenv("ALLOWED_ORIGINS","https://app.example.test")
    original = httpx.AsyncClient
    calls = []
    def upstream(request):
        calls.append(request)
        assert str(request.url) == BASE + "/" + path
        assert request.method == "POST"
        return httpx.Response(200,json={"ok":True})
    monkeypatch.setattr(neon_auth.httpx,"AsyncClient",lambda **kw: original(transport=httpx.MockTransport(upstream),**kw))
    async def run():
        async with original(transport=httpx.ASGITransport(app=app),base_url="https://app.example.test") as client:
            headers = {"Origin":"https://app.example.test"}
            assert (await client.post("/auth/"+path,json={"email":"fixture@example.test"},headers=headers)).status_code == 200
            assert (await client.post("/auth/admin/set-role",json={},headers=headers)).status_code == 404
    asyncio.run(run())
    assert len(calls) == 1


def test_neon_config_exposes_only_same_origin_proxy(neon):
    from resolvelab.config import public_auth_config
    assert public_auth_config() == {"mode":"neon","local":True,"configured":True,"demo":False,"auth_base_url":"/auth"}


def test_neon_proxy_limits_target_headers_cookies_and_redirects(neon, monkeypatch):
    from resolvelab.api import app
    from resolvelab import security
    monkeypatch.setattr(security,"consume_budget",lambda *args: None)
    monkeypatch.setenv("ALLOWED_HOSTS", "app.example.test")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.test")
    original = httpx.AsyncClient
    calls = []

    def upstream(request):
        calls.append(request)
        assert str(request.url) == BASE + "/sign-in/email"
        assert request.headers["origin"] == ORIGIN
        assert "authorization" not in request.headers and "x-forwarded-host" not in request.headers
        assert request.headers["cookie"] == "__Secure-better-auth.session_token=fixture"
        assert "protection-secret" not in request.headers["cookie"]
        return httpx.Response(200,json={"user":{"id":"review-user"}},headers={
            "set-cookie":"__Secure-better-auth.session_token=fixture; Domain=auth.example.test; Path=/; Secure; HttpOnly",
            "set-auth-jwt":"fixture-jwt"})

    monkeypatch.setattr(neon_auth.httpx,"AsyncClient",lambda **kw: original(transport=httpx.MockTransport(upstream), **kw))

    async def run():
        async with original(transport=httpx.ASGITransport(app=app),base_url="https://app.example.test") as client:
            response = await client.post("/auth/sign-in/email",json={"email":"review@example.test","password":"synthetic"},
                                         headers={"Origin":"https://app.example.test","Authorization":"Bearer must-not-forward",
                                                  "Cookie":"_vercel_jwt=protection-secret; __Secure-better-auth.session_token=fixture"})
            assert response.status_code == 200
            cookie = response.headers["set-cookie"]
            assert "Domain=" not in cookie and "Path=/auth" in cookie and "HttpOnly" in cookie and "Secure" in cookie
            assert response.headers["set-auth-jwt"] == "fixture-jwt"
            assert (await client.post("/auth/sign-in/email",json={},headers={"Origin":"https://other.example.test"})).status_code == 403
            assert (await client.get("/auth/../../admin")).status_code == 404
            assert (await client.post("/auth/sign-in/email",json={"callbackURL":"https://attacker.example/"},headers={"Origin":"https://app.example.test"})).status_code == 422
    asyncio.run(run())
    assert len(calls) == 1
