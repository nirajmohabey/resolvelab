"""Existing-case recovery and explicit human decline preserve the action boundary."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from resolvelab import service
from resolvelab.auth import fixture_principal
from resolvelab.evaluate import auth_headers, rows


def needs_info(actor):
    case=service.create_case(actor,{'message':'Where is my order?'},str(uuid4()))
    result=service.resolve_case(actor,case['case_id'],{'expected_case_version':case['version']},str(uuid4()))
    assert result['disposition']=='needs_info'
    return service.get_case(actor,case['case_id'])


def pending(actor):
    case=service.create_case(actor,{'message':'Please cancel my order.','order_ref':'A-100'},str(uuid4()))
    result=service.resolve_case(actor,case['case_id'],{'expected_case_version':case['version']},str(uuid4()))
    assert result['disposition']=='awaiting_approval'
    return service.get_case(actor,case['case_id']),result['proposal']


def reject_body(case,proposal):
    return {'expected_case_version':case['version'],'proposal_sha256':proposal['proposal_sha256'],'reason_code':'needs_manual_review'}


def test_existing_case_can_receive_reference_then_resolve_without_changing_identity(client,actor):
    case=needs_info(actor)
    response=client.post('/v1/cases/'+case['case_id']+'/context',json={'expected_case_version':case['version'],'order_ref':'A-101'},headers=auth_headers(key='attach'))
    assert response.status_code==200
    updated=response.json()
    assert updated['case_id']==case['case_id'] and updated['release_id']==case['release_id']
    assert updated['customer_subject']==case['customer_subject'] and updated['version']==case['version']+1
    assert updated['resolution'] is None and updated['state']=='needs_info'
    result=service.resolve_case(actor,case['case_id'],{'expected_case_version':updated['version']},'followup-resolve')
    assert result['disposition']=='resolved_read_only'
    assert len(rows(actor,"SELECT * FROM resolvelab.idempotency_keys WHERE operation='resolve'"))==2
    assert len(rows(actor,"SELECT * FROM resolvelab.cases"))==1


def test_followup_reference_never_changes_verified_customer_ownership(actor):
    case=needs_info(actor)
    updated=service.update_case_context(actor,case['case_id'],{'expected_case_version':case['version'],'order_ref':'A-103'},'wrong-owner-followup')
    result=service.resolve_case(actor,case['case_id'],{'expected_case_version':updated['version']},'wrong-owner-resolve')
    assert result['disposition']=='escalated' and 'customer-002' not in result['answer']
    assert not rows(actor,'SELECT * FROM resolvelab.actions')


def test_context_idempotency_role_and_current_version_are_enforced(client,actor):
    case=needs_info(actor)
    body={'expected_case_version':case['version'],'order_ref':'A-101'}
    path='/v1/cases/'+case['case_id']+'/context'
    assert client.post(path,json=body,headers=auth_headers('alpha-evaluator','context-denied')).status_code==403
    assert client.post(path,json=body,headers=auth_headers('beta-operator','context-other')).status_code==404
    first=service.update_case_context(actor,case['case_id'],body,'context-replay')
    assert service.update_case_context(actor,case['case_id'],body,'context-replay')==first
    with pytest.raises(service.DomainError) as changed:
        service.update_case_context(actor,case['case_id'],{**body,'order_ref':'A-100'},'context-replay')
    assert changed.value.status==409
    assert client.post(path,json=body,headers=auth_headers(key='stale-context')).status_code==409
    assert client.post(path,json={**body,'customer_subject':'customer-002'},headers=auth_headers(key='untrusted-context')).status_code==422


def test_decline_records_exact_proposal_handoff_and_no_order_effect(client,actor):
    case,proposal=pending(actor);body=reject_body(case,proposal)
    path='/v1/proposals/'+proposal['proposal_id']+'/reject'
    headers=auth_headers('alpha-approver-one','decline')
    rejected=client.post(path,json=body,headers=headers)
    assert rejected.status_code==200 and rejected.json()['order_changed'] is False
    assert client.post(path,json=body,headers=headers).json()==rejected.json()
    result=service.get_case(actor,case['case_id'])
    assert result['state']=='escalated' and result['proposals'][0]['state']=='rejected'
    assert result['version']==case['version']+1
    assert not rows(actor,'SELECT * FROM resolvelab.actions') and not rows(actor,'SELECT * FROM resolvelab.approvals')
    assert rows(actor,"SELECT status,version FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]=={'status':'pending','version':1}
    trace=rows(actor,"SELECT details FROM resolvelab.trace_events WHERE stage='reject'")
    assert len(trace)==1 and trace[0]['details']['reason_code']=='needs_manual_review'
    assert len(rows(actor,"SELECT * FROM resolvelab.handoffs WHERE reason_code='operator_rejected'"))==1
    approval={k:proposal[k] for k in ['proposal_sha256','expected_case_version','expected_order_version']}
    assert client.post('/v1/proposals/'+proposal['proposal_id']+'/approve',json=approval,headers=auth_headers('alpha-approver-one','approve-after-decline')).status_code==409


def test_decline_requires_role_tenant_digest_version_and_bounded_reason(client,actor):
    case,proposal=pending(actor);body=reject_body(case,proposal)
    path='/v1/proposals/'+proposal['proposal_id']+'/reject'
    assert client.post(path,json=body,headers=auth_headers(key='operator-reject')).status_code==403
    beta=fixture_principal('beta-operator')
    # The role check happens before any proposal lookup; no other-tenant content is returned.
    with pytest.raises(service.DomainError) as denied:service.reject_proposal(beta,proposal['proposal_id'],body,'other-tenant-reject')
    assert denied.value.status==403
    for field,value in [('proposal_sha256','0'*64),('expected_case_version',case['version']+1)]:
        assert client.post(path,json={**body,field:value},headers=auth_headers('alpha-approver-one','bad-'+field)).status_code==409
    assert client.post(path,json={**body,'reason_code':'unbounded free-form text'},headers=auth_headers('alpha-approver-one','invalid-reason')).status_code==422
    assert not rows(actor,'SELECT * FROM resolvelab.actions')


def test_approve_and_decline_race_has_one_atomic_terminal_decision(actor):
    case,proposal=pending(actor)
    gate=Barrier(2)
    def decide(kind):
        gate.wait(timeout=10)
        try:
            if kind=='approve':
                body={k:proposal[k] for k in ['proposal_sha256','expected_case_version','expected_order_version']}
                service.approve_proposal(fixture_principal('alpha-approver-one'),proposal['proposal_id'],body,'race-approve')
            else:
                service.reject_proposal(fixture_principal('alpha-approver-two'),proposal['proposal_id'],reject_body(case,proposal),'race-reject')
            return kind,200
        except service.DomainError as error:return kind,error.status
    with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(decide,['approve','reject']))
    assert sorted(status for _,status in outcomes)==[200,409],outcomes
    result=service.get_case(actor,case['case_id'])
    actions=rows(actor,'SELECT * FROM resolvelab.actions')
    approvals=rows(actor,'SELECT * FROM resolvelab.approvals')
    order=rows(actor,"SELECT status,version FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]
    if dict(outcomes)['approve']==200:
        assert result['state']=='resolved' and len(actions)==len(approvals)==1 and order=={'status':'cancelled','version':2}
    else:
        assert result['state']=='escalated' and not actions and not approvals and order=={'status':'pending','version':1}


@pytest.mark.parametrize('value',[True,1.5,2147483648])
def test_case_versions_are_bounded_integers(client,actor,value):
    case=needs_info(actor)
    response=client.post('/v1/cases/'+case['case_id']+'/context',json={'expected_case_version':value,'order_ref':'A-101'},headers=auth_headers(key='bad-version'))
    assert response.status_code==422
