"""Fixed-upstream Neon Auth proxy and bounded Ed25519 token verification."""
from http.cookies import CookieError, SimpleCookie
import json
import os
import re
import threading
import time
from urllib.parse import unquote, urlsplit

import httpx
import jwt
from fastapi import APIRouter, Request
from fastapi.responses import Response

from .schemas import DomainError

MAX_JWKS_BYTES = 65536
MAX_AUTH_BYTES = 262144
_cache = {}
_attempts = {}
_lock = threading.Lock()
router = APIRouter()


def settings():
    base = os.environ.get("NEON_AUTH_URL", "").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise RuntimeError("NEON_AUTH_URL must be a fixed HTTPS base URL")
    origin = "https://" + parsed.netloc
    jwks = os.environ.get("NEON_AUTH_JWKS_URL", "")
    key_url = urlsplit(jwks)
    if (key_url.scheme + "://" + key_url.netloc != origin or key_url.query or key_url.fragment
            or not key_url.path.startswith(parsed.path.rstrip("/") + "/")):
        raise RuntimeError("NEON_AUTH_JWKS_URL must be an explicit endpoint under the configured Auth base")
    return base, origin, jwks


def _read(response, maximum):
    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
        raise ValueError("Unsupported upstream content encoding")
    value = bytearray()
    pieces = (response.content,) if response.is_stream_consumed else response.iter_raw(chunk_size=16384)
    for piece in pieces:
        if len(piece) > maximum - len(value):
            raise ValueError("Upstream response exceeds its byte budget")
        value.extend(piece)
    return bytes(value)


def _keys(url, kid):
    now = time.monotonic()
    with _lock:
        cached = _cache.get(url)
        if cached and now - cached[0] < 300 and (kid in cached[1] or now - cached[2] < 30):
            return cached[1]
        if now - _attempts.get(url, float("-inf")) < 30:
            raise DomainError(503, "identity_provider_unavailable", "Identity signing keys are temporarily unavailable.", True)
        if len(_attempts) >= 4 and url not in _attempts:
            oldest = next(iter(_attempts))
            _attempts.pop(oldest)
            _cache.pop(oldest, None)
        _attempts[url] = now
        try:
            with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False, trust_env=False) as client:
                with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
                    if response.status_code != 200:
                        raise ValueError("JWKS is unavailable")
                    value = json.loads(_read(response, MAX_JWKS_BYTES))
            rows = value.get("keys") if isinstance(value, dict) else None
            if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
                raise ValueError("Invalid JWKS")
            keys = {}
            for row in rows:
                if (not isinstance(row, dict) or row.get("kty") != "OKP" or row.get("crv") != "Ed25519"
                        or row.get("alg", "EdDSA") != "EdDSA" or row.get("use", "sig") != "sig"
                        or not isinstance(row.get("kid"), str) or not 1 <= len(row["kid"]) <= 200
                        or row["kid"] in keys or "d" in row):
                    raise ValueError("Invalid Ed25519 key set")
                keys[row["kid"]] = jwt.PyJWK.from_dict(row, algorithm="EdDSA").key
            if len(_cache) >= 4 and url not in _cache:
                oldest = next(iter(_cache))
                _cache.pop(oldest)
                _attempts.pop(oldest, None)
            _cache[url] = (now, keys, now)
            return keys
        except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError, KeyError, RecursionError):
            raise DomainError(503, "identity_provider_unavailable", "Identity signing keys are temporarily unavailable.", True) from None


def claims(token):
    _, origin, url = settings()
    try:
        if not isinstance(token, str) or len(token) > 8192:
            raise ValueError()
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "EdDSA" or not isinstance(kid, str) or not 1 <= len(kid) <= 200:
            raise ValueError()
        key = _keys(url, kid).get(kid)
        if key is None:
            raise ValueError()
        value = jwt.decode(token, key, algorithms=["EdDSA"], issuer=origin, audience=origin,
                           leeway=10, options={"require": ["sub", "iss", "aud", "exp", "iat"], "strict_aud":True})
        if not isinstance(value["sub"], str) or not 1 <= len(value["sub"]) <= 300 or "\x00" in value["sub"]:
            raise ValueError()
        value["sub"].encode("utf-8")
        return value
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise DomainError(401, "invalid_token", "The bearer token is invalid or expired.") from None


GET_PATHS = {"get-session", "token", "jwks", ".well-known/jwks.json", "list-accounts", "verify-email"}
POST_PATHS = {"sign-in/email", "sign-up/email", "sign-out", "get-session", "token", "request-password-reset",
              "reset-password", "send-verification-email", "verify-email", "update-user", "change-password",
              "email-otp/send-verification-otp", "email-otp/verify-email",
              "email-otp/request-password-reset", "email-otp/reset-password"}


def _allowed_callback(value, origins):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value) or "\\" in unquote(value):
        return False
    try:
        target = urlsplit(value)
    except ValueError:
        return False
    return target.scheme + "://" + target.netloc in origins and not target.username and not target.password


