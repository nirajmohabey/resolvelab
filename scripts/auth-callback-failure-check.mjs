/** Callback cleanup regressions using the real bundled browser auth adapter.
 * All API traffic is intercepted; no backend or identity provider is contacted.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { ROOT, startHarness, scenario, finishHarness } from "./ui-race-check.mjs";

const output = path.join(ROOT, "artifacts/browser/auth-callback-failure-results.json");
const pendingKey = "resolvelab.oidc.pending.v1";
const sources = ["web/auth-client-src.js", "web/auth-client.js"];
const hashes = async () => Object.fromEntries(await Promise.all(sources.map(async filename => [
  filename, createHash("sha256").update(await readFile(path.join(ROOT, filename))).digest("hex"),
])));
const initialHashes = await hashes();
const evidence = new Map();
const failures = [
  { name: "HTTP 503 config callback clears code state and pending PKCE", reply: { status: 503, body: { message: "Synthetic unavailable config" } }, notice: /temporarily unavailable/i },
  { name: "network rejected config callback clears code state and pending PKCE", reply: { abort: true }, notice: /failed to fetch|networkerror|network request failed/i },
  { name: "malformed config callback clears code state and pending PKCE", reply: { body: { mode: "invalid" } }, notice: /configuration is invalid/i },
];

try {
  await startHarness();
  for (const failure of failures) {
    const observed = [];
    await scenario(failure.name, "fixture", async ({ page, requests, origin }) => {
      await page.locator("#notice").filter({ hasText: failure.notice }).waitFor();
      const current = new URL(page.url());
      assert.equal(current.origin, origin);
      assert.equal(current.pathname, "/");
      assert.equal(current.search, "", "Callback parameters must be removed even when config fails");
      assert.equal(current.hash, "");
      assert.equal(await page.evaluate(key => sessionStorage.getItem(key), pendingKey), null,
        "A failed callback must consume its pending PKCE state");
      const configRequests = requests.filter(request => request.pathname === "/v1/auth/config").length;
      const sessionRequests = requests.filter(request => ["/v1/session", "/v1/demo/session"].includes(request.pathname)).length;
      const tokenOrSessionRequests = observed.filter(url => /\/(?:token|session)(?:\/|$)/i.test(new URL(url).pathname)).length;
      assert.ok(configRequests >= 1, "The mocked configuration failure must actually be exercised");
      assert.equal(sessionRequests, 0,
        "A failed callback must not start an application session");
      assert.equal(tokenOrSessionRequests, 0,
        "A config failure must not reach any token or session endpoint");
      assert.equal(await page.locator("#caseList .case-row").count(), 0);
      evidence.set(failure.name, { callback_query_cleared: true, pending_pkce_removed: true,
        config_requests: configRequests, application_session_requests: sessionRequests,
        token_or_session_requests: tokenOrSessionRequests });
    }, {
      signedIn: false,
      urlSuffix: "/?code=synthetic-sensitive-code&state=synthetic-callback-state",
      prepare: async ({ page, setReply }) => {
        page.on("request", request => observed.push(request.url()));
        setReply("GET", "/v1/auth/config", () => failure.reply);
        await page.addInitScript(key => {
          if (location.search.includes("code=")) sessionStorage.setItem(key, JSON.stringify({
            createdAt: Date.now(), issuer: "https://idp.example.test/", clientId: "synthetic-public-client",
            redirectUri: new URL("/", location.origin).href, verifier: "synthetic-private-code-verifier",
            state: "synthetic-callback-state", nonce: "synthetic-private-nonce",
          }));
        }, pendingKey);
      },
    });
  }
} finally {
  const report = await finishHarness(output, failures.length);
  const finalHashes = await hashes();
  report.initial_auth_sha256 = initialHashes;
  report.auth_sha256 = finalHashes;
  report.source_stable &&= JSON.stringify(initialHashes) === JSON.stringify(finalHashes);
  report.scope = "Real bundled browser auth adapter; callback URL and pending PKCE cleanup during HTTP 503, network rejection and malformed config; all API calls intercepted; no backend or identity provider contacted";
  report.results.forEach(result => {
    result.authentication = "real bundled browser auth adapter; mocked config failure";
    if (evidence.has(result.name)) result.evidence = evidence.get(result.name);
  });
  await writeFile(output, JSON.stringify(report, null, 2) + "\n");
  if (!report.source_stable) process.exitCode = 1;
}
