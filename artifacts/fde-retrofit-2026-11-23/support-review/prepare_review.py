"""Prepare an offline, explicitly AI-assisted review packet; never execute the app.

Only the files below are read. Existing reports, data and model files are immutable.
Run with Python from any directory. This script writes only beside itself.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
REPORT = "artifacts/production-v0.3/evaluations/v4-blind-final/workflow-evaluation.json"
DATASET = "fixtures/v4_blind_v1.jsonl"
FIXTURE = "fixtures/evaluation_cases.json"
MANIFEST = "artifacts/v4/model_manifest.json"
QUEUE = "artifacts/v4/final-analysis/human_claim_review_queue.csv"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prepare():
    report = json.loads((ROOT / REPORT).read_text(encoding="utf-8"))
    dataset = [json.loads(line) for line in (ROOT / DATASET).read_text(encoding="utf-8").splitlines() if line]
    fixture = json.loads((ROOT / FIXTURE).read_text(encoding="utf-8"))
    model = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    assert report["dataset_sha256"] == sha(ROOT / DATASET), "Dataset/report mismatch"
    assert model["artifact_sha256"] == sha(ROOT / model["artifact_uri"]), "Model integrity mismatch"
    assert report["model_artifact_sha256"] == model["artifact_sha256"]
    sources = [REPORT, DATASET, FIXTURE, MANIFEST, model["artifact_uri"], QUEUE]
    before = {path: sha(ROOT / path) for path in sources}
    review_queue = list(csv.DictReader((ROOT / QUEUE).open(encoding="utf-8", newline="")))
    assert not any(row.get(key) for row in review_queue for key in ("reviewer_1", "reviewer_2", "adjudication"))

    # Eligibility is based on expected labels, never observed success or answer text.
    strata = (("track_order", "resolved_read_only"), ("cancel_request", "awaiting_approval"))
    selected = []
    eligible_counts = {}
    for intent, disposition in strata:
        candidates = sorted((item for item in dataset if item["expected_intent"] == intent
                             and item["expected_disposition"] == disposition), key=lambda item: item["id"])
        eligible_counts[intent] = len(candidates)
        assert len(candidates) >= 10
        selected.extend(candidates[:10])
    expected_ids = [f"V4-B{i:03}" for i in range(1, 11)] + [f"V4-B{i:03}" for i in range(41, 51)]
    assert [item["id"] for item in selected] == expected_ids, "Frozen sample membership changed"
    by_id = {item["id"]: item for item in report["cases"]}
    rows = []

    for spec in selected:
        archived = by_id.get(spec["id"], {})
        observed = archived.get("observed") or {}
        answer = observed.get("answer") or ""
        evidence = observed.get("evidence_snapshot", [])
        citations = observed.get("citations", [])
        assert all(item.get("corpus_id") == observed.get("versions", {}).get("corpus_id") for item in evidence)
        evidence_by_id = {item["chunk_id"]: item for item in evidence}
        citation_integrity = all(citation["chunk_id"] in evidence_by_id and all(
            str(citation.get(key)) == str(evidence_by_id[citation["chunk_id"]].get(key))
            for key in ("document_id", "corpus_id", "locator", "source_label"))
            and citation.get("excerpt") == evidence_by_id[citation["chunk_id"]].get("body")
            for citation in citations)
        order = next(item for item in fixture["orders"] if item["organization"] == "alpha"
                     and item["order_ref"] == spec["order_ref"] and item["customer_subject"] == "customer-001")
        permitted_fixture_context = {key: order[key] for key in (
            "order_ref", "customer_subject", "status", "tracking_summary", "cancel_deadline", "version")}
        claims = []
        completeness = "complete_for_bounded_workflow"
        missing = []
        if not answer:
            completeness = "missing_output_fail"
            missing = ["The required answer is absent; retain this case in every response denominator."]
        elif spec["expected_intent"] == "track_order" and answer == order["tracking_summary"]:
            claims.append({
                "text": answer, "kind": "current_order_fact", "judgment": "insufficient_archived_evidence",
                "citation_status": "policy_citation_does_not_establish_current_order_status",
                "cited_policy_support": "The ownership policy establishes the disclosure condition, not this order's status.",
                "fixture_corroboration": "Matches the selected customer's seeded tracking_summary and archived runner fact check.",
                "reason": "The archived output omits the raw tenant/customer-authorized order lookup result. A fixture definition and a pass assertion are indirect corroboration; they are not that observed tool result.",
            })
        elif spec["expected_intent"] == "cancel_request" and observed.get("disposition") == "awaiting_approval":
            first, second = answer.split(". ", 1)
            eligibility = next(item for item in evidence if item.get("source_label") == "alpha-cancel-v1" and item["locator"] == "eligibility")
            approval = next(item for item in evidence if item.get("source_label") == "alpha-cancel-v1" and item["locator"] == "approval")
            assert eligibility["chunk_id"] in {item["chunk_id"] for item in citations}
            assert approval["chunk_id"] in {item["chunk_id"] for item in citations}
            assert observed["proposal"]["order_ref"] == spec["order_ref"] and observed["proposal"]["state"] == "pending"
            claims.extend([
                {"text": first + ".", "kind": "policy_and_current_order_eligibility", "judgment": "insufficient_archived_evidence",
                 "citation_status": "policy_conditions_cited_current_order_inputs_not_archived", "evidence_ids": [eligibility["chunk_id"]],
                 "cited_policy_support": "The cited policy supports the pending/owned/deadline conditions for eligibility.",
                 "fixture_corroboration": "The selected customer's seeded A-100 is pending at version 1 with a 12:20Z deadline; evaluation fixes the clock at 12:00Z. The saved proposal is pending at order version 1.",
                 "reason": "The policy and saved proposal do not independently establish the observed ownership/status/deadline lookup. No raw authorized order-tool result is archived."},
                {"text": second, "kind": "approval_policy", "judgment": "supported", "citation_status": "supported_by_cited_permitted_policy",
                 "evidence_ids": [approval["chunk_id"]], "reason": "The permitted approval section requires approval of the exact proposal and current order version and states that preparing a draft never cancels an order."},
            ])
        elif spec["id"] == "V4-B003":
            assert answer == "This request is outside automated support. Please contact the support review team."
            claims.append({
                "text": "This request is outside automated support.", "kind": "scope_classification_claim",
                "judgment": "contradicted_by_frozen_workflow_rubric", "citation_status": "uncited",
                "reason": "The frozen case asks what the owned A-102 order record shows, labels tracking as supported, and expects resolved_read_only. The saved output routes out_of_scope. This is the already reported V4-B003 route_wrong failure, not a newly discovered merchant/provider failure.",
            })
            completeness = "missing_required_order_answer"
            missing = [f"Required owned-order answer absent: {order['tracking_summary']}"]
        else:
            raise AssertionError(f"Unreviewed output form in {spec['id']}; do not silently approve a new answer")
        rows.append({
            "id": spec["id"], "group_id": spec["group_id"], "slice": spec["slice"], "input": spec["message"],
            "expected_intent": spec["expected_intent"], "expected_disposition": spec["expected_disposition"],
            "order_ref": spec["order_ref"], "archived_case_id": archived.get("case_id"),
            "archived_workflow_passed": archived.get("passed"), "answer": answer,
            "observed_disposition": observed.get("disposition"), "reason_code": observed.get("reason_code"),
            "citations": citations, "permitted_policy_snapshot": evidence,
            "authorized_order_tool_snapshot": None,
            "indirect_fixture_context_not_an_observed_tool_result": permitted_fixture_context,
            "saved_proposal": observed.get("proposal"), "claims": claims,
            "citation_provenance_consistent_with_snapshot": citation_integrity,
            "completeness": completeness, "missing_required_facts": missing,
            "response_fully_supported_by_archived_primary_evidence": bool(answer) and bool(claims)
                and all(claim["judgment"] == "supported" for claim in claims)
                and completeness == "complete_for_bounded_workflow",
            "reviewer_type": "coding_agent_ai_assisted", "human_reviewer_1": None, "human_reviewer_2": None,
            "human_adjudication": None,
        })

    claim_counts = Counter(claim["judgment"] for row in rows for claim in row["claims"])
    metrics = {
        "sample_responses": len(rows), "material_claims": sum(claim_counts.values()),
        "claim_judgments": dict(claim_counts),
        "strict_support_given_archived_evidence": {
            "numerator": sum(row["response_fully_supported_by_archived_primary_evidence"] for row in rows),
            "denominator": len(rows), "interpretation": "Evidence-packet sufficiency only, not model factual accuracy: every order-based response lacks an archived raw authorized lookup result."},
        "substantive_truth": {"measured": False, "rate": None,
                              "interpretation": "Nineteen order claims are unverifiable from the saved primary tool evidence, not classified as false. No substantive factual-accuracy estimate is made."},
        "responses_with_contradicted_claim": sum(any(claim["judgment"].startswith("contradicted") for claim in row["claims"]) for row in rows),
        "responses_missing_required_facts": sum(bool(row["missing_required_facts"]) for row in rows),
        "missing_outputs": sum(not row["answer"] for row in rows),
        "saved_workflow_checks_passed": {"numerator": sum(bool(row["archived_workflow_passed"]) for row in rows), "denominator": len(rows)},
        "human_claim_support_review": None, "human_reviewer_agreement": None,
        "paid_provider_quality_and_cost": None,
    }
    packet = {
        "schema_version": "1.0", "review_type": "AI-assisted preparation for human review; not human validation",
        "prepared_at": datetime.now(timezone.utc).isoformat(), "source_report": REPORT,
        "source_report_generated_at": report["generated_at"], "source_report_code_tree_sha256": report["code_tree_sha256"],
        "model_artifact_sha256": model["artifact_sha256"], "source_hashes": before,
        "selection": {"method": "First 10 IDs in each of two expected-label strata: owned tracking resolved_read_only and eligible cancellation awaiting_approval. Select on frozen expectations, not observed success. Retain wrong routes and missing outputs.",
                      "eligible_counts": eligible_counts, "selected_ids": expected_ids,
                      "timing": "Retrospective deterministic sample after the report was viewed; not preregistered or blind.",
                      "unique_semantic_groups": len({row["group_id"] for row in rows})},
        "rubric": {
            "supported": "Every material factual claim requires evidence of the same fact from that case's permitted policy or observed authorized tool result.",
            "insufficient_archived_evidence": "Conditions, fixture seeds, proposal metadata, and runner assertions can corroborate a claim but cannot replace a missing observed authorized order lookup.",
            "contradicted_by_frozen_workflow_rubric": "The response's scope claim conflicts with the frozen supported-workflow expectation; this does not regrade or change the original benchmark.",
            "completeness": "Tracking must include the required owned-order status. Cancellation must state eligibility for review and exact-proposal approval before change. Missing answer or required fact fails response completeness.",
            "citations": "Check cited IDs, source/version/locator/text against this response's saved permitted evidence. Policy citations cannot substantiate current order status alone.",
        },
        "limitations": [
            "No human reviewed this packet. Reviewer disagreement is unavailable; blank reviewer fields are intentional.",
            "These are synthetic offline-deterministic answers from the frozen v4 report, not real customer or paid-provider output.",
            "The sample covers two answerable workflow strata and related semantic groups, not a random sample of production traffic.",
            "Raw authorized order lookup outputs were not saved in the selected report. No order fact is credited as fully supported on primary observed tool evidence.",
            "The already known V4-B003 routing failure is retained. No threshold, training data, final fixture, release, or model is changed.",
            "This packet is an evidence-readiness audit. It adds no claim of customer adoption, productivity savings, merchant integration, or completed human validation.",
        ], "metrics": metrics, "cases": rows,
    }
    write_json("review.json", packet)
    write_json("sample-manifest.json", {"schema_version": "1.0", "selection": packet["selection"],
                                       "source_hashes": before, "source_report": REPORT,
                                       "expected_answerable_cases": len(selected),
                                       "nonempty_saved_answers": sum(bool(row["answer"]) for row in rows),
                                       "human_review_status": "pending"})
    with (OUT / "human-review.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = ["case_id", "claim_number", "claim", "agent_judgment", "evidence_to_check", "reviewer_1_name", "reviewer_1_judgment", "reviewer_2_name", "reviewer_2_judgment", "adjudication", "completeness_notes"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            for number, claim in enumerate(row["claims"], 1):
                writer.writerow({"case_id": row["id"], "claim_number": number, "claim": claim["text"],
                                 "agent_judgment": claim["judgment"], "evidence_to_check": "; ".join(claim.get("evidence_ids", [])) or "See permitted snapshot and missing authorized_order_tool_snapshot in review.json",
                                 "completeness_notes": "; ".join(row["missing_required_facts"])})
    after = {path: sha(ROOT / path) for path in sources}
    assert before == after, "Existing evidence changed during preparation"
    write_json("verification.json", {"read_only_inputs_unchanged": before == after, "source_hashes": after,
                                     "selected_cases": len(rows), "material_claim_rows": sum(claim_counts.values()),
                                     "human_review_status": "pending", "metrics": metrics})
    print(json.dumps({"selected_cases": len(rows), "claim_judgments": dict(claim_counts), "inputs_unchanged": before == after}))


if __name__ == "__main__":
    prepare()
