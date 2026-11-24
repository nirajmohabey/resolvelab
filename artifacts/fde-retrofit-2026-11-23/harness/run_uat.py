"""Six-case simulated acceptance on an owned local PostgreSQL cluster only.

Reuses application migrations, fixture seed and runtime grants. Never reads a
production URL or changes the native .runtime cluster/process records.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4
from urllib.parse import quote

import psycopg
from psycopg import sql

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--label', required=True)
parser.add_argument('--project', type=Path)
parser.add_argument('--pg-bin', type=Path)
parser.add_argument('--node', type=Path)
args = parser.parse_args()
assert args.label in {'before', 'after', 'replay'}
ROOT = args.project.resolve() if args.project else next((item for item in HERE.parents if (item / 'pyproject.toml').is_file() and (item / 'src/resolvelab').is_dir()), None)
if ROOT is None: parser.error('Supply --project with the ResolveLab repository directory')
assert (ROOT / 'src/resolvelab/api.py').is_file()
sys.path.insert(0, str(ROOT / 'src'))
from resolvelab.provision import grant_runtime
from resolvelab.processes import terminate_owned_tree
OUT = ROOT / 'artifacts/fde-retrofit-2026-11-23' / args.label
OUT.mkdir(parents=True, exist_ok=False)
scratch = ROOT / 'work/fde-acceptance'
scratch.mkdir(parents=True, exist_ok=True)
owned = scratch / ('owned-' + uuid4().hex[:12])
owned.mkdir(exist_ok=False)
data = owned / 'postgres'
initdb = shutil.which('initdb')
PG = args.pg_bin or (Path(os.environ['PG_BIN']) if os.environ.get('PG_BIN') else Path(initdb).parent if initdb else None)
node = args.node or shutil.which('node')
if PG is None or node is None: parser.error('Provide PostgreSQL tools through --pg-bin/PG_BIN/PATH and Node through --node/PATH')
def pg_tool(name): return str(PG / (name + ('.exe' if os.name == 'nt' else '')))
flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
db_port, api_port = 55571, 8137
for port in (db_port, api_port):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))
db_name = 'resolvelab_uat_' + uuid4().hex[:12]
assert db_name.startswith('resolvelab_uat_')
admin_secret, app_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
base = f'http://127.0.0.1:{api_port}'
admin_url = f'postgresql+psycopg://postgres:{quote(admin_secret)}@127.0.0.1:{db_port}/{db_name}'
runtime_url = f'postgresql+psycopg://fde_uat_app:{quote(app_secret)}@127.0.0.1:{db_port}/{db_name}'
env = {key: value for key, value in os.environ.items() if not key.startswith(('DATABASE_', 'ADMIN_', 'EVALUATION_', 'TEST_', 'NEON_', 'OPENAI_', 'VERCEL', 'OIDC_'))}
env.update(DATABASE_URL=runtime_url, ADMIN_DATABASE_URL=admin_url,
           APP_ENV='local', DEMO_MODE='true', AUTH_MODE='fixture',
           AUTH_SECRET=secrets.token_urlsafe(48), GENERATION_ADAPTER='offline',
           FIXTURE_CLOCK='2026-11-23T12:00:00Z',
           ALLOWED_HOSTS='127.0.0.1,localhost', ALLOWED_ORIGINS=base,
           PYTHONPATH=str(ROOT / 'src'), DB_POOL_SIZE='2', DB_MAX_OVERFLOW='1',
           BASE_URL=base, UAT_OUTPUT=str(OUT), UAT_CONTROL=str(owned), UAT_PROJECT=str(ROOT))
connection = dict(host='127.0.0.1', port=db_port, user='postgres', password=admin_secret)
children, streams = [], []
started = False
report = dict(schema_version='1.0', reviewer='AI-agent simulated operator; no outside reviewer',
              source_database_touched=False, production_touched=False, own_cluster=True,
              own_database=db_name, db_host='127.0.0.1', db_port=db_port, base_url=base,
              source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip())
report['environment'] = dict(python=sys.version.split()[0], postgres=subprocess.check_output([pg_tool('postgres'), '--version'], text=True).strip(), node=subprocess.check_output([str(node), '--version'], text=True).strip())
protected = [ROOT / '.env', ROOT / '.runtime/api.process.json', ROOT / '.runtime/worker.process.json']
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
report['protected_file_hashes_before'] = {p.name:digest(p) for p in protected}

def save():
    (owned / 'private-isolation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    public = {key:value for key,value in report.items() if not key.startswith('protected_file_hashes_')}
    public['protected_files_checked'] = [str(item.relative_to(ROOT)) for item in protected]
    public['privacy'] = 'Local environment and process-record values/hashes omitted; unchanged comparison retained.'
    (OUT / 'isolation.json').write_text(json.dumps(public, indent=2) + '\n', encoding='utf-8')

def run(label, command, child_env=env):
    with (owned / (label + '.log')).open('w', encoding='utf-8') as log:
        completed = subprocess.run(command, cwd=ROOT, env=child_env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    if completed.returncode:
        raise RuntimeError(label + ' failed; inspect scratch log (not public evidence)')
    print(json.dumps({'phase': label, 'passed': True}), flush=True)

def stop_owned(child):
    """Allow one bounded Windows retry for the exact process we created."""
    try:
        terminate_owned_tree(child)
    except subprocess.TimeoutExpired:
        report.setdefault('owned_process_stop_retries', []).append(child.pid)
        if child.poll() is None:
            subprocess.run(['taskkill.exe', '/PID', str(child.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
    if child.poll() is None:
        child.wait(timeout=10)

def launch_api(adapter):
    child_env = {**env, 'GENERATION_ADAPTER': adapter}
    stdout = (owned / ('api-' + str(len(children)) + '-' + adapter + '.log')).open('w', encoding='utf-8')
    streams.append(stdout)
    child = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'resolvelab.api:app', '--host', '127.0.0.1', '--port', str(api_port)],
                             cwd=ROOT, env=child_env, stdout=stdout, stderr=subprocess.STDOUT,
                             creationflags=flags, start_new_session=os.name != 'nt')
    children.append(child)
    deadline = time.monotonic() + 60
    while True:
        try:
            with urllib.request.urlopen(base + '/health/ready', timeout=2) as response:
                if response.status == 200: return child
        except Exception: pass
        if child.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('Owned API readiness failed; inspect scratch log')
        time.sleep(.2)

try:
    password_file = owned / 'initial-password'
    password_file.write_text(admin_secret, encoding='utf-8')
    try:
        run('init_owned_cluster', [pg_tool('initdb'), '-D', str(data), '-U', 'postgres', '--encoding=UTF8', '--locale=C', '--auth-host=scram-sha-256', '--auth-local=scram-sha-256', '--pwfile', str(password_file)])
    finally:
        password_file.unlink(missing_ok=True)
    with (data / 'postgresql.conf').open('a', encoding='utf-8') as conf:
        conf.write(f"\nlisten_addresses='127.0.0.1'\nport={db_port}\nmax_connections=20\nshared_buffers='32MB'\n")
    run('start_owned_cluster', [pg_tool('pg_ctl'), '-D', str(data), '-l', str(owned / 'postgres.log'), '-w', 'start'])
    started = True
    with psycopg.connect(**connection, dbname='postgres', autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier('fde_uat_app'), sql.Literal(app_secret)))
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db_name)))
    run('migrate_owned_database', [sys.executable, '-m', 'alembic', 'upgrade', 'head'])
    with psycopg.connect(**connection, dbname=db_name, autocommit=True) as conn:
        assert conn.execute('SELECT current_database()').fetchone()[0] == db_name
        grant_runtime(conn, 'fde_uat_app')
        assert conn.execute('SELECT count(*) FROM resolvelab.tenants').fetchone()[0] == 0
    run('seed_owned_empty_database', [sys.executable, '-c', 'from resolvelab.seed import seed; seed(write_manifest=False)'])
    with psycopg.connect(**connection, dbname=db_name) as conn:
        report['baseline_state'] = {
            'orders': conn.execute('SELECT order_ref,status,version FROM resolvelab.fixture_orders ORDER BY order_ref').fetchall(),
            'actions': conn.execute('SELECT count(*) FROM resolvelab.actions').fetchone()[0],
            'proposals': conn.execute('SELECT state,count(*) FROM resolvelab.proposals GROUP BY state ORDER BY state').fetchall(),
            'cases': conn.execute('SELECT state,count(*) FROM resolvelab.cases GROUP BY state ORDER BY state').fetchall(),
        }
    api = launch_api('offline')
    stdout = (OUT / 'browser.log').open('w', encoding='utf-8')
    streams.append(stdout)
    browser = subprocess.Popen([str(node), str(HERE / 'six-cases.mjs')], cwd=ROOT, env=env, stdout=stdout, stderr=subprocess.STDOUT,
                               creationflags=flags, start_new_session=os.name != 'nt')
    children.append(browser)
    deadline = time.monotonic() + 240
    switched = set()
    while browser.poll() is None:
        for phase, adapter in [('fail-generation', 'uat-unavailable'), ('repair-generation', 'offline')]:
            if phase not in switched and (owned / phase).exists():
                stop_owned(api)
                api = launch_api(adapter)
                switched.add(phase)
                (owned / (phase + '-ready')).write_text('owned API ready', encoding='utf-8')
        if time.monotonic() > deadline: raise RuntimeError('Six-case browser timeout')
        time.sleep(.1)
    report['browser_exit_code'] = browser.returncode
    with psycopg.connect(**connection, dbname=db_name) as conn:
        report['final_state'] = {
            'orders': conn.execute('SELECT order_ref,status,version FROM resolvelab.fixture_orders ORDER BY order_ref').fetchall(),
            'actions': conn.execute('SELECT count(*) FROM resolvelab.actions').fetchone()[0],
            'proposals': conn.execute('SELECT state,count(*) FROM resolvelab.proposals GROUP BY state ORDER BY state').fetchall(),
            'cases': conn.execute('SELECT state,count(*) FROM resolvelab.cases GROUP BY state ORDER BY state').fetchall(),
        }
    report['passed'] = browser.returncode == 0
finally:
    for child in reversed(children): stop_owned(child)
    for stream in streams: stream.close()
    if started:
        assert data.resolve().parent == owned.resolve() and owned.resolve().parent == scratch.resolve() and ROOT.resolve() in scratch.resolve().parents
        assert db_name.startswith('resolvelab_uat_') and len(db_name) == len('resolvelab_uat_') + 12
        with psycopg.connect(**connection, dbname='postgres', autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(db_name)))
        report['owned_database_removed'] = True
        run('stop_owned_cluster', [pg_tool('pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'])
        report['owned_cluster_stopped'] = True
    report['protected_file_hashes_after'] = {p.name:digest(p) for p in protected}
    report['protected_files_unchanged'] = report['protected_file_hashes_before'] == report['protected_file_hashes_after']
    report['owned_scratch_cluster'] = owned.name
    save()
    print(json.dumps({'output': str(OUT), 'passed': report.get('passed'), 'cluster_stopped': report.get('owned_cluster_stopped'), 'protected_files_unchanged': report['protected_files_unchanged']}), flush=True)
if not report.get('passed'): raise SystemExit(1)
