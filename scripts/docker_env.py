"""Create ignored container-only development secrets."""
from pathlib import Path
import secrets
root=Path(__file__).resolve().parents[1]
target=root/'.env.docker'
if target.exists():
    raise SystemExit('.env.docker already exists. It was preserved.')
admin=secrets.token_urlsafe(32); runtime=secrets.token_urlsafe(32)
lines=['APP_ENV=local','DEMO_MODE=true','FIXTURE_CLOCK=2026-11-23T12:00:00Z','GENERATION_ADAPTER=offline',f'AUTH_SECRET={secrets.token_urlsafe(48)}',f'POSTGRES_PASSWORD={admin}',f'APP_DB_PASSWORD={runtime}']
for prefix,db in [('', 'resolvelab'),('EVALUATION_', 'resolvelab_evaluation'),('TEST_','resolvelab_test')]:
    lines.append(f'{prefix}DATABASE_URL=postgresql+psycopg://resolvelab_app:{runtime}@db:5432/{db}')
    lines.append(f'ADMIN_{prefix}DATABASE_URL=postgresql+psycopg://postgres:{admin}@db:5432/{db}')
target.write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('Created ignored .env.docker. Start: docker compose --env-file .env.docker up --build -d')
