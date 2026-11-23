"""Hard process/session death tests inspect durable PostgreSQL state after exit."""
import json
import os
import subprocess
import sys
import signal
import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from resolvelab import service
from resolvelab.db import ROOT, admin_engine, transaction
from resolvelab.evaluate import approval_body, create_and_resolve, principal, rows


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_real_process_exit_around_approval_commit(actor, boundary):
    proposal = create_and_resolve("Cancel A-100.", "A-100")[1]["proposal"]
    program = r'''
import json,sys,os
from resolvelab import service
from resolvelab.auth import fixture_principal
data=json.loads(sys.stdin.read())
if data['boundary']=='before_commit':
    original=service._remember
    def stop(conn,p,operation,*args,**kwargs):
        original(conn,p,operation,*args,**kwargs)
        if operation=='approve':
            print('hard-exit-with-uncommitted-order-and-receipt',flush=True)
            os._exit(71)
    service._remember=stop
service.approve_proposal(fixture_principal('alpha-approver-one'),data['id'],data['body'],'hard-crash',faults={'clock':'2026-11-23T12:00:00Z'})
print('hard-exit-after-durable-commit-before-client-response',flush=True)
os._exit(72)
'''
    completed = subprocess.run([sys.executable,"-u","-c",program],input=json.dumps({"id":proposal["proposal_id"],"body":approval_body(proposal),"boundary":boundary}),
                               cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True,timeout=30)
    assert completed.returncode == (71 if boundary=="before_commit" else 72), completed.stderr
    assert "hard-exit" in completed.stdout
    before = rows(actor,"SELECT status,version FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]
    actions = rows(actor,"SELECT action_id::text FROM resolvelab.actions")
    if boundary=="before_commit":
        assert before == {"status":"pending","version":1}
        assert not actions and not rows(actor,"SELECT * FROM resolvelab.approvals")
    else:
        assert before == {"status":"cancelled","version":2} and len(actions)==1
    replay = service.approve_proposal(principal("alpha-approver-one"),proposal["proposal_id"],approval_body(proposal),"hard-crash",faults={"clock":"2026-11-23T12:00:00Z"})
    assert replay["status"]=="succeeded"
    assert len(rows(actor,"SELECT * FROM resolvelab.actions"))==1
    assert rows(actor,"SELECT version FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]["version"]==2
    if actions:
        assert replay["action_id"]==actions[0]["action_id"]


def test_database_connection_termination_rolls_back_atomic_approval(actor, monkeypatch):
    proposal = create_and_resolve("Cancel A-100.","A-100")[1]["proposal"]
    original = service._remember
    def terminate(conn,p,operation,*args,**kwargs):
        original(conn,p,operation,*args,**kwargs)
        if operation=="approve":
            pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
            with admin_engine.begin() as admin:
                assert admin.execute(text("SELECT pg_terminate_backend(:pid)"),{"pid":pid}).scalar_one()
    monkeypatch.setattr(service,"_remember",terminate)
    with pytest.raises(DBAPIError):
        service.approve_proposal(principal("alpha-approver-one"),proposal["proposal_id"],approval_body(proposal),"connection-loss",faults={"clock":"2026-11-23T12:00:00Z"})
    monkeypatch.setattr(service,"_remember",original)
    assert rows(actor,"SELECT status,version FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]=={"status":"pending","version":1}
    assert not rows(actor,"SELECT * FROM resolvelab.actions")
    assert service.approve_proposal(principal("alpha-approver-one"),proposal["proposal_id"],approval_body(proposal),"connection-loss",faults={"clock":"2026-11-23T12:00:00Z"})["status"]=="succeeded"


def queued_job():
    evaluator = principal("alpha-evaluator")
    release = rows(evaluator,"SELECT release_id::text FROM resolvelab.active_releases")[0]["release_id"]
    return service.create_evaluation(evaluator,{"release_id":release,"dataset_key":"fixture-v1","mode":"deterministic_invariants"},str(uuid4()))


def test_worker_hard_kill_lease_reclaim_and_stale_publication_fence(actor,tmp_path):
    from resolvelab import worker
    job = queued_job()
    program = r'''
import time
from resolvelab import worker
original=worker._claim
def pause():
    claimed=original()
    import os
    print('durable-lease-claimed:'+str(os.getpid()),flush=True)
    time.sleep(300)
    return claimed
worker._claim=pause
worker.run_once()
'''
    child = subprocess.Popen([sys.executable,"-u","-c",program],cwd=ROOT,env=os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        marker=child.stdout.readline().strip()
        assert marker.startswith("durable-lease-claimed:"), marker
        interpreter_pid=int(marker.rsplit(":",1)[1])
        old = rows(actor,"SELECT environment FROM resolvelab.evaluation_runs WHERE evaluation_id=:id",{"id":job["evaluation_id"]})[0]["environment"]["worker_token"]
        os.kill(interpreter_pid,signal.SIGTERM); child.wait(timeout=15)
    finally:
        if child.poll() is None: child.kill();child.wait(timeout=15)
    with admin_engine.begin() as conn:
        conn.execute(text("UPDATE resolvelab.evaluation_runs SET environment=jsonb_set(environment,'{lease_expires_at}',to_jsonb('2000-01-01T00:00:00Z'::text)) WHERE evaluation_id=:id"),{"id":job["evaluation_id"]})
    owner,new_job,new_token=worker._claim()
    assert new_token!=old and new_job["environment"]["attempts"]==2
    assert not worker._complete(owner,new_job,old,"completed",{}, {"metrics":{},"cases":[]},tmp_path)
    assert not (tmp_path/"workflow-evaluation.json").exists()
    assert worker._complete(owner,new_job,new_token,"failed",{"runner_error":"deliberate-recovery-test"},destination=tmp_path)
    assert rows(actor,"SELECT state FROM resolvelab.evaluation_runs WHERE evaluation_id=:id",{"id":job["evaluation_id"]})[0]["state"]=="failed"


def test_evaluation_child_mutex_releases_only_on_actual_process_exit():
    from resolvelab.db import engine
    program = r'''
import time
from resolvelab import evaluate,evaluation_job
def pause(argv=None):
    import os
    print('independent-evaluator-mutex-held:'+str(os.getpid()),flush=True)
    time.sleep(300)
evaluate.main=pause
evaluation_job.main([])
'''
    child=subprocess.Popen([sys.executable,"-u","-c",program],cwd=ROOT,env=os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        marker=child.stdout.readline().strip()
        assert marker.startswith("independent-evaluator-mutex-held:"), marker
        interpreter_pid=int(marker.rsplit(":",1)[1])
        with engine.connect() as conn:
            assert not conn.execute(text("SELECT pg_try_advisory_lock(hashtext('resolvelab-isolated-evaluator'))")).scalar_one()
        os.kill(interpreter_pid,signal.SIGTERM);child.wait(timeout=15)
        with engine.connect() as conn:
            deadline=time.monotonic()+5
            while not conn.execute(text("SELECT pg_try_advisory_lock(hashtext('resolvelab-isolated-evaluator'))")).scalar_one():
                assert time.monotonic()<deadline,"PostgreSQL did not release the dead evaluator's session mutex"
                time.sleep(.02)
            conn.execute(text("SELECT pg_advisory_unlock(hashtext('resolvelab-isolated-evaluator'))"))
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=15)


def test_timeout_terminates_owned_launcher_and_grandchild_tree(tmp_path):
    from resolvelab.processes import run_bounded
    from resolvelab.db import engine
    marker=tmp_path/'mutex-held.txt'
    inner="""
import os,time
from pathlib import Path
from sqlalchemy import text
from resolvelab.db import engine
with engine.connect() as conn:
    conn.execute(text("SELECT pg_advisory_lock(hashtext('resolvelab-timeout-grandchild'))"))
    conn.commit()
    Path(os.environ['RECOVERY_BARRIER']).write_text(str(os.getpid()))
    time.sleep(300)
"""
    outer="import subprocess,sys,time;subprocess.Popen([sys.executable,'-u','-c',"+repr(inner)+"]);time.sleep(300)"
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded([sys.executable,'-u','-c',outer],cwd=ROOT,env=dict(os.environ,RECOVERY_BARRIER=str(marker)),timeout=5)
    assert marker.exists(),"The real grandchild must hold the database mutex before timeout"
    with engine.connect() as conn:
        deadline=time.monotonic()+5
        while not conn.execute(text("SELECT pg_try_advisory_lock(hashtext('resolvelab-timeout-grandchild'))")).scalar_one():
            assert time.monotonic()<deadline,"The timed-out evaluator grandchild remained alive"
            time.sleep(.02)
        conn.execute(text("SELECT pg_advisory_unlock(hashtext('resolvelab-timeout-grandchild'))"))
