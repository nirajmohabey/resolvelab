"""Durable outbox/reports, actual embedded native queue and isolated evaluation."""
import asyncio
import time
from uuid import uuid4

from sqlalchemy import text

from resolvelab import queue_jobs, service, worker
from resolvelab.evaluate import principal, rows


def queued():
    actor = principal("alpha-evaluator")
    release = rows(actor,"SELECT release_id::text FROM resolvelab.active_releases")[0]["release_id"]
    result = service.create_evaluation(actor,{"release_id":release,"dataset_key":"fixture-v1","mode":"deterministic_invariants"},str(uuid4()))
    return actor, result


def test_native_queue_sdk_embedded_delivery_uses_registered_private_consumer(monkeypatch):
    from vercel.queue.embedded import embedded_queue_service
    import queue_worker
    seen = []
    monkeypatch.setattr(queue_jobs,"consume",lambda payload: seen.append(payload))
    payload = {"tenant_id":"11111111-1111-4111-8111-111111111111","evaluation_id":str(uuid4())}

    async def run():
        async with embedded_queue_service() as broker:
            client = broker.get_async_client()
            message_id = await client.send(queue_jobs.TOPIC,payload)
            broker.dispatcher.wake()
            deadline = time.monotonic()+5
            while not broker.server.state.by_id[message_id].acknowledged:
                assert time.monotonic()<deadline,"Embedded native queue did not acknowledge the registered subscriber"
                await asyncio.sleep(.01)
    asyncio.run(run())
    assert seen == [payload]


def test_failed_queue_publish_keeps_committed_job_and_retryable_outbox(monkeypatch):
    actor, job = queued()
    monkeypatch.setenv("QUEUE_ENABLED","true")
    async def unavailable(*args):
        raise RuntimeError("Synthetic queue outage")
    monkeypatch.setattr(queue_jobs,"publish",unavailable)
    assert asyncio.run(queue_jobs.dispatch_pending(actor.tenant_id,job["evaluation_id"])) == 0
    assert rows(actor,"SELECT state FROM resolvelab.evaluation_runs")[0]["state"] == "queued"
    outbox = rows(actor,"SELECT * FROM resolvelab.evaluation_outbox")[0]
    assert outbox["delivered_at"] is None and outbox["lease_expires_at"] is None
    assert outbox["dispatch_attempts"] == 1 and outbox["last_error_code"] == "queue_publish_unavailable"


def test_durable_report_publication_obeys_worker_token_and_tenant_scope(monkeypatch, tmp_path):
    actor, job = queued()
    p, claimed, token = worker._claim()
    monkeypatch.setenv("APP_ENV","production")
    report = {"metrics":{"fixture":True},"cases":[{"id":"fixture","answer":"<script>not executable</script>"}]}
    assert not worker._complete(p,claimed,str(uuid4()),"completed",{},report)
    assert not rows(actor,"SELECT * FROM resolvelab.evaluation_reports")
    assert worker._complete(p,claimed,token,"completed",report["metrics"],report)
    assert rows(actor,"SELECT report FROM resolvelab.evaluation_reports")[0]["report"] == report
    beta = principal_for_beta()
    assert not rows(beta,"SELECT report FROM resolvelab.evaluation_reports")
    assert service.get_evaluation(actor,job["evaluation_id"])["case_results_uri"].startswith("db:")
    from resolvelab.api import evaluation_report, evaluation_report_html
    from uuid import UUID
    assert evaluation_report(UUID(job["evaluation_id"]),actor) == report
    document = evaluation_report_html(UUID(job["evaluation_id"]),actor).body.decode()
    assert "<script>not executable</script>" not in document and "&lt;script&gt;" in document


def principal_for_beta():
    from resolvelab.auth import Principal
    from resolvelab.seed import fixture_id
    return Principal("22222222-2222-4222-8222-222222222222",fixture_id("beta","beta-operator"),"operator","customer-002")


def test_native_consumer_runs_real_bounded20_and_stores_report_in_serving_db(monkeypatch):
    actor, job = queued()
    monkeypatch.setenv("DEPLOYMENT_TENANTS",actor.tenant_id)
    monkeypatch.setenv("APP_ENV","production")
    monkeypatch.delenv("AUTH_SECRET",raising=False)
    original = queue_jobs.run_bounded
    def bare_child(args, **kwargs):
        import os
        assert str(queue_jobs.ROOT / "src") in kwargs["env"]["PYTHONPATH"].split(os.pathsep)
        assert len(kwargs["env"]["AUTH_SECRET"]) >= 32
        # Disable editable-install/site bootstrapping: only the explicit child
        # package/vendor path contract can make this actual evaluator succeed.
        return original([args[0],"-S",*args[1:]], **kwargs)
    monkeypatch.setattr(queue_jobs,"run_bounded",bare_child)
    queue_jobs.consume({"tenant_id":actor.tenant_id,"evaluation_id":job["evaluation_id"]})
    result = service.get_evaluation(actor,job["evaluation_id"])
    assert result["state"] == "completed"
    assert result["metrics"]["workflow_success"] == {"numerator":20,"denominator":20,"rate":1.0}
    assert result["metrics"]["runner_errors"] == result["metrics"]["unauthorized_effect_count"] == 0
    report = rows(actor,"SELECT report FROM resolvelab.evaluation_reports")[0]["report"]
    assert len(report["cases"]) == 20 and report["release_id"] == job["release_id"]
    assert report["release_provenance"]["artifact_hash_matches_database"]
    queue_jobs.consume({"tenant_id":actor.tenant_id,"evaluation_id":job["evaluation_id"]})
    assert rows(actor,"SELECT environment->>'attempts' AS attempts FROM resolvelab.evaluation_runs")[0]["attempts"] == "1"


def test_fixture_seed_supports_a_real_nobypass_role_with_explicit_tenant_context(monkeypatch):
    """Grant setup rights in the disposable DB only; restore runtime grants after."""
    from psycopg import sql
    from resolvelab import db
    from resolvelab.provision import grant_runtime
    from resolvelab.seed import seed
    administrator, runtime = db.admin_engine, db.engine
    role = runtime.url.username
    with administrator.begin() as conn:
        assert conn.execute(text("SELECT current_database()")).scalar_one() == "resolvelab_test"
        conn.connection.driver_connection.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON ALL TABLES IN SCHEMA resolvelab TO {}").format(sql.Identifier(role)))
    try:
        with runtime.connect() as conn:
            assert not any(conn.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one())
        monkeypatch.setattr(db,"admin_engine",runtime)
        manifest = seed(reset=True,write_manifest=False)
        assert set(manifest["organizations"]) == {"alpha","beta"}
        for alias in ("alpha-operator","beta-operator"):
            actor = principal(alias)
            assert rows(actor,"SELECT count(*) AS n FROM resolvelab.corpus_versions")[0]["n"] == 1
        with runtime.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM resolvelab.tenants")).scalar_one() == 0
    finally:
        monkeypatch.setattr(db,"admin_engine",administrator)
        with administrator.begin() as conn:
            grant_runtime(conn.connection.driver_connection,role)
