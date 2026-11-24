/** Focused persisted-failure retry regression. Every /v1 request is intercepted.
 * The actual workbench DOM/module is used; OIDC is a synthetic adapter.
 * RETRY_REVIEW_PHASE=before|after selects a new evidence directory.
 */
import assert from "node:assert/strict";
import path from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
import { startHarness, scenario, finishHarness, ROOT, A, B, RELEASE, cases, within } from "./ui-race-check.mjs";

const phase = process.env.RETRY_REVIEW_PHASE || "current";
assert.match(phase, /^[a-z0-9_-]+$/, "Use a simple phase name for isolated evidence");
const output = path.join(ROOT, "artifacts/fde-retrofit-2026-11-23/retry-regression", phase);
await mkdir(output, { recursive: true });
const failureAnswer = "The response provider is temporarily unavailable. This case has been saved for retry.";
const observations = [];
let count = 0;

function fixture({ failFirst = false } = {}) {
  const record = { ...structuredClone(cases[0]), release_id: RELEASE,
    release: { model_version: "retry-regression", corpus_version: "synthetic-policy" },
    state: "failed", version: 7, intent: "track_order",
    resolution: { intent: "track_order", disposition: "failed", answer: failureAnswer,
      reason_code: "provider_unavailable", missing_information: [], citations: [] } };
  const events = [{ stage: "generate", outcome: "error", details: { reason_code: "provider_unavailable", preserved_marker: "ORIGINAL-FAILURE" }, duration_ms: 1 }];
  const failedResolution = structuredClone(record.resolution);
  if (failFirst) { record.state = "new"; record.version = 6; record.resolution = null; events.length = 0; }
  const writes = [];
  const install = setReply => {
    setReply("GET", "/v1/cases", () => ({ body: { items: [structuredClone(record), structuredClone(cases[1])], total: 2, limit: 25, offset: 0 } }));
    setReply("GET", "/v1/dashboard", () => {
      const states = { new: 1 }; states[record.state] = (states[record.state] || 0) + 1;
      return { body: { total_cases: 2, states } };
    });
    setReply("GET", `/v1/cases/${A}`, () => ({ body: structuredClone(record) }));
    setReply("GET", `/v1/cases/${A}/trace`, () => ({ body: { events: structuredClone(events) } }));
    setReply("POST", `/v1/cases/${A}/resolve`, request => {
      const body = request.postDataJSON();
      writes.push({ case_id: A, body, key: request.headers()["idempotency-key"] });
      if (failFirst && writes.length === 1) {
        assert.deepEqual(body, { expected_case_version: 6 });
        record.state = "failed"; record.version = 7; record.resolution = structuredClone(failedResolution);
        events.push({ stage: "generate", outcome: "error", details: { reason_code: "provider_unavailable", preserved_marker: "ORIGINAL-FAILURE" }, duration_ms: 1 });
        return { status: 503, body: { code: "provider_unavailable", message: failureAnswer, retryable: true } };
      }
      assert.deepEqual(body, { expected_case_version: 7 }, "Retry must use the failed case's current version");
      assert.equal(record.version, 7, "Only one retry may reach this simulated persistence boundary");
      record.state = "resolved"; record.version = 8;
      record.resolution = { intent: "track_order", disposition: "resolved_read_only", answer: "Order A-100 is pending", reason_code: "authorized_tracking", citations: [], missing_information: [] };
      events.push({ stage: "retry-resolved", outcome: "ok", details: { case_version: 8, preserved_marker: "RETRY-COMPLETED" }, duration_ms: 1 });
      return { body: structuredClone(record.resolution) };
    });
  };
  return { record, events, writes, install };
}

async function requireRetry(page, name) {
  assert.match(await page.locator("#detailBody").textContent(), /temporarily unavailable/, "Persisted failure answer must remain visible");
  await page.screenshot({ path: path.join(output, `${name}-failed-response.png`), fullPage: true });
  assert.equal(await page.locator("#resolveCase").count(), 1, "Failed case with a nonempty persisted response must expose Retry resolution");
  assert.equal(await page.locator("#resolveCase").textContent(), "Retry resolution");
  assert.equal(await page.locator("#resolveCase").isEnabled(), true);
}

