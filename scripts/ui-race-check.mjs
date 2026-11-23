/** Read-only workbench race regressions. All /v1 requests are intercepted.
 * Uses the real index, CSS and app module; OIDC scenarios substitute only the
 * authentication adapter so no identity provider or backend is contacted.
 * Run: node scripts/ui-race-check.mjs (installed Google Chrome required).
 */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = fileURLToPath(new URL("../", import.meta.url));
const OUTPUT = path.join(ROOT, "artifacts/browser/ui-race-results.json");
const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const EVAL_A = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const EVAL_B = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
const RELEASE = "ffffffff-ffff-4fff-8fff-ffffffffffff";
const initialAppHash = createHash("sha256").update(await readFile(path.join(ROOT, "web/app.js"))).digest("hex");
const reviewedWebFiles = ["web/app.js", "web/index.html", "web/app.css", "web/auth-client-src.js", "web/auth-client.js", "package.json", "package-lock.json"];
const webHashes = async () => Object.fromEntries(await Promise.all(reviewedWebFiles.map(async file => [file, createHash("sha256").update(await readFile(path.join(ROOT, file))).digest("hex")])));
const initialWebHashes = await webHashes();
const principal = { tenant_id: "11111111-1111-4111-8111-111111111111", role: "operator", subject: "ui-race-harness" };
const organization = { display_name: "Read-only race harness" };
const cases = [A, B].map((case_id, i) => ({
  case_id, message: `Harness case ${i ? "B" : "A"}`, order_ref: i ? "A-101" : "A-100",
  customer_subject: `synthetic-customer-${i ? "B" : "A"}`, state: "new", version: 1,
  created_at: "2026-11-23T12:00:00Z", resolution: null, proposals: [], actions: [],
}));
const nameOf = id => id === A ? "A" : "B";
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
async function within(promise, label) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`Timed out waiting for ${label}`)), 8000); })]); }
  finally { clearTimeout(timer); }
}
const files = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/static/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/static/auth-client.js", ["auth-client.js", "text/javascript; charset=utf-8"]],
  ["/static/app.css", ["app.css", "text/css; charset=utf-8"]],
]);
const server = createServer(async (request, response) => {
  const file = files.get(new URL(request.url, "http://127.0.0.1").pathname);
  if (!file) { response.writeHead(404); response.end(); return; }
  try {
    response.writeHead(200, { "Content-Type": file[1], "Cache-Control": "no-store" });
    response.end(await readFile(path.join(ROOT, "web", file[0])));
  } catch { response.writeHead(500); response.end(); }
});
let origin;
const results = [];
let browser;

function defaultReply(method, pathname) {
  if (pathname === "/v1/auth/config") return { mode: "fixture", local: true, demo: true, configured: true };
  if (pathname === "/v1/demo/session") return { access_token: "synthetic-fixture-token", principal, organization };
  if (pathname === "/v1/session") return { principal, organization };
  if (pathname === "/v1/cases" && method === "GET") return { items: cases };
  if (pathname === "/v1/dashboard") return { total_cases: 2, states: { new: 2 } };
  if (pathname === "/v1/releases") return { items: [{ release_id: RELEASE, model_version: "synthetic-race-release", corpus_version: "synthetic-policy", active: true }] };
  if (pathname === "/v1/evaluations") return { items: [EVAL_A, EVAL_B].map(evaluation_id => ({ evaluation_id, release_id: RELEASE, mode: "deterministic_invariants", state: "completed", metrics: { workflow_success: { numerator: 1, denominator: 1 }, runner_errors: 0 } })) };
  const report = pathname.match(/^\/v1\/evaluations\/([^/]+)\/report$/);
  if (report) return { model_version: report[1] === EVAL_A ? "REPORT-A" : "REPORT-B", mode: "synthetic-harness", generated_at: "2026-11-23T12:00:00Z", metrics: { workflow_success: { numerator: 1, denominator: 1 }, runner_errors: 0, unauthorized_effect_count: 0 }, cases: [] };
  const match = pathname.match(/^\/v1\/cases\/([^/]+)(?:\/(evidence|trace|resolve))?$/);
  if (match) {
    const [, id, action] = match;
    if (!action && method === "GET") return structuredClone(cases.find(item => item.case_id === id));
    if (action === "evidence") return { items: [{ title: `EVIDENCE-${nameOf(id)}`, locator: "synthetic-policy#test", body: `Permitted evidence for case ${nameOf(id)}`, chunk_id: `chunk-${nameOf(id)}` }] };
    if (action === "trace") return { events: [{ stage: `TRACE-${nameOf(id)}`, outcome: "ok", details: { case: nameOf(id) }, duration_ms: 1 }] };
    if (action === "resolve") return { state: "triaged" };
  }
  throw new Error(`Unexpected mock endpoint: ${method} ${pathname}`);
}

