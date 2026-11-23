# Security review evidence

The dependency scan at **2026-11-23 09:37:47 UTC** queried **63 exact PyPI versions** from the universal `uv.lock` and **33 npm versions** from `package-lock.json`, including locked platform variants. The OSV API returned no matching advisories. The official npm registry audit independently reported zero known vulnerabilities. This includes PyJWT 2.15.1, cryptography 50.0.2, cffi 2.1.1, pycparser 3.12, OAuth4WebAPI 3.8.9, Playwright, axe and esbuild after production authentication and accessibility support were added.

Inspect `audit-summary.json` for input lockfile hashes and limitations, `osv-audit.json` for every exact-version query/result, and `npm-audit.json` for the registry response. `initial/` preserves the earlier pre-OIDC scan. No advisory was suppressed and no dependency was automatically upgraded by this audit.

From the checkout root, rerun with:

```powershell
.venv\Scripts\python.exe artifacts/security/audit_dependencies.py
```

The script uses Python's standard library and npm; no audit tool is installed into the application. It returns nonzero for known findings or a failed query. Package names and versions are sent to the public advisory services, without application data or credentials.

Primary service references: [OSV API](https://google.github.io/osv.dev/api/), [OSV batch-version query](https://google.github.io/osv.dev/post-v1-querybatch/), and [npm audit documentation](https://docs.npmjs.com/cli/v11/commands/npm-audit/). Requests target `https://api.osv.dev/v1/querybatch` and `https://registry.npmjs.org`.

No known matching advisory at query time is not proof of security. This checks the resolved application dependency graph, including development and platform-specific PyPI entries. It does not scan a built container's operating-system packages, evaluate authorization or deployment configuration, or inspect pretrained model contents. Recheck after lockfile changes and as advisory feeds change.

## Static review

Bandit 1.9.4 scanned `src/resolvelab` and `scripts` at **2026-11-23 09:46:52 UTC**, producing **154 findings: zero high, five medium and 149 low**. The unfiltered report is `bandit.json`; `static-triage.json` records all category counts, the five medium call sites and their reviewed trust boundaries. Nothing was suppressed. The medium findings concern fixed build-time dataset URLs, two trusted local JUnit parses and two SQL constructions whose values use bind parameters. These are scoped call-site assessments; neither the downloader nor XML parser is approved for arbitrary untrusted inputs. Low findings include verification assertions, maintenance subprocesses, deterministic dataset shuffles and protocol field names.

Ruff 0.17.6 checked `src`, `scripts`, `migrations` and `tests` for undefined-name rules F821/F822/F823 and returned no findings in `ruff-undefined-names.json`. This focused check is not a full lint or security review. Audit tools were installed in a separate tooling environment, without adding them to application dependencies.

## Behavioral and source checks

The v0.2 suite passed **157 tests on native Windows/PostgreSQL 17.12 and 157 on clean Linux/PostgreSQL 18.6**, both using Python 3.12.14. See `../checks/extended-acceptance.xml`, `../checks/linux-acceptance.xml` and `../checks/linux-environment.json`. Coverage includes real transaction races and process recovery, tenant/role boundaries, disposable database verification, corpus/vector bounds, malformed JSON, bounded provider responses and operator follow-up/decline decisions. The expanded live browser run passed11 journeys against a fresh disposable fixture database, preserving the original serving database. Nine OIDC and26 additional intercepted-API scenarios also passed; inspect their separately labeled scopes.

`runtime-acceptance.json` records **13 passing checks** using in-process HTTP with a fresh real PostgreSQL audit database. Sixty-four simultaneous create attempts preserved one case identity. Twenty-four parallel approval responses replayed the same action ID and committed exactly one effect. These are correctness probes on synthetic fixtures, not hosted throughput measurements.

`source-exposure-check.json` at **2026-11-23 09:47:21 UTC** checked **378 publishable text files** against **seven unique local secret values** from ignored application, container and Vercel-linked environment files and common credential prefixes, with no findings. It reads the current Git index and unignored working files; it excludes binary contents, deleted history and external services. Re-run `python scripts/check_source_exposure.py` after final release edits. Its output names findings without printing credential values.

The retained `intrusive-audit-before.json`, `input-contract-before.json` and earlier browser before-fix reports document reproduced problems before hardening. See [the hardening note](../../docs/HARDENING.md) for the resulting boundaries. The private Linux Docker startup smoke passed separately in `../checks/linux-docker-smoke.json`; it does not establish full containerized acceptance or an OS package audit. No executed check establishes exhaustive vulnerability absence, real IdP interoperability, a public production backend or built-image OS security. Neon marketplace terms, final hosting/identity setup and GitHub account runner restrictions are distinct external steps.