def _safe_location(value, base, origins):
    if len(value) > 4096 or any(ord(c) < 32 for c in value) or "\\" in unquote(value):
        raise ValueError()
    if value == base or value.startswith(base + "/") or value.startswith(base + "?"):
        value = "/auth" + value[len(base):]
    if value.startswith("/") and not unquote(value).startswith("//"):
        return value
    if _allowed_callback(value, origins):
        return value
    raise ValueError()


def _auth_cookies(value):
    """Deployment-protection and unrelated first-party cookies stay on this host."""
    cookie = SimpleCookie()
    try:
        cookie.load(value)
    except CookieError:
        return ""
    prefixes = ("better-auth.", "neon-auth.", "__Secure-better-auth.", "__Host-better-auth.",
                "__Secure-neon-auth.", "__Host-neon-auth.")
    return "; ".join(item.OutputString(attrs=[]) for name, item in cookie.items() if name.startswith(prefixes))


@router.api_route("/auth/{path:path}", methods=["GET", "POST"])
async def proxy_auth(path: str, request: Request):
    from .config import auth_mode, allowed_origins
    reset_link = request.method == "GET" and re.fullmatch(r"reset-password/[A-Za-z0-9_-]{1,256}", path)
    if auth_mode() != "neon" or (not reset_link and path not in (GET_PATHS if request.method == "GET" else POST_PATHS)):
        raise DomainError(404, "not_found", "The authentication operation was not found.")
    base, origin, _ = settings()
    for name in ("callbackURL", "redirectTo", "newUserCallbackURL", "errorCallbackURL"):
        if name in request.query_params and not _allowed_callback(request.query_params[name], allowed_origins()):
            raise DomainError(403, "origin_forbidden", "The authentication callback is not allowed.")
    if request.method == "POST":
        if request.headers.get("origin", "").rstrip("/") not in allowed_origins():
            raise DomainError(403, "origin_forbidden", "The browser origin is not allowed.")
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise DomainError(415, "unsupported_media_type", "Authentication bodies require application/json.")
    from .security import consume_budget
    from starlette.concurrency import run_in_threadpool
    client_ip = request.client.host if request.client else "unknown"
    await run_in_threadpool(consume_budget, "neon-auth:" + client_ip, os.environ.get("LOGIN_REQUESTS_PER_MINUTE", "30"))
    body = await request.body()
    if len(body) > 32768 or len(request.url.query) > 4096 or len(request.headers.get("cookie", "")) > 16384:
        raise DomainError(413, "body_too_large", "The authentication request exceeds its limits.")
    if request.method == "POST":
        from .security import strict_request_json
        try:
            value = strict_request_json(body)
            if not isinstance(value, dict):
                raise ValueError()
            for key in ("callbackURL", "redirectTo", "newUserCallbackURL", "errorCallbackURL"):
                if key in value:
                    if not _allowed_callback(value[key], allowed_origins()):
                        raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise DomainError(422, "invalid_input", "The authentication request is invalid.") from None
    headers = {"Accept-Encoding": "identity", "Origin": origin}
    if "content-type" in request.headers:
        headers["content-type"] = request.headers["content-type"]
    cookies = _auth_cookies(request.headers.get("cookie", ""))
    if cookies:
        headers["cookie"] = cookies
    if request.headers.get("x-force-fetch", "").lower() == "true":
        headers["X-Force-Fetch"] = "true"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False) as client:
            async with client.stream(request.method, base + "/" + path, params=request.query_params,
                                     content=body, headers=headers) as upstream:
                if upstream.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                    raise ValueError()
                content = bytearray()
                if upstream.is_stream_consumed:
                    content.extend(upstream.content)
                    if len(content) > MAX_AUTH_BYTES:
                        raise ValueError()
                else:
                    async for piece in upstream.aiter_raw(chunk_size=16384):
                        if len(piece) > MAX_AUTH_BYTES - len(content):
                            raise ValueError()
                        content.extend(piece)
                location = None
                if 300 <= upstream.status_code < 400:
                    location = _safe_location(upstream.headers["location"], base, allowed_origins())
                response = Response(b"" if location else bytes(content), status_code=upstream.status_code,
                                    media_type="application/json", headers={"Cache-Control": "no-store"})
                if location:
                    response.headers["location"] = location
                auth_jwt = upstream.headers.get("set-auth-jwt")
                if auth_jwt:
                    if len(auth_jwt) > 8192:
                        raise ValueError()
                    response.headers["set-auth-jwt"] = auth_jwt
                cookies = upstream.headers.get_list("set-cookie")
                if len(cookies) > 16:
                    raise ValueError()
                for value in cookies:
                    if len(value) > 8192:
                        raise ValueError()
                    cookie = SimpleCookie()
                    cookie.load(value)
                    if not cookie:
                        raise ValueError()
                    for item in cookie.values():
                        item["domain"] = ""
                        item["path"] = "/" if item.key.startswith("__Host-") else "/auth"
                        item["secure"] = True
                        item["httponly"] = True
                        item["samesite"] = "Lax"
                        response.headers.append("set-cookie", item.OutputString())
                return response
    except (httpx.HTTPError, ValueError, TypeError, KeyError, CookieError):
        raise DomainError(503, "identity_provider_unavailable", "Authentication is temporarily unavailable.", True) from None
