"""Start an isolated local PostgreSQL cluster; never touch an existing user cluster."""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
PORT = 55439

def command(name):
    found = shutil.which(name)
    if found:
        return found
    candidate = Path(os.getenv('PG_BIN', r'C:\Program Files\PostgreSQL\17\bin')) / (name + '.exe')
    if candidate.exists():
        return str(candidate)
    raise SystemExit(f'{name} was not found. Install PostgreSQL 17 or set PG_BIN. Docker Compose is another option.')

def run(*args):
    subprocess.run(args, check=True, cwd=ROOT)

def bootstrap(stop=False):
    os.chdir(ROOT)
    RUNTIME.mkdir(exist_ok=True)
    data = RUNTIME / 'postgres'
    if stop:
        if data.exists():
            run(command('pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop')
        return
    envfile = ROOT / '.env'
    if not envfile.exists():
        admin_secret, app_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        envfile.write_text('\n'.join([
            'APP_ENV=local', 'DEMO_MODE=true', 'FIXTURE_CLOCK=2026-11-23T12:00:00Z',
            f'AUTH_SECRET={secrets.token_urlsafe(48)}', 'GENERATION_ADAPTER=offline',
            f'ADMIN_DATABASE_URL=postgresql+psycopg://postgres:{quote(admin_secret)}@127.0.0.1:{PORT}/resolvelab',
            f'DATABASE_URL=postgresql+psycopg://resolvelab_app:{quote(app_secret)}@127.0.0.1:{PORT}/resolvelab',
            ''
        ]), encoding='utf-8')
        (RUNTIME / 'admin-password').write_text(admin_secret, encoding='utf-8')
    from dotenv import load_dotenv
    load_dotenv(envfile, override=False)
    from sqlalchemy.engine import make_url
    admin_url = make_url(os.environ['ADMIN_DATABASE_URL'])
    app_url = make_url(os.environ['DATABASE_URL'])
    extra = {
        'EVALUATION_DATABASE_URL': app_url.set(database='resolvelab_evaluation').render_as_string(hide_password=False),
        'ADMIN_EVALUATION_DATABASE_URL': admin_url.set(database='resolvelab_evaluation').render_as_string(hide_password=False),
        'TEST_DATABASE_URL': app_url.set(database='resolvelab_test').render_as_string(hide_password=False),
        'ADMIN_TEST_DATABASE_URL': admin_url.set(database='resolvelab_test').render_as_string(hide_password=False),
    }
    original = envfile.read_text(encoding='utf-8')
    with envfile.open('a', encoding='utf-8') as f:
        for key,value in extra.items():
            if key+'=' not in original:
                f.write(f'{key}={value}\n')
    if admin_url.host not in ('127.0.0.1', 'localhost') or admin_url.port != PORT:
        raise SystemExit('Native bootstrap requires the private local port 55439. For an external database, run migrations and seed explicitly.')
    if not data.exists():
        pwfile = RUNTIME / 'admin-password'
        pwfile.write_text(admin_url.password or '', encoding='utf-8')
        run(command('initdb'), '-D', str(data), '-U', 'postgres', '--encoding=UTF8', '--locale=C', '--auth-host=scram-sha-256', '--auth-local=scram-sha-256', '--pwfile', str(pwfile))
        with (data / 'postgresql.conf').open('a', encoding='utf-8') as f:
            f.write(f"\nlisten_addresses='127.0.0.1'\nport={PORT}\nmax_connections=30\nshared_buffers='64MB'\n")
        pwfile.unlink(missing_ok=True)
    status = subprocess.run([command('pg_ctl'), '-D', str(data), 'status'], capture_output=True)
    if status.returncode:
        run(command('pg_ctl'), '-D', str(data), '-l', str(RUNTIME / 'postgres.log'), '-w', 'start')
    import psycopg
    from psycopg import sql
    config = dict(host=admin_url.host, port=PORT, user=admin_url.username, password=admin_url.password)
    with psycopg.connect(**config, dbname='postgres', autocommit=True) as conn:
        if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (app_url.username,)).fetchone():
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(app_url.username), sql.Literal(app_url.password)))
        for database in ('resolvelab','resolvelab_evaluation','resolvelab_test'):
            if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (database,)).fetchone():
                conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    for database in ('resolvelab','resolvelab_evaluation','resolvelab_test'):
        child_env=os.environ.copy()
        child_env['ADMIN_DATABASE_URL']=admin_url.set(database=database).render_as_string(hide_password=False)
        subprocess.run([sys.executable,'-m','alembic','upgrade','head'],check=True,cwd=ROOT,env=child_env)
        with psycopg.connect(**config, dbname=database, autocommit=True) as conn:
            role = sql.Identifier(app_url.username)
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA resolvelab TO {}').format(role))
            conn.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA resolvelab TO {}').format(role))
            for table in ('cases','trace_events','proposals','proposal_evidence','approvals','actions','handoffs','idempotency_keys','evaluation_runs','fixture_orders'):
                conn.execute(sql.SQL('GRANT INSERT, UPDATE ON resolvelab.{} TO {}').format(sql.Identifier(table),role))
    print('Private PostgreSQL ready on 127.0.0.1:55439; migrations applied; runtime role has no DDL or superuser privileges.')

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--stop', action='store_true')
    bootstrap(parser.parse_args().stop)
