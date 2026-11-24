# Reproduce the simulated acceptance exercise

This harness exercises six synthetic workflows with the current application UI, real HTTP API and PostgreSQL. It uses existing migrations, fixture seeding, runtime grants and the owned-process helper. It does not exercise Neon login, email delivery, a paid provider or a real merchant. No outside reviewer participated.

Requirements: project Python environment and npm dependencies already installed, PostgreSQL 17 tools, Node and Chrome. Frozen model/embedding artifacts must be present. Run from the repository root, with `node`, `initdb`, `pg_ctl` and `postgres` on PATH (or pass `--node` and `--pg-bin`). For example on Windows:

```powershell
.venv\Scripts\python.exe artifacts\fde-retrofit-2026-11-23\harness\run_uat.py --label replay --pg-bin 'C:\Program Files\PostgreSQL\17\bin'
```

The runner discovers the repository from its own path. `--project` accepts an explicit repository root when running a copied harness. It refuses an existing evidence output directory, so original before/after records remain intact. The `replay` label writes a new directory once; preserve that directory before a subsequent deliberate rerun.

The harness binds only loopback ports 55571 (a freshly initialized PostgreSQL cluster) and 8137 (its own API). Both must be available; otherwise it stops before creating the database. It creates random ephemeral database/auth credentials, a restricted runtime role and a generated `resolvelab_uat_*` database. The native `.runtime` cluster and process records are untouched. Owned scratch logs and private environment/process-file comparisons remain under ignored `work/fde-acceptance/owned-*`. Public isolation evidence includes filenames and an unchanged boolean, without private file values/hashes. Session/login responses, headers, tokens and cookies are excluded from captured transactions.

Case AC06 deliberately switches only the owned API to `GENERATION_ADAPTER=uat-unavailable`. The existing generation boundary raises `ProviderUnavailable` before any external HTTP request, and the real service saves its failed result and error trace. The harness then restores offline generation on the same owned database; the operator explicitly clicks Retry. This is an injected dependency/configuration failure, not a paid-provider availability benchmark.

On exit, cleanup targets only exact `Popen` process trees created by this runner, removes its generated database after checking its name, and stops its owned cluster after checking the absolute directory bounds. A bounded process-stop retry handles a Windows lifecycle timeout. No native serving database is reset, and no production deployment or database is changed.

`before` records the five successful workflows and reproduced missing Retry control. `after-interrupted-owned-process-timeout` preserves an interrupted replay: the Windows owned-process helper exceeded its ten-second timeout while restarting the API after five cases. It is not a six-case application result. `after` is the separate completed corrected-source replay. The final source hashes and API transactions in each completed summary bind evidence to that run; historical full-suite counts are not rerun or relabeled here.
