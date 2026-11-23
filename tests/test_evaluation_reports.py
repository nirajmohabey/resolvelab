"""Case-level reports obey database authorization and fixed bounded file paths."""
import json

from sqlalchemy import text

from resolvelab import api, service
from resolvelab.auth import fixture_principal
from resolvelab.db import admin_engine, transaction
from resolvelab.evaluate import auth_headers


def completed_evaluation(tmp_path, monkeypatch, state="completed"):
    actor = fixture_principal("alpha-evaluator")
    with transaction(actor) as conn:
        release = conn.execute(text("SELECT release_id FROM resolvelab.active_releases WHERE tenant_id=:tenant"),
                               {"tenant": actor.tenant_id}).scalar_one()
    result = service.create_evaluation(actor, {"release_id": str(release), "dataset_key": "fixture-v1", "mode": "deterministic_invariants"}, "report-fixture")
    with admin_engine.begin() as conn:
        conn.execute(text("UPDATE resolvelab.evaluation_runs SET state=:state,case_results_uri=:uri WHERE tenant_id=:tenant AND evaluation_id=:id"),
                     {"tenant": actor.tenant_id, "id": result["evaluation_id"], "state": state, "uri": str(tmp_path / "arbitrary-secret.json")})
    monkeypatch.setattr(api, "ROOT", tmp_path)
    path = tmp_path / "artifacts/evaluation" / result["evaluation_id"] / "workflow-evaluation.json"
    path.parent.mkdir(parents=True)
    report = {"metrics": {"workflow_success": {"numerator": 1, "denominator": 1}}, "cases": [{"id": "AI-E01", "passed": True}]}
    path.write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "arbitrary-secret.json").write_text('{"secret":"must-not-be-returned"}', encoding="utf-8")
    return result["evaluation_id"], path, report


def test_own_completed_report_uses_fixed_path_and_bounded_valid_json(client, tmp_path, monkeypatch):
    evaluation_id, path, report = completed_evaluation(tmp_path, monkeypatch)
    endpoint = f"/v1/evaluations/{evaluation_id}/report"
    headers = auth_headers("alpha-evaluator")
    response = client.get(endpoint, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == report
    assert "must-not-be-returned" not in response.text
    assert response.headers.get("X-Request-ID")
    for malformed in (b"{invalid json", b'{"metrics":{},"cases":[],"value":NaN}', b"x" * (4 * 1024 * 1024 + 1)):
        path.write_bytes(malformed)
        invalid = client.get(endpoint, headers=headers)
        assert invalid.status_code == 503
        assert invalid.json()["code"] == "report_invalid"
        assert invalid.json()["request_id"]


def test_other_tenant_cannot_read_same_existing_evaluation_report(client, tmp_path, monkeypatch):
    evaluation_id, path, report = completed_evaluation(tmp_path, monkeypatch)
    endpoint = f"/v1/evaluations/{evaluation_id}/report"
    assert client.get(endpoint, headers=auth_headers("alpha-evaluator")).status_code == 200
    denied = client.get(endpoint, headers=auth_headers("beta-operator"))
    assert denied.status_code == 404
    assert denied.json()["code"] == "not_found"
    assert "AI-E01" not in denied.text


def test_missing_or_uncompleted_report_is_not_ready(client, tmp_path, monkeypatch):
    evaluation_id, path, report = completed_evaluation(tmp_path, monkeypatch, state="queued")
    endpoint = f"/v1/evaluations/{evaluation_id}/report"
    headers = auth_headers("alpha-evaluator")
    response = client.get(endpoint, headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "report_not_ready"
    with admin_engine.begin() as conn:
        conn.execute(text("UPDATE resolvelab.evaluation_runs SET state='completed' WHERE evaluation_id=:id"), {"id": evaluation_id})
    path.unlink()
    missing = client.get(endpoint, headers=headers)
    assert missing.status_code == 409
    assert missing.json()["code"] == "report_not_ready"