async function run(name, mode, test, options) {
  count++;
  await scenario(name, mode, test, options);
}

try {
  await startHarness();
  {
    const model = fixture({ failFirst: true });
    await run("fixture-operator: initial 503 refreshes failed inbox and retry uses persisted newer version", "fixture", async ({ page, waitBody, requests }) => {
      assert.match(await page.locator(`[data-case="${A}"] .case-bottom`).textContent(), /New/);
      await page.locator("#resolveCase").click(); await waitBody("temporarily unavailable");
      await page.screenshot({ path: path.join(output, "initial-503-failed-inbox.png"), fullPage: true });
      assert.match(await page.locator(`[data-case="${A}"] .case-bottom`).textContent(), /Failed/, "503 must refresh the inbox's persisted Failed state");
      assert.match(await page.locator(".detail-subtitle").textContent(), /Version 7/);
      assert.equal(await page.locator("#statTotal").textContent(), "2");
      assert(requests.filter(r => r.method === "GET" && r.pathname === "/v1/dashboard").length >= 2, "503 recovery must refresh dashboard counts");
      await requireRetry(page, "initial-503");
      await page.locator("#resolveCase").click(); await waitBody("Order A-100 is pending");
      assert.deepEqual(model.writes.map(write => write.body.expected_case_version), [6, 7]);
      assert.match(await page.locator(`[data-case="${A}"] .case-bottom`).textContent(), /Resolved/);
      assert.equal(await page.locator("#statResolved").textContent(), "1");
      await page.locator('[data-tab="trace"]').click(); await waitBody("RETRY-COMPLETED");
      assert.match(await page.locator("#detailBody").textContent(), /ORIGINAL-FAILURE/);
      observations.push({ scenario: "initial-503", writes: model.writes, mock_case_version: model.record.version, original_failure_preserved: true });
    }, { role: "operator", prepare: async ({ setReply }) => model.install(setReply) });
  }
  for (const mode of ["fixture", "oidc"]) {
    for (const role of ["operator", "admin"]) {
      const model = fixture();
      const name = `${mode}-${role}`;
      const mobile = mode === "oidc" && role === "operator";
      let mobileLayout = null;
      await run(`${name}: persisted failure retries current version and preserves trace`, mode, async ({ page, hold, release, waitBody, requests }) => {
        await requireRetry(page, name);
        if (mobile) {
          await page.locator("#resolveCase").scrollIntoViewIfNeeded();
          assert.equal(await page.locator("#resolveCase").isVisible(), true, "Mobile retry must be visible");
          mobileLayout = await page.locator("#resolveCase").evaluate(button => {
            const bounds = button.getBoundingClientRect();
            return { viewport_width: innerWidth, viewport_height: innerHeight,
              document_width: document.documentElement.scrollWidth,
              retry_bounds: { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom } };
          });
          assert(mobileLayout.document_width <= mobileLayout.viewport_width, "Mobile retry must not introduce horizontal document overflow");
          assert(mobileLayout.retry_bounds.left >= 0 && mobileLayout.retry_bounds.right <= mobileLayout.viewport_width,
            "Retry control must fit within the mobile viewport");
          assert(mobileLayout.retry_bounds.top >= 0 && mobileLayout.retry_bounds.bottom <= mobileLayout.viewport_height,
            "Retry control must be in view after scrolling");
          await page.screenshot({ path: path.join(output, "oidc-operator-mobile-retry.png"), fullPage: true });
        }
        await page.locator('[data-tab="trace"]').click(); await waitBody("ORIGINAL-FAILURE");
        await page.locator('[data-tab="summary"]').click();
        assert.match(await page.locator("#detailBody").textContent(), /temporarily unavailable/);
        const pending = hold("POST", `/v1/cases/${A}/resolve`);
        await page.locator("#resolveCase").click(); await within(pending.observed.promise, "retry request");
        assert.equal(await page.locator("#resolveCase").isDisabled(), true, "Pending retry must be disabled");
        await page.locator('[data-tab="trace"]').click(); await waitBody("ORIGINAL-FAILURE");
        await page.locator('[data-tab="summary"]').click();
        assert.equal(await page.locator("#resolveCase").isDisabled(), true, "Redraw must not enable duplicate retries");
        await release(pending);
        await waitBody("Order A-100 is pending");
        assert.match(await page.locator(".detail-subtitle").textContent(), /Version 8/);
        assert.equal(await page.locator("#resolveCase").count(), 0, "Successful resolution must not retain retry control");
        await page.locator('[data-tab="trace"]').click(); await waitBody("RETRY-COMPLETED");
        assert.match(await page.locator("#detailBody").textContent(), /ORIGINAL-FAILURE/);
        assert.equal(model.writes.length, 1);
        assert.equal(model.writes[0].body.expected_case_version, 7);
        assert.equal(requests.filter(r => r.method === "POST" && r.pathname.endsWith("/resolve")).length, 1);
        assert.equal(requests.filter(r => r.method === "POST" && r.pathname.includes("/proposals/")).length, 0);
        await page.screenshot({ path: path.join(output, `${name}-recovered-trace.png`), fullPage: true });
        observations.push({ scenario: name, writes: model.writes, mock_case_version: model.record.version, original_failure_preserved: model.events[0].details.preserved_marker === "ORIGINAL-FAILURE", mobile_layout: mobileLayout });
      }, { role, ...(mobile ? { viewport: { width: 375, height: 812 } } : {}), prepare: async ({ setReply }) => model.install(setReply) });
    }
    const model = fixture();
    await run(`${mode}-evaluator: persisted failure remains inspectable without retry or writes`, mode, async ({ page, waitBody, requests }) => {
      assert.match(await page.locator("#detailBody").textContent(), /temporarily unavailable/);
      assert.equal(await page.locator("#resolveCase").count(), 0);
      await page.locator('[data-tab="trace"]').click(); await waitBody("ORIGINAL-FAILURE");
      await page.locator('[data-tab="summary"]').click();
      assert.equal(await page.locator("#resolveCase").count(), 0);
      assert.equal(model.writes.length, 0);
      assert.equal(requests.filter(r => r.method === "POST" && /^\/v1\/(cases|proposals)(\/|$)/.test(r.pathname)).length, 0);
    }, { role: "evaluator", prepare: async ({ setReply }) => model.install(setReply) });
  }
  {
    const model = fixture();
    await run("oidc-operator: disconnect during retry cannot restore private case or response", "oidc", async ({ page, hold, release, requests }) => {
      await requireRetry(page, "disconnect");
      const pending = hold("POST", `/v1/cases/${A}/resolve`);
      await page.locator("#resolveCase").click(); await within(pending.observed.promise, "retry before disconnect");
      const beforeDisconnect = requests.length;
      await page.locator("#signOut").click();
      await release(pending);
      await page.locator("#signIn").waitFor({ state: "visible" });
      assert.match(await page.locator("#detail").textContent(), /Sign in to continue/);
      assert.match(await page.locator("#notice").textContent(), /disconnected/);
      assert.doesNotMatch(await page.locator("#detail").textContent(), /A-100|temporarily unavailable|Order A-100 is pending/);
      assert.equal(await page.locator("#resolveCase").count(), 0);
      assert.equal(requests.slice(beforeDisconnect).filter(r => r.method === "GET" && r.pathname.startsWith("/v1/cases")).length, 0, "A late retry reply must not refresh the disconnected workspace");
      assert.equal(model.writes.length, 1, "The submitted mock retry may complete; disconnect only discards the private UI reply");
      await page.screenshot({ path: path.join(output, "disconnect-late-retry.png"), fullPage: true });
    }, { role: "operator", prepare: async ({ setReply }) => model.install(setReply) });
  }
} finally {
  await writeFile(path.join(output, "observations.json"), JSON.stringify({ phase, scope: "Synthetic API responses; real workbench UI; no backend or provider connected", observations }, null, 2) + "\n");
  await finishHarness(path.join(output, "results.json"), count);
}
