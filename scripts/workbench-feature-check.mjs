/** Operator journeys against the real DOM with intercepted API responses.
 * No serving, test, or evaluation database is contacted or modified.
 */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { startHarness, scenario, finishHarness, ROOT, A, B, C, RELEASE, cases, principal, organization, within } from "./ui-race-check.mjs";

const out = path.join(ROOT, "artifacts/browser/feature-review");
await mkdir(out, { recursive: true });
const waitReady = page => page.waitForFunction(() => document.querySelector("#caseList")?.getAttribute("aria-busy") === "false");
const uuid = index => `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`;

try {
  await startHarness();
  await scenario("older cases are reachable through paging and server search/filter", "fixture", async ({ page, setReply, requests }) => {
    const many = Array.from({ length: 123 }, (_, index) => ({ ...cases[0], case_id: uuid(index + 1), message: `Paged case ${index + 1}`, order_ref: `REF-${index + 1}` }));
    many[121].state = "awaiting_approval";
    many[122] = { ...many[122], state: "escalated", message: "Needle beyond the first hundred" };
    many.forEach(item => setReply("GET", `/v1/cases/${item.case_id}`, () => ({ body: item })));
    setReply("GET", "/v1/cases", request => {
      const params = new URL(request.url()).searchParams;
      const q = (params.get("q") || "").toLowerCase(), state = params.get("state");
      const offset = Number(params.get("offset") || 0), limit = Number(params.get("limit") || 25);
      const items = many.filter(item => (!state || item.state === state) && `${item.message} ${item.order_ref} ${item.case_id}`.toLowerCase().includes(q));
      return { body: { items: items.slice(offset, offset + limit), total: items.length, limit, offset } };
    });
    await page.locator("#refresh").click(); await waitReady(page);
    assert.match(await page.locator("#pageStatus").textContent(), /1.*25 of 123/);
    assert.equal(await page.locator("#previousPage").isDisabled(), true);
    for (let next = 0; next < 4; next++) { await page.locator("#nextPage").click(); await waitReady(page); }
    assert.match(await page.locator("#pageStatus").textContent(), /101.*123 of 123/);
    assert.equal(await page.locator("#nextPage").isDisabled(), true);
    await page.locator(`[data-case="${uuid(123)}"]`).click();
    await page.waitForFunction(() => document.querySelector("#detail h2")?.textContent === "Needle beyond the first hundred");
    await page.locator("#search").fill("Needle"); await page.locator("#search").press("Enter"); await waitReady(page);
    assert.equal(await page.locator(".case-row").count(), 1);
    assert.match(await page.locator("#pageStatus").textContent(), /1.*1 of 1/);
    await page.locator('[data-filter="awaiting_approval"]').click(); await waitReady(page);
    assert.equal(await page.locator(".case-row").count(), 0);
    assert.match(await page.locator("#detail").textContent(), /No matching cases/);
    await page.locator("#search").fill(""); await page.locator("#search").press("Enter"); await waitReady(page);
    assert.equal(await page.locator(`[data-case="${uuid(122)}"]`).count(), 1);
    assert.ok(requests.some(request => request.pathname === "/v1/cases"));
  });

  await scenario("a slow old search cannot overwrite newer matching cases", "fixture", async ({ page, setReply, hold, release, waitHeader }) => {
    setReply("GET", "/v1/cases", request => {
      const q = new URL(request.url()).searchParams.get("q");
      const items = q === "slow" ? [cases[0]] : q === "fast" ? [cases[1]] : cases;
      return { body: { items, total: items.length } };
    });
    const old = hold("GET", "/v1/cases", { match: request => new URL(request.url()).searchParams.get("q") === "slow" });
    await page.locator("#search").fill("slow"); await page.locator("#search").press("Enter"); await within(old.observed.promise, "old search");
    await page.locator("#search").fill("fast"); await page.locator("#search").press("Enter"); await waitHeader("B");
    await release(old); await waitReady(page);
    assert.equal(await page.locator(".case-row").count(), 1);
    assert.equal(await page.locator(".case-row").getAttribute("data-case"), B);
    assert.equal(await page.locator("#detail h2").textContent(), "Harness case B");
  });

  await scenario("inbox failure removes stale actions and retry restores usable cases", "fixture", async ({ page, setReply, waitHeader }) => {
    setReply("GET", "/v1/cases", () => ({ status: 503, body: { message: "Synthetic inbox outage" } }));
    await page.locator("#refresh").click(); await page.locator("#retryInbox").waitFor();
    assert.equal(await page.locator("#resolveCase").count(), 0);
    assert.equal(await page.locator("#nextPage").isDisabled(), true);
    setReply("GET", "/v1/cases", () => ({ body: { items: cases, total: 2 } }));
    await page.locator("#retryInbox").click(); await waitHeader("A"); await waitReady(page);
    setReply("GET", "/v1/dashboard", () => ({ status: 503, body: { message: "Synthetic totals outage" } }));
    await page.locator("#refresh").click(); await waitHeader("A"); await waitReady(page);
    assert.match(await page.locator("#notice").textContent(), /dashboard totals are unavailable/);
    assert.equal(await page.locator("#resolveCase").isEnabled(), true);
  });

  await scenario("identity changes clear old context immediately and recover failed connections", "fixture", async ({ page, setReply, hold, release, waitHeader }) => {
    const beta = { ...cases[1], case_id: C, message: "Beta workspace case", order_ref: "B-100" };
    setReply("POST", "/v1/demo/session", request => {
      const isBeta = request.postDataJSON().actor === "beta-operator";
      return { body: { access_token: isBeta ? "synthetic-beta-token" : "synthetic-fixture-token", principal: { ...principal, tenant_id: isBeta ? "22222222-2222-4222-8222-222222222222" : principal.tenant_id }, organization: { display_name: isBeta ? "Beta harness" : organization.display_name } } };
    });
    setReply("GET", "/v1/cases", request => ({ body: { items: request.headers().authorization?.includes("beta-token") ? [beta] : cases, total: request.headers().authorization?.includes("beta-token") ? 1 : 2 } }));
    setReply("GET", `/v1/cases/${C}`, () => ({ body: beta }));
    const pending = hold("POST", "/v1/demo/session");
    await page.locator("#actor").selectOption("beta-operator"); await within(pending.observed.promise, "new identity");
    assert.equal(await page.locator(".case-row").count(), 0);
    assert.equal(await page.locator("#resolveCase").count(), 0);
    assert.equal(await page.locator("#newCase").isDisabled(), true);
    assert.equal(await page.locator("#search").isDisabled(), true);
    assert.doesNotMatch(await page.locator("#detail").textContent(), /Harness case A/);
    await release(pending);
    await page.waitForFunction(() => document.querySelector("#detail h2")?.textContent === "Beta workspace case");
    const failed = hold("POST", "/v1/demo/session", { status: 503, body: { message: "Synthetic sign-in outage" } });
    await page.locator("#actor").selectOption("alpha-operator"); await within(failed.observed.promise, "failed identity"); await release(failed);
    await page.getByRole("button", { name: "Reconnect workspace" }).waitFor();
    assert.equal(await page.locator(".case-row").count(), 0);
    await page.getByRole("button", { name: "Reconnect workspace" }).click(); await waitHeader("A");
    const older = hold("POST", "/v1/demo/session");
    await page.locator("#actor").selectOption("beta-operator"); await within(older.observed.promise, "older identity");
    await page.locator("#actor").selectOption("alpha-operator"); await waitHeader("A");
    await release(older);
    assert.equal(await page.locator("#actor").inputValue(), "alpha-operator");
    assert.equal(await page.locator("#detail h2").textContent(), "Harness case A");
  });

  await scenario("read-only production reviewers can inspect with keyboard tabs without fixture actions", "oidc", async ({ page, waitBody }) => {
    assert.equal(await page.locator("#newCase").isDisabled(), true);
    assert.equal(await page.locator("#resolveCase").count(), 0);
    assert.equal(await page.locator("#actor").isHidden(), true);
    assert.equal(await page.locator("#apiReference").isHidden(), true);
    assert.equal(await page.locator(".examples").isHidden(), true);
    assert.match(await page.locator("#customerContextNote").textContent(), /verified server session/);
    await page.locator("#tab-summary").focus(); await page.keyboard.press("ArrowRight"); await waitBody("EVIDENCE-A");
    assert.equal(await page.locator("#tab-evidence").getAttribute("aria-selected"), "true");
    assert.equal(await page.evaluate(() => document.activeElement.id), "tab-evidence");
    await page.keyboard.press("End"); await waitBody("TRACE-A");
    await page.keyboard.press("Home");
    assert.equal(await page.evaluate(() => document.activeElement.id), "tab-summary");
    assert.equal(await page.locator("#detailBody").getAttribute("aria-labelledby"), "tab-summary");
  }, { role: "evaluator" });

  await scenario("pending creation protects the submitted draft and keyboard dialog recovery", "fixture", async ({ page, hold, release, setReply, requests }) => {
    const created = { ...cases[0], case_id: C, message: "Creation protected during a delayed response" };
    setReply("POST", "/v1/cases", () => ({ status: 201, body: created }));
    setReply("GET", "/v1/cases", () => ({ body: { items: [...cases, created], total: 3 } }));
    setReply("GET", `/v1/cases/${C}`, () => ({ body: created }));
    await page.locator("#newCase").click(); await page.locator("#message").fill("   ");
    await page.locator("#createSubmit").click();
    assert.equal(requests.filter(item => item.method === "POST" && item.pathname === "/v1/cases").length, 0);
    await page.locator('[data-example="track"]').click();
    assert.equal(await page.locator("#message").evaluate(input => input.checkValidity()), true);
    await page.locator("#message").fill(created.message);
    const pending = hold("POST", "/v1/cases");
    await page.locator("#createSubmit").click(); await within(pending.observed.promise, "create commit");
    assert.equal(await page.locator("#message").isDisabled(), true);
    assert.equal(await page.locator("#cancelDialog").isDisabled(), true);
    assert.equal(await page.locator("#closeDialog").isDisabled(), true);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#caseDialog").evaluate(dialog => dialog.open), true);
    await release(pending);
    await page.waitForFunction(expected => document.querySelector("#detail h2")?.textContent === expected, created.message);
    await page.locator("#newCase").click();
    assert.equal(await page.locator("#message").inputValue(), "");
    await page.keyboard.press("Escape");
    assert.equal(await page.evaluate(() => document.activeElement.id), "newCase");
  });

  await scenario("comparison snapshots survive refresh and partial retries create one job per release", "fixture", async ({ page, setReply, hold, release }) => {
    const releaseB = "99999999-9999-4999-8999-999999999999";
    setReply("GET", "/v1/releases", () => ({ body: { items: [{ release_id: RELEASE, model_version: "tfidf-logistic-v1", corpus_version: "synthetic-policy", active: false }, { release_id: releaseB, model_version: "synthetic-reviewed", corpus_version: "synthetic-policy", active: true }] } }));
    const persisted = new Map(), submitted = [];
    setReply("GET", "/v1/evaluations", () => ({ body: { items: [...persisted.values()] } }));
    setReply("POST", "/v1/evaluations", request => {
      const key = request.headers()["idempotency-key"], body = request.postDataJSON();
      submitted.push({ key, ...body });
      if (!persisted.has(key)) persisted.set(key, { evaluation_id: uuid(500 + persisted.size), release_id: body.release_id, state: "queued", mode: body.mode });
      if (submitted.length === 2) return { abort: true };
      return { status: 202, body: persisted.get(key) };
    });
    await page.locator('[data-view="evaluation"]').click(); await page.locator("#queueEvaluation").waitFor();
    const first = hold("POST", "/v1/evaluations");
    await page.locator("#queueEvaluation").click(); await within(first.observed.promise, "first comparison job");
    await page.locator("#refreshEvaluations").click();
    await page.waitForFunction(() => document.querySelector("#queueEvaluation")?.disabled && document.querySelector("#evalMode")?.disabled);
    await release(first);
    await page.waitForFunction(() => document.querySelector("#queueEvaluation") && !document.querySelector("#queueEvaluation").disabled);
    assert.match(await page.locator("#notice").textContent(), /Retry the same comparison/);
    assert.equal(await page.locator("#queueEvaluation").textContent(), "Compare releases");
    await page.locator("#queueEvaluation").click();
    await page.waitForFunction(() => document.querySelector("#notice")?.textContent.includes("Evaluation queued"));
    assert.equal(submitted.length, 4);
    assert.equal(persisted.size, 2);
    assert.equal(submitted[0].key, submitted[2].key);
    assert.equal(submitted[1].key, submitted[3].key);
    assert.ok(submitted.every(item => item.mode === "live_model"));
  }, { role: "evaluator" });

  await scenario("mobile dialogs, long identifiers and untrusted text remain usable", "fixture", async ({ page, setReply }) => {
    const hostile = { ...cases[0], message: '<img src=x onerror="window.__injected=true"> support request', order_ref: "R".repeat(100), customer_subject: "customer-" + "x".repeat(90) };
    setReply("GET", `/v1/cases/${A}`, () => ({ body: hostile }));
    await page.locator(`[data-case="${A}"]`).click();
    await page.waitForFunction(() => document.querySelector("#detail h2")?.textContent.includes("onerror"));
    assert.equal(await page.locator("#detail img").count(), 0);
    assert.equal(await page.evaluate(() => window.__injected === true), false);
    for (const width of [320, 375, 768]) {
      await page.setViewportSize({ width, height: 568 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Horizontal overflow at ${width}px`);
      assert.equal(await page.evaluate(() => {
        const parent = document.querySelector(".detail-subtitle").getBoundingClientRect();
        return [...document.querySelectorAll(".detail-subtitle span")].every(element => element.getBoundingClientRect().right <= parent.right + 1);
      }), true, `Long identifiers are clipped at ${width}px`);
    }
    await page.setViewportSize({ width: 375, height: 568 });
    await page.locator("#newCase").click();
    const dialog = await page.locator("#caseDialog").boundingBox();
    assert.ok(dialog.x >= 0 && dialog.x + dialog.width <= 375 && dialog.height <= 568);
    await page.screenshot({ path: path.join(out, "mobile-dialog.png") });
    await page.locator("#createSubmit").focus();
    const submit = await page.locator("#createSubmit").boundingBox();
    assert.ok(submit.y >= 0 && submit.y + submit.height <= 568, "The dialog submit button must be keyboard reachable");
    await page.screenshot({ path: path.join(out, "mobile-dialog-bottom.png") });
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.evaluate(() => document.documentElement.style.fontSize = "200%");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "Horizontal overflow at 200% text size");
    await page.screenshot({ path: path.join(out, "text-zoom.png") });
  }, { viewport: { width: 375, height: 812 } });
} finally {
  await finishHarness(path.join(ROOT, "artifacts/browser/workbench-feature-results.json"), 8);
}
