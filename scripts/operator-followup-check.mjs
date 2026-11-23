/** Bounded operator follow-up journeys; real DOM, intercepted APIs, no DB writes. */
import assert from "node:assert/strict";
import path from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { startHarness, scenario, finishHarness, ROOT, A, B, RELEASE, cases, within } from "./ui-race-check.mjs";

const proposalId = id => id === A ? "12121212-1212-4212-8212-121212121212" : "34343434-3434-4434-8434-343434343434";
const screenshotDirectory = path.join(ROOT, "artifacts/browser/followup-review");
await mkdir(screenshotDirectory, { recursive: true });
const additionalEvidence = {};
const pinned = { release_id: RELEASE, release: { model_version: "synthetic-pinned", corpus_version: "synthetic-policy-v1" } };
const needsInfo = id => ({ ...structuredClone(cases[id === A ? 0 : 1]), ...structuredClone(pinned), state: "needs_info", version: 3, order_ref: null, resolution: { intent: "track_order", disposition: "needs_info", answer: "Please provide the order reference.", missing_information: ["order_ref"], citations: [] } });
const awaiting = id => ({ ...structuredClone(cases[id === A ? 0 : 1]), ...structuredClone(pinned), state: "awaiting_approval", version: 4, order_ref: "A-100", resolution: { intent: "cancel_request", disposition: "awaiting_approval", answer: "Cancellation awaits a human review decision.", missing_information: [], citations: [] }, proposals: [{ proposal_id: proposalId(id), proposal_sha256: "b".repeat(64), expected_case_version: 4, expected_order_version: 7, order_ref: "A-100", state: "pending" }] });

function fixture(first = needsInfo(A), second = awaiting(B)) {
  const records = new Map([[A, first], [B, second]]);
  const contextReceipts = new Map(), declineReceipts = new Map();
  const contextRequests = [], declineRequests = [], resolveRequests = [];
  const order = { state: "pending", version: 7 };
  let contextUpdates = 0, handoffs = 0;
  const attach = request => {
    const id = new URL(request.url()).pathname.split("/")[3];
    const body = request.postDataJSON(), key = request.headers()["idempotency-key"];
    contextRequests.push({ id, key, body });
    assert.deepEqual(Object.keys(body).sort(), ["expected_case_version", "order_ref"]);
    if (contextReceipts.has(key)) return { body: contextReceipts.get(key) };
    const current = records.get(id);
    assert.equal(current.state, "needs_info");
    assert.equal(body.expected_case_version, current.version);
    current.order_ref = body.order_ref; current.version++; current.resolution = null;
    contextUpdates++;
    contextReceipts.set(key, structuredClone(current));
    return { body: contextReceipts.get(key) };
  };
  const decline = request => {
    const id = [...records.values()].find(record => record.proposals.some(proposal => new URL(request.url()).pathname.includes(proposal.proposal_id))).case_id;
    const body = request.postDataJSON(), key = request.headers()["idempotency-key"];
    declineRequests.push({ id, key, body });
    assert.deepEqual(Object.keys(body).sort(), ["expected_case_version", "proposal_sha256", "reason_code"]);
    assert.ok(["policy_concern", "customer_requested", "needs_manual_review", "other"].includes(body.reason_code));
    if (declineReceipts.has(key)) return { body: declineReceipts.get(key) };
    const current = records.get(id), proposal = current.proposals[0];
    assert.equal(body.expected_case_version, current.version);
    assert.equal(body.proposal_sha256, proposal.proposal_sha256);
    proposal.state = "rejected"; current.state = "escalated"; current.version++;
    current.resolution = { intent: "cancel_request", disposition: "escalated", answer: "The proposal was declined for human review.", missing_information: [], citations: [] };
    handoffs++;
    const acknowledgement = { case_id: id, proposal_id: proposal.proposal_id, state: "rejected", case_state: "escalated", case_version: current.version, reason_code: body.reason_code, order_changed: false };
    declineReceipts.set(key, acknowledgement);
    return { body: acknowledgement };
  };
  const install = setReply => {
    setReply("GET", "/v1/cases", request => {
      const params = new URL(request.url()).searchParams;
      const matching = [...records.values()].filter(record => !params.get("state") || record.state === params.get("state"));
      return { body: { items: matching, total: matching.length, limit: 25, offset: 0 } };
    });
    setReply("GET", "/v1/dashboard", () => ({ body: { total_cases: records.size, states: [...records.values()].reduce((counts, record) => ({ ...counts, [record.state]: (counts[record.state] || 0) + 1 }), {}) } }));
    for (const id of [A, B]) {
      setReply("GET", `/v1/cases/${id}`, () => ({ body: structuredClone(records.get(id)) }));
      setReply("POST", `/v1/cases/${id}/context`, attach);
      setReply("POST", `/v1/proposals/${proposalId(id)}/reject`, decline);
      setReply("POST", `/v1/cases/${id}/resolve`, request => {
        const current = records.get(id), body = request.postDataJSON();
        resolveRequests.push({ id, body });
        assert.equal(body.expected_case_version, current.version);
        current.state = "resolved"; current.version++;
        current.resolution = { intent: "track_order", disposition: "resolved_read_only", answer: "The verified order is pending.", missing_information: [], citations: [] };
        return { body: current.resolution };
      });
    }
  };
  return { records, contextRequests, declineRequests, resolveRequests, contextReceipts, declineReceipts, order, attach, decline, install, get contextUpdates() { return contextUpdates; }, get handoffs() { return handoffs; } };
}

