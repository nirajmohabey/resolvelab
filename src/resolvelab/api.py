"""Authenticated HTTP API and small operator interface."""
from contextlib import asynccontextmanager
import asyncio
import json
import logging
import os
import time
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from . import service
from .config import allowed_hosts, allowed_origins, environment, public_auth_config, validate_configuration
from .security import actor_budget, browser_policy, consume_budget
from .auth import Principal, authenticate, demo_enabled, fixture_principal, issue_token
from .db import ROOT, engine, transaction
from .schemas import ApproveRequest, CreateCase, DemoLogin, DomainError, EvaluationRequest, ResolveRequest

logger = logging.getLogger("resolvelab.http")
bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app):
    validate_configuration()
    yield


app = FastAPI(title="ResolveLab", version="0.1.0", description="Evidence-backed fixture support workflow. Routing scores do not authorize actions.", lifespan=lifespan,
              docs_url="/docs" if environment() == "local" else None, redoc_url=None)


def error_body(request, status, code, message, retryable=False):
    return JSONResponse(status_code=status, content={"code": code, "message": message,
                        "request_id": getattr(request.state, "request_id", str(uuid4())), "retryable": retryable})


@app.middleware("http")
async def request_context(request, call_next):
    request.state.request_id = str(uuid4())
    supplied = request.headers.get("x-request-id")
    if supplied:
        try:
            request.state.request_id = str(UUID(supplied))
        except ValueError:
            pass
    started = time.perf_counter()
    try:
        host = request.url.hostname or ""
        if host.lower() not in allowed_hosts():
            raise DomainError(400, "invalid_host", "The request host is not allowed.")
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in allowed_origins():
            raise DomainError(403, "origin_forbidden", "The browser origin is not allowed.")
        if environment() == "production" and not request.url.path.startswith("/health/") and request.url.scheme != "https":
            raise DomainError(400, "https_required", "HTTPS is required.")
        if request.url.path.startswith("/v1/"):
            ip = request.client.host if request.client else "unknown"
            quota = os.environ.get("LOGIN_REQUESTS_PER_MINUTE", "30") if request.url.path == "/v1/demo/session" else os.environ.get("IP_REQUESTS_PER_MINUTE", "1200")
            category = "login" if request.url.path == "/v1/demo/session" else "api"
            await run_in_threadpool(consume_budget, "ip:" + category + ":" + ip, quota)
        if len(request.headers.get("authorization", "")) > 8200:
            raise DomainError(401, "invalid_token", "The bearer token is invalid or expired.")
        length = request.headers.get("content-length")
        if sum(key.lower() == b"content-length" for key, _ in request.scope.get("headers", [])) > 1:
            raise DomainError(400, "ambiguous_request_length", "Multiple content-length headers are not allowed.")
        if request.method in {"POST", "PUT", "PATCH"} and request.url.path.startswith("/v1/") and request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise DomainError(415, "unsupported_media_type", "API request bodies must use application/json.")
        if length and (not length.isdigit() or int(length) > 32768):
            response = error_body(request, 413, "body_too_large", "The request body exceeds 32 KiB.")
        else:
            body = bytearray()
            async with asyncio.timeout(float(os.environ.get("BODY_TIMEOUT_SECONDS", "5"))):
                async for chunk in request.stream():
                    if len(chunk) > 32768 - len(body):
                        body.extend(b"x" * (32769 - len(body)))
                        break
                    body.extend(chunk)
            if len(body) > 32768:
                response = error_body(request, 413, "body_too_large", "The request body exceeds 32 KiB.")
            else:
                request._body = bytes(body)
                response = await call_next(request)
    except SQLAlchemyError:
        response = error_body(request, 503, "storage_unavailable", "The database is temporarily unavailable.", True)
    except DomainError as exc:
        response = error_body(request, exc.status, exc.code, exc.message, exc.retryable)
        if getattr(exc, "retry_after", None):
            response.headers["Retry-After"] = str(exc.retry_after)
    except TimeoutError:
        response = error_body(request, 408, "request_timeout", "The request body was not received in time.", True)
    except ClientDisconnect:
        response = error_body(request, 400, "request_disconnected", "The request body was incomplete.")
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = browser_policy(request.url.path)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") in allowed_origins():
        response.headers["Access-Control-Allow-Origin"] = origin.rstrip("/")
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Expose-Headers"] = "X-Request-ID, Retry-After"
    if environment() == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    logger.info(json.dumps({"request_id": request.state.request_id, "operation": request.method,
                            "path": request.url.path, "status": response.status_code,
                            "duration_ms": round((time.perf_counter()-started)*1000, 3)}))
    return response


