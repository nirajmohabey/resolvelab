# Handbook retrofit evidence

This is a simulated delivery exercise dated November 23, 2026 (America/New_York), starting from commit `1207948cc6684830fed29d9c73ca47ad38352120`. Timestamps in JSON use UTC. No customer or human reviewer participated.

Read the [customer discovery, identity/data map, acceptance and handoff](../../docs/CUSTOMER_DELIVERY.md) first.

| Evidence | Outcome and boundary |
|---|---|
| [Documentation consistency](docs-consistency.json) | Pinned before/after corrections to case study, operations and video narrative; agent review |
| [Real UI/API/PostgreSQL before](before/summary.json) | 5/6: a genuine saved provider failure exposed a missing Retry control |
| [Real UI/API/PostgreSQL after](after/summary.json) | 6/6 after the frontend fix; same case recovery, no unexpected API errors or page JavaScript errors |
| [After isolation/cleanup](after/isolation.json) | Owned database removed and cluster stopped; protected local files unchanged; zero actions and unchanged order snapshots |
| [Interrupted replay](after-interrupted-owned-process-timeout/isolation.json) | Windows owned-process shutdown timeout after five checks; not counted as a complete application result; retained rather than hidden |
| [Portable six-case harness](harness/README.md) | Reuses existing fixtures/migrations; guarded disposable loopback PostgreSQL; no production access |
| [Focused retry before](retry-regression/before/results.json) | 2/7 pass; five failures reproduce missing control; APIs intercepted |
| [Focused retry after](retry-regression/after/results.json) | 8/8 pass, including the added initial503 inbox/dashboard check, permissions, current version, duplicate guard and disconnect |
| [Focused observations](retry-regression/after/observations.json) | 375px mobile control geometry and exact source binding; synthetic intercepted APIs/OIDC |
| [Twenty-case answer-support preparation](support-review/README.md) | AI-assisted archive review and blank human worksheet; evidence gaps and known routing failure retained |
| [Scoped verification](verification.json) | Document links/hashes, test denominators and unchanged protected backend/model/data/dependency inputs |

The UI correction is bounded to `web/app.js`: render an authorized Retry control for a failed case with a nonempty saved response, bind the existing handler, and refresh persisted inbox/dashboard state after a failed resolution. No automatic retry or new authorization is introduced. The focused regression is part of the existing [CI workflow](../../.github/workflows/ci.yml).

The completed six-case runs each use five distinct synthetic cases because proposing and declining a cancellation share one lifecycle. The deliberately injected generation failure returns503; the browser also requests a missing favicon404. Both are recorded. Neither is silently counted as an unexpected application error.

The original full-suite, model and cloud results retain their original release provenance. This packet does not rerun or relabel the 210-test proof, change the frozen 139/160 workflow score, or establish hosted owner sign-in/email delivery. No merchant mutation, paid-provider benchmark, business savings, customer adoption or human claim-support result is claimed. The [current production boundaries](../../docs/PRODUCTION.md) still apply.
