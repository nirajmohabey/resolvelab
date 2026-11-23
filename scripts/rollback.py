"""Explicit local release pointer change; old cases are never rewritten."""
import argparse
import json
import os
from uuid import uuid4
from dotenv import load_dotenv
from sqlalchemy import text
from resolvelab.db import ROOT,admin_engine
from resolvelab.service import load_pinned_router

parser=argparse.ArgumentParser()
parser.add_argument('--organization',choices=['alpha','beta'],default='alpha')
parser.add_argument('--model-version',required=True)
args=parser.parse_args()
load_dotenv(ROOT/'.env')
if os.environ.get('APP_ENV')!='local':
    raise SystemExit('This fixture administration command is local-only.')
tenant='11111111-1111-4111-8111-111111111111' if args.organization=='alpha' else '22222222-2222-4222-8222-222222222222'
with admin_engine.begin() as conn:
    release=conn.execute(text('SELECT r.*,m.artifact_uri,m.artifact_sha256,m.validation_threshold,m.version_label AS model_version FROM resolvelab.releases r JOIN resolvelab.model_versions m USING(tenant_id,model_id) WHERE r.tenant_id=:t AND m.version_label=:v'),{'t':tenant,'v':args.model_version}).mappings().first()
    if not release:
        raise SystemExit('That immutable release is not registered.')
    load_pinned_router(release)
    old=conn.execute(text('SELECT release_id FROM resolvelab.active_releases WHERE tenant_id=:t FOR UPDATE'),{'t':tenant}).scalar_one()
    conn.execute(text('UPDATE resolvelab.active_releases SET release_id=:r,updated_at=now() WHERE tenant_id=:t'),{'t':tenant,'r':release['release_id']})
record={'operation_id':str(uuid4()),'organization':args.organization,'previous_release_id':str(old),'new_release_id':str(release['release_id']),'model_version':args.model_version,'verified_artifact_sha256':release['artifact_sha256']}
with (ROOT/'artifacts/release_operations.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(record)+'\n')
print(json.dumps(record,indent=2))
