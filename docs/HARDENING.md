# v0.2 hardening and operator decisions

> Version note: The 157-test Linux/Windows and container results below are historical v0.2.0 evidence. The current v0.2.1 native review passed 173 tests; current Linux and container reruns were unavailable. See [the current review](REVIEW-2026-11-23.md) and its versioned manifest.

The expanded backend suite passes **157 tests on Windows/PostgreSQL 17.12 and 157 on clean Linux/PostgreSQL 18.6**, using Python 3.12.14. The XML reports are `artifacts/checks/extended-acceptance.xml` and `artifacts/checks/linux-acceptance.xml`; the latter environment is recorded separately. These are local/synthetic acceptance results. They establish neither a public production deployment nor freedom from vulnerabilities.

## Same-case follow-up

`POST /v1/cases/{case_id}/context` accepts an `order_ref` and strict integer `expected_case_version`, with an idempotency key. It is restricted to write roles and the current `needs_info` state. The transaction locks the case, retains its verified customer, immutable release and history, attaches the claimed reference, clears the previous current resolution and increments the version. It does not resolve automatically. A later explicit resolve repeats ownership, policy and order-state checks. A browser-supplied order reference is not authorization to inspect or change that order.

Repeated identical requests replay the saved outcome; changed content under the same key conflicts. Wrong tenant, stale versions and invalid states cannot modify the case. Historical traces and the original resolution receipt remain available.

## Declining a proposal

`POST /v1/proposals/{proposal_id}/reject` accepts the exact `proposal_sha256`, strict `expected_case_version` and one fixed reason: `policy_concern`, `customer_requested`, `needs_manual_review` or `other`. Only an approver or administrator can decide. The proposal/case locks serialize this path with approval. A successful decline marks the proposal rejected, escalates the case and records the actor, reason and digest in an immutable trace, without creating an approval/action or executing the cancellation. The response's `order_changed:false` describes this decision's effect, not a claim that unrelated actors could never change the order.

The real-database race test requires exactly one winning approve/decline decision and consistent case/proposal/action state. Idempotency replay preserves the saved acknowledgement; terminal or stale decisions fail closed.

## Data and request boundaries

- Automatic test/evaluation fixture resets verify the *connected* PostgreSQL database, server address and port using both runtime and administration URLs. Only dedicated `resolvelab_test` and `resolvelab_evaluation` destinations are eligible. Libpq routing overrides are rejected. The direct pytest path enforces the same guard before fixture resets.
- Request JSON is checked before FastAPI parsing: valid UTF-8, depth at most 32, no duplicate object keys or nonfinite numbers, no NUL message/reference values, strict bounded integer versions. Streaming requests retain the 32 KiB body limit and receipt timeout; oversized Content-Length values are rejected without unbounded integer conversion.
- A serving corpus admits at most 64 sections across access scopes, 16 KiB per section and 256 KiB combined body text. Metadata has UTF-8 byte bounds. Vectors must have the pinned 384 dimensions and finite, unit-normalized values. Bounded metadata admission precedes body/vector materialization. Over-budget or invalid published evidence fails closed rather than silently truncating a policy.
- New database checks protect writes; historical incompatible rows are not silently rewritten. Serving validation also checks retained legacy values. Parent-row serialization and a separate MVCC migration close mutation/publication races. Readiness fails for an unusable active corpus while liveness remains available.
- The optional provider adapter limits evidence to 20 sections/96 KiB and the streamed provider response to 256 KiB. Invalid envelopes fail as provider unavailable. It cannot authorize tools, change route/citations or produce a noncanonical answer. No paid-provider call or performance claim is part of this review.
- Inbox search is server-backed, tenant-scoped and parameterized, with bounded pages/offset/query length and literal wildcard escaping. The count and page use one PostgreSQL statement/snapshot. This release uses offset pagination; it does not claim a keyset cursor.

## Security review evidence and remaining boundaries

`artifacts/security/runtime-acceptance.json` records 13 in-process HTTP/real-PostgreSQL checks in a fresh disposable audit database: 64 simultaneous creates preserve one case identity; 24 parallel approvals return the same action receipt and produce one order effect; malformed inputs and tenant reads are checked. It is not a remote load or capacity benchmark.

`artifacts/security/static-triage.json` retains every Bandit category and the five medium findings with reviewed call sites. The raw scanner report is available beside it; there are zero high, five medium and 149 low findings. No findings were suppressed to obtain a clean count. The focused Ruff undefined-name checks produced no findings. Known dependency advisories and source exposure are separate bounded checks. See `artifacts/security/README.md` for exact tools, timestamps and limitations.

The final v0.2 browser and current-source model reports must be checked independently before packaging. A private Linux Docker 29.1.3 build/startup smoke passed with healthy PostgreSQL 17.12, API readiness HTTP 200 and a running worker; cleanup completed. It did not run the full suite/browser inside containers or audit image OS packages. Linux also passed a real dump/restore probe and 128 serving-consistency comparisons with zero intent/acceptance mismatches (score tolerance 1e-6). Public TLS/real-provider operation, scheduled cloud recovery and independently reviewed customer quality remain separate verification work. The selected Vercel/Neon connection is not an executed production deployment; Neon requires account-side terms acceptance. GitHub-hosted jobs were blocked before execution by an account billing/runner restriction.