@app.exception_handler(DomainError)
async def domain_error(request, exc):
    response = error_body(request, exc.status, exc.code, exc.message, exc.retryable)
    if getattr(exc, "retry_after", None):
        response.headers["Retry-After"] = str(exc.retry_after)
    return response


@app.exception_handler(RequestValidationError)
async def invalid_input(request, exc):
    return error_body(request, 422, "invalid_input", "The request does not match the API contract. Check required fields and versions.")


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return error_body(request, exc.status_code, "not_found" if exc.status_code == 404 else "http_error", str(exc.detail))


@app.exception_handler(SQLAlchemyError)
async def storage_error(request, exc):
    return error_body(request, 503, "storage_unavailable", "The database is temporarily unavailable.", True)


def identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials:
        raise DomainError(401, "authentication_required", "A bearer token is required.")
    principal = authenticate(credentials.credentials)
    actor_budget(principal)
    return principal


def idempotency_key(value: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
    return value


@app.get("/health/live", tags=["health"])
def live():
    return {"status": "alive"}


@app.options("/v1/{path:path}", include_in_schema=False)
def preflight(path: str, request: Request):
    if not request.headers.get("origin") or request.headers.get("access-control-request-method", "") not in {"GET", "POST"}:
        raise DomainError(403, "cors_forbidden", "The requested cross-origin operation is not allowed.")
    headers = {value.strip().lower() for value in request.headers.get("access-control-request-headers", "").split(",") if value.strip()}
    if not headers <= {"authorization", "content-type", "idempotency-key", "x-request-id"}:
        raise DomainError(403, "cors_forbidden", "The requested cross-origin headers are not allowed.")
    return Response(status_code=204, headers={"Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key, X-Request-ID", "Access-Control-Max-Age": "600"})


@app.get("/health/ready", tags=["health"])
def ready():
    try:
        with engine.connect() as conn:
            role = conn.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).mappings().one()
            if role["rolsuper"] or role["rolbypassrls"]:
                raise DomainError(503, "unsafe_database_role", "The application database role must not bypass tenant isolation.")
            owned = conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='resolvelab' AND tableowner=current_user")).scalar_one()
            if owned:
                raise DomainError(503, "unsafe_database_role", "The application role must not own its tables.")
        from .retrieval import _encoder
        _encoder()
        tenants = json.loads((ROOT / "fixtures/evaluation_cases.json").read_text(encoding="utf-8"))["organizations"]
        for tenant in tenants:
            p = Principal(tenant["tenant_id"], "00000000-0000-4000-8000-000000000000", "", "")
            with transaction(p) as conn:
                active = conn.execute(text("SELECT release_id FROM resolvelab.active_releases WHERE tenant_id=:tenant"),
                                      {"tenant": p.tenant_id}).mappings().first()
                if not active:
                    raise DomainError(503, "corpus_unavailable", "A published active corpus is missing.")
                release = service._release(conn, p, active["release_id"])
                if release["corpus_state"] != "published":
                    raise DomainError(503, "corpus_unavailable", "A published active corpus is missing.")
            service.load_pinned_router(release)
        return {"status": "ready", "model": "loaded", "embedding": "loaded", "corpus": "published", "database_role": "nonowner_rls"}
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(503, "not_ready", "Database, model, or corpus readiness has not passed.", True) from exc


@app.post("/v1/demo/session", tags=["local demo"])
def demo_session(body: DemoLogin):
    principal = fixture_principal(body.actor)
    token = issue_token(principal)
    return {"access_token": token, "token": token, "token_type": "bearer", "principal": principal.as_dict(),
            "organization":service.organization(principal),"demo": True, "fixture_clock": os.environ.get("FIXTURE_CLOCK")}


