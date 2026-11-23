# Neon application-schema recovery addendum

On **2026-11-23**, an actual ResolveLab Neon PostgreSQL **18.7** application-schema dump was restored into a newly owned temporary database on the same project, using compatible PostgreSQL 18.7 clients and one exported source snapshot.

The exercise passed these checks:

- All **22 application table hashes** matched the exported snapshot exactly.
- All **20 forced-RLS tables** were restored.
- The restricted runtime role saw **zero unscoped tenants** and exactly **one tenant under each explicit tenant context**.
- Source access was read-only. The owned restore destination was marked/verified before use, then removed; the private dump was also removed.
- No password or dump content was persisted in the evidence.

The [public allowlisted recovery proof](../artifacts/production-v0.3/cloud/recovery/resolvelab.json) records the checks. Its original administrative evidence remains in the private verification workspace. Its recorded application snapshot is a point-in-time exercise, rather than a promise that every subsequent change has been backed up.

**Scope:** This covers the serving application's schema. It excludes managed `neon_auth` accounts/schema, warehouse schemas and the separate evaluation database. It does not establish restoration of provider sessions/passwords, full account/project recovery, provider PITR retention, cross-region failover, an RPO/RTO or uptime SLA. A complete recovery plan must separately cover those components and the actual account configuration.

This cloud exercise is distinct from the local 22-table restore that also ran tracking and atomic approval on a restore-only probe. The local workflow result must not be relabeled as a hosted restored-workflow result.
