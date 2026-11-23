"""Transactional outbox, native queue delivery and bounded leased evaluations."""
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from starlette.concurrency import run_in_threadpool

from .auth import Principal
from .config import deployment_tenants
from .db import ROOT, transaction
from .processes import run_bounded
from .schemas import DomainError

TOPIC = "resolvelab-evaluations"
router = APIRouter()


def enabled():
    return os.environ.get("QUEUE_ENABLED", "false").lower() == "true"


async def publish(payload, token):
    from vercel.queue import QueueClient
    client = QueueClient(region=os.environ.get("QUEUE_REGION", "iad1"), timeout=8)
    return await client.send(TOPIC, payload, idempotency_key="evaluation:" + token, retention=86400)


def _dispatch_claim(tenant, evaluation_id=None):
    p = Principal(tenant, "00000000-0000-4000-8000-000000000000", "", "")
    with transaction(p) as conn:
        # A delivered message may be lost/exhausted or belong to an old deployment.
        # Its durable queued/stale job can be dispatched again; execution is fenced.
        row = conn.execute(text("""SELECT o.evaluation_id FROM resolvelab.evaluation_outbox o
            JOIN resolvelab.evaluation_runs r USING(tenant_id,evaluation_id)
            WHERE o.tenant_id=:tenant AND (CAST(:id AS uuid) IS NULL OR o.evaluation_id=:id)
              AND r.state IN ('queued','running') AND o.available_at<=now()
              AND (o.lease_expires_at IS NULL OR o.lease_expires_at<now())
              AND (o.delivered_at IS NULL OR o.delivered_at<now()-interval '10 minutes')
              AND (r.state='queued' OR COALESCE((r.environment->>'lease_expires_at')::timestamptz,'epoch')<now())
            ORDER BY o.available_at FOR UPDATE OF o SKIP LOCKED LIMIT 1"""),
            {"tenant":tenant,"id":evaluation_id}).first()
        if not row:
            return None
        token = str(uuid4())
        conn.execute(text("""UPDATE resolvelab.evaluation_outbox SET dispatch_token=:token,
            lease_expires_at=now()+interval '30 seconds',dispatch_attempts=dispatch_attempts+1
            WHERE tenant_id=:tenant AND evaluation_id=:id"""), {"tenant":tenant,"id":row[0],"token":token})
        return p, str(row[0]), token


def _dispatch_finish(p, evaluation_id, token, success):
    with transaction(p) as conn:
        conn.execute(text("""UPDATE resolvelab.evaluation_outbox SET lease_expires_at=NULL,
            delivered_at=CASE WHEN :success THEN now() ELSE NULL END,
            available_at=CASE WHEN :success THEN now() ELSE now()+interval '60 seconds' END,
            last_error_code=CASE WHEN :success THEN NULL ELSE 'queue_publish_unavailable' END
            WHERE tenant_id=:tenant AND evaluation_id=:id AND dispatch_token=:token"""),
            {"tenant":p.tenant_id,"id":evaluation_id,"token":token,"success":success})


async def dispatch_pending(tenant=None, evaluation_id=None, limit=10):
    if not enabled():
        return 0
    tenants = deployment_tenants()
    if tenant is not None and tenant not in tenants:
        raise DomainError(403, "tenant_not_deployed", "The tenant is not enabled in this deployment.")
    sent = attempted = 0
    budget = min(max(limit, 1), 20)
    for target in ([tenant] if tenant else tenants):
        for _ in range(budget):
            if attempted >= budget:
                return sent
            claim = await run_in_threadpool(_dispatch_claim, target, evaluation_id)
            if not claim:
                break
            p, job_id, token = claim
            attempted += 1
            success = False
            try:
                await publish({"tenant_id":target,"evaluation_id":job_id}, token)
                success = True
                sent += 1
            except Exception:
                # The committed job/outbox remains retryable. A failed SDK call
                # must never turn durable acceptance into dropped work.
                pass
            finally:
                await run_in_threadpool(_dispatch_finish, p, job_id, token, success)
            if evaluation_id:
                break
    return sent


@router.get("/internal/queue/reconcile", include_in_schema=False)
async def reconcile(request: Request):
    secret = os.environ.get("CRON_SECRET", "")
    if len(secret) < 32 or not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + secret):
        raise DomainError(401, "authentication_required", "A valid scheduler credential is required.")
    return {"dispatched": await dispatch_pending()}


def _claim_job(tenant, evaluation_id):
    p = Principal(tenant, "00000000-0000-4000-8000-000000000000", "", "")
    with transaction(p) as conn:
        row = conn.execute(text("SELECT * FROM resolvelab.evaluation_runs WHERE tenant_id=:tenant AND evaluation_id=:id FOR UPDATE"),
                           {"tenant":tenant,"id":evaluation_id}).mappings().one_or_none()
        if not row or row["state"] in {"completed", "failed"}:
            return None
        job = dict(row)
        metadata = dict(job["environment"])
        if job["state"] == "running" and datetime.fromisoformat(metadata["lease_expires_at"]) > datetime.now(timezone.utc):
            raise RuntimeError("Evaluation lease is still active; redelivery must retry")
        attempts = int(metadata.get("attempts", 0)) + 1
        if attempts > 3:
            conn.execute(text("""UPDATE resolvelab.evaluation_runs SET state='failed',metrics=CAST(:metrics AS jsonb)
                WHERE tenant_id=:tenant AND evaluation_id=:id"""),
                {"tenant":tenant,"id":evaluation_id,"metrics":json.dumps({"runner_error":"retry_limit_exhausted","attempts":3})})
            return None
        token = str(uuid4())
        metadata.update(worker_token=token, attempts=attempts,
                        lease_expires_at=(datetime.now(timezone.utc)+timedelta(seconds=300)).isoformat())
        conn.execute(text("""UPDATE resolvelab.evaluation_runs SET state='running',environment=CAST(:metadata AS jsonb)
            WHERE tenant_id=:tenant AND evaluation_id=:id"""), {"tenant":tenant,"id":evaluation_id,"metadata":json.dumps(metadata)})
        job["environment"] = metadata
        return p, job, token


