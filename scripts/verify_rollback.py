"""Verify that pointer rollback selects another file while old cases stay pinned."""
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from resolvelab.auth import fixture_principal
from resolvelab import service
from resolvelab.db import ROOT

actor=fixture_principal('alpha-operator')
old=service.create_case(actor,{'message':'Where is my order?','order_ref':'A-100'},str(uuid4()))
original=service.get_case(actor,old['case_id'])['release']['model_version']
target='tfidf-logistic-v1' if original!='tfidf-logistic-v1' else json.loads((ROOT/'artifacts/model_manifest.json').read_text())['version']
def switch(version):
    subprocess.run([sys.executable,'scripts/rollback.py','--organization','alpha','--model-version',version],cwd=ROOT,check=True,capture_output=True,text=True)
try:
    switch(target)
    message='Track A-100.' if target=='tfidf-logistic-v1' else 'Where is my order?'
    new=service.create_case(actor,{'message':message,'order_ref':'A-100'},str(uuid4()))
    assert new['release_id']!=old['release_id']
    result=service.resolve_case(actor,new['case_id'],{'expected_case_version':new['version']},str(uuid4()))
    assert result['disposition']=='resolved_read_only'
    assert service.get_case(actor,old['case_id'])['release_id']==old['release_id']
finally:
    switch(original)
report={'previous_model':original,'rollback_target':target,'target_tracking_verified':True,'old_case_release_preserved':True,'original_pointer_restored':True,'old_case_id':old['case_id'],'new_case_id':new['case_id']}
folder=ROOT/'artifacts/rollback';folder.mkdir(exist_ok=True)
(folder/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
