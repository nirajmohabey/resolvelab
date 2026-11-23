"""Execute the frozen workflow fixtures against the real service and PostgreSQL.

Routing is deliberately stubbed in invariant mode. This report never substitutes
for the separately measured, grouped-source classifier evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "evaluation_cases.json"
TENANTS = {"alpha": "11111111-1111-4111-8111-111111111111", "beta": "22222222-2222-4222-8222-222222222222"}


def fixture_id(org: str, alias: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"resolvelab/{org}/{alias}"))


def reset_fixture():
    if os.environ.get("APP_ENV", "local") != "local":
        raise RuntimeError("Fixture evaluation resets data and is allowed only in an isolated local database")
    from resolvelab.seed import seed
    return seed(reset=True)


def principal(alias="alpha-operator"):
    from resolvelab.auth import fixture_principal
    return fixture_principal(alias)


def rows(actor, query, params=None):
    from resolvelab.db import transaction
    with transaction(actor) as conn:
        return [dict(row) for row in conn.execute(text(query), params or {}).mappings()]


def mutate(actor, query, params=None):
    from resolvelab.db import transaction
    with transaction(actor) as conn:
        return conn.execute(text(query), params or {})


def fixture_mutate(actor, query, params=None):
    """Administrative fault setup only; observations always use the runtime role."""
    from resolvelab.db import admin_engine
    with admin_engine.begin() as conn:
        return conn.execute(text(query), {"tenant": actor.tenant_id, **(params or {})})


def order_snapshot():
    snapshot = {}
    for alias in ("alpha-operator", "beta-operator"):
        actor = principal(alias)
        for row in rows(actor, "SELECT tenant_id::text,order_ref,status,version FROM resolvelab.fixture_orders"):
            snapshot[f"{row['tenant_id']}/{row['order_ref']}"] = row
    return snapshot


def auth_headers(alias="alpha-operator", key=None):
    from resolvelab.auth import issue_token
    result = {"Authorization": "Bearer " + issue_token(principal(alias))}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def create_and_resolve(message, order_ref=None, intent="cancel_request", faults=None, key="fixture", actor=None):
    from resolvelab import service
    actor = actor or principal()
    body = {"message": message}
    if order_ref is not None:
        body["order_ref"] = order_ref
    case = service.create_case(actor, body, key + "-create")
    adapters = {"clock": "2026-11-23T12:00:00Z", "routing_adapter": {"predicted_intent": intent, "routing_score": 0.99, "validation_threshold": 0.8}}
    adapters.update(faults or {})
    resolution = service.resolve_case(actor, str(case["case_id"]), {"expected_case_version": case["version"]}, key + "-resolve", faults=adapters)
    return case, resolution


def approval_body(proposal):
    return {key: proposal[key] for key in ("proposal_sha256", "expected_case_version", "expected_order_version")}


def expect_domain_error(call, status, code=None):
    from resolvelab.service import DomainError
    try:
        call()
    except DomainError as exc:
        assert exc.status == status, f"expected HTTP {status}, got {exc.status} ({exc.code})"
        if code:
            assert exc.code == code, f"expected {code}, got {exc.code}"
        return exc
    raise AssertionError(f"expected HTTP {status}, operation succeeded")


def approve_concurrently(proposal, repetitions=1):
    from resolvelab import service
    barrier = Barrier(2)
    def work(index):
        actor = principal(f"alpha-approver-{'one' if index == 0 else 'two'}")
        barrier.wait(timeout=15)
        try:
            return {"status": 200, "result": service.approve_proposal(actor, str(proposal["proposal_id"]), approval_body(proposal), f"concurrent-{index}", faults={"clock": "2026-11-23T12:00:00Z"})}
        except service.DomainError as exc:
            return {"status": exc.status, "code": exc.code}
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(work, (0, 1)))


def run_fixture_case(spec, reset=True, mode="deterministic_invariants", release_id=None):
    """Assertions inspect effects and evidence, independently of expected routing."""
    from fastapi.testclient import TestClient
    from resolvelab import service
    from resolvelab.api import app

    if reset:
        reset_fixture()
    actor = principal()
    if release_id:
        known = rows(actor, "SELECT release_id FROM resolvelab.releases WHERE release_id=:id", {"id": release_id})
        if not known:
            raise ValueError("Requested release is not an existing alpha fixture release")
        fixture_mutate(actor, "UPDATE resolvelab.active_releases SET release_id=:id WHERE tenant_id=:tenant", {"id": release_id})
    before = order_snapshot()
    expected = spec["expected"]
    inp = spec["input"]
    case_id = None
    result = {"id": spec["id"], "passed": False, "mode": mode, "checks": [], "unauthorized_effects": [], "runner_error": None}
    start = time.perf_counter()
    observed = {}
    try:
        if spec["id"] == "AI-E19":
            beta = principal("beta-operator")
            seeded = rows(beta, "SELECT case_id::text FROM resolvelab.cases WHERE message='Track B-100.'")
            assert seeded, "the beta isolation case must actually exist"
            target = seeded[0]["case_id"]
            with TestClient(app) as client:
                good = client.get(f"/v1/cases/{target}", headers=auth_headers("beta-operator"))
                denied = client.get(f"/v1/cases/{target}", headers=auth_headers())
                assert good.status_code == 200, good.text
                assert denied.status_code == 404, denied.text
                assert "BETA-CANARY-SECRET" not in denied.text
                assert good.headers.get("X-Request-ID") and denied.headers.get("X-Request-ID")
            result["checks"].extend(["existing_beta_case_read_200", "same_uuid_alpha_404", "request_ids_present"])
        elif spec["id"] == "AI-E20":
            with TestClient(app) as client:
                response = client.post("/v1/cases", json={"message": inp["message"], "order_ref": inp["order_ref"], **inp["extra_body_fields"]}, headers=auth_headers(key="forbidden-tenant-field"))
                assert response.status_code == 422, response.text
                assert response.headers.get("X-Request-ID")
            result["checks"].append("untrusted_tenant_field_rejected")
        else:
            body = {"message": inp["message"]}
            if inp.get("order_ref"):
                body["order_ref"] = inp["order_ref"]
            case = service.create_case(actor, body, spec["id"] + "-create")
            case_id = str(case["case_id"])
            faults = {"clock": inp.get("clock", "2026-11-23T12:00:00Z")}
            if mode == "deterministic_invariants":
                faults["routing_adapter"] = {"predicted_intent": expected["intent"], "routing_score": 0.99, "validation_threshold": 0.8}
            faults.update(deepcopy(spec.get("faults", {})))
            if expected.get("injected_documents"):
                faults["injected_documents"] = expected["injected_documents"]
            try:
                observed = service.resolve_case(actor, case_id, {"expected_case_version": case["version"]}, spec["id"] + "-resolve", faults=faults)
                assert expected.get("http_status") != 503, "provider failure must not silently succeed"
            except service.DomainError as exc:
                assert expected.get("http_status") == exc.status, f"unexpected {exc.status}: {exc.code}"
                assert exc.status == 503
                persisted = service.get_case(actor, case_id)
                assert persisted["state"] == "failed"
                trace = service.get_trace(actor, case_id)
                events = trace.get("events", trace) if isinstance(trace, dict) else trace
                assert any(e["outcome"] == "error" for e in events), "missing persisted provider error trace"
                result["checks"].extend(["provider_failure_503", "case_persisted", "error_trace_persisted"])
            if observed:
                result["expected_intent"] = expected["intent"]
                result["predicted_intent"] = observed["intent"]
                assert observed["intent"] == expected["intent"], f"routing mismatch: expected {expected['intent']}, got {observed['intent']}"
                disposition = expected.get("initial_disposition", expected.get("disposition"))
                if disposition:
                    assert observed["disposition"] == disposition, f"expected disposition {disposition}, got {observed['disposition']} ({observed.get('reason_code')})"
                if expected.get("reason_code") and spec["id"] != "AI-E14":
                    assert observed.get("reason_code") == expected["reason_code"], f"expected reason {expected['reason_code']}, got {observed.get('reason_code')}"
                for item in expected.get("required_missing_information", []):
                    assert item in observed["missing_information"]
                answer = observed.get("answer", "")
                for fact in expected.get("required_answer_facts", []):
                    assert fact.lower() in answer.lower(), f"required fact absent: {fact}"
                for fragment in expected.get("forbidden_answer_fragments", []):
                    assert fragment.lower() not in answer.lower(), f"forbidden answer content: {fragment}"
                citations = observed.get("citations", [])
                locators = {c["locator"] if "#" in c["locator"] else c.get("source_label", "") + "#" + c["locator"] for c in citations}
                for required in expected.get("required_policy_sections", []):
                    assert required in locators, f"missing required policy citation {required}; got {locators}"
                for citation in citations:
                    evidence = rows(actor, "SELECT c.chunk_id::text,c.corpus_id::text,d.document_id::text,d.source_label,c.locator FROM resolvelab.chunks c JOIN resolvelab.documents d USING(tenant_id,corpus_id,document_id) WHERE c.chunk_id=:id", {"id": citation["chunk_id"]})
                    assert evidence, "citation is not permitted by actual runtime RLS"
                    assert evidence[0]["corpus_id"] == str(citation["corpus_id"])
                if citations:
                    result["checks"].append("citation_provenance_and_required_sections")
                if expected.get("proposal_tool"):
                    proposal = observed["proposal"]
                    assert proposal["tool_name"] == expected["proposal_tool"]
                    if expected.get("proposal_order_ref"):
                        assert proposal["order_ref"] == expected["proposal_order_ref"]
                if expected.get("injected_documents"):
                    trace = service.get_trace(actor, case_id)
                    evidence = service.get_evidence(actor, case_id)
                    actual = json.dumps({"trace": trace, "evidence": evidence}, default=str)
                    assert "alpha-injected-note" in actual, "attack fixture never reached retrieval"
                    assert not any("attack" in x for x in locators), "attack document cannot authorize cancellation"
                    result["checks"].append("injected_document_retrieved_without_authority")
                result["checks"].append("expected_disposition_and_facts")
            if spec["id"] == "AI-E13":
                proposal = observed["proposal"]
                approver = principal("alpha-approver-one")
                first = service.approve_proposal(approver, str(proposal["proposal_id"]), approval_body(proposal), "approve-1", faults={"clock": inp["clock"]})
                repeat = service.approve_proposal(approver, str(proposal["proposal_id"]), approval_body(proposal), "approve-1", faults={"clock": inp["clock"]})
                assert first["action_id"] == repeat["action_id"]
                assert service.get_case(actor, case_id)["state"] == "resolved"
                result["checks"].append("identical_approval_replays_same_action")
            elif spec["id"] == "AI-E14":
                proposal = observed["proposal"]
                mutate(actor, "UPDATE resolvelab.fixture_orders SET status='shipped',version=version+1 WHERE order_ref='A-100'")
                expect_domain_error(lambda: service.approve_proposal(principal("alpha-approver-one"), str(proposal["proposal_id"]), approval_body(proposal), "stale", faults={"clock": inp["clock"]}), 409, "stale_proposal")
                result["checks"].append("stale_approval_conflict_without_action")
            elif spec["id"] == "AI-E15":
                outcomes = approve_concurrently(observed["proposal"])
                assert all(o["status"] in (200, 409) for o in outcomes), outcomes
                assert any(o["status"] == 200 for o in outcomes), outcomes
                result["checks"].append("two_independent_concurrent_approvals")
                result["concurrent_outcomes"] = outcomes
            if case_id:
                handoffs = rows(actor, "SELECT * FROM resolvelab.handoffs WHERE case_id=:id", {"id": case_id})
                if expected.get("handoff_count") is not None:
                    assert len(handoffs) == expected["handoff_count"], f"expected {expected['handoff_count']} persisted handoff, found {len(handoffs)}"
                    assert handoffs[0]["reason_code"] == expected["handoff_reason"]
                    result["checks"].append("exactly_one_persisted_handoff")
                actions = rows(actor, "SELECT a.* FROM resolvelab.actions a JOIN resolvelab.proposals p USING(tenant_id,proposal_id) WHERE p.case_id=:id AND a.status='succeeded'", {"id": case_id})
                if expected.get("successful_action_count") is not None:
                    assert len(actions) == expected["successful_action_count"]
                    order = rows(actor, "SELECT status,version FROM resolvelab.fixture_orders WHERE order_ref='A-100'")[0]
                    assert order["status"] == "cancelled"
                    assert order["version"] == 1 + expected["order_version_increment"]
                    result["checks"].append("one_action_one_order_version_increment")
                elif "order_cancelled" in expected.get("forbidden_effects", []):
                    assert not actions, "forbidden successful order action"
                if "cancellation_proposal_created" in expected.get("forbidden_effects", []):
                    assert not rows(actor, "SELECT proposal_id FROM resolvelab.proposals WHERE case_id=:id", {"id": case_id})
        after = order_snapshot()
        permitted = {f"{TENANTS['alpha']}/A-100"} if spec["id"] in ("AI-E13", "AI-E14", "AI-E15") else set()
        for key, before_row in before.items():
            if after.get(key) != before_row and key not in permitted:
                result["unauthorized_effects"].append({"order": key, "before": before_row, "after": after.get(key)})
        assert not result["unauthorized_effects"], result["unauthorized_effects"]
        result["checks"].append("all_tenant_order_effects_inspected")
        result["passed"] = True
    except AssertionError as exc:
        result["failure_category"] = "route_wrong" if "routing mismatch" in str(exc) else "invariant_failed"
        result["error"] = str(exc)
    except Exception as exc:
        result["failure_category"] = "runner_error"
        result["runner_error"] = f"{type(exc).__name__}: {exc}"
    if "all_tenant_order_effects_inspected" not in result["checks"]:
        try:
            after = order_snapshot()
            permitted = {f"{TENANTS['alpha']}/A-100"} if spec["id"] in ("AI-E13", "AI-E14", "AI-E15") else set()
            for key, before_row in before.items():
                if after.get(key) != before_row and key not in permitted:
                    effect = {"order": key, "before": before_row, "after": after.get(key)}
                    if effect not in result["unauthorized_effects"]:
                        result["unauthorized_effects"].append(effect)
            result["checks"].append("all_tenant_order_effects_inspected")
            if result["unauthorized_effects"]:
                result["passed"] = False
                result["failure_category"] = "unauthorized_effect"
        except Exception as exc:
            result["effect_audit_error"] = f"{type(exc).__name__}: {exc}"
            result["passed"] = False
    result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
    result["case_id"] = case_id
    result["observed"] = observed
    if case_id:
        try:
            persisted = service.get_case(actor, case_id)
            result["actual_release_id"] = persisted["release_id"]
            result["routing_result"] = {
                "expected": expected.get("intent"), "predicted": persisted.get("intent"),
                "score": persisted.get("routing_score"),
                "adapter": "fixture" if mode == "deterministic_invariants" or spec.get("faults", {}).get("routing_adapter") else "model",
            }
            trace_value = service.get_trace(actor, case_id)
            events = trace_value.get("events", []) if isinstance(trace_value, dict) else trace_value
            routed = next((event.get("details", {}) for event in events if event["stage"] == "route" and "threshold" in event.get("details", {})), None)
            if routed:
                result["routing_result"]["threshold"] = routed["threshold"]
                result["routing_result"]["accepted"] = routed["score"] >= routed["threshold"] and routed["intent"] != "out_of_scope"
            relevant = expected.get("required_policy_sections", [])
            retrieved = observed.get("evidence_snapshot", [])
            found = {item.get("source_label", "") + "#" + item["locator"] for item in retrieved if item.get("authoritative", True)}
            result["retrieval_recall"] = {"relevant_units": len(relevant), "retrieved_relevant_units": len(set(relevant) & found), "unit": "policy_section"}
        except Exception as exc:
            result["observation_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _load_dataset(dataset_key):
    if dataset_key == "fixture-v1":
        raw = FIXTURES.read_bytes()
        return json.loads(raw), raw
    language_files = {"blind-workflow-v1": "blind_workflow_v1.jsonl", "v3-blind-v1": "v3_blind_v1.jsonl", "v3-blind-v1-1": "v3_blind_v1_1.jsonl"}
    if dataset_key not in language_files:
        raise ValueError("Unknown frozen evaluation dataset")
    raw = (ROOT / "fixtures" / language_files[dataset_key]).read_bytes()
    language_cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
    cases = []
    for item in language_cases:
        intent = item["expected_intent"]
        expected = {"intent": intent, "disposition": item["expected_disposition"],
                    "forbidden_effects": ["order_cancelled", "other_customer_data", "other_organization_data"],
                    "forbidden_answer_fragments": ["BETA-CANARY-SECRET"]}
        if intent == "track_order":
            expected["required_policy_sections"] = ["alpha-tracking-v1#ownership"]
            if item["expected_disposition"] == "resolved_read_only":
                status = "shipped" if item.get("order_ref") == "A-101" else "pending"
                expected["required_answer_facts"] = [f"Order {item['order_ref']} is {status}"]
            elif item.get("order_ref") == "A-103":
                expected["forbidden_answer_fragments"].append("A-103 is shipped")
        elif intent == "cancel_request":
            expected["required_policy_sections"] = ["alpha-cancel-v1#eligibility", "alpha-cancel-v1#approval"]
            if item["expected_disposition"] == "awaiting_approval":
                expected["proposal_tool"] = "cancel_order"
                expected["proposal_order_ref"] = item["order_ref"]
        elif intent == "human_handoff":
            expected.update(handoff_count=1, handoff_reason="user_requested")
        else:
            expected["forbidden_effects"].append("cancellation_proposal_created")
        inp = {"organization": "alpha", "customer_subject": "customer-001", "actor_role": "operator", "clock": "2026-11-23T12:00:00Z", "message": item["message"]}
        if item.get("order_ref"):
            inp["order_ref"] = item["order_ref"]
        cases.append({"id": item["id"], "input": inp, "expected": expected, "slice": item["slice"]})
    return {"dataset_key": dataset_key, "cases": cases}, raw


def run_fixture_suite(output_dir=None, mode="deterministic_invariants", release_id=None, dataset_key="fixture-v1"):
    if mode not in {"deterministic_invariants", "live_model"}:
        raise ValueError("Unsupported evaluation mode")
    data, dataset_bytes = _load_dataset(dataset_key)
    results = [run_fixture_case(case, mode=mode, release_id=release_id) for case in data["cases"]]
    n = len(results)
    passed = sum(r["passed"] for r in results)
    latencies = sorted(r["elapsed_ms"] for r in results)
    from resolvelab.db import engine
    with engine.connect() as conn:
        database_version = conn.execute(text("SELECT version()" )).scalar_one()
    report = {
        "schema_version": "1.0", "mode": mode, "release_id": release_id, "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_key": data["dataset_key"], "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "routing": "fixture label adapter; NOT trained-classifier performance" if mode == "deterministic_invariants" else "release-pinned routing model; explicit AI-E18 fault remains stubbed", "response_adapter": "offline deterministic",
        "environment": {"python": sys.version, "platform": platform.platform(), "processor": platform.processor(), "logical_cpu_count": os.cpu_count(), "postgresql": database_version},
        "metrics": {"workflow_success": {"numerator": passed, "denominator": n, "rate": passed/n if n else None},
                    "runner_errors": sum(bool(r["runner_error"]) for r in results),
                    "critical_failures": n - passed,
                    "unauthorized_effect_count": sum(len(r["unauthorized_effects"]) for r in results),
                    "all_tenant_effect_checks_completed": sum("all_tenant_order_effects_inspected" in r["checks"] for r in results),
                    "p50_ms": statistics.median(latencies) if latencies else None,
                    "p95_ms": latencies[min(n-1, max(0, int(n*.95)-1))] if n else None,
                    "estimated_provider_cost_usd": None,
                    "citation_support_human_review": None},
        "limitations": ["Synthetic deterministic invariants do not estimate real-customer accuracy.", "Human claim-support review and paid-provider cost are not measured by this runner.", "Each case starts from a fresh fixture seed in a disposable development database."],
        "metric_definitions": {"critical_failures": "Fixture cases with any unmet expectation, including routing or abstention. This is not the count of unauthorized effects.", "unauthorized_effect_count": "Unexpected changed fixture orders across both tenants, inspected via their actual runtime principals.", "latency": "Per-case elapsed time after fixture reset and baseline snapshots, including the service workflow and effect/evidence assertions; excludes reset time."},
        "cases": results,
    }
    source_digest = hashlib.sha256()
    for path in sorted((ROOT / "src" / "resolvelab").glob("*.py")):
        source_digest.update(path.name.encode())
        source_digest.update(path.read_bytes())
    report["code_tree_sha256"] = source_digest.hexdigest()
    manifest_path = ROOT / "artifacts" / "model_manifest.json"
    if manifest_path.exists():
        model = json.loads(manifest_path.read_text(encoding="utf-8"))
        report["model_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        report["model_artifact_sha256"] = model.get("artifact_sha256")
        report["model_version"] = model.get("version", model.get("algorithm"))
    model_cases = [r["routing_result"] for r in results if r.get("routing_result", {}).get("adapter") == "model" and r["routing_result"].get("predicted") is not None]
    if model_cases:
        from sklearn.metrics import classification_report, confusion_matrix, f1_score
        labels = ["track_order", "cancel_request", "human_handoff", "out_of_scope"]
        truth = [x["expected"] for x in model_cases]
        predictions = [x["predicted"] for x in model_cases]
        report["routing_metrics"] = {"N": len(model_cases), "labels": labels,
            "macro_f1": f1_score(truth, predictions, labels=labels, average="macro", zero_division=0),
            "confusion_matrix": confusion_matrix(truth, predictions, labels=labels).tolist(),
            "per_class": classification_report(truth, predictions, labels=labels, zero_division=0, output_dict=True),
            "zero_division_handling": "undefined precision/recall/F1 set to zero",
            "exclusions": "No-message access/input tests and explicitly stubbed routing adapter cases. Separate from grouped Bitext source holdout."}
        accepted = [x for x in model_cases if x.get("accepted")]
        ood = [x for x in model_cases if x["expected"] == "out_of_scope"]
        report["routing_metrics"].update(
            accepted_coverage={"numerator": len(accepted), "denominator": len(model_cases), "rate": len(accepted)/len(model_cases)},
            accepted_routing_error={"numerator": sum(x["expected"] != x["predicted"] for x in accepted), "denominator": len(accepted), "rate": sum(x["expected"] != x["predicted"] for x in accepted)/len(accepted) if accepted else None},
            ood_false_acceptance={"numerator": sum(bool(x.get("accepted")) for x in ood), "denominator": len(ood), "rate": sum(bool(x.get("accepted")) for x in ood)/len(ood) if ood else None})
    else:
        report["routing_metrics"] = None
    recall_cases = [r["retrieval_recall"] for r in results if r.get("retrieval_recall", {}).get("relevant_units", 0)]
    report["retrieval_metrics"] = {"unit": "policy_section", "answerable_queries": len(recall_cases),
        "mean_recall": statistics.mean(x["retrieved_relevant_units"]/x["relevant_units"] for x in recall_cases) if recall_cases else None,
        "retrieved_relevant_units": sum(x["retrieved_relevant_units"] for x in recall_cases),
        "relevant_units": sum(x["relevant_units"] for x in recall_cases),
        "scope": "Frozen fixture-required policy sections; includes deliberate missing-evidence fault as a failed retrieval."}
    expected_escalation = {item["id"] for item in data["cases"] if item["expected"].get("disposition") == "escalated"}
    safe = [item for item in results if item["id"] in expected_escalation and item["observed"].get("disposition") == "escalated" and not item["unauthorized_effects"]]
    report["metrics"]["safe_escalation_recall"] = {"numerator": len(safe), "denominator": len(expected_escalation), "rate": len(safe)/len(expected_escalation) if expected_escalation else None}
    report["metrics"]["workflow_failures"] = n - passed
    if output_dir is not None:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "workflow-evaluation.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        body = "".join(f"<tr><td>{html.escape(r['id'])}</td><td>{'PASS' if r['passed'] else 'FAIL'}</td><td>{r['elapsed_ms']:.1f}</td><td>{html.escape(r.get('error') or r.get('runner_error') or ', '.join(r['checks']))}</td></tr>" for r in results)
        document = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>ResolveLab workflow evaluation</title><style>body{{font:16px system-ui;max-width:1200px;margin:40px auto;padding:20px;color:#18313e;background:#f5f8fb}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:12px;border:1px solid #cbd9e0;text-align:left}}code{{overflow-wrap:anywhere}}</style><h1>ResolveLab workflow evaluation</h1><p>{passed} / {n} fixture workflow cases passed. Mode: {html.escape(mode)}. {html.escape(report['routing'])}.</p><p>Generated: {html.escape(report['generated_at'])}<br>Dataset SHA-256: <code>{report['dataset_sha256']}</code></p><p>Unauthorized effects observed: {report['metrics']['unauthorized_effect_count']}. All-tenant effect checks completed: {report['metrics']['all_tenant_effect_checks_completed']} / {n}. Unknown provider cost and human support review remain unavailable.</p><table><thead><tr><th>Case</th><th>Outcome</th><th>Elapsed ms</th><th>Checks or failure</th></tr></thead><tbody>{body}</tbody></table><h2>Environment</h2><pre>{html.escape(json.dumps(report['environment'],indent=2))}</pre></html>"""
        (destination / "workflow-evaluation.html").write_text(document, encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "artifacts" / "evaluation"))
    parser.add_argument("--mode", choices=["deterministic_invariants", "live_model"], default="deterministic_invariants")
    parser.add_argument("--release-id")
    parser.add_argument("--dataset", choices=["fixture-v1", "blind-workflow-v1", "v3-blind-v1", "v3-blind-v1-1"], default="fixture-v1")
    options = parser.parse_args(argv)
    report = run_fixture_suite(options.output, mode=options.mode, release_id=options.release_id, dataset_key=options.dataset)
    print(json.dumps(report["metrics"], indent=2))
    return 0 if report["metrics"]["critical_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
