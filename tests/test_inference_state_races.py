"""Time and ownership can change while untrusted inference runs without locks."""
from datetime import datetime,timezone
import pytest
from sqlalchemy import text

from resolvelab import generation,service
from resolvelab.db import admin_engine
from resolvelab.evaluate import rows


def test_tracking_does_not_disclose_stale_ownership_after_generation(actor,monkeypatch):
    case=service.create_case(actor,{'message':'Where is my order?','order_ref':'A-100'},'ownership-create')
    original=generation.generate
    def changed(context):
        with admin_engine.begin() as conn:
            conn.execute(text("UPDATE resolvelab.fixture_orders SET customer_subject='customer-002' WHERE tenant_id=:tenant AND order_ref='A-100'"),{'tenant':actor.tenant_id})
        return original(context)
    monkeypatch.setattr(generation,'generate',changed)
    with pytest.raises(service.DomainError) as failure:
        service.resolve_case(actor,case['case_id'],{'expected_case_version':case['version']},'ownership-resolve',
                             faults={'routing_adapter':{'predicted_intent':'track_order','routing_score':.99,'validation_threshold':.8}})
    assert failure.value.status==409 and failure.value.code=='stale_order'
    assert service.get_case(actor,case['case_id'])['resolution'] is None
    assert not rows(actor,'SELECT * FROM resolvelab.actions')


def test_cancellation_cutoff_rechecked_after_inference(actor,monkeypatch):
    case=service.create_case(actor,{'message':'Please cancel my order.','order_ref':'A-100'},'cutoff-create')
    times=iter([datetime(2026,11,23,12,0,tzinfo=timezone.utc),datetime(2026,11,23,12,21,tzinfo=timezone.utc)])
    monkeypatch.setattr(service,'clock',lambda faults=None:next(times))
    with pytest.raises(service.DomainError) as failure:
        service.resolve_case(actor,case['case_id'],{'expected_case_version':case['version']},'cutoff-resolve',
                             faults={'routing_adapter':{'predicted_intent':'cancel_request','routing_score':.99,'validation_threshold':.8}})
    assert failure.value.status==409 and failure.value.code=='stale_order'
    assert not rows(actor,'SELECT * FROM resolvelab.proposals')
    assert not rows(actor,'SELECT * FROM resolvelab.actions')


@pytest.mark.parametrize('mode,version',[('dense','dense-minilm-v1'),('hybrid','hybrid-rrf-v1')])
def test_retrieval_mode_follows_pinned_release(actor,monkeypatch,mode,version):
    from resolvelab import retrieval
    from resolvelab.db import transaction
    observed=[]
    def capture(conn,tenant_id,corpus_id,query,role,mode,limit):
        observed.append(mode)
        return []
    monkeypatch.setattr(retrieval,'retrieve',capture)
    with transaction(actor) as conn:
        assert service._retrieve(conn,actor,{'message':'Where is my order?'},{'corpus_id':'00000000-0000-4000-8000-000000000000','retrieval_mode':mode,'retrieval_version':version})==[]
    assert observed==[mode]
