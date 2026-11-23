# Dependency advisory audit

The final scan at **2026-11-23 08:14:03 UTC** queried **63 exact PyPI versions** from the universal `uv.lock` and **33 npm versions** from `package-lock.json`, including locked platform variants. The OSV API returned no matching advisories. The official npm registry audit independently reported zero known vulnerabilities. This includes PyJWT 2.15.1, cryptography 50.0.2, cffi 2.1.1, pycparser 3.12, OAuth4WebAPI 3.8.9, Playwright, axe and esbuild after production authentication and accessibility support were added.

Inspect `audit-summary.json` for input lockfile hashes and limitations, `osv-audit.json` for every exact-version query/result, and `npm-audit.json` for the registry response. `initial/` preserves the earlier pre-OIDC scan. No advisory was suppressed and no dependency was automatically upgraded by this audit.

From the checkout root, rerun with:

```powershell
.venv\Scripts\python.exe artifacts/security/audit_dependencies.py
```

The script uses Python's standard library and npm; no audit tool is installed into the application. It returns nonzero for known findings or a failed query. Package names and versions are sent to the public advisory services, without application data or credentials.

Primary service references: [OSV API](https://google.github.io/osv.dev/api/), [OSV batch-version query](https://google.github.io/osv.dev/post-v1-querybatch/), and [npm audit documentation](https://docs.npmjs.com/cli/v11/commands/npm-audit/). Requests target `https://api.osv.dev/v1/querybatch` and `https://registry.npmjs.org`.

No known matching advisory at query time is not proof of security. This checks the resolved application dependency graph, including development and platform-specific PyPI entries. It does not scan a built container's operating-system packages, evaluate authorization or deployment configuration, or inspect pretrained model contents. Recheck after lockfile changes and as advisory feeds change.
