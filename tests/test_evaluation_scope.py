"""The frozen Alpha benchmark must never pretend to evaluate another tenant."""
from sqlalchemy import text

from resolvelab.auth import fixture_principal
from resolvelab.db import admin_engine, transaction
from resolvelab.evaluate import auth_headers


def request_body(actor):
    with transaction(actor) as conn:
        release = conn.execute(text("SELECT release_id FROM resolvelab.active_releases WHERE tenant_id=:tenant"),
                               {"tenant": actor.tenant_id}).scalar_one()
    return {"release_id": str(release), "dataset_key": "fixture-v1", "mode": "deterministic_invariants"}


def test_beta_operator_cannot_queue_evaluation(client):
    actor = fixture_principal("beta-operator")
    response = client.post("/v1/evaluations", json=request_body(actor), headers=auth_headers("beta-operator", "beta-operator-eval"))
    assert response.status_code == 403
    with transaction(actor) as conn:
        assert conn.execute(text("SELECT count(*) FROM resolvelab.evaluation_runs")).scalar_one() == 0


def test_beta_evaluator_receives_explicit_unsupported_dataset(client):
    actor = fixture_principal("beta-operator")
    with admin_engine.begin() as conn:
        conn.execute(text("UPDATE resolvelab.memberships SET role='evaluator' WHERE tenant_id=:tenant AND actor_id=:actor"),
                     {"tenant": actor.tenant_id, "actor": actor.actor_id})
    try:
        response = client.post("/v1/evaluations", json=request_body(actor), headers=auth_headers("beta-operator", "beta-evaluator-eval"))
        assert response.status_code == 422
        assert response.json()["code"] == "fixture_dataset_organization_unsupported"
        with transaction(actor) as conn:
            assert conn.execute(text("SELECT count(*) FROM resolvelab.evaluation_runs")).scalar_one() == 0
    finally:
        with admin_engine.begin() as conn:
            conn.execute(text("UPDATE resolvelab.memberships SET role='operator' WHERE tenant_id=:tenant AND actor_id=:actor"),
                         {"tenant": actor.tenant_id, "actor": actor.actor_id})


def test_alpha_evaluator_can_queue_isolated_fixture_benchmark(client):
    actor = fixture_principal("alpha-evaluator")
    response = client.post("/v1/evaluations", json=request_body(actor), headers=auth_headers("alpha-evaluator", "alpha-evaluator-eval"))
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["state"] == "queued"
    assert result["mode"] == "deterministic_invariants"
    with transaction(actor) as conn:
        assert conn.execute(text("SELECT count(*) FROM resolvelab.evaluation_runs WHERE evaluation_id=:id"),
                            {"id": result["evaluation_id"]}).scalar_one() == 1
