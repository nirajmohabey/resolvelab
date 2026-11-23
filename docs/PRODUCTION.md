# Production deployment and evidence

Status checked on **2026-11-23** for v0.3.0. The stable public app is [resolvelab-blush.vercel.app](https://resolvelab-blush.vercel.app). FastAPI, the pinned routing/embedding assets, and the private queue consumer are deployed on Vercel. Neon provides the serving database, an isolated evaluation database, and Managed Better Auth. The accounts currently use their free plans; quotas and provider availability remain operating limits.

The retailer, policies and orders are synthetic portfolio data. Real provider authentication does not turn those fixtures into customer or merchant production data. No paid generation-provider benchmark or real merchant/refund/payment integration has been executed.

## Authentication and enrollment

The live app uses `AUTH_MODE=neon`, `APP_ENV=production` and `DEMO_MODE=false`. Fixture identities are unavailable. The provider JWKS endpoint was fetched successfully and exposes Ed25519 keys. An actual verified owner identity has been checked against the provider and explicitly enrolled as an Alpha administrator. No email, subject, password, session token or credential is included here.

The browser uses the pinned official Better Auth client with JWT support. Provider HTTP-only cookies travel through the fixed same-origin `/auth/` proxy, avoiding a third-party-cookie dependency. API JWTs stay in browser memory. The proxy has an endpoint allowlist, exact origin/callback checks, request/response/cookie limits and a fixed upstream. It emits host-only `Secure`, `HttpOnly`, `SameSite=Lax` cookies, preserves valid `Partitioned`/`Priority` attributes on Python 3.12, and keeps `__Host-` cookie paths at `/`.

JWT verification requires EdDSA, a known Ed25519 key, the configured Auth URL **origin** as issuer and audience, and valid subject/expiry/issued-at claims. JWKS caching and failed/unknown-key fetch cooldowns are bounded. A signed JWT role or tenant claim grants no application authority. A server-maintained issuer/subject mapping, active membership and reviewed customer context determine access. Database membership checks are repeated on requests/actions. Signup and verification alone do not enroll an operator.

The shared provider email service uses verification/reset codes. Signup, verification, sign-in, session retrieval, password reset and sign-out have explicit UI flows. Provider session revocation and a locally verified short-lived JWT have different lifetimes; logout does not immediately revoke every already issued JWT. Administrator membership revocation is checked by the API on the next request.

Generic OIDC Authorization Code + PKCE remains available for another explicitly configured provider. It supports RS256/ES256 access-token verification and the browser's state, nonce, PKCE and ID-token checks. Its nine mocked-provider browser cases are separate from the live Neon provider/account evidence. There is no production fixture fallback.

Enroll additional identities only after reviewing the exact verified issuer/subject, membership, role and customer binding. `scripts/register_identity.py` refuses conflicting/remapped/revoked identities. Administrative credentials belong only in the setup environment; the deployed app does not receive a serving-owner URL. A real multi-customer intake must establish ownership through a trusted integration rather than an unverified browser field.

## Vercel and Neon configuration

The deployed runtime is Python 3.12. `app.py` exposes FastAPI and `queue_worker.py` registers the native private Vercel Queue subscriber. `vercel.json` builds the auth bundle with `npm ci && npm run build:auth` and bounds API/consumer functions to 270 seconds. Frozen models, embedding/tokenizer files and licenses must remain in the deployment bundle. Build acceptance and successful hosted model execution were observed; request-time training or artifact downloads are unnecessary.

| Setting | Required boundary |
|---|---|
| `DATABASE_URL` | Pooled Neon serving URL for the restricted nonowner app role |
| `EVALUATION_DATABASE_URL` | Direct URL for the separate `resolvelab_evaluation` database |
| `ADMIN_EVALUATION_DATABASE_URL` | Restricted evaluation migrator that owns only the isolated evaluation database |
| `DEPLOYMENT_TENANTS` | Explicit allowlist of reviewed deployed tenant UUIDs |
| `EVALUATION_TENANT_ID` | Reviewed evaluation owner within that allowlist |
| `NEON_AUTH_URL`, `NEON_AUTH_JWKS_URL` | Fixed trusted provider base and reviewed JWKS endpoint |
| `ALLOWED_HOSTS`, `ALLOWED_ORIGINS` | Exact application host and HTTPS browser origin |
| `QUEUE_ENABLED`, `QUEUE_REGION`, `CRON_SECRET` | Native queue delivery and private scheduler authentication |

Serving and evaluation roles are nonsuperuser and do not bypass RLS. The schema has 22 tables, with 20 tenant tables protected by forced RLS and transaction-local tenant context. Administrative setup, runtime access and evaluation migration use separate roles. Production app configuration contains no serving-owner administration URL. API traffic uses Neon connection pooling; evaluator session advisory locks require a direct connection rather than a transaction pooler.

Provider configuration requires email verification, exact production trusted origins, and session revocation on password reset. Localhost is not enabled on the production provider. Keep credentials in provider/Vercel secret stores and ignored local administration files. `/v1/auth/config` publishes only reviewed public sign-in metadata. `FIXTURE_CLOCK` is unset in production; stored cancellation deadlines use the actual UTC clock.

## Durable bounded evaluations

The hosted worker is a native private Vercel Queue consumer. Job creation and its outbox commit together in PostgreSQL. Publication failures retain durable work for retry; status polling and the authenticated daily reconciliation route can dispatch pending work. The Hobby cron schedule is daily at 03:00 UTC, so that fallback is not a minute-by-minute recovery guarantee. There is no always-running daemon.

Each hosted attempt evaluates the fixed **20-case** fixture workload. The child has a maximum 210-second execution window within the function's 270-second bound. A database mutex prevents overlapping evaluation resets, and lease tokens fence publication by stale workers. Application attempts are capped at three. Production creation defaults to five evaluations per actor per hour; request budgets and database waits are also bounded. These are application controls, not proof of unlimited free capacity or resistance to every denial-of-service pattern.

The evaluator verifies that runtime/admin URLs reach the same explicitly named isolated evaluation database and differ physically from serving. Only that evaluation database is reset. Its child uses fixture authentication and offline generation. Report JSON is published under the worker fence into a tenant-scoped database table, and HTML is rendered from the authorized stored report. Function-local disk holds only an owned temporary attempt workspace.

Two actual Vercel → private Queue → Neon executions completed: deterministic invariants **20/20** and V4 live-model fixture workflows **20/20**, each on the first attempt with zero runner errors/unauthorized effects, durable 20-case reports and unchanged serving business hashes. Their creation precondition was an explicitly reviewed internal service operation. This proves deployed queue/model/report execution, rather than an enrolled user's HTTP job-creation journey.

## Current and historical verification

- **Current cookie-fix backend:** 210 native Windows/PostgreSQL tests passed with no failures, errors or skips and unchanged inputs during the run. The 210-test GitHub-hosted Linux CI run is green. See [native proof](../artifacts/production-v0.3/checks/auth-cookie-fix/native-full.source-proof.json) and [GitHub Actions](https://github.com/nirajmohabey/resolvelab/actions).
- **Provider-cookie regression:** synthetic provider HTTP 200 responses with modern cookie attributes reproduced proxy 503 failures for signup, verification and sign-in before the fix. All 28 focused auth checks then passed, including session forwarding, expiry, prefix and malformed/size boundaries. These use synthetic credentials, separately from the live provider/account proof.
- **Frozen model compatibility:** the five pre-cookie reruns retained V4/V3/V1 **139/95/76 of 160**, baseline **19/20**, and invariants **20/20**. All 520 tenant-effect audits completed with zero runner errors or unauthorized effects observed. Weights, thresholds and cases were unchanged. [Reports](../artifacts/production-v0.3/evaluations/verification.json) retain the pre-cookie whole-backend hash. The [component attestation](../artifacts/production-v0.3/checks/auth-cookie-fix/ml-component-attestation.json) identifies the later auth-only changes and verifies unchanged evaluation/model/retrieval/worker inputs; it does not relabel those reports as new whole-backend executions.
- **Frontend/account screens:** 69 ResolveLab checks passed using real frontend files and intercepted provider/API responses. There were 35 ResolveLab and 30 OrderFlow account-form accessibility scans, **65 combined**, with no automated violations in those scans. Incomplete findings remain recorded. This is not full WCAG or complete assistive-technology conformance, and these intercepted runs are not authenticated production tenant journeys.
- **Recovery:** an exported-snapshot dump/restore matched all **22 tables**, including a real completed 20-case report and nonempty outbox. Tracking and atomic approval succeeded on a labeled restore-only probe. Source table hashes stayed unchanged; the owned restore database/private dump were removed. Fault regressions verify dump cleanup after snapshot-close or database-drop failure. See [recovery proof](../artifacts/production-v0.3/backup/verification.json).

The local recovery exercise is separate from a subsequent actual **Neon PostgreSQL 18.7 application-schema recovery**. That exported-snapshot dump/restore matched all 22 table hashes, restored 20 forced-RLS tables, verified restricted-role unscoped/tenant isolation, and removed its marked owned temporary database and private dump. The source was read-only. Managed authentication, warehouse and evaluation data were excluded. This does not establish provider PITR, full account recovery, cross-region failover or a recovery-time guarantee. See the [cloud recovery addendum](CLOUD-RECOVERY-2026-11-23.md).

The original v0.2.1 review, sealed model/data artifacts and earlier v0.2.0 Linux/container observations remain historical evidence in [REVIEW-2026-11-23.md](REVIEW-2026-11-23.md). Earlier hosting, IdP-provisioning and GitHub runner limits describe those checkpoints and are superseded by the current status above. The Caddy/Compose production template remains an alternative template, rather than the architecture of the live Vercel app.

## Operating limits

V4 still has disclosed synthetic routing failures: four wrong accepted routes, one of 40 unsupported requests accepted, and supported requests over-escalated. Synthetic accuracy and zero observed unauthorized effects are not customer accuracy or reliability guarantees. Model routing never authorizes an order mutation; human permission, ownership, evidence, digest and version checks remain mandatory.

The default generator is offline/deterministic. The optional OpenAI adapter is mock-tested, with no paid-provider latency, quality or cost benchmark. Free-plan ceilings, cold starts, email delivery limits, third-party availability and account configuration can affect service. Monitor the actual Vercel/Neon dashboards and error/job ledgers before increasing usage. There is no uptime SLA, measured production capacity claim, host/container OS audit, zero-vulnerability guarantee or claim that every accessibility requirement has been established.

Primary references: [Vercel Python Queues SDK](https://vercel.com/docs/queues/python-sdk), [Neon JWT contract](https://neon.com/docs/auth/guides/plugins/jwt), [JWT security best practices](https://www.rfc-editor.org/rfc/rfc8725.html).