export async function scenario(name, mode, run, options = {}) {
  const started = performance.now();
  const context = await browser.newContext({ viewport: options.viewport || { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.setDefaultNavigationTimeout(10000);
  const pageErrors = [], harnessErrors = [], gates = [], requests = [];
  const replies = new Map();
  page.on("pageerror", error => pageErrors.push(error.message));
  if (mode === "oidc") {
    await page.route("**/static/auth-client.js", route => route.fulfill({ contentType: "text/javascript", body: `
      export async function readAuthentication() { return { config: { mode: "oidc", configured: true }, token: "synthetic-oidc-token", expiresAt: Date.now() + 600000 }; }
      export async function beginSignIn() {}
      export function clearAuthentication() { window.__raceAuthCleared = true; }
      export async function signInWithPassword() { throw new Error("Not a Neon scenario"); }
      export async function signOutAuthentication() {}
      export async function accountAction() { throw new Error("Not a Neon scenario"); }
    ` }));
  }
  await page.route("**/static/reports/summary.json", route => route.fulfill({ contentType: "application/json", body: "{}" }));
  await page.route("**/v1/**", async route => {
    const method = route.request().method(), pathname = new URL(route.request().url()).pathname;
    requests.push({ method, pathname });
    const gate = gates.find(item => !item.used && item.method === method && item.pathname === pathname && (!item.match || item.match(route.request())));
    try {
      if (gate) { gate.used = true; gate.observed.resolve(); await gate.release.promise; }
      if (gate?.abort) { await route.abort("failed"); return; }
      const custom = replies.get(`${method} ${pathname}`);
      const reply = custom ? await custom(route.request()) : null;
      if (reply?.abort) { await route.abort("failed"); return; }
      let body = gate?.body ?? reply?.body ?? defaultReply(method, pathname);
      if (options.role && ["/v1/demo/session", "/v1/session"].includes(pathname)) body = { ...body, principal: { ...body.principal, role: options.role } };
      await route.fulfill({ status: gate?.status || reply?.status || 200, contentType: "application/json", body: JSON.stringify(body) });
      gate?.finished.resolve();
    } catch (error) { if (!page.isClosed()) harnessErrors.push(error.message); }
  });
  const hold = (method, pathname, response = {}) => {
    const gate = { method, pathname, used: false, observed: deferred(), release: deferred(), finished: deferred(), ...response };
    gates.push(gate); return gate;
  };
  const release = async gate => { gate.release.resolve(); if (!gate.abort) await within(gate.finished.promise, "held response"); await page.waitForTimeout(100); };
  const waitHeader = label => page.waitForFunction(expected => document.querySelector("#detail h2")?.textContent === `Harness case ${expected}`, label);
  const waitBody = text => page.waitForFunction(expected => document.querySelector("#detailBody")?.textContent.includes(expected), text);
  const setReply = (method, pathname, handler) => replies.set(`${method} ${pathname}`, handler);
  try {
    await options.prepare?.({ page, hold, setReply });
    await page.goto(origin + (options.urlSuffix || ""), { waitUntil: "networkidle" });
    if (options.signedIn !== false) await waitHeader("A");
    await run({ page, hold, release, waitHeader, waitBody, setReply, requests, origin });
    await page.waitForTimeout(100);
    assert.deepEqual(pageErrors, [], "Uncaught browser errors");
    assert.deepEqual(harnessErrors, [], "Harness transport errors");
    results.push({ name, passed: true, authentication: mode === "oidc" ? "synthetic OIDC adapter; real workbench session handling" : "real fixture auth adapter", api_requests_intercepted: requests.length, page_errors: pageErrors, duration_ms: Math.round(performance.now() - started) });
  } catch (error) {
    results.push({ name, passed: false, error: error.message, page_errors: pageErrors, harness_errors: harnessErrors, api_requests_intercepted: requests.length, duration_ms: Math.round(performance.now() - started) });
  } finally {
    gates.forEach(gate => gate.release.resolve());
    await context.close();
  }
}

export async function startHarness() {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ channel: "chrome", headless: true });
}
export async function finishHarness(output = OUTPUT, expected = 10) {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
  const appBytes = await readFile(path.join(ROOT, "web/app.js"));
  const finalAppHash = createHash("sha256").update(appBytes).digest("hex");
  const finalWebHashes = await webHashes();
  const report = { generated_at: new Date().toISOString(), read_only: true, backend_connected: false, initial_app_sha256: initialAppHash, app_sha256: finalAppHash, initial_source_hashes: initialWebHashes, source_hashes: finalWebHashes, source_stable: finalAppHash === initialAppHash && JSON.stringify(initialWebHashes) === JSON.stringify(finalWebHashes), scope: "Actual workbench DOM and application module; all API responses intercepted; creation is persisted only in a mock Map; OIDC adapter is synthetic, not an IdP interoperability test", passed: results.filter(result => result.passed).length, total: results.length, failures: results.filter(result => !result.passed).length, results };
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report, null, 2));
  if (report.failures || report.total !== expected || !report.source_stable) process.exitCode = 1;
  return report;
}
export { ROOT, A, B, C, EVAL_A, EVAL_B, RELEASE, cases, principal, organization, within };

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
try {
  await startHarness();
  await scenario("latest case selection wins over an older case response", "fixture", async ({ page, hold, release, waitHeader }) => {
    const old = hold("GET", `/v1/cases/${A}`);
    await page.locator(`[data-case="${A}"]`).click(); await within(old.observed.promise, "older case request");
    await page.locator(`[data-case="${B}"]`).click(); await waitHeader("B");
    await release(old);
    assert.equal(await page.locator("#detail h2").textContent(), "Harness case B");
    assert.equal(await page.locator(".case-row.selected").getAttribute("data-case"), B);
  });
  await scenario("late case A evidence cannot replace case B evidence", "fixture", async ({ page, hold, release, waitHeader, waitBody }) => {
    const old = hold("GET", `/v1/cases/${A}/evidence`);
    await page.locator('[data-tab="evidence"]').click(); await within(old.observed.promise, "older evidence request");
    await page.locator(`[data-case="${B}"]`).click(); await waitHeader("B"); await waitBody("EVIDENCE-B");
    await release(old);
    assert.match(await page.locator("#detailBody").textContent(), /EVIDENCE-B/);
    assert.doesNotMatch(await page.locator("#detailBody").textContent(), /EVIDENCE-A/);
  });
  await scenario("late case A trace cannot replace case B trace", "fixture", async ({ page, hold, release, waitHeader, waitBody }) => {
    const old = hold("GET", `/v1/cases/${A}/trace`);
    await page.locator('[data-tab="trace"]').click(); await within(old.observed.promise, "older trace request");
    await page.locator(`[data-case="${B}"]`).click(); await waitHeader("B"); await waitBody("TRACE-B");
    await release(old);
    assert.match(await page.locator("#detailBody").textContent(), /TRACE-B/);
    assert.doesNotMatch(await page.locator("#detailBody").textContent(), /TRACE-A/);
  });
  await scenario("late evidence cannot overwrite the newly selected trace tab", "fixture", async ({ page, hold, release, waitBody }) => {
    const old = hold("GET", `/v1/cases/${A}/evidence`);
    await page.locator('[data-tab="evidence"]').click(); await within(old.observed.promise, "older evidence tab request");
    await page.locator('[data-tab="trace"]').click(); await waitBody("TRACE-A");
    await release(old);
    assert.match(await page.locator("#detailBody").textContent(), /TRACE-A/);
    assert.doesNotMatch(await page.locator("#detailBody").textContent(), /EVIDENCE-A/);
  });
  await scenario("late evidence error cannot replace another case", "fixture", async ({ page, hold, release, waitHeader, waitBody }) => {
    const old = hold("GET", `/v1/cases/${A}/evidence`, { status: 503, body: { message: "OLD-CASE-A-ERROR" } });
    await page.locator('[data-tab="evidence"]').click(); await within(old.observed.promise, "older evidence error request");
    await page.locator(`[data-case="${B}"]`).click(); await waitHeader("B"); await waitBody("EVIDENCE-B");
    await release(old);
    assert.match(await page.locator("#detailBody").textContent(), /EVIDENCE-B/);
    assert.doesNotMatch(await page.locator("#detailBody").textContent(), /OLD-CASE-A-ERROR/);
  });
  for (const action of ["evidence", "resolve"]) {
    await scenario(`OIDC 401 during ${action} clears the workspace without a page error`, "oidc", async ({ page, hold, release }) => {
      const pending = hold(action === "resolve" ? "POST" : "GET", `/v1/cases/${A}/${action}`, { status: 401, body: { code: "invalid_token", message: "Synthetic expired token" } });
      await page.locator(action === "resolve" ? "#resolveCase" : '[data-tab="evidence"]').click(); await within(pending.observed.promise, "expiring request");
      await release(pending);
      await page.locator("#signIn").waitFor({ state: "visible" });
      assert.match(await page.locator("#detail").textContent(), /Sign in to continue/);
      assert.equal(await page.locator("#detailBody").count(), 0);
      assert.equal(await page.locator("#caseList .case-row").count(), 0);
      assert.equal(await page.evaluate(() => window.__raceAuthCleared), true);
    });
  }
  await scenario("a rejected old request cannot redraw after disconnect", "oidc", async ({ page, hold, release }) => {
    const pending = hold("GET", `/v1/cases/${A}/evidence`, { abort: true });
    await page.locator('[data-tab="evidence"]').click(); await within(pending.observed.promise, "request before disconnect");
    await page.locator("#signOut").click(); await release(pending);
    await page.locator("#signIn").waitFor({ state: "visible" });
    assert.match(await page.locator("#detail").textContent(), /Sign in to continue/);
    assert.equal(await page.locator("#detailBody").count(), 0);
  });
  await scenario("lost create response retries the same idempotency key and creates one case", "fixture", async ({ page, setReply }) => {
    const persisted = new Map(), keys = [];
    const created = { ...cases[0], case_id: C, message: "Create request for the read-only browser harness." };
    setReply("POST", "/v1/cases", request => {
      const key = request.headers()["idempotency-key"];
      keys.push(key);
      assert.equal(request.postDataJSON().message, created.message);
      if (!persisted.has(key)) persisted.set(key, { ...created });
      if (keys.length === 1) return { abort: true }; // Commit happened; only the response is lost.
      return { status: 201, body: persisted.get(key) };
    });
    setReply("GET", "/v1/cases", () => ({ body: { items: [...cases, ...persisted.values()] } }));
    setReply("GET", `/v1/cases/${C}`, () => ({ body: created }));
    await page.locator("#newCase").click();
    await page.locator("#message").fill(created.message);
    await page.locator("#orderRef").fill("A-100");
    await page.locator("#createSubmit").click();
    await page.locator("#formError").waitFor({ state: "visible" });
    await page.waitForFunction(() => !document.querySelector("#createSubmit").disabled);
    assert.equal(await page.locator("#caseDialog").evaluate(dialog => dialog.open), true);
    await page.locator("#createSubmit").click();
    await page.waitForFunction(message => document.querySelector("#detail h2")?.textContent === message, created.message);
    assert.equal(keys.length, 2);
    assert.ok(keys[0], "An idempotency key must be present");
    assert.equal(keys[1], keys[0], "A retry of the same logical creation must reuse its key");
    assert.equal(persisted.size, 1, "The mock backend must contain one created case");
    assert.equal(await page.locator("#caseDialog").evaluate(dialog => dialog.open), false);
  });
  await scenario("late report A cannot replace a newly opened report B", "fixture", async ({ page, hold, release }) => {
    await page.locator('[data-view="evaluation"]').click();
    await page.locator(`[data-evaluation-report="${EVAL_A}"]`).waitFor();
    const old = hold("GET", `/v1/evaluations/${EVAL_A}/report`);
    await page.locator(`[data-evaluation-report="${EVAL_A}"]`).click();
    await within(old.observed.promise, "older report request");
    await page.locator("#closeEvaluationDialog").click();
    await page.locator(`[data-evaluation-report="${EVAL_B}"]`).click();
    await page.waitForFunction(() => document.querySelector("#evaluationDetails")?.textContent.includes("REPORT-B"));
    await release(old);
    assert.equal(await page.locator("#evaluationDialog").evaluate(dialog => dialog.open), true);
    assert.match(await page.locator("#evaluationDetails").textContent(), /REPORT-B/);
    assert.doesNotMatch(await page.locator("#evaluationDetails").textContent(), /REPORT-A/);
  });
} finally {
  await finishHarness();
}
}
