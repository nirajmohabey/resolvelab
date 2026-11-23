"""Explicit deployment settings; an omitted environment is never a local identity."""
import os
from urllib.parse import urlsplit


def environment():
    return os.environ.get("APP_ENV", "production")


def auth_mode():
    return os.environ.get("AUTH_MODE", "fixture" if environment() == "local" else "oidc")


def allowed_hosts():
    configured = os.environ.get("ALLOWED_HOSTS", "")
    return [v.strip().lower() for v in configured.split(",") if v.strip()] or (["127.0.0.1", "localhost", "testserver", "[::1]"] if environment() == "local" else [])


def allowed_origins():
    configured = os.environ.get("ALLOWED_ORIGINS", "")
    return [v.strip().rstrip("/") for v in configured.split(",") if v.strip()] or (["http://127.0.0.1:8000", "http://localhost:8000", "http://testserver"] if environment() == "local" else [])


def trusted_https_url(value):
    parsed = urlsplit(value)
    return bool(parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.fragment and not parsed.query)


def public_auth_config():
    if auth_mode() == "fixture":
        enabled = environment() == "local" and os.environ.get("DEMO_MODE", "false").lower() == "true"
        return {"mode":"fixture","local":environment()=="local","configured":enabled,"demo":enabled}
    names={"client_id":"OIDC_CLIENT_ID","issuer":"OIDC_ISSUER","authorization_endpoint":"OIDC_AUTHORIZATION_URL",
           "token_endpoint":"OIDC_TOKEN_URL","jwks_uri":"OIDC_JWKS_URL","audience":"OIDC_AUDIENCE"}
    values={key:os.environ.get(setting,"") for key,setting in names.items()}
    configured=all(values.values()) and all(trusted_https_url(values[key]) for key in ("issuer","authorization_endpoint","token_endpoint","jwks_uri"))
    result={"mode":"oidc","local":environment()=="local","configured":bool(configured),"demo":False}
    if configured:
        result.update(values,scope=os.environ.get("OIDC_SCOPE","openid profile"),algorithm=os.environ.get("OIDC_ALGORITHM","RS256"))
    else:
        result["message"]="Browser sign-in is not configured. The API requires a registered external identity and a valid provider access token."
    return result


def validate_configuration():
    if environment() not in {"local", "production"}:
        raise RuntimeError("APP_ENV must be explicitly local or production")
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required")
    for key, default, minimum, maximum in (("BODY_TIMEOUT_SECONDS", 5, .1, 30), ("LOGIN_REQUESTS_PER_MINUTE", 30, 1, 10000),
                                          ("IP_REQUESTS_PER_MINUTE", 1200, 1, 10000), ("ACTOR_REQUESTS_PER_MINUTE", 300, 1, 10000)):
        try:
            parsed = float(os.environ.get(key, default)) if key == "BODY_TIMEOUT_SECONDS" else int(os.environ.get(key, default))
            valid = minimum <= parsed <= maximum
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError(f"{key} is outside its supported range")
    if auth_mode() not in {"fixture", "oidc"}:
        raise RuntimeError("AUTH_MODE must be fixture or oidc")
    if environment() != "local":
        if os.environ.get("DEMO_MODE", "false").lower() == "true" or os.environ.get("FIXTURE_CLOCK"):
            raise RuntimeError("Fixture identities and clocks are forbidden in production")
        if auth_mode() != "oidc":
            raise RuntimeError("Production requires AUTH_MODE=oidc")
        if not allowed_hosts() or any("*" in host or "/" in host or "://" in host for host in allowed_hosts()):
            raise RuntimeError("Production requires explicit ALLOWED_HOSTS without wildcards")
        if not allowed_origins():
            raise RuntimeError("Production requires explicit HTTPS ALLOWED_ORIGINS")
        for origin in allowed_origins():
            parsed = urlsplit(origin)
            if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise RuntimeError("ALLOWED_ORIGINS must contain exact HTTPS origins")
    if auth_mode() == "oidc":
        for key in ("OIDC_ISSUER", "OIDC_JWKS_URL"):
            parsed = urlsplit(os.environ.get(key, ""))
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.fragment:
                raise RuntimeError(f"{key} must be an explicitly trusted HTTPS URL")
        if not os.environ.get("OIDC_AUDIENCE"):
            raise RuntimeError("OIDC_AUDIENCE is required")
        if os.environ.get("OIDC_ALGORITHM", "RS256") not in {"RS256", "ES256"}:
            raise RuntimeError("OIDC_ALGORITHM must be RS256 or ES256")
        for key in ("OIDC_AUTHORIZATION_URL","OIDC_TOKEN_URL"):
            if os.environ.get(key) and not trusted_https_url(os.environ[key]):
                raise RuntimeError(f"{key} must be a trusted HTTPS endpoint without query or fragment")
        if len(os.environ.get("OIDC_CLIENT_ID",""))>300 or len(os.environ.get("OIDC_SCOPE","openid profile"))>500:
            raise RuntimeError("OIDC browser configuration exceeds its allowed size")
    elif len(os.environ.get("AUTH_SECRET", "")) < 32:
        raise RuntimeError("Local fixture sessions require AUTH_SECRET of at least 32 characters")
