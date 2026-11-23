# Handoff

The bounded local first release is implemented. AI01–AI11 have executable code and saved evidence: scaffold/locking, PostgreSQL/RLS fixtures, state and policy gates, grouped datasets, trained models, filtered retrieval, structured responses, tracking/handoff, atomic approvals, evaluation and the workbench. AI12 uses the recorded local demonstration, model rollback, backup restoration, runbook and case study. Public hosting and a paid provider benchmark remain unperformed.

Run `scripts/local.ps1 setup`, then `scripts/local.ps1 run` and `scripts/local.ps1 worker` in separate terminals. Optional background lifecycle is `scripts/background.ps1 start|stop`. The local demo is at http://127.0.0.1:8000 and has six inspectable cases including one pending proposal and one recoverable provider failure.

Final checks: 64 PostgreSQL-backed tests, 9 browser checks, 20/20 deterministic workflows. The independently authored final language/workflow set scored 74/100; its 26 failures remain visible and are not release safety-test passes. Model artifact SHA-256 starts `6856ace9` and ends `217e8829`; the full freeze is in `artifacts/model_freeze.json`.

The code tree and report identities are in `artifacts/release_manifest.json`. The GitHub `v0.1.0` tag identifies the packaged source revision. Hosted jobs could not start because GitHub restricted runners at the account level; no Docker/Linux execution claim is made. Local native validation is complete.

Next bounded work: resolve the account runner restriction, rerun CI and container smoke, then use new retailer development evidence and an independently reviewed holdout to improve excessive escalation. Do not tune against the published final cases or replace a historical model file. Integration with a real identity/intake provider precedes any public demo with real customer data.
