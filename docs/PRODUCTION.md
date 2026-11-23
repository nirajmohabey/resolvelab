# Production controls and deployment boundary

> Version note: The 157-test Linux/Windows and container results below are historical v0.2.0 evidence. The current v0.2.1 native review passed 173 tests; current Linux and container reruns were unavailable. See [the current review](REVIEW-2026-11-23.md) and its versioned manifest.

The API now supports verified OIDC access tokens and the browser supports Authorization Code with PKCE, state, nonce and ID-token signature checks. The local fixture selector is disabled outside explicit local fixture mode. This is implemented and tested against synthetic signing keys; no real identity-provider tenant, public callback, cloud host or merchant connection was provisioned by this work.

`infra/compose.production.yml` prepares a different deployment from the local fixture Compose stack: Caddy terminates TLS, PostgreSQL has no published port, and the nonroot API has a read-only filesystem. Only the migration job receives `ADMIN_DATABASE_URL`. The API receives its restricted runtime URL. Both deployment files passed the official Compose JSON schema. A private Linux microVM independently passed the 157-test backend suite, and Docker 29.1.3 built/started the **local fixture** Compose stack with healthy PostgreSQL 17.12, API readiness HTTP 200 and a running worker; cleanup completed. That startup smoke does not establish execution of this separate production template, full containerized acceptance, image OS vulnerability scanning, public DNS/TLS or external-provider compatibility. GitHub-hosted jobs were blocked before starting by an account billing/runner restriction.

The selected managed target is **Vercel/Neon only**. A separate `resolvelab` Vercel project is linked, but no backend deployment is claimed. The explicitly free Neon marketplace plan still requires the account holder's terms acceptance before provisioning; no database connection or IdP enrollment should be inferred from the project link. The existing FastAPI/OIDC/PostgreSQL application is retained rather than represented as a different stack.

## Configure a real identity provider

Register a public browser client with Authorization Code, PKCE S256 and no client secret. Set its allowed callback to the exact application root, such as `https://support.example.com/`. Enable the token endpoint's CORS policy for that exact origin. Configure an API/resource audience and an access-token signing algorithm supported here: RS256 or ES256.

The API requires `APP_ENV=production`, `AUTH_MODE=oidc`, `DATABASE_URL`, exact `ALLOWED_HOSTS` and HTTPS `ALLOWED_ORIGINS`, `OIDC_ISSUER`, `OIDC_AUDIENCE` and a trusted HTTPS `OIDC_JWKS_URL`. The browser additionally needs `OIDC_CLIENT_ID`, `OIDC_AUTHORIZATION_URL`, `OIDC_TOKEN_URL` and an `OIDC_SCOPE` containing `openid`. These browser fields are public metadata; a provider client secret must never be placed in them. API-only OIDC can operate without the optional browser metadata; the UI then explains that sign-in has not been configured and offers no fixture fallback.

`GET /v1/auth/config` publishes only this allowlisted public configuration. The CSP allows the exact configured token/JWKS origins for browser fetches. Redirects during provider fetches are rejected. The Python verifier uses PyJWT's JWKS client, a fixed configured asymmetric algorithm, issuer, audience, expiry and issued-at checks, a 5-second key-fetch timeout, caching and refresh cooldown. The browser verifies state before exchanging a code, validates nonce and claims, and checks the ID-token signature. These are separate checks: an ID token is not sent to the API as its access token.

Access tokens stay in browser memory. Only the temporary PKCE verifier/state/nonce record uses session storage, for at most ten minutes; callback parameters and consumed pending data are removed. Disconnect clears workspace data and invalidates outstanding UI requests so a late response cannot restore protected case content. It disconnects this workbench; it does not claim to terminate a provider-wide session or revoke an already issued provider token.

## Enroll verified context

A valid external signature alone grants no organization or role. `external_identities` binds the exact verified issuer and subject to an existing active membership and a reviewed customer intake subject. Token-supplied tenant, role and customer fields are ignored. Membership/role activity is checked again in the database on requests and actions.

