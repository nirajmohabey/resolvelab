"""Authorized local-only regressions for reproduced reset/search boundaries."""
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url

from resolvelab import service,worker
from resolvelab.db import ROOT,admin_engine,transaction
from resolvelab.evaluate import auth_headers,principal,rows
from resolvelab.isolation import validate_disposable_urls


def test_postgres_database_override_is_rejected_before_queue_or_worker(actor,monkeypatch):
    for setting,source in [('EVALUATION_DATABASE_URL','DATABASE_URL'),('ADMIN_EVALUATION_DATABASE_URL','ADMIN_DATABASE_URL')]:
        value=make_url(os.environ[source]).set(database='resolvelab_evaluation',query={'dbname':'resolvelab_test'})
        monkeypatch.setenv(setting,value.render_as_string(hide_password=False))
    # The historical vulnerability is real libpq routing behavior, reproduced
    # only against the disposable TEST database, never the serving database.
    with create_engine(os.environ['EVALUATION_DATABASE_URL']).connect() as conn:
        assert conn.execute(text('SELECT current_database()')).scalar_one()=='resolvelab_test'
    with pytest.raises(RuntimeError,match='query overrides'):
        worker._configuration()
    evaluator=principal('alpha-evaluator')
    release=rows(evaluator,'SELECT release_id::text FROM resolvelab.active_releases')[0]['release_id']
    with pytest.raises(service.DomainError) as denied:
        service.create_evaluation(evaluator,{'release_id':release,'dataset_key':'fixture-v1','mode':'deterministic_invariants'},'override')
    assert denied.value.status==503 and denied.value.code=='evaluation_not_isolated'
    assert not rows(evaluator,'SELECT * FROM resolvelab.evaluation_runs')


def test_runtime_and_administration_targets_must_identify_same_real_database():
    with pytest.raises(RuntimeError,match='same verified'):
        with patch('resolvelab.isolation.connected_identity',side_effect=[{'database':'resolvelab_test','server':'127.0.0.1','port':55439},
                                                                        {'database':'resolvelab_test','server':'127.0.0.2','port':55439}]):
            validate_disposable_urls(os.environ['DATABASE_URL'],os.environ['ADMIN_DATABASE_URL'],'resolvelab_test')


def test_direct_pytest_refuses_serving_named_url_before_any_seed():
    env=dict(os.environ,DATABASE_URL='postgresql+psycopg://unused:unused@127.0.0.1:1/resolvelab',
             ADMIN_DATABASE_URL='postgresql+psycopg://unused:unused@127.0.0.1:1/resolvelab')
    result=subprocess.run([sys.executable,'-m','pytest','--collect-only','-q','tests/test_pinned_artifacts.py'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode!=0
    assert 'dedicated resolvelab_test database' in result.stdout+result.stderr


def test_evaluation_reset_refuses_non_disposable_database_before_seed(monkeypatch):
    from resolvelab.evaluate import reset_fixture
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://unused:unused@127.0.0.1:1/resolvelab')
    with patch('resolvelab.seed.seed') as seed:
        with pytest.raises(RuntimeError,match='explicitly named disposable'):
            reset_fixture()
        seed.assert_not_called()


def test_case_search_pagination_reaches_rows_beyond_first_hundred_and_counts(client,actor):
    release=rows(actor,'SELECT release_id FROM resolvelab.active_releases')[0]['release_id']
    with admin_engine.begin() as conn:
        conn.execute(text("""INSERT INTO resolvelab.cases(tenant_id,release_id,created_by,customer_subject,message,order_ref,state,created_at)
            VALUES(:tenant,:release,:actor,'customer-001',:message,:order_ref,:state,'2026-11-23T12:00:00Z'::timestamptz+make_interval(secs=>:seconds))"""),
                     [{'tenant':actor.tenant_id,'release':release,'actor':actor.actor_id,'message':f'Audit case {index:03d}',
                       'order_ref':f'SEARCH-{index:03d}','state':'new' if index%2==0 else 'failed','seconds':index} for index in range(105)])
    headers=auth_headers()
    first=client.get('/v1/cases?limit=100',headers=headers).json()
    assert first['total']==105 and len(first['items'])==100
    rest=client.get('/v1/cases?limit=100&offset=100',headers=headers).json()
    assert rest['total']==105 and len(rest['items'])==5
    assert not {row['case_id'] for row in first['items']} & {row['case_id'] for row in rest['items']}
    search=client.get('/v1/cases',params={'q':'Audit case 000'},headers=headers).json()
    assert search['total']==1 and search['items'][0]['order_ref']=='SEARCH-000'
    assert client.get('/v1/cases',params={'q':'search-000'},headers=headers).json()['total']==1
    assert client.get('/v1/cases',params={'q':search['items'][0]['case_id']},headers=headers).json()['total']==1
    failed=client.get('/v1/cases?state=failed&limit=10',headers=headers).json()
    assert failed['total']==52 and len(failed['items'])==10
    empty=client.get('/v1/cases?offset=1000',headers=headers).json()
    assert empty['total']==105 and empty['items']==[]


def test_case_search_is_literal_tenant_scoped_and_bounded(client,actor):
    alpha=service.create_case(actor,{'message':r'Literal 100%_safe\path'},'literal-search')
    service.create_case(actor,{'message':r'Literal 100XXXsafe\path'},'literal-other')
    beta=principal('beta-operator')
    other=service.create_case(beta,{'message':'BETA-SEARCH-CANARY'},'beta-search')
    headers=auth_headers()
    assert client.get('/v1/cases',params={'q':'100%_safe'},headers=headers).json()['total']==1
    assert client.get('/v1/cases',params={'q':r'\path'},headers=headers).json()['total']==2
    assert client.get('/v1/cases',params={'q':'BETA-SEARCH-CANARY'},headers=headers).json()['total']==0
    assert client.get('/v1/cases',params={'q':other['case_id']},headers=headers).json()['total']==0
    assert client.get('/v1/cases',params={'q':'BETA-SEARCH-CANARY'},headers=auth_headers('beta-operator')).json()['total']==1
    assert client.get('/v1/cases',params={'q':'x'*201},headers=headers).status_code==422
    assert client.get('/v1/cases?offset=1000001',headers=headers).status_code==422
    assert client.get('/v1/cases?state=unknown',headers=headers).status_code==422
    assert client.get('/v1/cases',params={'q':'bad\x00query'},headers=headers).status_code==422
