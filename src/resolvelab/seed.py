"""Reviewed local administration. No public source/account registration endpoint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]

def fixture_id(organization, alias):
    return str(uuid5(NAMESPACE_URL, 'resolvelab/' + organization + '/' + alias))

def seed(reset=False):
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    if os.environ.get('APP_ENV', 'production') != 'local' or os.environ.get('DEMO_MODE', 'false').lower() != 'true':
        raise RuntimeError('Fixture administration requires APP_ENV=local and DEMO_MODE=true.')
    from resolvelab.db import admin_engine
    from resolvelab.retrieval import embed, EMBEDDING_DIMENSION, EMBEDDING_MODEL
    data = json.loads((ROOT / 'fixtures/evaluation_cases.json').read_text(encoding='utf-8'))
    manifest_path = ROOT / 'artifacts/model_manifest.json'
    if not manifest_path.exists():
        raise RuntimeError('Model manifest missing. Run python scripts/train_all.py before seeding.')
    model = json.loads(manifest_path.read_text(encoding='utf-8'))
    zero = hashlib.sha256(b'fixture-bootstrap').hexdigest()
    manifest = {'organizations': {}, 'actors': {}, 'cases': {}, 'corpora': {}, 'releases': {}, 'models': {}}
    sections = [s['text'] for d in data['policy_documents'] for s in d['sections']]
    vectors = iter(embed(sections).tolist())
    vectors_by_text = {s: next(vectors) for s in sections}
    with admin_engine.begin() as conn:
        if reset:
            # This only affects this explicitly configured project schema.
            conn.exec_driver_sql('TRUNCATE ' + ','.join('resolvelab.'+x for x in ['tenants']) + ' CASCADE')
        for org in data['organizations']:
            alias, tid = org['alias'], org['tenant_id']
            manifest['organizations'][alias] = tid
            conn.execute(text('INSERT INTO resolvelab.tenants(tenant_id,display_name) VALUES(:t,:n) ON CONFLICT DO NOTHING'), {'t':tid,'n':alias.title()+' Retail'})
        actors=data['actors']+[{'alias':'alpha-admin','organization':'alpha','role':'admin','customer_subject':'customer-001'}]
        for a in actors:
            tid=manifest['organizations'][a['organization']]
            aid=fixture_id(a['organization'],a['alias'])
            manifest['actors'][a['alias']]={'tenant_id':tid,'actor_id':aid,'role':a['role'],'customer_subject':a['customer_subject'],'alias':a['alias']}
            conn.execute(text('INSERT INTO resolvelab.memberships(tenant_id,actor_id,role) VALUES(:t,:a,:r) ON CONFLICT DO NOTHING'), {'t':tid,'a':aid,'r':a['role']})
        for org,tid in manifest['organizations'].items():
            docs=[d for d in data['policy_documents'] if d['organization']==org and d['corpus']!='adversarial-fixture-only']
            corpus_alias=org+'-published-v1'
            cid=fixture_id(org,corpus_alias)
            manifest['corpora'][corpus_alias]=cid
            content_hash=hashlib.sha256(json.dumps(docs,sort_keys=True).encode()).hexdigest()
            conn.execute(text("INSERT INTO resolvelab.corpus_versions(tenant_id,corpus_id,version_label,content_sha256,embedding_model_revision,embedding_dimension,state) VALUES(:t,:c,'policy-v1',:h,:e,:dim,'published') ON CONFLICT DO NOTHING"),{'t':tid,'c':cid,'h':content_hash,'e':EMBEDDING_MODEL,'dim':EMBEDDING_DIMENSION})
            for d in docs:
                did=fixture_id(org,d['alias'])
                conn.execute(text("INSERT INTO resolvelab.documents(tenant_id,corpus_id,document_id,source_label,title,content_sha256,effective_at,access_scope) VALUES(:t,:c,:d,:s,:title,:h,'2026-11-01T00:00:00Z','support') ON CONFLICT DO NOTHING"),{'t':tid,'c':cid,'d':did,'s':d['alias'],'title':d['alias'].replace('-',' ').title(),'h':hashlib.sha256(json.dumps(d['sections'],sort_keys=True).encode()).hexdigest()})
                for s in d['sections']:
                    chunk=fixture_id(org,d['alias']+'/'+s['locator'])
                    conn.execute(text('INSERT INTO resolvelab.chunks(tenant_id,corpus_id,document_id,chunk_id,locator,body,embedding,embedding_dimension) VALUES(:t,:c,:d,:ch,:l,:b,:e,:dim) ON CONFLICT DO NOTHING'),{'t':tid,'c':cid,'d':did,'ch':chunk,'l':s['locator'],'b':s['text'],'e':vectors_by_text[s['text']],'dim':EMBEDDING_DIMENSION})
            # Two trained immutable artifacts allow a real model rollback.
            baseline_path=ROOT/'artifacts/model_manifest-tfidf-v1.json'
            baseline=json.loads(baseline_path.read_text(encoding='utf-8')) if baseline_path.exists() else model
            versions={m.get('version','routing-v1'):m for m in (baseline,model)}
            for version, model_info in versions.items():
                existing=conn.execute(text('SELECT artifact_sha256 FROM resolvelab.model_versions WHERE tenant_id=:t AND version_label=:v'),{'t':tid,'v':version}).scalar_one_or_none()
                if existing and existing != model_info['artifact_sha256']:
                    raise RuntimeError('immutable_version_conflict: changed artifacts require a new model version label.')
                mid=fixture_id(org,version)
                rid=fixture_id(org,org+'-'+version+'-release')
                manifest['models'][org+'/'+version]=mid
                manifest['releases'][org+'/'+version]=rid
                metrics=model_info.get('metrics',{})
                conn.execute(text('INSERT INTO resolvelab.model_versions(tenant_id,model_id,version_label,artifact_uri,artifact_sha256,dataset_sha256,split_manifest_sha256,algorithm,validation_threshold,metrics) VALUES(:t,:m,:v,:uri,:h,:dh,:sh,:algo,:threshold,CAST(:metrics AS jsonb)) ON CONFLICT DO NOTHING'),{'t':tid,'m':mid,'v':version,'uri':model_info.get('artifact_uri','artifacts/router.joblib'),'h':model_info.get('artifact_sha256',zero),'dh':model_info.get('dataset_sha256',zero),'sh':model_info.get('split_manifest_sha256',zero),'algo':model_info.get('algorithm','tfidf_logistic'),'threshold':model_info.get('threshold',0.5),'metrics':json.dumps(metrics)})
                conn.execute(text("INSERT INTO resolvelab.releases(tenant_id,release_id,model_id,corpus_id,prompt_version,tool_policy_version) VALUES(:t,:r,:m,:c,'bounded-answer-v1','fixture-policy-v1') ON CONFLICT DO NOTHING"),{'t':tid,'r':rid,'m':mid,'c':cid})
            rid=manifest['releases'][org+'/'+model.get('version','routing-v1')]
            manifest['releases'][org+'/routing-v1']=rid
            manifest['releases'][org+'-active-release']=rid
            conn.execute(text('INSERT INTO resolvelab.active_releases(tenant_id,release_id) VALUES(:t,:r) ON CONFLICT DO NOTHING'), {'t':tid,'r':rid})
        for order in data['orders']:
            p={k:v for k,v in order.items() if k!='organization'}
            p['tenant_id']=manifest['organizations'][order['organization']]
            conn.execute(text('INSERT INTO resolvelab.fixture_orders(tenant_id,order_ref,customer_subject,status,tracking_summary,cancel_deadline,version) VALUES(:tenant_id,:order_ref,:customer_subject,:status,:tracking_summary,:cancel_deadline,:version) ON CONFLICT DO NOTHING'),p)
        for case in data.get('seed_cases',[]):
            org=case['organization']; cid=fixture_id(org,case['alias']); manifest['cases'][case['alias']]=cid
            conn.execute(text("INSERT INTO resolvelab.cases(tenant_id,case_id,release_id,created_by,customer_subject,order_ref,message,state) VALUES(:t,:c,:r,:a,:s,:o,:m,'new') ON CONFLICT DO NOTHING"), {'t':manifest['organizations'][org],'c':cid,'r':manifest['releases'][org+'-active-release'],'a':manifest['actors'][case['created_by']]['actor_id'],'s':case['customer_subject'],'o':case['order_ref'],'m':case['message']})
    (ROOT/'artifacts').mkdir(exist_ok=True)
    (ROOT/'artifacts/seed_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--reset',action='store_true')
    result=seed(parser.parse_args().reset)
    print(f"Seeded {len(result['organizations'])} organizations and {len(result['actors'])} fixture identities.")
