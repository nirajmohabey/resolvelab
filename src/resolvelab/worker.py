"""Leased evaluation jobs. The evaluator resets only its dedicated database.

The serving database's session advisory lock serializes all evaluator subprocesses.
Lease tokens fence acknowledgements after a worker crash or lease replacement.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
import time
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url

from .auth import Principal
from .db import ROOT, engine, primitive, transaction
from .processes import run_bounded


def _configuration():
    target = os.environ.get("EVALUATION_DATABASE_URL", "")
    admin_target = os.environ.get("ADMIN_EVALUATION_DATABASE_URL", "")
    if not target or not admin_target:
        raise RuntimeError("EVALUATION_DATABASE_URL and ADMIN_EVALUATION_DATABASE_URL are required")
    serving = make_url(os.environ.get("DATABASE_URL", str(engine.url)))
    parsed, admin_parsed = make_url(target), make_url(admin_target)
    if not parsed.database or parsed.database == serving.database or admin_parsed.database != parsed.database:
        raise RuntimeError("Evaluation runtime/admin URLs must share a dedicated database name different from the serving database")
    from .isolation import connected_identity,validate_disposable_urls
    target_identity=validate_disposable_urls(target,admin_target,"resolvelab_evaluation")
    with engine.connect() as conn:
        if connected_identity(conn)==target_identity:
            raise RuntimeError("The evaluation database must be physically separate from the serving database.")
    return target, admin_target


def _tenants():
    configured = os.environ.get("WORKER_TENANTS")
    if configured:
        return [str(UUID(value.strip())) for value in configured.split(",") if value.strip()]
    from .config import deployment_tenants
    return deployment_tenants()


def _claim():
    for tenant in _tenants():
        p = Principal(tenant, "00000000-0000-4000-8000-000000000000", "", "")
        with transaction(p) as conn:
            job = conn.execute(text("""SELECT * FROM resolvelab.evaluation_runs
                WHERE tenant_id=:tenant AND (state='queued' OR
                    (state='running' AND COALESCE((environment->>'lease_expires_at')::timestamptz,'epoch'::timestamptz)<now()))
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"""), {"tenant": tenant}).mappings().first()
            if not job:
                continue
            job = dict(job)
            metadata = dict(job["environment"] or {})
            attempts = int(metadata.get("attempts", 0)) + 1
            if attempts > 3:
                conn.execute(text("UPDATE resolvelab.evaluation_runs SET state='failed',metrics=CAST(:metrics AS jsonb) WHERE tenant_id=:tenant AND evaluation_id=:id"),
                             {"tenant": tenant, "id": job["evaluation_id"], "metrics": json.dumps({"runner_error": "retry_limit_exhausted", "attempts": attempts-1})})
                continue
            token = str(uuid4())
            metadata.update(worker_token=token, attempts=attempts,
                            lease_expires_at=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat())
            conn.execute(text("UPDATE resolvelab.evaluation_runs SET state='running',environment=CAST(:environment AS jsonb) WHERE tenant_id=:tenant AND evaluation_id=:id"),
                         {"tenant": tenant, "id": job["evaluation_id"], "environment": json.dumps(metadata)})
            job["environment"] = metadata
            return p, job, token
    return None


def _complete(principal, job, token, state, metrics, report=None, destination=None):
    """Fence filesystem publication and the database acknowledgement together."""
    with transaction(principal) as conn:
        current = conn.execute(text("SELECT 1 FROM resolvelab.evaluation_runs WHERE tenant_id=:tenant AND evaluation_id=:id AND state='running' AND environment->>'worker_token'=:token FOR UPDATE"),
                               {"tenant":principal.tenant_id,"id":job["evaluation_id"],"token":token}).first()
        if not current:
            return False
        uri = None
        if report is not None:
            conn.execute(text("""INSERT INTO resolvelab.evaluation_reports(tenant_id,evaluation_id,report)
                VALUES(:tenant,:id,CAST(:report AS jsonb)) ON CONFLICT(tenant_id,evaluation_id)
                DO UPDATE SET report=EXCLUDED.report"""),
                {"tenant":principal.tenant_id,"id":job["evaluation_id"],"report":json.dumps(primitive(report),allow_nan=False)})
            uri = "db:evaluation_reports/" + str(job["evaluation_id"])
        if report is not None and os.environ.get("APP_ENV", "local") == "local":
            destination = destination or ROOT / "artifacts/evaluation" / str(job["evaluation_id"])
            destination.mkdir(parents=True,exist_ok=True)
            temporary = destination / ("workflow-evaluation."+token+".tmp")
            temporary.write_text(json.dumps(primitive(report),allow_nan=False),encoding="utf-8")
            final = destination / "workflow-evaluation.json"
            temporary.replace(final)
            uri = str(final)
        conn.execute(text("""UPDATE resolvelab.evaluation_runs SET state=:state,metrics=CAST(:metrics AS jsonb),
            case_results_uri=:uri,environment=environment || CAST(:extra AS jsonb)
            WHERE tenant_id=:tenant AND evaluation_id=:id AND environment->>'worker_token'=:token"""),
                     {"tenant":principal.tenant_id,"id":job["evaluation_id"],"token":token,"state":state,"metrics":json.dumps(primitive(metrics)),"uri":uri,
                      "extra":json.dumps({"completed_at":datetime.now(timezone.utc).isoformat(),"runner_environment":report.get("environment") if report else None})})
    return True


def run_once():
    target, admin_target = _configuration()
    # This session lock is deliberately held across the isolated subprocess so
    # two workers cannot both reset the same evaluation database.
    with engine.connect() as coordinator:
        acquired = coordinator.execute(text("SELECT pg_try_advisory_lock(hashtext('resolvelab-evaluator'))")).scalar_one()
        if not acquired:
            return False
        try:
            claimed = _claim()
            if not claimed:
                return False
            principal, job, token = claimed
            evaluation_id = str(UUID(str(job["evaluation_id"])))
            destination = ROOT / "artifacts/evaluation" / evaluation_id
            destination.mkdir(parents=True, exist_ok=True)
            attempt_directory = destination / ("attempt-"+token)
            attempt_directory.mkdir(parents=True,exist_ok=True)
            env = dict(os.environ)
            env.update(DATABASE_URL=target, ADMIN_DATABASE_URL=admin_target, APP_ENV="local", AUTH_MODE="fixture", DEMO_MODE="true", GENERATION_ADAPTER="offline",
                       ALLOWED_HOSTS="testserver,localhost,127.0.0.1", ALLOWED_ORIGINS="http://testserver")
            state, metrics, report = "failed", {"runner_error": "evaluation_failed"}, None
            try:
                completed = run_bounded([sys.executable, "-m", "resolvelab.evaluation_job", "--output", str(attempt_directory),
                                            "--mode", job["mode"], "--release-id", str(job["release_id"])],
                                           cwd=ROOT, env=env, timeout=720)
                report_path = attempt_directory / "workflow-evaluation.json"
                if report_path.exists():
                    if report_path.stat().st_size>4*1024*1024:
                        raise ValueError("Evaluation report exceeds bounded size")
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    if (report.get("release_id") != str(job["release_id"]) or report.get("dataset_sha256") != job["dataset_sha256"] or report.get("mode") != job["mode"]
                            or not isinstance(report.get("metrics"),dict) or not isinstance(report.get("cases"),list)):
                        raise ValueError("Evaluation provenance does not match the leased job")
                    metrics = report["metrics"]
                    state = "completed"
                else:
                    metrics = {"runner_error": "subprocess_did_not_produce_report", "exit_code": completed.returncode}
            except subprocess.TimeoutExpired:
                metrics = {"runner_error": "evaluation_timeout", "timeout_seconds": 720}
            except Exception as exc:
                metrics = {"runner_error": "evaluation_worker_error", "exception_type": type(exc).__name__}
            _complete(principal,job,token,state,metrics,report if state=="completed" else None,destination)
            print(json.dumps({"evaluation_id": evaluation_id, "state": state, "attempt": job["environment"]["attempts"]}), flush=True)
            return True
        finally:
            coordinator.execute(text("SELECT pg_advisory_unlock(hashtext('resolvelab-evaluator'))"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    options = parser.parse_args(argv)
    _configuration()
    while True:
        worked = run_once()
        if options.once:
            return 0
        if not worked:
            time.sleep(max(0.25, min(options.poll_seconds, 30)))


if __name__ == "__main__":
    raise SystemExit(main())
