/** Read-only viewport evidence: no case/order mutations or full-page compositor. */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
const base = process.env.BASE_URL || "http://127.0.0.1:8000";
const output = new URL("../artifacts/browser/", import.meta.url);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
try {
  await page.goto(base); await page.locator("#statTotal").filter({ hasText: /\d/ }).waitFor();
  const login = await page.request.post(base + "/v1/demo/session", { data: { actor: "alpha-operator" } });
  assert.equal(login.status(), 200);
  const token = (await login.json()).access_token;
  const result = await page.request.get(base + "/v1/cases?state=needs_info&limit=1", { headers: { Authorization: "Bearer " + token } });
  assert.equal(result.status(), 200);
  const selected = (await result.json()).items[0];
  if (selected) {
    await page.locator("#search").fill(selected.case_id); await page.locator("#search").press("Enter");
    await page.waitForFunction(id => document.querySelector("#detail .case-id")?.textContent.includes(id), selected.case_id.slice(0, 8).toUpperCase());
  }
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 600); });
  await page.waitForTimeout(150);
  const bounds = await page.evaluate(() => ({ scroll_y: window.scrollY, sidebar_top: document.querySelector(".sidebar").getBoundingClientRect().top, skip_link_bottom: document.querySelector(".skip-link").getBoundingClientRect().bottom }));
  assert.equal(bounds.sidebar_top, 0);
  assert(bounds.skip_link_bottom < 0);
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  await page.waitForTimeout(150);
  await page.screenshot({ path: new URL("10-clean-followup-viewport.png", output).pathname.replace(/^\/([A-Z]:)/, "$1"), fullPage: false });
  await fs.writeFile(new URL("viewport-results.json", output), JSON.stringify({ passed: true, read_only: true, bounds, screenshot: "10-clean-followup-viewport.png", full_page: false, viewport: [1440, 1000], case_mutations: 0 }, null, 2) + "\n");
  console.log(JSON.stringify({ passed: true, bounds, screenshot: "10-clean-followup-viewport.png" }));
} finally { await browser.close(); }