The registry has no direct runtime SELECT/INSERT/UPDATE grants. A small reviewed `SECURITY DEFINER` lookup, with a fixed search path, returns only the mapped context and active membership. Organization data still uses forced RLS and transaction-local tenant/actor settings. Enrollment is an administrative CLI, not a public signup endpoint. For a real operator handling multiple customers, a verified intake integration must establish each case's customer context; do not turn an unverified browser field into that context.

After reviewing the membership and customer binding, enroll through an administrative environment:

```text
python scripts/register_identity.py --issuer https://your-provider.example/ --subject verified-provider-subject --tenant-id YOUR_TENANT_UUID --actor-id YOUR_ACTOR_UUID --customer-subject verified-customer-subject
```

The issuer must exactly match `OIDC_ISSUER`. The command refuses conflicting remaps and revoked identities, and prints no token or credential. To revoke access, an administrator can set the mapping's `active` flag false or deactivate the membership. The next authenticated request checks the database again. New memberships and intake ownership remain reviewed administrative work.

## Prepare the deployment

Copy `infra/production.env.example` into ignored `.env.production`, then supply actual host/provider values and generated secrets. Use URL-safe random passwords or URL-encode them in the database URLs. The URLs must address the same private database, for example `db:5432/resolvelab`, with separate restricted runtime and migration users. Keep `.env.production` out of Git and restrict its host filesystem permissions.

Prepared commands, not executed on this machine:

```text
docker compose --env-file .env.production -f infra/compose.production.yml config
docker compose --env-file .env.production -f infra/compose.production.yml up --build -d
```

The migration job applies Alembic migrations and explicit runtime grants, refusing an existing runtime role with administrative flags. Its optional `SEED_SYNTHETIC_DEMO=true` initializes only the fictional retailer. The default is false. An empty production database will remain unready until reviewed organizations, published corpora, models, releases and identity mappings exist. Seeding sample data does not enroll a real external identity.

The production template intentionally does not enable the fixture evaluation worker or provide its administrative evaluation-database credentials to the API. Offline evaluation stays in its separate disposable database. The default response adapter remains deterministic; enabling the optional OpenAI adapter requires separately supplied credentials/model and measured provider behavior. No real order, refund, payment, email or external merchant integration is supplied.

