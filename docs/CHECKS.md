# Release verification

Measured on Windows 11, Python 3.12.14, PostgreSQL 17.12 and locked dependencies. All retailer data is synthetic. Report hashes and environment are recorded in `artifacts/release_manifest.json`.

| Check | Executed result |
|---|---|
| Real PostgreSQL suite | 95 passed; 25 repeated approval races, RLS, OIDC, immutability, order-state races, real process termination and fenced worker recovery |
| Deterministic workflows | 20/20 passed; zero runner errors and unauthorized effects; all 20 effect audits completed |
| v4 trained routing on handbook fixtures | 20/20 workflows; retained v1 baseline 19/20 |
| Frozen synthetic holdout | 139/160 workflow outcomes matched, 21 retained failures; zero runner errors or unauthorized effects; all 160 effect audits completed |
| Holdout routing | Macro-F1 0.8705; 139/160 correct; 104 accepted, including 4 wrong; unsupported false acceptance 1/40 |
| Same-workload comparison | v1 76/160, v3 95/160, v4 139/160 expected workflows; identical frozen cases, no tuning after final evaluation |
| Operator browser journey | 9 checks passed, desktop 1440x1000/mobile 390x844; approval, evidence, traces, evaluation worker/report viewer, tenant switching; no JavaScript errors |
| Browser sign-in | 9 scenarios passed with mocked HTTPS IdP and real RSA signatures: PKCE/state/nonce, signature/expiry rejection, disconnect and delayed-response race, invalid pending record, no fixture fallback |
| UI races | 10 intercepted-API scenarios passed on the actual DOM/module: out-of-order case/evidence/trace/report requests, expired sessions, disconnect and lost-response creation retry; zero page errors |
| Automated accessibility | 9 states; zero axe WCAG-tagged violations; keyboard dialog open/close/focus return, 200% text zoom and mobile overflow checks |
| Dependency advisories | OSV: 63 exact PyPI and 33 npm versions; official npm registry: zero matching known advisories at query time |
| Source exposure | Publishable text checked against generated local secrets and common key prefixes; no findings; limited scan, not exhaustive security review |
| Retrieval | 80 sections, 32 permitted, 16 held-out synthetic queries; selected dense recall@3 0.84375, recall@5 1.0, MRR@10 0.8094; tenant/access/retired canaries tested |
| Recovery | Real pg_dump/pg_restore in a disposable database; rollback preserved old case pins and restored the active pointer |
| Rebuild | Isolated retraining compares selected parameters and metrics; serialized bytes are not claimed identical |
| Production configuration | TLS/OIDC/restricted role template supplied and Compose schema validated; no executed production-container result |
| Provider adapter | Mock-tested structured contract; no paid-provider quality, latency or cost benchmark |
| Hosted CI / Docker | Earlier jobs were blocked before starting by GitHub's account runner restriction; no Linux/Docker pass is claimed without an executed result |
| Public backend | Not provisioned; the portfolio presents the actual recorded local demo |

The final v4 set contains 160 separately authored cases, 40 per intent and 87 semantic groups. It was frozen after v4 weights/thresholds and authored without inspecting v4 training data or model source. Group-aware intervals and paired results are in the v4 analysis artifacts. This remains agent-authored synthetic evidence rather than independent human validation. The related cases are not 160 independent customer trials.

Of the 21 failures, 17 supported requests were routed out of scope, three received the wrong supported intent, and one unsupported request was routed to tracking. Human-handoff recall was 28/40. All four wrong accepted routes remain reported. Authorization, ownership, current-order and approval checks are separate from routing; zero observed unauthorized effects is a bounded test result.

The 27 scope-rule denials are deterministic decisions, not model probabilities; calibration excludes them. Offline drafts are deterministic and narrow. Human citation-support review, real provider interoperability, production load behavior, remote merchant effects and cloud backup scheduling remain unmeasured.

Historical v3 100-case results (74/100 after three documented ownership-label corrections; original 71/100 retained) remain in their original directories and are not directly comparable to the new 160-case score. Automated checks do not establish complete WCAG conformance or freedom from vulnerabilities.
