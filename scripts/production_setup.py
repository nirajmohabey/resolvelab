"""One-shot production migration and runtime provisioning; API never needs admin credentials."""
import os
from pathlib import Path
import subprocess
import sys
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

root=Path(__file__).resolve().parents[1]
if os.environ.get('APP_ENV')!='production':raise SystemExit('This command requires APP_ENV=production.')
admin=make_url(os.environ['ADMIN_DATABASE_URL']);runtime=make_url(os.environ['DATABASE_URL'])
if (admin.host,admin.port,admin.database)!=(runtime.host,runtime.port,runtime.database):
    raise SystemExit('Migration and runtime URLs must identify the same reviewed database.')
config=dict(host=admin.host,port=admin.port,user=admin.username,password=admin.password)
with psycopg.connect(**config,dbname='postgres',autocommit=True) as conn:
    existing=conn.execute('SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s',(runtime.username,)).fetchone()
    if existing and any(existing):raise SystemExit('Runtime role has administrative privileges; use a dedicated restricted login.')
    if not existing:
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(runtime.username),sql.Literal(runtime.password)))
    if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(runtime.database,)).fetchone():
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(runtime.database)))
subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=root,check=True)
with psycopg.connect(**config,dbname=runtime.database,autocommit=True) as conn:
    from resolvelab.provision import grant_runtime
    grant_runtime(conn,runtime.username)
if os.environ.get('SEED_SYNTHETIC_DEMO','false').lower()=='true':
    env=dict(os.environ,APP_ENV='local',DEMO_MODE='true',AUTH_MODE='fixture')
    subprocess.run([sys.executable,'-m','resolvelab.seed'],cwd=root,env=env,check=True)
print('Migrations and restricted runtime grants complete. External identities require separate reviewed enrollment.')