@app.get("/v1/session")
def session(p=Depends(identity)):
    return {"principal": p.as_dict(), "organization":service.organization(p),"demo": demo_enabled(), "fixture_clock": os.environ.get("FIXTURE_CLOCK") if demo_enabled() else None}


@app.get("/v1/auth/config")
def authentication_config():
    return public_auth_config()


@app.get("/v1/cases")
def cases(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), state: str | None = None, p=Depends(identity)):
    return service.list_cases(p, limit, offset, state)


@app.post("/v1/cases", status_code=201)
def create(body: CreateCase, request: Request, p=Depends(identity), key=Depends(idempotency_key)):
    return service.create_case(p, body.model_dump(), key, request.state.request_id)


@app.get("/v1/cases/{case_id}")
def case(case_id: UUID, p=Depends(identity)):
    return service.get_case(p, str(case_id))


@app.post("/v1/cases/{case_id}/triage")
def triage(case_id: UUID, body: ResolveRequest, request: Request, p=Depends(identity), key=Depends(idempotency_key)):
    return service.triage_case(p, str(case_id), body.model_dump(), key, request.state.request_id)


@app.post("/v1/cases/{case_id}/resolve")
def resolve(case_id: UUID, body: ResolveRequest, request: Request, p=Depends(identity), key=Depends(idempotency_key)):
    return service.resolve_case(p, str(case_id), body.model_dump(), key, request.state.request_id)


@app.get("/v1/cases/{case_id}/evidence")
def evidence(case_id: UUID, limit: int = Query(default=4, ge=1, le=10), p=Depends(identity)):
    return service.get_evidence(p, str(case_id), limit)


@app.get("/v1/cases/{case_id}/trace")
def trace(case_id: UUID, p=Depends(identity)):
    return service.get_trace(p, str(case_id))


@app.post("/v1/proposals/{proposal_id}/approve")
def approve(proposal_id: UUID, body: ApproveRequest, request: Request, p=Depends(identity), key=Depends(idempotency_key)):
    return service.approve_proposal(p, str(proposal_id), body.model_dump(), key, request.state.request_id)


@app.get("/v1/dashboard")
def dashboard(p=Depends(identity)):
    return service.dashboard(p)


@app.get("/v1/releases")
def releases(p=Depends(identity)):
    return service.list_releases(p)


@app.get("/v1/evaluations")
def evaluations(p=Depends(identity)):
    return service.list_evaluations(p)


@app.post("/v1/evaluations", status_code=202)
def evaluate(body: EvaluationRequest, request: Request, p=Depends(identity), key=Depends(idempotency_key)):
    return service.create_evaluation(p, body.model_dump(), key, request.state.request_id)


@app.get("/v1/evaluations/{evaluation_id}")
def evaluation(evaluation_id: UUID, p=Depends(identity)):
    return service.get_evaluation(p, str(evaluation_id))


@app.get("/v1/evaluations/{evaluation_id}/report")
def evaluation_report(evaluation_id: UUID, p=Depends(identity)):
    record = service.get_evaluation(p, str(evaluation_id))
    if record["state"] != "completed":
        raise DomainError(409, "report_not_ready", "The evaluation report is not ready.")
    # The stored URI is diagnostic metadata, never permission to read a path.
    # UUID parsing and this fixed layout bind the report to the authorized job.
    path = ROOT / "artifacts/evaluation" / str(evaluation_id) / "workflow-evaluation.json"
    try:
        with path.open("rb") as report_file:
            content = report_file.read(4 * 1024 * 1024 + 1)
    except FileNotFoundError:
        raise DomainError(409, "report_not_ready", "The evaluation report is not ready.") from None
    except OSError:
        raise DomainError(503, "report_unavailable", "The evaluation report is temporarily unavailable.", True) from None
    if len(content) > 4 * 1024 * 1024:
        raise DomainError(503, "report_invalid", "The evaluation report exceeds its 4 MiB limit.")
    try:
        report = json.loads(content.decode("utf-8"))
        if not isinstance(report, dict) or not isinstance(report.get("metrics"), dict) or not isinstance(report.get("cases"), list):
            raise ValueError("Invalid evaluation report structure")
        json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise DomainError(503, "report_invalid", "The evaluation report is malformed.") from None
    return report


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "web/index.html")


if (ROOT / "web").exists():
    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
