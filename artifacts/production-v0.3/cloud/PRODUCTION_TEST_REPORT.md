# Production cloud test record — 2026-11-23

Both stable applications have real managed hosting/authentication. This packet records bounded tests and their limits, not an SLA or a capacity certification.

| Check | Actual result |
|---|---|
| OrderFlow source ingestion | All 14 frozen HTTP posts checked; Alpha: 10 inbox events, 10 receipts, 8 revisions, 2 quarantines; Beta: 1 terminal event matched the oracle. |
| ResolveLab private evaluations | Two actual hosted 20-case runs completed first-attempt with durable reports, zero runner errors/unauthorized effects and serving business hashes unchanged. |
| OrderFlow good daily report | Private Queue stage/check/publish succeeded; two orders totaled 8000 minor units; all 13 dbt tests including seven required checks passed. |
| Wrong independent oracle | A consistent 8001 total failed actual order/total reconciliation tests; last-good run/manifest pointer remained exactly unchanged. |
| Inventory | Processing succeeded; observed 20 versus reference 22 correctly recorded quality failure and difference−2. |
| Data/tenant controls | 19 immutable bytea objects checksum-verified. Eleven fixture-business tables in each of two tenant contexts stayed unchanged. Beta SQL tenant scope saw zero Alpha pipeline objects. |
| Resolve cloud recovery | PostgreSQL 18.7 app-schema restore matched 22 tables / 20 forced-RLS tables; restricted-role isolation and owned cleanup verified. |
| Order cloud recovery | Current restore matched 25 tables / 23 forced-RLS tables and 19 objects; immutable object mutation rejected. Earlier five-object restore evidence is preserved separately. |

Two unexpected OrderFlow daily failures remain in history. The first deployment omitted seven warehouse SQL tests; its dbt run exited 2 without a retained detailed log. After packaging was corrected, a persisted diagnostic confirmed a missing process semaphore in Vercel's runtime. The finite dbt child was fixed and its fresh good/wrong/inventory checks then completed as specified. No failed job history was reset.

Real owner identities are explicitly enrolled in Alpha with server-maintained roles. The final source-bound frontend review passed 72 ResolveLab and 46 OrderFlow checks with intercepted provider/API responses; 35 + 30 account scans and 12 actual public anonymous mobile-navigation scans reported no automated violations, with incomplete findings retained. Static review retained 167 Bandit findings (0 high, 7 medium, 160 low), all seven medium call sites reviewed; Ruff found zero undefined names. npm advisory snapshots returned zero known advisories for both unchanged lockfiles. These are scoped observations, not a zero-vulnerability or full-WCAG claim.

The latest OrderFlow owner-authorized hosted browser run passed all 28 read-only checks on its separately recorded frontend commit with real provider/network responses. No owner identity ID, email, subject digest, password, JWT or cookie value is exported. Anonymous provider-origin contract checks and live owner/session checks have separate proof files. Reviewed internal job setup is not represented as user HTTP authentication.

An actual OrderFlow logout probe measured a 900-second JWT: provider logout 200 / session absent, anonymous API 401, and the previously issued bearer still API 200 before expiry while membership stayed active. This did not test post-expiry replay or password reset, and Resolve logout was not directly measured. Browser RAM clearing and server membership revocation are separate from provider cookie-session logout.

The historical fixture interval is old, so a successful daily report can still be stale by age. Recovery excludes managed authentication, warehouse and evaluation data; no provider PITR, full-account recovery or recovery-time guarantee is established. Throughput/large-workload benchmark evidence is separate from these small cloud fixtures.

The JSON files include exact job/deployment IDs, checks, object digests and scope notes. `manifest.json` binds every exported file; the private-value scanner emits only counts/hashes, never matching values. Originals and failed-run history remain in the private verification workspace. Runner `.txt` files are unchanged audit copies; do not execute them from their relocated packet location.
