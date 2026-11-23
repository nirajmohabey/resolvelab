"""Populate inspectable local examples without approving or changing orders."""
import json
from pathlib import Path
from resolvelab.auth import fixture_principal
from resolvelab import service

actor=fixture_principal('alpha-operator')
examples=[('tracking','Where is my order?','A-101',None),('missing-reference','Where is my order?',None,None),('unsupported','I need a refund for my last purchase.',None,None),('handoff','I want to contact a human agent.',None,None),('provider-failure','Where is my order?','A-100',{'generation':'provider_timeout'}),('cancellation','Please cancel my order.','A-100',None)]
ids={}
for alias,message,ref,fault in examples:
    case=service.create_case(actor,{'message':message,'order_ref':ref},'demo-create-'+alias)
    try:
        service.resolve_case(actor,case['case_id'],{'expected_case_version':case['version']},'demo-resolve-'+alias,faults=fault)
    except Exception as exc:
        if alias!='provider-failure':raise
    ids[alias]=case['case_id']
print(json.dumps(ids,indent=2))