try {
  await startHarness();
  {
    const model = fixture();
    await scenario("needs-info attachment retries once and requires explicit resolution", "fixture", async ({ page, setReply }) => {
      const original = structuredClone(model.records.get(A));
      const accessibility = await new AxeBuilder({ page }).include("#contextForm").analyze();
      assert.deepEqual(accessibility.violations, []);
      additionalEvidence.context_form_accessibility_violations = accessibility.violations.length;
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.locator("#contextForm").screenshot({ path: path.join(screenshotDirectory, "add-order-reference.png") });
      let responses = 0;
      setReply("POST", `/v1/cases/${A}/context`, request => {
        const reply = model.attach(request);
        return ++responses === 1 ? { abort: true } : reply;
      });
      await page.locator("#contextOrderRef").fill("A-100"); await page.locator("#saveOrderRef").click();
      await page.locator("#contextError").waitFor({ state: "visible" });
      await page.waitForFunction(() => !document.querySelector("#saveOrderRef").disabled);
      assert.equal(model.contextUpdates, 1);
      await page.locator("#saveOrderRef").click();
      await page.waitForFunction(() => document.querySelector("#resolveCase") && !document.querySelector("#resolveCase").disabled);
      assert.equal(model.contextRequests.length, 2);
      assert.equal(model.contextRequests[0].key, model.contextRequests[1].key);
      assert.equal(model.contextUpdates, 1);
      assert.equal(model.resolveRequests.length, 0, "Saving context must not run resolution automatically");
      assert.equal(model.records.get(A).customer_subject, original.customer_subject);
      assert.equal(model.records.get(A).release_id, original.release_id);
      assert.equal(await page.locator("#contextOrderRef").inputValue(), "A-100");
      await page.locator("#resolveCase").click();
      await page.waitForFunction(() => document.querySelector("#detail .badge.resolved"));
      assert.equal(model.resolveRequests.length, 1);
      assert.equal(model.resolveRequests[0].body.expected_case_version, original.version + 1);
      assert.equal(await page.locator("#contextForm").count(), 0);
    }, { viewport: { width: 375, height: 812 }, prepare: async ({ setReply }) => model.install(setReply) });
  }
  {
    const model = fixture(awaiting(A), needsInfo(B));
    await scenario("decline is deliberate, idempotent, escalates and never approves or cancels", "fixture", async ({ page, setReply, requests }) => {
      const originalOrder = structuredClone(model.order), confirmations = [];
      const decisions = [false, true, true];
      page.on("dialog", async dialog => { confirmations.push(dialog.message()); if (decisions.shift()) await dialog.accept(); else await dialog.dismiss(); });
      await page.locator('[data-filter="awaiting_approval"]').click();
      await page.locator("#declineReason").waitFor();
      const accessibility = await new AxeBuilder({ page }).include("#declineForm").analyze();
      assert.deepEqual(accessibility.violations, []);
      additionalEvidence.decline_form_accessibility_violations = accessibility.violations.length;
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.locator("#declineForm").screenshot({ path: path.join(screenshotDirectory, "decline-proposal.png") });
      assert.equal(await page.locator("#declineForm textarea").count(), 0);
      assert.equal(await page.locator("#declineReason option").count(), 5);
      await page.locator("#declineReason").selectOption("policy_concern");
      await page.locator("#declineProposal").click();
      assert.equal(model.declineRequests.length, 0, "Dismissing confirmation must not send a decision");
      let replies = 0;
      setReply("POST", `/v1/proposals/${proposalId(A)}/reject`, request => {
        const reply = model.decline(request);
        return ++replies === 1 ? { abort: true } : reply;
      });
      await page.locator("#declineProposal").click(); await page.locator("#declineError").waitFor({ state: "visible" });
      await page.waitForFunction(() => !document.querySelector("#declineProposal").disabled);
      await page.locator("#declineProposal").click();
      await page.waitForFunction(() => document.querySelector("#detail .badge.escalated"));
      assert.equal(model.declineRequests.length, 2);
      assert.equal(model.declineRequests[0].key, model.declineRequests[1].key);
      assert.equal(model.handoffs, 1);
      assert.deepEqual(model.order, originalOrder);
      assert.equal(requests.filter(request => request.pathname.endsWith("/approve")).length, 0);
      assert.equal(await page.locator("#approveProposal").count(), 0);
      assert.equal(await page.locator("#declineForm").count(), 0);
      assert.match(await page.locator("#detailBody").textContent(), /decline operation did not execute a cancellation/);
      assert.ok(confirmations.every(message => /escalates the case for human review/.test(message) && /does not execute a cancellation/.test(message)));
    }, { role: "approver", viewport: { width: 375, height: 812 }, prepare: async ({ setReply }) => model.install(setReply) });
  }
  {
    const model = fixture();
    await scenario("read-only reviewers are not offered either follow-up mutation", "oidc", async ({ page, waitHeader, requests }) => {
      assert.equal(await page.locator("#contextForm").count(), 0);
      await page.locator(`[data-case="${B}"]`).click(); await waitHeader("B");
      assert.equal(await page.locator("#declineForm").count(), 0);
      assert.equal(await page.locator("#approveProposal").count(), 0);
      assert.equal(requests.filter(request => request.method === "POST").length, 0);
    }, { role: "evaluator", prepare: async ({ setReply }) => model.install(setReply) });
  }
  {
    const model = fixture();
    await scenario("switching cases during saved context or decline never retargets the write", "fixture", async ({ page, hold, release, waitHeader }) => {
      const context = hold("POST", `/v1/cases/${A}/context`);
      await page.locator("#contextOrderRef").fill("A-100"); await page.locator("#saveOrderRef").click(); await within(context.observed.promise, "captured context update");
      await page.locator(`[data-case="${B}"]`).click(); await waitHeader("B");
      assert.equal(await page.locator("#declineProposal").isDisabled(), true);
      await release(context);
      assert.equal(await page.locator("#detail h2").textContent(), "Harness case B");
      await page.waitForFunction(() => !document.querySelector("#declineProposal").disabled);
      const rejection = hold("POST", `/v1/proposals/${proposalId(B)}/reject`);
      page.once("dialog", dialog => dialog.accept());
      await page.locator("#declineReason").selectOption("needs_manual_review"); await page.locator("#declineProposal").click(); await within(rejection.observed.promise, "captured decline");
      await page.locator(`[data-case="${A}"]`).click(); await waitHeader("A");
      assert.equal(await page.locator("#resolveCase").isDisabled(), true);
      await release(rejection);
      assert.equal(await page.locator("#detail h2").textContent(), "Harness case A");
      assert.equal(await page.locator("#contextOrderRef").inputValue(), "A-100");
      assert.equal(model.contextRequests[0].id, A);
      assert.equal(model.declineRequests[0].id, B);
      assert.equal(model.resolveRequests.length, 0);
    }, { role: "approver", prepare: async ({ setReply }) => model.install(setReply) });
  }
  {
    const model = fixture();
    await scenario("disconnect during context update cannot restore a private case or form", "oidc", async ({ page, hold, release }) => {
      const pending = hold("POST", `/v1/cases/${A}/context`);
      await page.locator("#contextOrderRef").fill("A-100"); await page.locator("#saveOrderRef").click(); await within(pending.observed.promise, "update before disconnect");
      await page.locator("#signOut").click(); await release(pending);
      await page.locator("#signIn").waitFor({ state: "visible" });
      assert.equal(await page.locator("#contextForm").count(), 0);
      assert.equal(await page.locator(".case-row").count(), 0);
      assert.doesNotMatch(await page.locator("#detail").textContent(), /Harness case A/);
    }, { prepare: async ({ setReply }) => model.install(setReply) });
  }
} finally {
  const output = path.join(ROOT, "artifacts/browser/operator-followup-results.json");
  const report = await finishHarness(output, 5);
  report.additional_evidence = additionalEvidence;
  await writeFile(output, JSON.stringify(report, null, 2) + "\n");
}
