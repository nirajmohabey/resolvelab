# Evidence-bound retail support

ResolveLab connects trained intent routing, permission-filtered policy retrieval, order checks and human approval in one inspectable workbench. Supported paths are owned-order tracking, cancellation proposals and internal handoff for a fictional retailer.

## Current delivery: v0.3.0

The [live workbench](https://resolvelab-blush.vercel.app) runs its FastAPI backend and private evaluation consumer on Vercel, with restricted Neon PostgreSQL roles, separate serving/evaluation databases and Neon Managed Better Auth. Production fixture login is disabled. A verified provider identity must be explicitly mapped to a server-maintained membership and customer context; public signup alone grants no workspace access. The retailer, orders and policies remain synthetic portfolio data. See [production scope and evidence](PRODUCTION.md) for configuration, enrollment and operating limits.

The default generator returns a deterministic answer. The optional [HTTP generation adapter](../src/resolvelab/generation.py) calls the OpenAI Responses API but is constrained to that canonical answer, with deterministic validation before publication. It is mock-tested, not an unconstrained agent or a live merchant connector. No paid-provider quality, latency or cost benchmark has been measured.

## Model and workflow evidence

The first TF-IDF model achieved 0.9968 source-holdout macro-F1 but only 0.4735 on an independently authored 80-case audit. That distribution gap motivated a retailer-development dataset with grouped semantic families. The final v4 pipeline combines word/character TF-IDF and frozen MiniLM representations in hierarchical scope/intent classifiers, uses separate temperature calibration and preserves explicit scope denials as nonprobabilistic decisions. No final holdout case was used for fitting or threshold selection.

On a newly frozen 160-case synthetic set, v4 achieved 0.8705 macro-F1 and 139/160 expected workflows. Paired v1/v3 runs completed 76/160 and 95/160. V4 accepted 104 routes with four errors, including one of 40 unsupported requests. Seventeen supported requests were over-escalated; contextual human-handoff requests are the main weakness. Failures and small-sample uncertainty remain visible. This is synthetic evaluation, not customer evidence.

Routing never grants permission. The API maps verified identity to server-maintained memberships and uses a nonowner PostgreSQL role with forced RLS. A short transaction locks the proposal, case and order, checks the exact digest, actor, owner, versions, policy and deadline, then commits approval, order change, receipt, trace and idempotent response together. Inference occurs outside long row locks, with current order state rechecked before publication. Approval races, real process termination, fenced worker recovery, stale state and direct tenant access checks are covered by the PostgreSQL suite. The current post-cookie-fix native suite passed 210 tests without failures or skips; the corresponding 210-test Linux CI run is green. The earlier 95-test checkpoint remains historical evidence, not a current test count.

Retrieval experiments use 80 policy sections, 32 permitted sections and 16 held-out queries. Dense ranking selected on development queries reached recall@3 0.84375 and MRR@10 0.8094; lexical and hybrid alternatives remain documented. The small corpus uses exact ranking. Cases pin the model, corpus, prompt, policy and retrieval configuration.

The [v0.2.1 review](REVIEW-2026-11-23.md) retains its original local browser, mocked-OIDC, accessibility, restore and rollback evidence. Those runs are separate from the later 72 ResolveLab frontend/account checks with intercepted provider/API responses and 35 ResolveLab account-form accessibility scans. No automated violations were found in those scanned states; this is not full WCAG conformance or proof of an authenticated production owner journey.

Two actual hosted 20-case queue jobs completed deterministic invariants and V4 live-model fixture workflows at 20/20 each, with zero runner errors or unauthorized effects observed and unchanged serving business hashes. Job creation used a reviewed internal service precondition, rather than an enrolled user's HTTP submission. A separate [Neon application-schema restore](CLOUD-RECOVERY-2026-11-23.md) matched all 22 table hashes and checked restricted-role isolation. It excluded managed authentication, warehouse and evaluation data and does not establish full-account recovery or a recovery-time guarantee.

The five frozen model evaluations were rerun before the final auth-cookie patch, retaining V4/V3/V1 workflow scores of 139/95/76 of 160, baseline 19/20 and invariants 20/20. Their original whole-backend hashes remain intact. The [later component attestation](../artifacts/production-v0.3/cloud/resolvelab/native-and-model-components.json) verifies unchanged model, retrieval, worker, fixture and evaluation inputs across that patch; these are not relabeled current-tree reruns. Weights, thresholds and holdout cases were not tuned for this retrofit.

## Delivery boundaries

Hosted backend and provider authentication are implemented in v0.3.0. The Caddy/Compose TLS/OIDC template remains an alternative deployment template; it is not the live Vercel architecture. The supplied video is a historical v0.2.1 local fixture walkthrough, as labeled in the [demo narrative](DEMO.md).

A real merchant connector, measured paid-provider performance and a human claim-support study remain unvalidated. No customer participation, operational savings, uptime SLA or production capacity result is claimed. A future merchant connector needs provider idempotency and explicit unknown-outcome/reconciliation; local database atomicity cannot guarantee a remote effect.

See the [simulated customer delivery exercise](CUSTOMER_DELIVERY.md) for the existing-system mapping, six-case acceptance record, observed friction and operator handoff. It records an agent-run exercise, not external customer acceptance.
