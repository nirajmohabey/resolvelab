# Release handoff

ResolveLab implements a bounded, operator-reviewed synthetic support workflow: FastAPI/PostgreSQL, forced tenant isolation, OIDC-ready browser/API identity, trained routing, filtered retrieval, structured responses, versioned proposals, atomic local approval, traces, leased evaluation, immutable pins, rollback and recovery.

Evidence includes 95 PostgreSQL tests, nine operator browser checks, nine OIDC scenarios, ten UI race scenarios and nine accessibility states with zero automated violations. Deterministic workflows pass 20/20. Frozen v4 synthetic evaluation matched 139/160 expected workflows, with 21 retained failures, 4/104 wrong accepted routes, 1/40 unsupported false acceptances and zero unauthorized effects across 160 audits.

Run `scripts/local.ps1 setup`, then `scripts/local.ps1 run` and `scripts/local.ps1 worker`. Optional hidden process ownership uses `scripts/background.ps1`. Local URL: http://127.0.0.1:8000. Fixtures use a frozen clock and affect no real merchant.

See `artifacts/release_manifest.json`, `docs/CHECKS.md`, `docs/MODEL_CARD.md` and `docs/PRODUCTION.md`. Retained historical models/reports must not be overwritten or used to tune against published holdouts.

Remaining external work: backend hosting and a real IdP/memberships, account runner restriction and executed Docker/Linux acceptance, scheduled operational backups, provider/merchant integrations and independently reviewed real-world quality. The portfolio uses the actual local recording; no hosted customer deployment or business lift is claimed.
