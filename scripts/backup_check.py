"""Restore this project into a new disposable DB and verify tracking + approval."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4
from datetime import datetime,timezone
from dotenv import load_dotenv
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from backup_guard import check_restore_urls, verify_restore_destination

root=Path(__file__).resolve().parents[1];load_dotenv(root/'.env')
if os.environ.get('APP_ENV')!='local':raise SystemExit('Local fixture backup check only.')
admin=make_url(os.environ['ADMIN_DATABASE_URL']);runtime=make_url(os.environ['DATABASE_URL'])
database='resolvelab_restore_'+uuid4().hex[:12]
out=Path(os.environ.get('BACKUP_CHECK_OUTPUT',str(root/'artifacts/backup'))).resolve();out.mkdir(parents=True,exist_ok=True)
archive=root/'.runtime'/f'{database}.dump';archive.parent.mkdir(exist_ok=True)
created=False
restore_runtime=runtime.set(database=database).render_as_string(hide_password=False)
restore_admin=admin.set(database=database).render_as_string(hide_password=False)
check_restore_urls(restore_runtime,restore_admin,database)
binpath=Path(os.getenv('PG_BIN',r'C:\Program Files\PostgreSQL\17\bin'))
def binary(name):return shutil.which(name) or str(binpath/(name+'.exe'))

def table_snapshot(conn):
    result={}
    for (table,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='resolvelab' ORDER BY tablename"):
        digest=hashlib.sha256();count=0
        statement=sql.SQL('SELECT to_jsonb(t)::text FROM resolvelab.{} t ORDER BY to_jsonb(t)::text COLLATE "C"').format(sql.Identifier(table))
        for (row,) in conn.execute(statement):
            raw=row.encode('utf-8');digest.update(len(raw).to_bytes(8,'big'));digest.update(raw);count+=1
        result[table]={'rows':count,'sha256':digest.hexdigest()}
    return result
env=os.environ.copy();env['PGPASSWORD']=admin.password or ''
config=dict(host=admin.host,port=admin.port,user=admin.username,password=admin.password)
source_snapshot=None
try:
    source_snapshot=psycopg.connect(**config,dbname=admin.database)
    source_snapshot.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    identity=source_snapshot.execute('SELECT current_database(),inet_server_addr()::text,inet_server_port()').fetchone()
    source_identity=dict(zip(('database','server','port'),identity))
    snapshot_id=source_snapshot.execute('SELECT pg_export_snapshot()').fetchone()[0]
    baseline=source_snapshot.execute('SELECT tenant_id,order_ref,status,version FROM resolvelab.fixture_orders ORDER BY tenant_id,order_ref').fetchall()
    original_tables=table_snapshot(source_snapshot)
    env['RESOLVELAB_RESTORE_EXPECTED']=json.dumps(baseline,default=str)
    with psycopg.connect(**config,dbname='postgres',autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        created=True
    common=['-h',admin.host,'-p',str(admin.port),'-U',admin.username]
    subprocess.run([binary('pg_dump'),*common,'--snapshot',snapshot_id,'-Fc','-f',str(archive),admin.database],env=env,check=True,timeout=120)
    source_snapshot.close();source_snapshot=None
    subprocess.run([binary('pg_restore'),*common,'--exit-on-error','--no-owner','-d',database,str(archive)],env=env,check=True,timeout=120)
    destination=verify_restore_destination(restore_runtime,restore_admin,database,source_identity)
    with psycopg.connect(**config,dbname=database) as restored:
        restored_tables=table_snapshot(restored)
    if restored_tables!=original_tables:
        raise RuntimeError('The restored table contents differ from the exported recovery snapshot')
    env['DATABASE_URL']=restore_runtime
    env['ADMIN_DATABASE_URL']=restore_admin
    program="""
from resolvelab.auth import fixture_principal
from resolvelab import service
from resolvelab.db import admin_engine
from sqlalchemy import text
from uuid import uuid4
import json,os
p=fixture_principal('alpha-operator')
with admin_engine.begin() as conn:
    restored=conn.execute(text('SELECT tenant_id,order_ref,status,version FROM resolvelab.fixture_orders ORDER BY tenant_id,order_ref')).fetchall()
    assert json.loads(json.dumps([list(r) for r in restored],default=str))==json.loads(os.environ['RESOLVELAB_RESTORE_EXPECTED'])
    # This new probe exists only in the randomly named restored database.
    probe='RESTORE-PROBE-'+uuid4().hex[:8]
    conn.execute(text("INSERT INTO resolvelab.fixture_orders(tenant_id,order_ref,customer_subject,status,tracking_summary,cancel_deadline,version) VALUES(:t,:o,'customer-001','pending','Restore probe is pending','2026-11-23T12:20:00Z',1)"),{'t':p.tenant_id,'o':probe})
c=service.create_case(p,{'message':'Where is my order?','order_ref':probe},str(uuid4()))
r=service.resolve_case(p,c['case_id'],{'expected_case_version':c['version']},str(uuid4()))
assert r['disposition']=='resolved_read_only',r
c=service.create_case(p,{'message':'Please cancel my order.','order_ref':probe},str(uuid4()))
r=service.resolve_case(p,c['case_id'],{'expected_case_version':c['version']},str(uuid4()))
proposal=r['proposal'];a=fixture_principal('alpha-approver-one')
body={k:proposal[k] for k in ['proposal_sha256','expected_case_version','expected_order_version']}
outcome=service.approve_proposal(a,proposal['proposal_id'],body,str(uuid4()))
assert outcome['status']=='succeeded',outcome
print('Restored tracking and atomic approval verified.')
"""
    result=subprocess.run([sys.executable,'-c',program],cwd=root,env=env,check=False,capture_output=True,text=True,timeout=120)
    if result.returncode:
        raise RuntimeError('Restored workflow check failed:\n'+result.stderr)
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'disposable_database':database,'destination_verified':True,'destination':destination,'restored':True,'exported_snapshot_used':True,'tables_verified':len(original_tables),'table_snapshot':original_tables,'original_order_snapshot_verified':True,'synthetic_restore_probe_used':True,'tracking_verified':True,'approval_verified':True,'source_database_written_by_check':False,'command_output':result.stdout.strip()}
    (out/'restore-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
finally:
    try:
        if source_snapshot is not None:
            source_snapshot.close()
    finally:
        try:
            if created:
                with psycopg.connect(**config,dbname='postgres',autocommit=True) as conn:
                    # A failed CREATE must never authorize deleting a pre-existing DB.
                    conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
        finally:
            # A failed connection close or DROP must not retain a private dump.
            archive.unlink(missing_ok=True)
