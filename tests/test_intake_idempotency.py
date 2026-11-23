"""Idempotent creation binds the verified intake customer, including legacy receipts."""
from dataclasses import replace
import json
import pytest
from sqlalchemy import text

from resolvelab import service
from resolvelab.db import admin_engine
from resolvelab.evaluate import rows


def test_create_key_is_bound_to_verified_customer_and_legacy_replay_remains_safe(actor):
    body={'message':'Where is my order?','order_ref':'A-100'}
    first=service.create_case(actor,body,'bound-customer')
    assert service.create_case(actor,body,'bound-customer')['case_id']==first['case_id']
    another_intake=replace(actor,customer_subject='customer-002')
    with pytest.raises(service.DomainError) as changed:
        service.create_case(another_intake,body,'bound-customer')
    assert changed.value.status==409 and changed.value.code=='idempotency_conflict'
    # Preserve the real pre-hardening receipt representation, without updating
    # any immutable receipt or weakening its original customer binding.
    with admin_engine.begin() as conn:
        conn.execute(text("""INSERT INTO resolvelab.idempotency_keys
            (tenant_id,actor_id,operation,idempotency_key,request_sha256,response_status,response_body)
            VALUES(:tenant,:actor,'create_case','legacy-create',:hash,201,CAST(:body AS jsonb))"""),
                     {'tenant':actor.tenant_id,'actor':actor.actor_id,'hash':service.fingerprint(body),'body':json.dumps(first)})
    assert service.create_case(actor,body,'legacy-create')['case_id']==first['case_id']
    with pytest.raises(service.DomainError) as legacy_changed:
        service.create_case(another_intake,body,'legacy-create')
    assert legacy_changed.value.status==409
    assert len(rows(actor,'SELECT case_id FROM resolvelab.cases'))==1
