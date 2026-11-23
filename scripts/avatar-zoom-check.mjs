/** Enlarged role initials must remain inside their background at every visible
 * breakpoint. Uses actual frontend files and intercepted APIs; no live data.
 * Usage: node scripts/avatar-zoom-check.mjs <scratch-report-path>
 */
import assert from "node:assert/strict";
import path from "node:path";
import { startHarness, scenario, finishHarness } from "./ui-race-check.mjs";

const output = process.argv[2];
assert.ok(output, "Provide an explicit scratch report path");
const roles = ["operator", "approver", "evaluator", "admin"];
const observations = [];
await startHarness();
try {
  for (const role of roles) {
    await scenario(`Role initials stay visible with 200% text: ${role}`, "fixture", async ({ page }) => {
      await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
      for (const width of [1440, 1280, 1024, 768, 375, 320]) {
        await page.setViewportSize({ width, height: 1000 });
        const geometry = await page.locator("#avatar").evaluate((element) => {
          const style = getComputedStyle(element);
          if (style.display === "none") return { hidden: true };
          const bounds = element.getBoundingClientRect(), range = document.createRange();
          range.selectNodeContents(element);
          const text = range.getBoundingClientRect();
          const points = [text.left + 0.5, (text.left + text.right) / 2, text.right - 0.5]
            .flatMap(x => [text.top + 1, (text.top + text.bottom) / 2, text.bottom - 1]
              .map(y => { const top = document.elementFromPoint(x, y); return !!top && (top === element || element.contains(top)); }));
          return { hidden: false, initials: element.textContent, font_size: style.fontSize,
            bounds: { width: bounds.width, height: bounds.height }, text: { width: text.width, height: text.height },
            text_fits: text.left >= bounds.left && text.right <= bounds.right && text.top >= bounds.top && text.bottom <= bounds.bottom,
            all_text_points_visible: points.every(Boolean) };
        });
        observations.push({ role, width, ...geometry });
        if (width <= 650) assert.ok(geometry.hidden, "Compact layout hides the redundant avatar");
        else {
          assert.equal(geometry.hidden, false);
          assert.ok(geometry.text_fits, `${role} initials must fit their background at ${width}px`);
          assert.ok(geometry.all_text_points_visible, `${role} initials must be unobscured at ${width}px`);
        }
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Text sizing must not cause page overflow");
      }
    }, { role });
  }
  await scenario("Narrow enlarged text preserves action labels and controls", "fixture", async ({ page }) => {
    for (const width of [375, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
      for (const selector of ["#refresh", "#newCase", "#resolveCase", ".inbox-pagination .button"]) {
        const checks = await page.locator(selector).evaluateAll(elements => elements.map(element => {
          const r = element.getBoundingClientRect(), panel = element.closest(".detail-panel, .inbox-panel, main").getBoundingClientRect();
          const range = document.createRange(); range.selectNodeContents(element); const text = range.getBoundingClientRect();
          return { selector: element.id || element.textContent.trim(), width: r.width,
            control_inside_panel: r.left >= panel.left && r.right <= panel.right,
            label_inside_control: text.left >= r.left && text.right <= r.right && text.top >= r.top && text.bottom <= r.bottom };
        }));
        observations.push({ width, controls: checks });
        assert.ok(checks.length > 0);
        for (const check of checks) {
          assert.ok(check.control_inside_panel, `${check.selector} must stay within its panel at ${width}px`);
          assert.ok(check.label_inside_control, `${check.selector} label must remain visible at ${width}px`);
        }
      }
      for (const tab of ["evidence", "trace", "summary"]) {
        await page.locator(`[data-tab="${tab}"]`).focus();
        const visible = await page.locator(`[data-tab="${tab}"]`).evaluate(element => {
          const r = element.getBoundingClientRect(), region = element.parentElement.getBoundingClientRect();
          return r.left >= region.left && r.right <= region.right;
        });
        assert.ok(visible, `Keyboard focus must reveal ${tab} tab at ${width}px`);
      }
    }
  });
} finally {
  const report = await finishHarness(path.resolve(output), roles.length + 1);
  const { writeFile } = await import("node:fs/promises");
  await writeFile(path.resolve(output), JSON.stringify({ ...report, observations }, null, 2) + "\n");
}
