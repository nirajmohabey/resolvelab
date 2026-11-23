"""Current reader permissions apply to immutable decisions and old receipts."""
import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from resolvelab import service
from resolvelab.auth import fixture_principal, issue_token
from resolvelab.db import admin_engine, transaction


def auth(principal, key=None):
    return {"Authorization": "Bearer " + issue_token(principal),
            **({"Idempotency-Key": key} if key else {})}


def private_policy_case(client, actor):
    administrator = fixture_principal("alpha-admin")
    corpus, release = str(uuid4()), str(uuid4())
    with admin_engine.begin() as conn:
        conn.execute(text("""INSERT INTO resolvelab.corpus_versions
            (tenant_id,corpus_id,version_label,content_sha256,embedding_model_revision,embedding_dimension,state)
            SELECT tenant_id,:corpus,:label,content_sha256,embedding_model_revision,embedding_dimension,'draft'
            FROM resolvelab.corpus_versions WHERE tenant_id=:tenant AND version_label='policy-v1'"""),
            {"corpus": corpus, "label": "private-review-" + corpus, "tenant": actor.tenant_id})
        conn.execute(text("""INSERT INTO resolvelab.documents
            (tenant_id,corpus_id,document_id,source_label,title,content_sha256,effective_at,access_scope)
            SELECT tenant_id,:corpus,document_id,source_label,title,content_sha256,effective_at,'admin'
            FROM resolvelab.documents WHERE tenant_id=:tenant AND corpus_id<>:corpus"""),
            {"corpus": corpus, "tenant": actor.tenant_id})
        conn.execute(text("""INSERT INTO resolvelab.chunks
            (tenant_id,corpus_id,document_id,chunk_id,locator,body,embedding,embedding_dimension)
            SELECT tenant_id,:corpus,document_id,gen_random_uuid(),locator,body,embedding,embedding_dimension
            FROM resolvelab.chunks WHERE tenant_id=:tenant AND corpus_id<>:corpus"""),
            {"corpus": corpus, "tenant": actor.tenant_id})
        conn.execute(text("UPDATE resolvelab.corpus_versions SET state='published' WHERE tenant_id=:tenant AND corpus_id=:corpus"),
                     {"corpus": corpus, "tenant": actor.tenant_id})
        conn.execute(text("""INSERT INTO resolvelab.releases
            (tenant_id,release_id,model_id,corpus_id,prompt_version,tool_policy_version,retrieval_mode,retrieval_version)
            SELECT r.tenant_id,:release,r.model_id,:corpus,r.prompt_version,r.tool_policy_version,'lexical','lexical-v1'
            FROM resolvelab.releases r JOIN resolvelab.active_releases a USING(tenant_id,release_id)
            WHERE r.tenant_id=:tenant"""), {"corpus": corpus, "release": release, "tenant": actor.tenant_id})
        conn.execute(text("UPDATE resolvelab.active_releases SET release_id=:release WHERE tenant_id=:tenant"),
                     {"release": release, "tenant": actor.tenant_id})
    headers = auth(administrator, "private-create")
    created = client.post("/v1/cases", json={"message": "Track A-100.", "order_ref": "A-100"}, headers=headers).json()
    path = "/v1/cases/" + created["case_id"]
    route = {"intent": "track_order", "score": .99, "threshold": .8, "version": "visibility-fixture"}
    body = {"expected_case_version": created["version"]}
    with patch.object(service, "_routing", return_value=route):
        result = client.post(path + "/resolve", json=body, headers=auth(administrator, "private-resolve"))
    assert result.status_code == 200
    assert result.json()["evidence_snapshot"]
    return administrator, path, body, result.json()


@pytest.mark.parametrize("alias", ["alpha-operator", "alpha-approver-one", "alpha-evaluator"])
def test_retained_private_evidence_is_filtered_on_all_read_surfaces(client, actor, alias):
    administrator, path, _, original = private_policy_case(client, actor)
    protected = [value for item in original["evidence_snapshot"] for value in (item["body"], item["chunk_id"])]
    reader = fixture_principal(alias)
    for endpoint in (path, "/v1/cases", path + "/evidence", path + "/trace"):
        response = client.get(endpoint, headers=auth(reader))
        assert response.status_code == 200
        assert not any(value in json.dumps(response.json()) for value in protected), endpoint
    administrator_view = client.get(path, headers=auth(administrator)).json()
    assert administrator_view["resolution"]["evidence_snapshot"] == original["evidence_snapshot"]
    assert client.get(path + "/evidence", headers=auth(administrator)).json()["items"] == original["evidence_snapshot"]


def test_role_downgrade_projects_old_receipt_without_rewriting_it(client, actor):
    administrator, path, body, original = private_policy_case(client, actor)
    original_headers = auth(administrator, "private-resolve")
    with admin_engine.begin() as conn:
        before = conn.execute(text("SELECT response_body FROM resolvelab.idempotency_keys WHERE actor_id=:actor AND operation='resolve'"),
                              {"actor": administrator.actor_id}).scalar_one()
        conn.execute(text("UPDATE resolvelab.memberships SET role='operator' WHERE tenant_id=:tenant AND actor_id=:actor"),
                     {"tenant": administrator.tenant_id, "actor": administrator.actor_id})
    replay = client.post(path + "/resolve", json=body, headers=original_headers)
    assert replay.status_code == 200 and replay.json()["evidence_restricted"] is True
    assert replay.json()["evidence_snapshot"] == [] and replay.json()["citations"] == []
    assert all(item["body"] not in json.dumps(replay.json()) for item in original["evidence_snapshot"])
    with admin_engine.connect() as conn:
        after = conn.execute(text("SELECT response_body FROM resolvelab.idempotency_keys WHERE actor_id=:actor AND operation='resolve'"),
                             {"actor": administrator.actor_id}).scalar_one()
        retained = conn.execute(text("SELECT resolution FROM resolvelab.cases WHERE case_id=:case"),
                                {"case": path.rsplit("/", 1)[-1]}).scalar_one()
    assert after == before == original and retained == original


def test_unknown_snapshot_references_fail_closed_even_without_any_valid_ids(actor):
    value = {"answer": "Restricted canary", "evidence_snapshot": [{"chunk_id": "unknown", "body": "Restricted canary"}], "citations": []}
    with transaction(actor) as conn:
        projected = service._scoped_response(conn, actor, value)
    assert projected["evidence_snapshot"] == [] and "Restricted canary" not in json.dumps(projected)
    assert value["evidence_snapshot"][0]["body"] == "Restricted canary"


@pytest.mark.parametrize("outage", [False, True])
def test_stale_triage_does_not_invoke_inference_or_change_current_state(client, actor, outage):
    created = service.create_case(actor, {"message": "Track my order."}, "triage-create")
    path = "/v1/cases/" + created["case_id"]
    route = {"intent": "track_order", "score": .99, "threshold": .8, "version": "triage-fixture"}
    with patch.object(service, "_routing", return_value=route):
        service.triage_case(actor, created["case_id"], {"expected_case_version": 1}, "first-triage")
    before = service.get_case(actor, created["case_id"])
    trace_before = service.get_trace(actor, created["case_id"])
    with patch.object(service, "_routing", **({"side_effect": RuntimeError("fixture unavailable")} if outage else {"return_value": route})) as inference:
        response = client.post(path + "/triage", json={"expected_case_version": 1}, headers=auth(actor, "stale-triage"))
        assert response.status_code == 409
        inference.assert_not_called()
    assert service.get_case(actor, created["case_id"]) == before
    assert service.get_trace(actor, created["case_id"]) == trace_before
