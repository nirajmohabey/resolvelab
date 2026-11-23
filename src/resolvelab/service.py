"""Deterministic authorization around trained routing and untrusted generated drafts.

Inference runs before the short final transaction. Cancellation effects, approval,
case state, and the idempotent result commit together under row locks.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import time
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import text

from .auth import assert_member
from .db import primitive, rows, transaction
from .schemas import ApproveRequest, CreateCase, DomainError, Draft, ResolveRequest

WRITE_ROLES = {"operator", "approver", "admin"}
READ_ROLES = WRITE_ROLES | {"evaluator"}
STATES = {
    "new": {"triaged", "needs_info", "awaiting_approval", "resolved", "escalated", "failed"},
    "triaged": {"needs_info", "draft_ready", "awaiting_approval", "resolved", "escalated", "failed"},
    "needs_info": {"triaged", "needs_info", "awaiting_approval", "resolved", "escalated", "failed"},
    "draft_ready": {"awaiting_approval", "resolved", "escalated", "failed"},
    "failed": {"triaged", "needs_info", "awaiting_approval", "resolved", "escalated", "failed"},
    "awaiting_approval": {"executing", "resolved", "escalated", "failed"},
    "executing": {"resolved", "failed"},
    "resolved": set(), "escalated": set(),
}


def can_transition(source, target):
    return target in STATES.get(source, set())


def _json(value):
    return json.dumps(primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value):
    return sha256(_json(value).encode()).hexdigest()


def clock(faults=None):
    value = (faults or {}).get("clock")
    if not value and os.environ.get("APP_ENV", "local") == "local":
        value = os.environ.get("FIXTURE_CLOCK")
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now(timezone.utc)


def _query_one(conn, sql, params):
    row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def _base(p):
    return {"tenant": p.tenant_id, "actor": p.actor_id}


def _case(conn, p, case_id, lock=False):
    record = _query_one(conn, "SELECT * FROM resolvelab.cases WHERE tenant_id=:tenant AND case_id=:case" + (" FOR UPDATE" if lock else ""),
                        {**_base(p), "case": case_id})
    if record is None:
        raise DomainError(404, "not_found", "The requested case was not found.")
    return record


def _release(conn, p, release_id):
    record = _query_one(conn, """SELECT r.*, c.version_label AS corpus_version, c.state AS corpus_state,
            c.content_sha256 AS corpus_sha256, m.version_label AS model_version,
            m.artifact_uri, m.artifact_sha256, m.validation_threshold, m.algorithm
        FROM resolvelab.releases r JOIN resolvelab.corpus_versions c USING(tenant_id,corpus_id)
        JOIN resolvelab.model_versions m USING(tenant_id,model_id)
        WHERE r.tenant_id=:tenant AND r.release_id=:release""", {**_base(p), "release": release_id})
    if not record:
        raise DomainError(409, "release_unavailable", "The pinned release is unavailable.")
    return record


def _idem(conn, p, operation, key, payload):
    if not key or not 1 <= len(key) <= 128:
        raise DomainError(422, "invalid_idempotency_key", "Idempotency-Key must contain 1 to 128 characters.")
    scope = _json([p.tenant_id, p.actor_id, operation, key])
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"), {"scope": scope})
    found = _query_one(conn, """SELECT request_sha256,response_status,response_body FROM resolvelab.idempotency_keys
        WHERE tenant_id=:tenant AND actor_id=:actor AND operation=:operation AND idempotency_key=:key""",
                       {**_base(p), "operation": operation, "key": key})
    if found and found["request_sha256"] != fingerprint(payload):
        raise DomainError(409, "idempotency_conflict", "This idempotency key was already used with a different request.")
    return found


def _remember(conn, p, operation, key, payload, body, status=200):
    conn.execute(text("""INSERT INTO resolvelab.idempotency_keys
        (tenant_id,actor_id,operation,idempotency_key,request_sha256,response_status,response_body)
        VALUES(:tenant,:actor,:operation,:key,:hash,:status,CAST(:body AS jsonb))"""),
                 {**_base(p), "operation": operation, "key": key, "hash": fingerprint(payload), "status": status, "body": _json(body)})


def _replay(found):
    if found["response_status"] >= 400:
        body = found["response_body"]
        raise DomainError(found["response_status"], body["code"], body["message"], body.get("retryable", False))
    return primitive(found["response_body"])


def _trace(conn, p, case_id, request_id, stage, outcome="ok", details=None, duration_ms=None, cost=None):
    conn.execute(text("""INSERT INTO resolvelab.trace_events
       (tenant_id,case_id,request_id,stage,outcome,details,duration_ms,estimated_cost_usd,cost_basis,created_at)
       VALUES(:tenant,:case,:request,:stage,:outcome,CAST(:details AS jsonb),:duration,:cost,:basis,clock_timestamp())"""),
                 {**_base(p), "case": case_id, "request": request_id, "stage": stage, "outcome": outcome,
                  "details": _json(details or {}), "duration": duration_ms, "cost": cost,
                  "basis": "offline adapter: no paid provider call" if cost == 0 else None})


def create_case(principal, body, key, request_id=None):
    body = CreateCase.model_validate(body).model_dump()
    if not body["message"].strip():
        raise DomainError(422, "invalid_message", "The support message cannot be blank.")
    request_id = request_id or str(uuid4())
    with transaction(principal) as conn:
        p = assert_member(conn, principal, WRITE_ROLES)
        found = _idem(conn, p, "create_case", key, body)
        if found:
            return _replay(found)
        active = _query_one(conn, "SELECT release_id FROM resolvelab.active_releases WHERE tenant_id=:tenant", _base(p))
        if not active:
            raise DomainError(503, "release_unavailable", "There is no active evaluated release.", True)
        result = _query_one(conn, """INSERT INTO resolvelab.cases
            (tenant_id,release_id,created_by,customer_subject,message,order_ref)
            VALUES(:tenant,:release,:actor,:customer,:message,:order_ref) RETURNING *""",
                            {**_base(p), **body, "release": active["release_id"], "customer": p.customer_subject})
        _trace(conn, p, result["case_id"], request_id, "auth", details={"role": p.role, "verified_intake": True})
        result = primitive(result)
        _remember(conn, p, "create_case", key, body, result, 201)
        return result


def get_case(principal, case_id):
    with transaction(principal) as conn:
        p = assert_member(conn, principal, READ_ROLES)
        result = _case(conn, p, case_id)
        release = _release(conn, p, result["release_id"])
        result["release"] = primitive({k: release[k] for k in ("release_id", "model_id", "model_version", "corpus_id", "corpus_version", "prompt_version", "tool_policy_version")})
        result["proposals"] = rows(conn.execute(text("SELECT * FROM resolvelab.proposals WHERE tenant_id=:tenant AND case_id=:case ORDER BY created_at"), {**_base(p), "case": case_id}))
        result["actions"] = rows(conn.execute(text("""SELECT a.* FROM resolvelab.actions a JOIN resolvelab.proposals p USING(tenant_id,proposal_id)
            WHERE a.tenant_id=:tenant AND p.case_id=:case ORDER BY a.created_at"""), {**_base(p), "case": case_id}))
        return primitive(result)


def list_cases(principal, limit=50, offset=0, state=None):
    with transaction(principal) as conn:
        p = assert_member(conn, principal, READ_ROLES)
        items = rows(conn.execute(text("""SELECT * FROM resolvelab.cases WHERE tenant_id=:tenant
            AND (CAST(:state AS text) IS NULL OR state=:state) ORDER BY created_at DESC,case_id LIMIT :limit OFFSET :offset"""),
                                  {**_base(p), "state": state, "limit": min(max(limit, 1), 100), "offset": max(offset, 0)}))
        return {"items": items, "limit": limit, "offset": offset}


def load_pinned_router(release):
    """Load precisely the immutable local artifact identified by this release.

    The mutable default model pointer is deliberately ignored. A checksum,
    version, or threshold mismatch fails closed before prediction or readiness.
    """
    from .ml import Router
    from .db import ROOT
    from pathlib import Path
    artifact = Path(release["artifact_uri"])
    artifact = (artifact if artifact.is_absolute() else ROOT / artifact).resolve()
    if not artifact.is_relative_to((ROOT / "artifacts").resolve()):
        raise RuntimeError("routing artifact is outside the reviewed artifact directory")
    if sha256(artifact.read_bytes()).hexdigest() != release["artifact_sha256"]:
        raise RuntimeError("pinned routing artifact checksum mismatch")
    router = Router(artifact_path=str(artifact))
    expected_version = release.get("model_version")
    if expected_version and router.version != expected_version:
        raise RuntimeError("pinned routing artifact version mismatch")
    expected_threshold = release.get("validation_threshold")
    if expected_threshold is not None and abs(router.threshold - float(expected_threshold)) > 0.00001:
        raise RuntimeError("pinned routing artifact threshold mismatch")
    return router


def _routing(message, release, faults):
    if (faults or {}).get("routing_adapter"):
        spec = faults["routing_adapter"]
        return {"intent": spec["predicted_intent"], "score": float(spec["routing_score"]),
                "threshold": float(spec["validation_threshold"]), "version": "deterministic-invariant-adapter"}
    from .ml import keyword_predict
    if release.get("algorithm") == "keyword":
        return {"intent": keyword_predict(message), "score": 1.0, "threshold": 0.0, "version": "routing-keyword-v0"}
    prediction = load_pinned_router(release).predict(message)
    return prediction


def _inference_failed(p, case_id, operation, key, payload, request_id, expected_version, exception_type,
                      stage="route", prior_traces=None):
    code = "inference_unavailable" if stage == "route" else "retrieval_unavailable"
    error = DomainError(503, code, "A required inference dependency is unavailable. The case and trace were saved.", True)
    with transaction(p) as conn:
        p = assert_member(conn, p, WRITE_ROLES)
        prior = _idem(conn, p, operation, key, payload)
        if prior:
            return _replay(prior)
        record = _case(conn, p, case_id, True)
        if record["version"] != expected_version or not can_transition(record["state"], "failed"):
            raise DomainError(409, "stale_case", "The case changed while routing was attempted.")
        failure = {"case_id": str(case_id), "case_version": record["version"]+1, "intent": record["intent"] or "out_of_scope",
                   "disposition": "failed", "answer": "The routing model is temporarily unavailable. Retry after recovery.",
                   "citations": [], "missing_information": [], "proposal": None, "reason_code": code}
        conn.execute(text("""UPDATE resolvelab.cases SET state='failed',version=version+1,resolution=CAST(:resolution AS jsonb),updated_at=now()
            WHERE tenant_id=:tenant AND case_id=:case"""), {**_base(p), "case": case_id, "resolution": _json(failure)})
        for trace_stage, outcome, detail, duration, cost in prior_traces or []:
            _trace(conn, p, case_id, request_id, trace_stage, outcome, detail, duration, cost)
        _trace(conn, p, case_id, request_id, stage, "error", {"reason_code": code, "exception_type": exception_type})
        _remember(conn, p, operation, key, payload, {"code": error.code, "message": error.message, "retryable": True}, 503)
    raise error


def triage_case(principal, case_id, body, key, request_id=None, faults=None):
    body = ResolveRequest.model_validate(body).model_dump()
    payload = {"case_id": str(case_id), **body}
    request_id = request_id or str(uuid4())
    with transaction(principal) as conn:
        p = assert_member(conn, principal, WRITE_ROLES)
        prior = _idem(conn, p, "triage", key, payload)
        if prior:
            return _replay(prior)
        record = _case(conn, p, case_id)
        release = _release(conn, p, record["release_id"])
    start = time.perf_counter()
    try:
        route = _routing(record["message"], release, faults)
    except Exception as exc:
        return _inference_failed(p, case_id, "triage", key, payload, request_id, record["version"], type(exc).__name__)
    with transaction(p) as conn:
        p = assert_member(conn, p, WRITE_ROLES)
        prior = _idem(conn, p, "triage", key, payload)
        if prior:
            return _replay(prior)
        record = _case(conn, p, case_id, True)
        if record["version"] != body["expected_case_version"] or not can_transition(record["state"], "triaged"):
            raise DomainError(409, "stale_case", "The case changed; refresh before triage.")
        accepted = route["score"] >= route["threshold"] and route["intent"] != "out_of_scope"
        result = {"intent": route["intent"], "routing_score": route["score"], "accepted": accepted,
                  "model_id": str(release["model_id"]), "case_version": record["version"] + 1,
                  "reason_code": "accepted" if accepted else "low_routing_confidence"}
        conn.execute(text("""UPDATE resolvelab.cases SET state='triaged',intent=:intent,routing_score=:score,
            version=version+1,updated_at=now() WHERE tenant_id=:tenant AND case_id=:case"""),
                     {**_base(p), "case": case_id, "intent": route["intent"], "score": round(route["score"], 5)})
        _trace(conn, p, case_id, request_id, "route", "ok" if accepted else "abstained", route, (time.perf_counter()-start)*1000)
        _remember(conn, p, "triage", key, payload, result)
        return result


def _citation(item):
    return {"chunk_id": str(item["chunk_id"]), "document_id": str(item["document_id"]),
            "corpus_id": str(item["corpus_id"]), "locator": item["locator"],
            "excerpt": item["body"][:2000], "source_label": item.get("source_label"), "title": item.get("title")}


def _retrieve(conn, p, record, release, faults=None):
    from .retrieval import retrieve
    evidence = retrieve(conn, p.tenant_id, str(release["corpus_id"]), record["message"], p.role, mode="hybrid", limit=4)
    evidence = primitive(evidence)
    if (faults or {}).get("retrieval") == "no_required_policy_evidence":
        evidence = []
    injected = (faults or {}).get("injected_documents", [])
    if injected:
        from .db import ROOT
        fixture = json.loads((ROOT / "fixtures" / "evaluation_cases.json").read_text(encoding="utf-8"))
        for doc in fixture["policy_documents"]:
            if doc["alias"] in injected:
                for section in doc["sections"]:
                    # This deterministic dependency-fault adapter deliberately delivers
                    # attack text to generation, but cannot mint authoritative citations.
                    evidence.append({"chunk_id": "injected-" + doc["alias"], "document_id": "injected",
                                     "corpus_id": str(release["corpus_id"]), "source_label": doc["alias"],
                                     "title": "Untrusted injected document", "locator": section["locator"],
                                     "body": section["text"], "score": 1.0, "authoritative": False})
    return evidence


def get_evidence(principal, case_id, limit=4):
    with transaction(principal) as conn:
        p = assert_member(conn, principal, READ_ROLES)
        record = _case(conn, p, case_id)
        if record["resolution"] and record["resolution"].get("evidence_snapshot") is not None:
            evidence = record["resolution"]["evidence_snapshot"]
        else:
            try:
                evidence = _retrieve(conn, p, record, _release(conn, p, record["release_id"]))
            except Exception as exc:
                raise DomainError(503, "retrieval_unavailable", "Policy retrieval is temporarily unavailable.", True) from exc
        return {"case_id": str(case_id), "citations": [_citation(x) for x in evidence[:limit] if x.get("authoritative", True)],
                "items": evidence[:limit], "retrieval_version": "hybrid-rrf-v1"}


def _required_evidence(intent, evidence, corpus_id):
    trusted = [x for x in evidence if x.get("authoritative", True) and str(x["corpus_id"]) == str(corpus_id)]
    kind = {"track_order": "tracking", "cancel_request": "cancel", "human_handoff": "handoff"}.get(intent)
    locators = {"track_order": {"ownership"}, "cancel_request": {"eligibility", "approval"}, "human_handoff": {"scope"}}.get(intent, set())
    selected = [x for x in trusted if x.get("source_label", "").endswith(f"-{kind}-v1") and x["locator"] in locators]
    return selected if locators <= {x["locator"] for x in selected} else []


def _evidence_unchanged(conn, p, evidence, corpus_id):
    """Guard against an administrative corpus edit under an unchanged version ID."""
    for item in evidence:
        if not item.get("authoritative", True):
            continue
        current = _query_one(conn, """SELECT c.body,c.locator,c.document_id,c.corpus_id,d.source_label,d.access_scope
            FROM resolvelab.chunks c JOIN resolvelab.documents d USING(tenant_id,corpus_id,document_id)
            JOIN resolvelab.corpus_versions v USING(tenant_id,corpus_id)
            WHERE c.tenant_id=:tenant AND c.chunk_id=:chunk AND c.corpus_id=:corpus AND v.state='published'
              AND (d.access_scope='support' OR :is_admin)""",
                             {**_base(p), "chunk": item["chunk_id"], "corpus": corpus_id, "is_admin": p.role == "admin"})
        if not current or any(str(current[field]) != str(item.get(field)) for field in ("body", "locator", "document_id", "corpus_id", "source_label")):
            return False
    return True


def _draft(record, release, route, evidence, order, now):
    intent = route["intent"]
    policy = _required_evidence(intent, evidence, release["corpus_id"])
    value = {"case_id": str(record["case_id"]), "case_version": record["version"] + 1, "intent": intent,
             "disposition": "escalated", "answer": "This request needs support review.",
             "citations": [_citation(x) for x in policy], "missing_information": [], "proposal": None, "reason_code": None}
    if route["score"] < route["threshold"]:
        value.update(answer="I could not confidently route this request. A support operator should review it.", reason_code="low_routing_confidence")
    elif intent == "out_of_scope":
        value.update(answer="This request is outside automated support. Please contact the support review team.", reason_code="unsupported_intent")
    elif intent == "human_handoff":
        value.update(answer="Your request has been placed in the internal support review queue.", reason_code="user_requested")
    elif not record["order_ref"]:
        value.update(disposition="needs_info", answer="Please provide the order reference so it can be verified.", missing_information=["order_ref"], reason_code="missing_order_ref")
    elif order is None:
        value.update(disposition="needs_info", answer="Please provide a verified order reference. No order information could be verified.", missing_information=["verified_order_ref"], reason_code="order_not_verified")
    elif order["customer_subject"] != record["customer_subject"]:
        value.update(answer="I could not verify this order for the authenticated customer. Support review is required.", reason_code="order_not_verified")
    elif not policy:
        value.update(answer="The required published policy evidence is unavailable. Support review is required.", reason_code="missing_evidence")
    elif intent == "track_order":
        value.update(disposition="resolved_read_only", answer=order["tracking_summary"], reason_code="authorized_tracking")
    elif order["status"] != "pending":
        value.update(answer="This order is not eligible for automated cancellation. Support review is required.", reason_code="order_not_eligible")
    elif now > order["cancel_deadline"]:
        value.update(answer="The cancellation deadline has passed. Support review is required.", reason_code="cancellation_deadline_passed")
    else:
        value.update(disposition="awaiting_approval", answer=f"Cancellation of {record['order_ref']} is eligible for review. An approver must approve the exact proposal before any order change.", reason_code="approval_required")
    return value


def _validate_generated(generated, allowed, evidence, corpus_id):
    try:
        validated = Draft.model_validate(generated).model_dump()
    except (ValidationError, ValueError, TypeError):
        return "schema_invalid"
    # The first connector uses bounded approved response text. Free-form claims
    # are never sufficient authority to disclose or mutate an order.
    allowed_keys = ["case_id", "case_version", "intent", "disposition", "missing_information", "proposal", "reason_code"]
    if any(validated.get(k) != allowed.get(k) for k in allowed_keys):
        return "unknown_tool" if validated.get("proposal") else "unsupported_claim"
    ids = {str(x["chunk_id"]): x for x in evidence if x.get("authoritative", True) and str(x["corpus_id"]) == str(corpus_id)}
    for citation in validated["citations"]:
        item = ids.get(citation["chunk_id"])
        if not item or any(str(citation[k]) != str(item[k]) for k in ("document_id", "corpus_id", "locator")):
            return "unsupported_citation"
    if {x["chunk_id"] for x in validated["citations"]} != {x["chunk_id"] for x in allowed["citations"]}:
        return "missing_evidence"
    if validated["answer"] != allowed["answer"]:
        return "unsupported_claim"
    return None


def _proposal_digest(record, release, order, expires_at):
    return fingerprint({"tenant_id": str(record["tenant_id"]), "case_id": str(record["case_id"]),
                        "case_version": record["version"] + 1, "order_ref": record["order_ref"],
                        "order_version": order["version"], "corpus_id": str(release["corpus_id"]),
                        "corpus_sha256": release["corpus_sha256"], "tool_policy_version": release["tool_policy_version"],
                        "tool_name": "cancel_order", "expires_at": expires_at})


def resolve_case(principal, case_id, body, key, request_id=None, faults=None):
    body = ResolveRequest.model_validate(body).model_dump()
    payload = {"case_id": str(case_id), **body}
    request_id = request_id or str(uuid4())
    faults = faults or {}
    with transaction(principal) as conn:
        p = assert_member(conn, principal, WRITE_ROLES)
        prior = _idem(conn, p, "resolve", key, payload)
        if prior:
            return _replay(prior)
        record = _case(conn, p, case_id)
        if record["version"] != body["expected_case_version"] or record["state"] not in {"new", "triaged", "needs_info", "draft_ready", "failed"}:
            raise DomainError(409, "stale_case", "The case changed or has already completed this workflow.")
        release = _release(conn, p, record["release_id"])
        if release["corpus_state"] != "published":
            raise DomainError(409, "stale_corpus", "The pinned corpus is no longer published.")
    traces = []
    start = time.perf_counter()
    try:
        route = _routing(record["message"], release, faults)
    except Exception as exc:
        return _inference_failed(p, case_id, "resolve", key, payload, request_id, record["version"], type(exc).__name__)
    traces.append(("route", "ok" if route["score"] >= route["threshold"] else "abstained", route, (time.perf_counter()-start)*1000, None))
    start = time.perf_counter()
    try:
        with transaction(p) as conn:
            evidence = _retrieve(conn, p, record, release, faults)
            order = _query_one(conn, "SELECT * FROM resolvelab.fixture_orders WHERE tenant_id=:tenant AND order_ref=:order_ref",
                               {**_base(p), "order_ref": record["order_ref"]}) if record["order_ref"] else None
    except Exception as exc:
        return _inference_failed(p, case_id, "resolve", key, payload, request_id, record["version"], type(exc).__name__, "retrieve", traces)
    traces.append(("retrieve", "ok", {"chunk_ids": [x["chunk_id"] for x in evidence], "corpus_id": str(release["corpus_id"]),
                                      "injected_documents_seen": [x["source_label"] for x in evidence if not x.get("authoritative", True)]},
                   (time.perf_counter()-start)*1000, None))
    now = clock(faults)
    allowed = _draft(record, release, route, evidence, order, now)
    deferred_error = None
    start = time.perf_counter()
    try:
        if faults.get("generation") == "provider_timeout":
            raise TimeoutError("deterministic timeout adapter")
        from .generation import generate
        generated = generate({"draft": allowed, "intent": route["intent"], "message": record["message"],
                              "order": primitive(order) if order and order["customer_subject"] == record["customer_subject"] else None,
                              "evidence": evidence, "release": primitive(release)})
        if faults.get("generation") in {"invalid_json", "schema_invalid"}:
            generated = {"invalid": True}
        elif faults.get("generation") == "unknown_tool":
            generated = {**allowed, "proposal": {"tool_name": "arbitrary_tool"}}
        elif faults.get("generation") in {"unsupported_citation", "stale_corpus"}:
            generated = {**allowed, "citations": [{"chunk_id": "unknown", "document_id": "unknown", "corpus_id": "unknown", "locator": "unknown"}]}
        traces.append(("generate", "ok", {"adapter": os.environ.get("GENERATION_ADAPTER", "offline"), "prompt_version": release["prompt_version"]}, (time.perf_counter()-start)*1000,
                       0 if os.environ.get("GENERATION_ADAPTER", "offline") == "offline" else None))
        invalid = _validate_generated(generated, allowed, evidence, release["corpus_id"])
        if invalid:
            allowed.update(disposition="escalated", answer="The generated draft failed validation. Support review is required.", proposal=None, reason_code=invalid)
        traces.append(("validate", "denied" if invalid else "ok", {"reason_code": invalid or "bounded_response_valid", "citations_checked": len(allowed["citations"]), "tool_allowlist": ["cancel_order"]}, None, None))
    except (TimeoutError, ConnectionError) as exc:
        allowed.update(disposition="failed", answer="The response provider is temporarily unavailable. This case has been saved for retry.", proposal=None, reason_code="provider_unavailable")
        traces.append(("generate", "error", {"reason_code": "provider_unavailable", "exception_type": type(exc).__name__}, (time.perf_counter()-start)*1000, None))
        deferred_error = DomainError(503, "provider_unavailable", "The response provider is temporarily unavailable. The case and trace were saved.", True)
    except Exception as exc:
        # Provider wrappers may raise their own typed unavailable error. No raw
        # exception text or customer text enters traces.
        allowed.update(disposition="failed", answer="The response provider is temporarily unavailable. This case has been saved for retry.", proposal=None, reason_code="provider_unavailable")
        traces.append(("generate", "error", {"reason_code": "provider_unavailable", "exception_type": type(exc).__name__}, (time.perf_counter()-start)*1000, None))
        deferred_error = DomainError(503, "provider_unavailable", "The response provider is temporarily unavailable. The case and trace were saved.", True)
    with transaction(p) as conn:
        p = assert_member(conn, p, WRITE_ROLES)
        prior = _idem(conn, p, "resolve", key, payload)
        if prior:
            return _replay(prior)
        current = _case(conn, p, case_id, True)
        if current["version"] != record["version"] or current["release_id"] != record["release_id"]:
            raise DomainError(409, "stale_case", "The case changed while its draft was prepared. Refresh and retry.")
        live_release = _release(conn, p, current["release_id"])
        if live_release["corpus_state"] != "published" or live_release["corpus_sha256"] != release["corpus_sha256"]:
            raise DomainError(409, "stale_corpus", "The published policy changed while preparing the draft.")
        if not _evidence_unchanged(conn, p, evidence, release["corpus_id"]):
            raise DomainError(409, "stale_corpus", "The policy evidence changed while preparing the draft.")
        for stage, outcome, detail, duration, cost in traces:
            _trace(conn, p, case_id, request_id, stage, outcome, detail, duration, cost)
        if allowed["disposition"] == "awaiting_approval":
            live_order = _query_one(conn, "SELECT * FROM resolvelab.fixture_orders WHERE tenant_id=:tenant AND order_ref=:order_ref FOR UPDATE", {**_base(p), "order_ref": current["order_ref"]})
            if not live_order or live_order["version"] != order["version"]:
                raise DomainError(409, "stale_order", "The order changed while preparing the proposal.")
            expires = min(now + timedelta(minutes=15), order["cancel_deadline"])
            digest = _proposal_digest(record, release, order, expires)
            proposal = _query_one(conn, """INSERT INTO resolvelab.proposals
                (tenant_id,case_id,tool_name,order_ref,expected_order_version,expected_case_version,proposal_sha256,tool_policy_version,expires_at)
                VALUES(:tenant,:case,'cancel_order',:order_ref,:order_version,:case_version,:digest,:policy,:expires) RETURNING *""",
                                  {**_base(p), "case": case_id, "order_ref": record["order_ref"], "order_version": order["version"],
                                   "case_version": record["version"]+1, "digest": digest, "policy": release["tool_policy_version"], "expires": expires})
            for citation in allowed["citations"]:
                conn.execute(text("INSERT INTO resolvelab.proposal_evidence(tenant_id,proposal_id,chunk_id) VALUES(:tenant,:proposal,:chunk)"),
                             {**_base(p), "proposal": proposal["proposal_id"], "chunk": citation["chunk_id"]})
            allowed["proposal"] = primitive(proposal)
            _trace(conn, p, case_id, request_id, "propose", details={"proposal_id": str(proposal["proposal_id"]), "proposal_sha256": digest, "effect_executed": False})
        state = {"resolved_read_only": "resolved", "needs_info": "needs_info", "awaiting_approval": "awaiting_approval", "escalated": "escalated", "failed": "failed"}[allowed["disposition"]]
        if not can_transition(current["state"], state):
            raise DomainError(409, "invalid_transition", "The case no longer permits this transition.")
        if route["intent"] == "human_handoff" and allowed["reason_code"] == "user_requested":
            conn.execute(text("""INSERT INTO resolvelab.handoffs(tenant_id,case_id,reason_code)
                VALUES(:tenant,:case,'user_requested') ON CONFLICT(tenant_id,case_id) DO NOTHING"""), {**_base(p), "case": case_id})
            _trace(conn, p, case_id, request_id, "handoff", details={"reason_code": "user_requested", "queue": "support_review"})
        allowed["evidence_snapshot"] = evidence
        allowed["versions"] = primitive({k: release[k] for k in ("release_id", "model_id", "corpus_id", "prompt_version", "tool_policy_version")})
        conn.execute(text("""UPDATE resolvelab.cases SET intent=:intent,routing_score=:score,state=:state,
            resolution=CAST(:resolution AS jsonb),version=version+1,updated_at=now() WHERE tenant_id=:tenant AND case_id=:case"""),
                     {**_base(p), "case": case_id, "intent": route["intent"], "score": round(route["score"], 5), "state": state, "resolution": _json(allowed)})
        if deferred_error:
            _remember(conn, p, "resolve", key, payload, {"code": deferred_error.code, "message": deferred_error.message, "retryable": True}, 503)
        else:
            _remember(conn, p, "resolve", key, payload, allowed)
    if deferred_error:
        raise deferred_error
    return primitive(allowed)


def approve_proposal(principal, proposal_id, body, key, request_id=None, faults=None):
    body = ApproveRequest.model_validate(body).model_dump()
    payload = {"proposal_id": str(proposal_id), **body}
    request_id = request_id or str(uuid4())
    faults = faults or {}
    with transaction(principal) as conn:
        p = assert_member(conn, principal, {"approver", "admin"})
        prior = _idem(conn, p, "approve", key, payload)
        if prior:
            return _replay(prior)
        proposal = _query_one(conn, "SELECT * FROM resolvelab.proposals WHERE tenant_id=:tenant AND proposal_id=:proposal FOR UPDATE",
                              {**_base(p), "proposal": proposal_id})
        if not proposal:
            raise DomainError(404, "not_found", "The requested proposal was not found.")
        if proposal["proposal_sha256"] != body["proposal_sha256"] or proposal["expected_case_version"] != body["expected_case_version"] or proposal["expected_order_version"] != body["expected_order_version"]:
            raise DomainError(409, "stale_proposal", "The approval does not match the exact proposal and its versions.")
        existing = _query_one(conn, "SELECT * FROM resolvelab.actions WHERE tenant_id=:tenant AND proposal_id=:proposal", {**_base(p), "proposal": proposal_id})
        if existing:
            _remember(conn, p, "approve", key, payload, primitive(existing))
            return primitive(existing)
        record = _case(conn, p, proposal["case_id"], True)
        release = _release(conn, p, record["release_id"])
        order = _query_one(conn, "SELECT * FROM resolvelab.fixture_orders WHERE tenant_id=:tenant AND order_ref=:order_ref FOR UPDATE", {**_base(p), "order_ref": proposal["order_ref"]})
        now = clock(faults)
        if (proposal["state"] != "pending" or record["state"] != "awaiting_approval" or record["version"] != proposal["expected_case_version"]
                or not order or order["version"] != proposal["expected_order_version"] or order["status"] != "pending"
                or order["customer_subject"] != record["customer_subject"] or proposal["expires_at"] <= now
                or order["cancel_deadline"] < now or release["corpus_state"] != "published"
                or proposal["tool_policy_version"] != release["tool_policy_version"]):
            raise DomainError(409, "stale_proposal", "The proposal is stale or no longer eligible. Prepare a new case after review.")
        digest_record = {**record, "version": record["version"] - 1}
        if _proposal_digest(digest_record, release, order, proposal["expires_at"]) != proposal["proposal_sha256"]:
            raise DomainError(409, "stale_proposal", "The proposal no longer matches its policy and resource snapshot.")
        evidence = rows(conn.execute(text("""SELECT c.*,d.source_label FROM resolvelab.proposal_evidence pe
            JOIN resolvelab.chunks c USING(tenant_id,chunk_id)
            JOIN resolvelab.documents d USING(tenant_id,corpus_id,document_id)
            WHERE pe.tenant_id=:tenant AND pe.proposal_id=:proposal"""), {**_base(p), "proposal": proposal_id}))
        if not _required_evidence("cancel_request", evidence, release["corpus_id"]):
            raise DomainError(409, "stale_proposal", "The required proposal evidence is no longer valid.")
        snapshot = [item for item in (record["resolution"] or {}).get("evidence_snapshot", [])
                    if item.get("authoritative", True) and str(item["chunk_id"]) in {str(row["chunk_id"]) for row in evidence}]
        if len(snapshot) != len(evidence) or not _evidence_unchanged(conn, p, snapshot, release["corpus_id"]):
            raise DomainError(409, "stale_proposal", "The policy evidence differs from the approved proposal snapshot.")
        conn.execute(text("""INSERT INTO resolvelab.approvals(tenant_id,proposal_id,actor_id,approved_sha256)
            VALUES(:tenant,:proposal,:actor,:digest)"""), {**_base(p), "proposal": proposal_id, "digest": body["proposal_sha256"]})
        conn.execute(text("""UPDATE resolvelab.fixture_orders SET status='cancelled',tracking_summary=:summary,
            version=version+1,updated_at=now() WHERE tenant_id=:tenant AND order_ref=:order_ref"""),
                     {**_base(p), "order_ref": order["order_ref"], "summary": f"Order {order['order_ref']} is cancelled"})
        result = {"order_ref": order["order_ref"], "status": "cancelled", "order_version": order["version"] + 1, "case_id": str(record["case_id"])}
        action = _query_one(conn, """INSERT INTO resolvelab.actions(tenant_id,proposal_id,status,connector,connector_idempotency_key,result)
            VALUES(:tenant,:proposal,'succeeded','local_fixture',:key,CAST(:result AS jsonb)) RETURNING *""",
                            {**_base(p), "proposal": proposal_id, "key": str(proposal_id), "result": _json(result)})
        conn.execute(text("UPDATE resolvelab.proposals SET state='executed' WHERE tenant_id=:tenant AND proposal_id=:proposal"), {**_base(p), "proposal": proposal_id})
        resolution = dict(record["resolution"] or {})
        resolution.update(answer=f"Order {order['order_ref']} has been cancelled after approval.", action=primitive(action), case_version=record["version"]+1, reason_code="approved_cancellation")
        # The public disposition remains the resolution schema's read-only/proposal
        # vocabulary; the durable case state identifies final completion.
        resolution["disposition"] = "resolved_read_only"
        conn.execute(text("""UPDATE resolvelab.cases SET state='resolved',resolution=CAST(:resolution AS jsonb),version=version+1,updated_at=now()
            WHERE tenant_id=:tenant AND case_id=:case"""), {**_base(p), "case": record["case_id"], "resolution": _json(resolution)})
        _trace(conn, p, record["case_id"], request_id, "approve", details={"actor_id": p.actor_id, "proposal_sha256": proposal["proposal_sha256"]})
        _trace(conn, p, record["case_id"], request_id, "execute", details={"action_id": str(action["action_id"]), "connector": "local_fixture", "status": "succeeded"})
        action = primitive(action)
        _remember(conn, p, "approve", key, payload, action)
        if faults.get("crash_before_commit"):
            raise RuntimeError("deterministic crash before approval commit")
    if faults.get("crash_after_commit"):
        raise RuntimeError("deterministic crash after approval commit")
    return action


def get_trace(principal, case_id):
    with transaction(principal) as conn:
        p = assert_member(conn, principal, READ_ROLES)
        _case(conn, p, case_id)
        return {"case_id": str(case_id), "events": rows(conn.execute(text("""SELECT * FROM resolvelab.trace_events
            WHERE tenant_id=:tenant AND case_id=:case ORDER BY created_at,trace_id"""), {**_base(p), "case": case_id}))}


def dashboard(principal):
    with transaction(principal) as conn:
        p = assert_member(conn, principal, READ_ROLES)
        counts = rows(conn.execute(text("SELECT state,count(*) AS count FROM resolvelab.cases WHERE tenant_id=:tenant GROUP BY state"), _base(p)))
        actions = conn.execute(text("SELECT count(*) FROM resolvelab.actions WHERE tenant_id=:tenant AND status='succeeded'"), _base(p)).scalar_one()
        latencies = _query_one(conn, """SELECT percentile_cont(0.5) WITHIN GROUP(ORDER BY duration_ms) AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP(ORDER BY duration_ms) AS p95_ms,count(duration_ms) AS observations
            FROM resolvelab.trace_events WHERE tenant_id=:tenant AND stage='generate' AND created_at > now()-interval '24 hours'""", _base(p))
        return primitive({"states": {x["state"]: x["count"] for x in counts}, "total_cases": sum(x["count"] for x in counts),
                          "successful_actions": actions, "generation_latency": latencies, "window": "last 24 hours", "provider_cost_usd": None})


def list_releases(principal):
    with transaction(principal) as conn:
        p = assert_member(conn, principal, READ_ROLES)
        return {"items": rows(conn.execute(text("""SELECT r.*,m.version_label AS model_version,m.algorithm,m.metrics AS model_metrics,
            c.version_label AS corpus_version, a.release_id IS NOT NULL AS active
            FROM resolvelab.releases r JOIN resolvelab.model_versions m USING(tenant_id,model_id)
            JOIN resolvelab.corpus_versions c USING(tenant_id,corpus_id)
            LEFT JOIN resolvelab.active_releases a USING(tenant_id,release_id)
            WHERE r.tenant_id=:tenant ORDER BY r.created_at DESC"""), _base(p)))}


def list_evaluations(principal):
    with transaction(principal) as conn:
        p = assert_member(conn, principal, READ_ROLES)
        return {"items": rows(conn.execute(text("SELECT * FROM resolvelab.evaluation_runs WHERE tenant_id=:tenant ORDER BY created_at DESC LIMIT 100"), _base(p)))}


def get_evaluation(principal, evaluation_id):
    with transaction(principal) as conn:
        p = assert_member(conn, principal, READ_ROLES)
        result = _query_one(conn, "SELECT * FROM resolvelab.evaluation_runs WHERE tenant_id=:tenant AND evaluation_id=:evaluation", {**_base(p), "evaluation": evaluation_id})
        if not result:
            raise DomainError(404, "not_found", "The requested evaluation was not found.")
        return primitive(result)


def create_evaluation(principal, body, key, request_id=None):
    """Queue only into an isolated evaluation database; never reset live cases."""
    from .db import ROOT
    from .schemas import EvaluationRequest
    body = primitive(EvaluationRequest.model_validate(body).model_dump())
    with transaction(principal) as conn:
        p = assert_member(conn, principal, {"evaluator", "admin"})
        if p.tenant_id != "11111111-1111-4111-8111-111111111111":
            raise DomainError(422, "fixture_dataset_organization_unsupported", "The fixture-v1 benchmark is defined only for Alpha Retail. No evaluation was queued.")
        if not os.environ.get("EVALUATION_DATABASE_URL") or not os.environ.get("ADMIN_EVALUATION_DATABASE_URL"):
            raise DomainError(503, "evaluation_not_configured", "A dedicated evaluation database must be configured before scheduling a run.", False)
        from sqlalchemy.engine import make_url
        if make_url(os.environ["EVALUATION_DATABASE_URL"]).database == make_url(os.environ.get("DATABASE_URL", "postgresql:///resolvelab")).database:
            raise DomainError(503, "evaluation_not_isolated", "The evaluation database must differ from the serving database.", False)
        prior = _idem(conn, p, "create_evaluation", key, body)
        if prior:
            return _replay(prior)
        _release(conn, p, body["release_id"])
        dataset_hash = sha256((ROOT / "fixtures/evaluation_cases.json").read_bytes()).hexdigest()
        result = _query_one(conn, """INSERT INTO resolvelab.evaluation_runs
            (tenant_id,release_id,created_by,dataset_sha256,mode,state,environment)
            VALUES(:tenant,:release,:actor,:hash,:mode,'queued',CAST(:environment AS jsonb)) RETURNING *""",
                            {**_base(p), "release": body["release_id"], "hash": dataset_hash, "mode": body["mode"],
                             "environment": _json({"dataset_key": body["dataset_key"], "request_id": request_id or str(uuid4()), "attempts": 0})})
        result = primitive(result)
        _remember(conn, p, "create_evaluation", key, body, result, 202)
        return result
