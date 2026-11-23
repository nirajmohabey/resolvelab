"""Check the running API and actual background worker without resetting its inbox."""
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx

root=Path(__file__).resolve().parents[1]
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=15) as client:
    response=client.get('/health/ready');response.raise_for_status()
    assert response.headers.get('x-request-id')
    readiness=response.json()
    schema=client.get('/openapi.json');schema.raise_for_status()
    (root/'contracts/openapi.json').write_text(json.dumps(schema.json(),indent=2),encoding='utf-8')
    def identity(actor):
        response=client.post('/v1/demo/session',json={'actor':actor});response.raise_for_status()
        return {'Authorization':'Bearer '+response.json()['access_token']}
    operator=identity('alpha-operator')
    before=sorted(x['case_id'] for x in client.get('/v1/cases',headers=operator).json()['items'])
    evaluator=identity('alpha-evaluator')
    releases=client.get('/v1/releases',headers=evaluator).json()['items']
    jobs=[]
    for release in releases:
        response=client.post('/v1/evaluations',headers={**evaluator,'Idempotency-Key':str(uuid4())},json={'release_id':release['release_id'],'dataset_key':'fixture-v1','mode':'live_model'})
        response.raise_for_status();jobs.append(response.json()['evaluation_id'])
    deadline=time.monotonic()+60
    while True:
        rows=[client.get('/v1/evaluations/'+job,headers=evaluator).json() for job in jobs]
        if all(row['state'] in ('completed','failed') for row in rows):break
        if time.monotonic()>deadline:raise RuntimeError('Worker verification timeout')
        time.sleep(.5)
    assert all(row['state']=='completed' for row in rows),rows
    after=sorted(x['case_id'] for x in client.get('/v1/cases',headers=operator).json()['items'])
    assert before==after,'Serving cases changed during isolated evaluation'
report={'readiness':readiness,'serving_case_count_before':len(before),'serving_case_count_after':len(after),'inbox_preserved':True,'evaluation_jobs':[{'evaluation_id':r['evaluation_id'],'release_id':r['release_id'],'state':r['state'],'metrics':r['metrics']} for r in rows]}
folder=root/'artifacts/live_worker';folder.mkdir(exist_ok=True)
(folder/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
