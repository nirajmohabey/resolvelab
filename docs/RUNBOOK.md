# Local operations

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
- Provider outage: use the deterministic offline adapter for the local demo. `GENERATION_ADAPTER=openai` requires an environment key and explicit model. The adapter has a 5-second connect and 20-second total timeout. No paid provider run has been measured.
- Evaluation backlog: confirm the worker runs, the evaluation DB differs from serving, and the active artifact exists. Expired leases retry; three failed attempts remain visible. Do not reset the serving database to repair a job.
- Damaged model: restore its exact immutable file, or change the active pointer to a retained evaluated release. Do not overwrite old model files.

```powershell
.venv\Scripts\python.exe scripts/rollback.py --organization alpha --model-version tfidf-logistic-v1
.venv\Scripts\python.exe scripts/rollback.py --organization alpha --model-version domain-hybrid-v3
```

The command verifies artifact hash/version/threshold before moving the pointer and writes `artifacts/release_operations.jsonl`. Existing cases keep their pinned release. This is local fixture administration; a public release requires an established identity provider and separate operational controls.

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

## Deployment boundary

Docker Compose contains an API, worker, private PostgreSQL, durable volumes and one-shot migration/artifact preparation. `scripts/docker_env.py` generates ignored secrets. The default port is loopback-only, and demo identities are local-only. Do not publish this fixture identity selector to the Internet. A hosted production deployment needs HTTPS, a real verified intake/authentication integration, managed secrets, database backup scheduling and provider-cost review. No AWS resource or billable hosting is created by this release.
