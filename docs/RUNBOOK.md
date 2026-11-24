# Operations and local reproduction

## Current hosted deployment: v0.3.0

The [live app](https://resolvelab-blush.vercel.app) runs on Vercel with Neon PostgreSQL and Neon Managed Better Auth. It uses production authentication, restricted database roles and an isolated evaluation database. The retailer and business data remain synthetic. [PRODUCTION.md](PRODUCTION.md) is the current deployment/configuration reference; [the v0.2.1 review](REVIEW-2026-11-23.md) and its local recording retain their original historical scope.

- Access: sign in with an existing verified account. A reviewed provider issuer/subject must be enrolled using `scripts/register_identity.py` with the intended tenant, role and customer binding; signup alone does not grant membership. Use a separate authorized setup environment for administrative credentials. Do not enable fixture login or add production localhost trust to bypass enrollment.
- Case failure: inspect the preserved Execution trace before retrying. Refresh the case and retry using its current version and a new idempotency key after resolving the failing dependency. Escalate access, policy or ownership ambiguity to the workspace administrator; an operator-entered order reference does not establish ownership.
- Evaluation backlog: inspect job status, outbox and attempt errors alongside Vercel Queue/function logs. Confirm the configured evaluation database differs from serving. Application attempts are bounded at three; expired leases can retry. The authenticated reconciliation route also has a daily 03:00 UTC Hobby cron fallback, not continuous polling. Never reset serving data to repair evaluation jobs.
- Access revocation: deactivate the server-maintained membership/identity mapping for immediate application denial on the next API check. Provider logout and password-reset session revocation do not establish immediate invalidation of every already issued bearer JWT; the API separately verifies signature, expiry and membership.
- Release recovery: preserve the immutable model/retrieval assets required by pinned cases. Review the model registry and retained evaluation evidence before moving an active release pointer. The local commands below illustrate the existing rollback mechanism; they are not instructions to run fixture administration against a hosted database. Record and validate any authorized production release/configuration change separately.

The [Neon recovery addendum](CLOUD-RECOVERY-2026-11-23.md) records an executed 22-table application-schema restore and its exclusions. Monitor actual Vercel/Neon quotas, queue failures and database readiness. No scheduled external-backup guarantee, account-wide recovery, uptime SLA or measured free-plan capacity is claimed.

## Local operations

Run commands from the checkout root after `uv sync --frozen`. PowerShell commands use `.venv\Scripts\python.exe`; on Linux/macOS use `.venv/bin/python`.

The native bootstrap creates only its project-owned `.runtime/postgres` cluster on `127.0.0.1:55439`. It creates three databases: `resolvelab` (serving), `resolvelab_evaluation` (evaluation resets), and `resolvelab_test` (test resets). It never connects to another local PostgreSQL service. Generated passwords and the signing secret remain in ignored `.env`.

```powershell
.\scripts\local.ps1 start
.\scripts\local.ps1 run
```

In another terminal start `.\scripts\local.ps1 worker`. `/health/live` checks the process; `/health/ready` checks the database role, each active artifact hash/version/threshold, embedding encoder and published corpus. Database outages cause readiness failure without making liveness fail.

## Inspect and recover

- Case failure: open Execution trace, identify `route`, `retrieve`, `generate` or `validate`, repair the dependency and retry with the current case version and a new idempotency key. Preserve the old failed trace.
- Stale approval: refresh the case. A proposal whose case/order/corpus changed cannot execute; resolve the changed context into a fresh proposal where permitted.
- Provider outage: the local demo defaults to the deterministic offline adapter. `GENERATION_ADAPTER=openai` requires an environment key and explicit model. The optional HTTP adapter has a 5-second connect and 20-second total timeout and is restricted to the canonical answer; it cannot select tools or authorize an action. No paid provider run has been measured. Preserve a failed attempt before changing local configuration or retrying.
- Evaluation backlog: confirm the worker runs, the evaluation DB differs from serving, and the active artifact exists. Expired leases retry; three failed attempts remain visible. Do not reset the serving database to repair a job.
- Damaged model: restore its exact immutable file, or change the active pointer to a retained evaluated release. Do not overwrite old model files.

```powershell
.venv\Scripts\python.exe scripts/rollback.py --organization alpha --model-version tfidf-logistic-v1
.venv\Scripts\python.exe scripts/rollback.py --organization alpha --model-version domain-hybrid-v3
```

The command verifies artifact hash/version/threshold before moving the pointer and writes `artifacts/release_operations.jsonl`. Existing cases keep their pinned release. These commands are local fixture administration. The hosted release already has provider authentication and separate operational controls described in [PRODUCTION.md](PRODUCTION.md).

## Backup restoration

```powershell
.\scripts\local.ps1 backup-check
```

The executed check uses `pg_dump -Fc`, restores into a newly named `resolvelab_restore_<random>` database, verifies the original restored order snapshot, then tracking and an atomic cancellation on a synthetic probe there, and removes only that disposable database. The serving database is preserved. The saved result is `artifacts/backup/restore-report.json`. Repeat this check after migration changes. It does not prove an external cloud backup schedule.

## Deliberate fixture reset

```powershell
.\scripts\local.ps1 reset
.venv\Scripts\python.exe scripts/populate_demo.py
```

Reset truncates this configured project's fixture schema and requires `APP_ENV=local` plus `DEMO_MODE=true`. It is never exposed as an HTTP endpoint. Tests and evaluations select their separate databases before importing the database module. Stop the private cluster with `.\scripts\local.ps1 stop`.

## Alternative Compose deployment

Docker Compose contains an API, worker, private PostgreSQL, durable volumes and one-shot migration/artifact preparation. `scripts/docker_env.py` generates ignored secrets. The default port is loopback-only, and demo identities are local-only. Do not publish this fixture identity selector to the Internet. The hardened TLS/OIDC template is an alternative to the current Vercel/Neon architecture, not a description of it. Any separately deployed instance needs its own reviewed authentication/intake mapping, managed secrets, recovery policy and provider-cost review. These local commands do not create cloud resources.

See the [simulated customer delivery exercise and handoff](CUSTOMER_DELIVERY.md) for a bounded acceptance sequence, failure interpretation and escalation responsibilities. Its agent-run fixture checks are separate from hosted owner acceptance.