def consume(payload):
    """At most one isolated 20-case attempt; no daemon or post-response task."""
    started = time.monotonic()
    if not isinstance(payload, dict) or set(payload) != {"tenant_id", "evaluation_id"}:
        raise ValueError("Invalid queue message")
    tenant, evaluation_id = str(UUID(payload["tenant_id"])), str(UUID(payload["evaluation_id"]))
    if tenant not in deployment_tenants():
        raise ValueError("Queue tenant is not deployed")
    from .worker import _configuration, _complete
    target, admin_target = _configuration()
    from sqlalchemy.engine import make_url
    for url in (target, admin_target):
        if "-pooler" in (make_url(url).host or ""):
            raise RuntimeError("Evaluation session locks require direct PostgreSQL URLs")
    isolated = create_engine(target, poolclass=NullPool, connect_args={"connect_timeout":5})
    try:
        with isolated.connect() as coordinator:
            locked = coordinator.execute(text("SELECT pg_try_advisory_lock(hashtext('resolvelab-cloud-evaluator'))")).scalar_one()
            coordinator.commit()
            if not locked:
                raise RuntimeError("Another isolated evaluation is running")
            try:
                claimed = _claim_job(tenant, evaluation_id)
                if not claimed:
                    return
                p, job, token = claimed
                with tempfile.TemporaryDirectory(prefix="resolvelab-evaluation-") as directory:
                    env = dict(os.environ, DATABASE_URL=target, ADMIN_DATABASE_URL=admin_target,
                               APP_ENV="local", AUTH_MODE="fixture", DEMO_MODE="true", GENERATION_ADAPTER="offline",
                               AUTH_SECRET=secrets.token_urlsafe(48),
                               ALLOWED_HOSTS="testserver,localhost,127.0.0.1", ALLOWED_ORIGINS="http://testserver",
                               DB_POOL_SIZE="2", DB_MAX_OVERFLOW="2", QUEUE_ENABLED="false")
                    fixture = json.loads((ROOT / "fixtures/evaluation_cases.json").read_text(encoding="utf-8"))
                    env["DEPLOYMENT_TENANTS"] = ",".join(row["tenant_id"] for row in fixture["organizations"])
                    # Vercel's entrypoint sys.path bootstrap is process-local.
                    # Carry its trusted package/vendor paths into this bounded
                    # child; the project itself is not installed by the builder.
                    paths = [str(ROOT / "src")] + [value for value in sys.path if value and Path(value).is_absolute()]
                    paths += [value for value in env.get("PYTHONPATH", "").split(os.pathsep) if value]
                    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
                    # No production OIDC/Neon credentials are used inside fixtures.
                    env.pop("FIXTURE_CLOCK", None)
                    try:
                        completed = run_bounded([sys.executable,"-m","resolvelab.evaluation_job","--output",directory,
                                                 "--mode",job["mode"],"--release-id",str(job["release_id"])],
                                                cwd=ROOT,env=env,timeout=max(1,min(210,240-(time.monotonic()-started))))
                        path = Path(directory) / "workflow-evaluation.json"
                        with path.open("rb") as source:
                            raw = source.read(4*1024*1024+1)
                        if len(raw) > 4*1024*1024:
                            raise ValueError("Report exceeds its byte budget")
                        report = json.loads(raw)
                        json.dumps(report, allow_nan=False)
                        if (report.get("release_id") != str(job["release_id"]) or report.get("dataset_sha256") != job["dataset_sha256"]
                                or report.get("mode") != job["mode"] or len(report.get("cases", [])) != 20
                                or not isinstance(report.get("metrics"),dict) or completed.returncode not in {0,1}):
                            raise ValueError("Report does not match the leased 20-case job")
                        _complete(p,job,token,"completed",report["metrics"],report)
                    except Exception as error:
                        # Leave the durable lease/job reclaimable; queue retries
                        # and scheduler reconciliation recover process death too.
                        with transaction(p) as conn:
                            conn.execute(text("""UPDATE resolvelab.evaluation_runs SET environment=environment || CAST(:extra AS jsonb)
                                WHERE tenant_id=:tenant AND evaluation_id=:id AND state='running'
                                  AND environment->>'worker_token'=:token"""),
                                {"tenant":tenant,"id":evaluation_id,"token":token,
                                 "extra":json.dumps({"lease_expires_at":datetime.now(timezone.utc).isoformat(),
                                                      "last_runner_error":type(error).__name__})})
                        raise
            finally:
                coordinator.execute(text("SELECT pg_advisory_unlock(hashtext('resolvelab-cloud-evaluator'))"))
                coordinator.commit()
    finally:
        isolated.dispose()
