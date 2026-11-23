import { chromium } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createHash } from "node:crypto";
const base = process.env.BASE_URL || "http://127.0.0.1:8000";
const output = new URL("../artifacts/browser/", import.meta.url);
const sourceFiles = ["web/app.js", "web/app.css", "web/index.html", "web/auth-client.js"];
const sourceHashes = async () => Object.fromEntries(await Promise.all(sourceFiles.map(async (name) => [name,createHash("sha256").update(await fs.readFile(new URL("../"+name,import.meta.url))).digest("hex")])));
const initialHashes = await sourceHashes();
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({
  channel: process.env.BROWSER_CHANNEL || "chrome",
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  recordVideo: {
    dir: output.pathname.replace(/^\/([A-Z]:)/, "$1"),
    size: { width: 1440, height: 1000 },
  },
  reducedMotion: "reduce",
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (dialog) => dialog.accept());
const wait = () => page.waitForTimeout(900);
const check = async (name, fn) => {
  await fn();
  console.log("PASS " + name);
};
async function create(example) {
  await page.getByRole("button", { name: "＋ New case", exact: true }).click();
  await page.getByRole("button", { name: example, exact: true }).click();
  await page.getByRole("button", { name: "Create case", exact: true }).click();
  await page
    .getByRole("button", { name: "Resolve this case", exact: true })
    .waitFor();
  await wait();
  await page
    .getByRole("button", { name: "Resolve this case", exact: true })
    .click();
}
try {
  await page.goto(base);
  await page.locator("#statTotal").filter({ hasText: /\d/ }).waitFor();
  await page.screenshot({
    path: new URL("01-inbox.png", output).pathname.replace(/^\/([A-Z]:)/, "$1"),
    fullPage: true,
  });
  await check("tracking resolves with policy evidence", async () => {
    await create("Track an order");
    await page.locator("#detail .badge.resolved").waitFor();
    await page
      .getByRole("button", { name: "Policy evidence", exact: true })
      .click();
    await page.locator(".evidence-card").first().waitFor();
    assert(
      (await page.locator("#detailBody").innerText()).includes("ownership"),
    );
    await wait();
    await page.screenshot({
      path: new URL("02-evidence.png", output).pathname.replace(
        /^\/([A-Z]:)/,
        "$1",
      ),
      fullPage: true,
    });
  });
  await check("trace stages visible", async () => {
    await page
      .getByRole("button", { name: "Execution trace", exact: true })
      .click();
    await page.locator(".trace-row").first().waitFor();
    assert(
      (await page.locator("#detailBody").innerText())
        .toLowerCase()
        .includes("validate"),
    );
    await wait();
  });
  await check("cancellation stops for approval", async () => {
    await create("Request cancellation");
    await page.locator("#detail .badge.awaiting_approval").waitFor();
    await page
      .getByText("Switch to Alpha · Approver", { exact: false })
      .waitFor();
    await page.screenshot({
      path: new URL("03-proposal.png", output).pathname.replace(
        /^\/([A-Z]:)/,
        "$1",
      ),
      fullPage: true,
    });
    await wait();
  });
  await check("approver executes one local cancellation", async () => {
    await page.locator("#actor").selectOption("alpha-approver-one");
    await page
      .getByRole("button", { name: "Approve cancellation", exact: true })
      .waitFor();
    await wait();
    await page
      .getByRole("button", { name: "Approve cancellation", exact: true })
      .click();
    await page
      .getByText("Cancellation completed. One durable action was recorded.", {
        exact: true,
      })
      .waitFor();
    await page.screenshot({
      path: new URL("04-receipt.png", output).pathname.replace(
        /^\/([A-Z]:)/,
        "$1",
      ),
      fullPage: true,
    });
  });
  await check("human request persists escalation", async () => {
    await page.locator("#actor").selectOption("alpha-operator");
    await page.locator("#statTotal").filter({ hasText: /\d/ }).waitFor();
    await create("Human handoff");
    await page.locator("#detail .badge.escalated").waitFor();
    await wait();
  });
  await check("evaluation and releases render", async () => {
    await page
      .getByRole("button", { name: "Evaluation lab", exact: false })
      .click();
    await page.getByRole("heading", { name: "Workflow invariants" }).waitFor();
    await page.screenshot({
      path: new URL("05-evaluations.png", output).pathname.replace(
        /^\/([A-Z]:)/,
        "$1",
      ),
      fullPage: true,
    });
    await wait();
    await page
      .getByRole("button", { name: "Release registry", exact: false })
      .click();
    await page
      .getByRole("heading", { name: "Release registry", exact: true })
      .waitFor();
  });
  await check("evaluation comparison exposes scoped case reports", async () => {
    await page
      .getByRole("button", { name: "Evaluation lab", exact: false })
      .click();
    await page.locator("#actor").selectOption("alpha-evaluator");
    await page
      .getByRole("button", { name: "Compare releases", exact: true })
      .waitFor();
    const login = await page.request.post(base + "/v1/demo/session", {
      data: { actor: "alpha-evaluator" },
    });
    const auth = {
      Authorization: "Bearer " + (await login.json()).access_token,
    };
    const jobs = [];
    const capture = async (response) => {
      if (
        response.url().endsWith("/v1/evaluations") &&
        response.request().method() === "POST" &&
        response.status() === 202
      )
        jobs.push((await response.json()).evaluation_id);
    };
    page.on("response", capture);
    await page
      .getByRole("button", { name: "Compare releases", exact: true })
      .click();
    const deadline = Date.now() + 60000;
    while (jobs.length < 2) {
      if (Date.now() > deadline)
        throw Error("Two comparison jobs were not queued");
      await page.waitForTimeout(200);
    }
    while (true) {
      const values = await Promise.all(
        jobs.map(async (id) =>
          (
            await page.request.get(base + "/v1/evaluations/" + id, {
              headers: auth,
            })
          ).json(),
        ),
      );
      if (values.every((v) => v.state === "completed")) break;
      if (Date.now() > deadline) throw Error("Comparison worker timed out");
      await page.waitForTimeout(400);
    }
    page.off("response", capture);
    await page
      .getByRole("button", { name: "Refresh results", exact: true })
      .click();
    await page.locator("[data-evaluation-report]").first().click();
    await page.locator("#onlyEvaluationFailures").waitFor();
    await page.locator("#onlyEvaluationFailures").uncheck();
    assert.equal(await page.locator(".evaluation-case").count(), 20);
    await page.locator(".evaluation-case details").first().click();
    await page.screenshot({
      path: new URL("07-evaluation-details.png", output).pathname.replace(
        /^\/([A-Z]:)/,
        "$1",
      ),
      fullPage: true,
    });
    await wait();
    await page
      .getByRole("button", { name: "Close evaluation details" })
      .click();
  });
  await check("tenant switch removes alpha case context", async () => {
    await page
      .getByRole("button", { name: "Case inbox", exact: false })
      .click();
    await page.locator("#actor").selectOption("beta-operator");
    await page
      .locator("#tenantName")
      .filter({ hasText: "Beta Retail" })
      .waitFor();
    await page
      .locator("#detail h2")
      .filter({ hasText: "Track B-100" })
      .waitFor();
    assert(!(await page.locator("#detail").innerText()).includes("A-100"));
  });
  await check("mobile layout fits viewport", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await wait();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 1,
    );
    assert(!overflow, "horizontal overflow");
    await page.screenshot({
      path: new URL("06-mobile.png", output).pathname.replace(
        /^\/([A-Z]:)/,
        "$1",
      ),
      fullPage: true,
    });
  });
  assert.deepEqual(errors, [], "browser JavaScript errors");
  assert.deepEqual(await sourceHashes(), initialHashes, "Browser source changed during acceptance");
  await fs.writeFile(
    new URL("browser-results.json", output),
    JSON.stringify(
      {
        passed: 9,
        source_hashes: initialHashes,
        source_stable: true,
        errors,
        base_url: base,
        viewport: [1440, 1000],
        mobile: [390, 844],
        generated_at: new Date().toISOString(),
      },
      null,
      2,
    ),
  );
} finally {
  await context.close();
  await browser.close();
}
