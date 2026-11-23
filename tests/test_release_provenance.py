"""Model/retrieval metadata describe the selected release, never a global pointer."""
import json
from sqlalchemy import text

from resolvelab import service
from resolvelab.db import ROOT,admin_engine
from resolvelab.evaluate import principal,rows,run_fixture_suite


def test_baseline_report_uses_its_actual_registered_artifact(actor):
    chosen=rows(actor,"SELECT r.release_id::text,m.artifact_sha256 FROM resolvelab.releases r JOIN resolvelab.model_versions m USING(tenant_id,model_id) WHERE m.version_label='tfidf-logistic-v1'")[0]
    baseline=json.loads((ROOT/'artifacts/model_manifest-tfidf-v1.json').read_text())
    report=run_fixture_suite(mode='deterministic_invariants',release_id=chosen['release_id'])
    assert report['model_version']=='tfidf-logistic-v1'
    assert report['model_artifact_sha256']==baseline['artifact_sha256']==chosen['artifact_sha256']
    assert report['release_provenance']['manifest_path']=='artifacts/model_manifest-tfidf-v1.json'
    assert report['release_provenance']['artifact_hash_matches_database'] is True
    assert report['all_observed_case_releases_match_selected'] is True
    assert report['metrics']['runner_errors']==0


def test_v4_scope_denial_is_visible_as_rule_not_probability(actor):
    chosen=rows(actor,"SELECT r.release_id::text,r.retrieval_mode,r.retrieval_version FROM resolvelab.releases r JOIN resolvelab.model_versions m USING(tenant_id,model_id) WHERE m.version_label='domain-router-v4'")[0]
    assert chosen['retrieval_mode']=='dense' and chosen['retrieval_version']=='dense-minilm-v1'
    with admin_engine.begin() as conn:
        conn.execute(text('UPDATE resolvelab.active_releases SET release_id=:release WHERE tenant_id=:tenant'),{'release':chosen['release_id'],'tenant':actor.tenant_id})
    case=service.create_case(actor,{'message':'Please refund my order.','order_ref':'A-100'},'scope-create')
    resolution=service.resolve_case(actor,case['case_id'],{'expected_case_version':case['version']},'scope-resolve')
    assert resolution['intent']=='out_of_scope' and resolution['disposition']=='escalated'
    assert resolution['routing_result']['score_source']=='scope_rule'
    assert resolution['routing_result']['scope_guard_reason']
    trace=service.get_trace(actor,case['case_id'])['events']
    assert any(event['stage']=='route' and event['details']['score_source']=='scope_rule' for event in trace)
    assert any(event['stage']=='retrieve' and event['details']['retrieval_version']=='dense-minilm-v1' for event in trace)
    assert not rows(actor,'SELECT * FROM resolvelab.actions')
