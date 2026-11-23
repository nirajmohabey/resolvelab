"""Real PostgreSQL isolation, retries and action transaction regressions."""
from copy import deepcopy

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from resolvelab.evaluate import (
    approval_body, approve_concurrently, auth_headers, create_and_resolve,
    expect_domain_error, fixture_mutate, mutate, principal, rows,
)

CLOCK = {"clock": "2026-11-23T12:00:00Z"}


def proposal():
    return create_and_resolve("Cancel A-100.", "A-100")[1]["proposal"]


def approve(item, key="approval", faults=None):
    from resolvelab.service import approve_proposal
    return approve_proposal(principal("alpha-approver-one"), str(item["proposal_id"]), approval_body(item), key, faults={**CLOCK, **(faults or {})})


def test_runtime_role_and_missing_context_fail_closed(actor):
    from resolvelab.db import engine, transaction
    with engine.connect() as conn:
        role = conn.execute(text("SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).mappings().one()
        assert not role["rolsuper"] and not role["rolbypassrls"]
        assert conn.execute(text("SELECT count(*) FROM resolvelab.fixture_orders")).scalar_one() == 0
    with transaction(actor) as conn:
        assert conn.execute(text("SELECT count(*) FROM resolvelab.fixture_orders")).scalar_one() == 4
    with transaction(principal("beta-operator")) as conn:
        assert conn.execute(text("SELECT count(*) FROM resolvelab.fixture_orders")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM resolvelab.fixture_orders WHERE order_ref='A-100'")).scalar_one() == 0
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM resolvelab.fixture_orders")).scalar_one() == 0


def test_composite_foreign_key_denies_cross_tenant_release(actor):
    beta_release = rows(principal("beta-operator"), "SELECT release_id FROM resolvelab.active_releases")[0]["release_id"]
    with pytest.raises(IntegrityError):
        mutate(actor, "INSERT INTO resolvelab.cases(tenant_id,release_id,created_by,customer_subject,message) VALUES(:tenant,:release,:actor,'customer-001','cross tenant')", {"tenant": actor.tenant_id, "release": beta_release, "actor": actor.actor_id})


def test_authentication_roles_request_id_and_input_contract(client):
    response = client.get("/v1/cases")
    assert response.status_code == 401
    assert response.headers.get("X-Request-ID")
    assert response.json().get("request_id")
    assert client.get("/v1/cases", headers={"Authorization": "Bearer malformed"}).status_code == 401
    invalid = client.post("/v1/cases", headers=auth_headers(key="input"), json={"message": "hello", "customer_subject": "customer-002"})
    assert invalid.status_code == 422
    assert invalid.headers.get("X-Request-ID")
    denied = client.post("/v1/cases", headers=auth_headers("alpha-evaluator", "no-role"), json={"message": "hello"})
    assert denied.status_code == 403
    oversized = client.post("/v1/cases", headers=auth_headers(key="oversized"), json={"message": "a" * 6001})
    assert oversized.status_code in (413, 422)


def test_operator_cannot_approve_and_no_unapproved_effect(actor):
    from resolvelab import service
    item = proposal()
    expect_domain_error(lambda: service.approve_proposal(actor, str(item["proposal_id"]), approval_body(item), "operator", faults=CLOCK), 403)
    assert rows(actor, "SELECT status FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]["status"] == "pending"
    assert not rows(actor, "SELECT * FROM resolvelab.actions")


def test_idempotency_binds_body_and_resource(actor):
    from resolvelab import service
    body = {"message": "Track A-100.", "order_ref": "A-100"}
    first = service.create_case(actor, body, "create-key")
    assert service.create_case(actor, body, "create-key")["case_id"] == first["case_id"]
    expect_domain_error(lambda: service.create_case(actor, {"message": "Track A-101.", "order_ref": "A-101"}, "create-key"), 409)
    second = service.create_case(actor, body, "create-second")
    faults = {**CLOCK, "routing_adapter": {"predicted_intent": "track_order", "routing_score": .99, "validation_threshold": .8}}
    service.resolve_case(actor, str(first["case_id"]), {"expected_case_version": first["version"]}, "resource-key", faults=faults)
    expect_domain_error(lambda: service.resolve_case(actor, str(second["case_id"]), {"expected_case_version": second["version"]}, "resource-key", faults=faults), 409)
    assert service.get_case(actor, str(second["case_id"]))["state"] == "new"


def test_approval_replay_changed_body_conflicts(actor):
    from resolvelab import service
    item = proposal()
    result = approve(item, "same-key")
    repeat = approve(item, "same-key")
    assert result["action_id"] == repeat["action_id"]
    changed = approval_body(item)
    changed["expected_order_version"] += 1
    expect_domain_error(lambda: service.approve_proposal(principal("alpha-approver-one"), str(item["proposal_id"]), changed, "same-key", faults=CLOCK), 409)
    assert len(rows(actor, "SELECT * FROM resolvelab.actions")) == 1


def test_approval_rollback_and_safe_retry(actor):
    item = proposal()
    with pytest.raises(RuntimeError):
        approve(item, "crash", {"crash_before_commit": True})
    assert not rows(actor, "SELECT * FROM resolvelab.actions")
    assert not rows(actor, "SELECT * FROM resolvelab.approvals")
    order = rows(actor, "SELECT status,version FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]
    assert order == {"status": "pending", "version": 1}
    assert approve(item, "crash")["status"] == "succeeded"
    assert len(rows(actor, "SELECT * FROM resolvelab.actions")) == 1


def test_crash_after_commit_recovers_persisted_outcome(actor):
    item = proposal()
    with pytest.raises(RuntimeError):
        approve(item, "post-commit", {"crash_after_commit": True})
    action = rows(actor, "SELECT action_id::text FROM resolvelab.actions")[0]
    replay = approve(item, "post-commit")
    assert str(replay["action_id"]) == action["action_id"]
    assert len(rows(actor, "SELECT * FROM resolvelab.actions")) == 1
    assert rows(actor, "SELECT version FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]["version"] == 2


@pytest.mark.parametrize("mutation", ["case", "order", "corpus", "digest", "expired", "evidence"])
def test_stale_proposal_variants_do_not_mutate(actor, mutation):
    item = proposal()
    faults = None
    if mutation == "case":
        mutate(actor, "UPDATE resolvelab.cases SET version=version+1 WHERE case_id=(SELECT case_id FROM resolvelab.proposals WHERE proposal_id=:id)", {"id": item["proposal_id"]})
    elif mutation == "order":
        mutate(actor, "UPDATE resolvelab.fixture_orders SET version=version+1,status='shipped' WHERE order_ref='A-100'")
    elif mutation == "corpus":
        fixture_mutate(actor, "UPDATE resolvelab.corpus_versions SET state='retired' WHERE tenant_id=:tenant")
    elif mutation == "digest":
        item = deepcopy(item)
        item["proposal_sha256"] = "0" * 64
    elif mutation == "expired":
        faults = {"clock": "2026-11-23T13:00:00Z"}
    elif mutation == "evidence":
        fixture_mutate(actor, "UPDATE resolvelab.chunks SET body='Changed policy with no matching publication' WHERE tenant_id=:tenant AND locator='approval'")
    expect_domain_error(lambda: approve(item, "stale-" + mutation, faults), 409)
    assert not rows(actor, "SELECT * FROM resolvelab.actions")
    assert rows(actor, "SELECT status FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]["status"] != "cancelled"


def test_concurrent_approval_repeated_25_times(actor):
    from resolvelab.seed import seed
    for repetition in range(25):
        if repetition:
            seed(reset=True)
        outcomes = approve_concurrently(proposal())
        assert any(x["status"] == 200 for x in outcomes), outcomes
        assert all(x["status"] in (200, 409) for x in outcomes), outcomes
        assert len(rows(actor, "SELECT * FROM resolvelab.actions WHERE status='succeeded'")) == 1
        assert len(rows(actor, "SELECT * FROM resolvelab.approvals")) == 1
        order = rows(actor, "SELECT status,version FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]
        assert order == {"status": "cancelled", "version": 2}


@pytest.mark.parametrize("fault", ["invalid_json", "unknown_tool", "unsupported_citation"])
def test_invalid_generation_cannot_create_action(actor, fault):
    from resolvelab import service
    case = service.create_case(actor, {"message": "Cancel A-100.", "order_ref": "A-100"}, "invalid-create")
    faults = {**CLOCK, "routing_adapter": {"predicted_intent": "cancel_request", "routing_score": .99, "validation_threshold": .8}, "generation": fault}
    try:
        result = service.resolve_case(actor, str(case["case_id"]), {"expected_case_version": case["version"]}, "invalid-resolve", faults=faults)
        assert result["disposition"] in ("escalated", "failed", "needs_info")
        assert not result.get("proposal")
    except service.DomainError as exc:
        assert exc.status in (422, 503)
    assert not rows(actor, "SELECT * FROM resolvelab.actions")
    assert not rows(actor, "SELECT * FROM resolvelab.proposals")


def test_provider_timeout_is_recoverable(actor):
    from resolvelab import service
    case = service.create_case(actor, {"message": "Track A-100.", "order_ref": "A-100"}, "provider-create")
    faults = {**CLOCK, "routing_adapter": {"predicted_intent": "track_order", "routing_score": .99, "validation_threshold": .8}, "generation": "provider_timeout"}
    expect_domain_error(lambda: service.resolve_case(actor, str(case["case_id"]), {"expected_case_version": case["version"]}, "provider-resolve", faults=faults), 503)
    failed = service.get_case(actor, str(case["case_id"]))
    assert failed["state"] == "failed"
    faults.pop("generation")
    result = service.resolve_case(actor, str(case["case_id"]), {"expected_case_version": failed["version"]}, "provider-retry", faults=faults)
    assert result["disposition"] == "resolved_read_only"
    assert not rows(actor, "SELECT * FROM resolvelab.actions")


@pytest.mark.parametrize("dependency,stage", [("_routing", "route"), ("_retrieve", "retrieve")])
def test_inference_dependency_outage_preserves_failed_case_and_trace(actor, monkeypatch, dependency, stage):
    from resolvelab import service
    case = service.create_case(actor, {"message": "Track A-100.", "order_ref": "A-100"}, "dependency-create")
    def unavailable(*args, **kwargs):
        raise RuntimeError("deterministic dependency outage")
    monkeypatch.setattr(service, dependency, unavailable)
    expect_domain_error(lambda: service.resolve_case(actor, str(case["case_id"]), {"expected_case_version": case["version"]}, "dependency-failure", faults={**CLOCK, "routing_adapter": {"predicted_intent": "track_order", "routing_score": .99, "validation_threshold": .8}}), 503)
    assert service.get_case(actor, str(case["case_id"]))["state"] == "failed"
    trace = service.get_trace(actor, str(case["case_id"]))
    assert any(event["stage"] == stage and event["outcome"] == "error" for event in trace["events"])
    assert not rows(actor, "SELECT * FROM resolvelab.actions")


def test_fixture_auth_disabled_outside_local_environment(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    response = client.post("/v1/demo/session", json={"actor": "alpha-operator"})
    assert response.status_code == 404


def test_database_membership_overrides_claimed_role(actor):
    from dataclasses import replace
    from resolvelab import service
    item = proposal()
    forged_role = replace(actor, role="admin")
    expect_domain_error(lambda: service.approve_proposal(forged_role, str(item["proposal_id"]), approval_body(item), "forged-role", faults=CLOCK), 403)
    assert not rows(actor, "SELECT * FROM resolvelab.actions")