Caddy is the only public ingress. The API trusts forwarded headers only from its fixed private gateway address. Raw HTTP is rejected for application routes in production; readiness remains available internally and is blocked at the public gateway. Do not add a direct API/database host port or broad forwarded-header trust. The template uses fixed component versions, including [Caddy 2.11.9](https://github.com/caddyserver/caddy/releases/tag/v2.11.9); verify image digests, OS advisories, resource limits, backups and provider configuration in the actual target before claiming a hosted production release.

The sample orders have explicit stored cancellation deadlines. Production uses the actual UTC clock and will escalate expired orders. Refreshing a protected synthetic demonstration requires reviewed new order data, not enabling `FIXTURE_CLOCK` in production.

## Request, data and recovery controls

API bodies require JSON, are bounded at 32 KiB while streaming, and have a five-second receipt timeout. Host and browser-origin allowlists are checked. Shared PostgreSQL request budgets return structured 429 responses and `Retry-After`; defaults are 30 fixture logins, 1,200 API requests per client address, and 300 authenticated requests per actor per minute. Client-supplied forwarding headers are not trusted by the application. Pool/connection, statement and lock waits are bounded. These controls are not a measured DDoS or capacity guarantee.

The v0.2 strict JSON precheck rejects invalid UTF-8, nesting beyond 32 levels, duplicate keys and nonfinite numbers before normal request validation. Message/reference NULs and noninteger/out-of-range versions are rejected. Quotas require bounded integers at startup; body timeout permits a bounded numeric value. Provider responses and admitted corpus evidence have independent byte/count/vector bounds. Readiness checks that the active corpus is usable rather than treating a published pointer alone as sufficient. See [the hardening note](HARDENING.md).

Headers include a CSP that denies framing, objects and inline script, plus nosniff, frame denial, a permissions policy and production HSTS. Existing dynamic inline styles remain allowed. The local Swagger page has a narrower documentation-only script exception; interactive docs are disabled in production. Unknown origins do not receive wildcard CORS or credential permission.

Published/retired policy content, model records, release records, trace events, approvals, actions and stored idempotent outcomes are protected by database triggers. A changed policy/model requires a new version. The application also compares evidence snapshots at finalization and approval. It rechecks tracking ownership/state after inference and cancellation cutoff before preparing a proposal; approval rechecks all permissions and versions under locks. No provider network request runs while those order/proposal locks are held.

Evaluation jobs use durable leases, bounded attempts, an independent child-process database mutex and fenced publication. A dead worker cannot publish over a later owner. Attempt files are isolated; completed reports must match the queued release, dataset hash and mode before an atomic canonical-file replacement and acknowledged result. A parent worker's death cannot allow a surviving evaluator to overlap a new evaluator's database reset. Timeout cleanup terminates only the exact owned subprocess tree, including Windows venv-launcher descendants.

Windows background control now uses a real-interpreter PID and unique launch-instance handshake. `scripts/background.ps1 start|status|stop` validates checkout/component ownership before a process-tree stop. It does not terminate processes by a generic Python name.

## Executed evidence

The following were run against the private native PostgreSQL environment, without resetting the serving inbox:

- **157 PostgreSQL acceptance tests passed on Windows** in `artifacts/checks/extended-acceptance.xml` and **157 independently on clean Linux** in `artifacts/checks/linux-acceptance.xml`. Coverage includes concurrent approval/decline decisions, verified-customer idempotency, hard interpreter exits, database-session termination, fenced worker recovery, disposable destination checks, corpus publication/MVCC guards and input/provider bounds. The earlier 95-test `production-acceptance.xml` is retained v0.1 evidence.
- **9 browser OIDC scenarios passed** in `artifacts/security/oidc-browser-results.json`: real RSA signatures with mocked HTTPS provider/API, PKCE/state/nonce, invalid state/nonce/signature/expiry, missing provider configuration, forbidden fixture fallback, malformed pending timestamps, and delayed protected responses after disconnect. The original reproduced disconnect failure is retained in `oidc-browser-before-fix.json`.
- A fresh disposable database applied every migration, seeded, passed readiness, logged in and resolved trained tracking: `artifacts/checks/clean-install.json`.
- A real dump restored into a new disposable database, matched the original order snapshot, and completed tracking plus atomic cancellation on a labeled restore-only probe: `artifacts/backup/restore-report.json`.
- Ownership-safe restart preserved all seven seeded cases (six Alpha, one Beta) and five orders: `artifacts/checks/restart-preserved-state.json`.
- OSV checked **63 PyPI and 33 npm exact lockfile versions**; OSV and the official npm audit returned **zero known advisories** at the saved query time. See `artifacts/security/audit-summary.json`. This is not proof of security and excludes built container OS packages.
- Both Compose documents passed the official specification schema: `artifacts/security/compose-schema-validation.json`. This does not substitute for building or running the containers.
- The local fixture Compose stack built/started inside a private Linux microVM using Docker 29.1.3, PostgreSQL 17.12 became healthy, API readiness returned HTTP 200 and the worker was running: `artifacts/checks/linux-docker-smoke.json`. The owned stack/volumes were removed afterward. This is a startup smoke, not a full containerized suite or production-template result.
- Linux serving consistency matched all 128 frozen intent/acceptance decisions within a 1e-6 score tolerance; Linux dump/restore completed tracking and approval without writing the source database. See `artifacts/checks/linux-serving-consistency.json` and `artifacts/backup/linux-restore-report.json`.

Reproduce the backend checks with `python scripts/run_tests.py`, the isolated browser checks with `node scripts/oidc-browser-check.mjs`, the fresh installation with `python scripts/check_clean_install.py`, restoration with `python scripts/backup_check.py`, and advisory queries with `python artifacts/security/audit_dependencies.py`. Use the environment's configured Python executable; on Windows the project executable is `.venv\Scripts\python.exe`.

References: [PyJWT JWT/JWKS usage](https://pyjwt.readthedocs.io/en/stable/usage.html), [JWT security best practices](https://www.rfc-editor.org/rfc/rfc8725.html), [PostgreSQL security-definer function guidance](https://www.postgresql.org/docs/17/sql-createfunction.html), and the [Compose specification](https://github.com/compose-spec/compose-spec/blob/main/schema/compose-spec.json).
