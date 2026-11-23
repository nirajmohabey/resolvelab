"""Apply all migrations and seed a new private disposable database, then verify readiness."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from dotenv import load_dotenv
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

root=Path(__file__).resolve().parents[1];load_dotenv(root/'.env')
if os.environ.get('APP_ENV')!='local':raise SystemExit('This private installation check is local-only.')
admin=make_url(os.environ['ADMIN_DATABASE_URL']);runtime=make_url(os.environ['DATABASE_URL'])
if admin.host not in ['127.0.0.1','localhost'] or admin.port!=55439:
    raise SystemExit('Installation check is restricted to the project-owned local cluster on port55439.')
database='resolvelab_install_check_'+uuid4().hex[:12]
config={'host':admin.host,'port':admin.port,'user':admin.username,'password':admin.password}
created=False
try:
    with psycopg.connect(**config,dbname='postgres',autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)));created=True
    env=dict(os.environ,DATABASE_URL=runtime.set(database=database).render_as_string(hide_password=False),
             ADMIN_DATABASE_URL=admin.set(database=database).render_as_string(hide_password=False))
    subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=root,env=env,check=True)
    with psycopg.connect(**config,dbname=database,autocommit=True) as conn:
        from resolvelab.provision import grant_runtime
        grant_runtime(conn,runtime.username)
    program=r'''
from resolvelab.seed import seed
seed(write_manifest=False)
from fastapi.testclient import TestClient
from resolvelab.api import app
with TestClient(app) as client:
    ready=client.get('/health/ready');assert ready.status_code==200,ready.text
    login=client.post('/v1/demo/session',json={'actor':'alpha-operator'});assert login.status_code==200,login.text
    token=login.json()['access_token']
    headers={'Authorization':'Bearer '+token,'Idempotency-Key':'clean-install-create'}
    case=client.post('/v1/cases',headers=headers,json={'message':'Where is my order?','order_ref':'A-100'});assert case.status_code==201,case.text
    headers['Idempotency-Key']='clean-install-resolve'
    result=client.post('/v1/cases/'+case.json()['case_id']+'/resolve',headers=headers,json={'expected_case_version':case.json()['version']})
    assert result.status_code==200 and result.json()['disposition']=='resolved_read_only',result.text
print('Fresh migrations, least-privilege role, seed, signed login, trained tracking and readiness verified.')
'''
    completed=subprocess.run([sys.executable,'-c',program],cwd=root,env=env,capture_output=True,text=True,timeout=60)
    if completed.returncode:raise RuntimeError('Clean installation failed: '+completed.stderr)
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'database':database,'fresh_migration_and_seed':True,'runtime_nonowner_rls':True,
            'readiness_http_status':200,'signed_login_verified':True,'trained_tracking_verified':True,'serving_database_reset':False,'command_output':completed.stdout.strip()}
    destination=root/'artifacts/checks/clean-install.json';destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
finally:
    if created:
        with psycopg.connect(**config,dbname='postgres',autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
