"""One-shot container migration, artifact preparation and safe initial fixture seed."""
import os
from pathlib import Path
import subprocess
import sys
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

root=Path(__file__).resolve().parents[1]
admin=make_url(os.environ['ADMIN_DATABASE_URL'])
runtime=make_url(os.environ['DATABASE_URL'])
config=dict(host=admin.host,port=admin.port,user=admin.username,password=admin.password)
with psycopg.connect(**config,dbname='postgres',autocommit=True) as conn:
    if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(runtime.username,)).fetchone():
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(runtime.username),sql.Literal(runtime.password)))
    for database in ['resolvelab','resolvelab_evaluation','resolvelab_test']:
        if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(database,)).fetchone():
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
for database in ['resolvelab','resolvelab_evaluation','resolvelab_test']:
    env=os.environ.copy();env['ADMIN_DATABASE_URL']=admin.set(database=database).render_as_string(hide_password=False)
    subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=root,env=env,check=True)
    with psycopg.connect(**config,dbname=database,autocommit=True) as conn:
        from resolvelab.provision import grant_runtime
        grant_runtime(conn,runtime.username)
if not (root/'artifacts/router.joblib').exists():
    subprocess.run([sys.executable,'scripts/train_all.py'],cwd=root,check=True)
subprocess.run([sys.executable,'-m','resolvelab.seed'],cwd=root,check=True)
# Named artifact volumes are writable by the nonroot API and worker.
if hasattr(os,'geteuid') and os.geteuid()==0:
    for directory in [root/'artifacts',root/'work']:
        for path in [directory,*directory.rglob('*')]:
            os.chown(path,10001,10001)
print('Container initialization complete.')
