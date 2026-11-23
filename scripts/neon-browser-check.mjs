/** Production login UX with real Better Auth client and mocked provider/API. */
import assert from "node:assert/strict";
import path from "node:path";
import { writeFile } from "node:fs/promises";
import { startHarness, scenario, finishHarness } from "./ui-race-check.mjs";
import { config, installProvider, password } from "./neon-provider-fixture.mjs";
assert.ok(process.argv[2], "Provide explicit scratch report path");
const storage = [];
async function run(name, options, test) {
  let provider;
  await scenario(name, "fixture", async context => {
    if (!options.signedIn && !options.resetCallback) await context.page.locator("#loginEmail").waitFor();
    await test({ ...context, provider });
    storage.push({ name, ...await provider.assertNoPersistence() });
  }, { signedIn: false, urlSuffix: options.urlSuffix, prepare: async ({ page, setReply }) => {
    setReply("GET", "/v1/auth/config", () => ({ body: config }));
    if (options.membershipDenied) setReply("GET", "/v1/session", () => ({ status: options.unregistered ? 401 : 403, body: { error: { code: options.unregistered ? "identity_not_registered" : "membership_required" } } }));
    provider = await installProvider(page, options);
  } });
}
const submit = async page => { await page.locator("#loginEmail").fill("browser-fixture@example.invalid"); await page.locator("#loginPassword").fill(password); await page.locator("#neonSignInForm button[type=submit]").click(); };
await startHarness();
try {
  await run("Neon sign-in uses explicit JWT and signs out without persistence", { holdSignIn: true }, async ({ page, provider, requests }) => {
    await submit(page); await provider.signInObserved;
    assert.equal(await page.locator("#loginPassword").inputValue(), "");
    assert.equal(await page.locator("#loginPassword").isDisabled(), true);
    provider.releaseSignIn(); await page.locator("#detail h2").filter({ hasText: "Harness case A" }).waitFor();
    assert.ok(provider.sentTokens.length === 1);
    assert.ok(requests.some(request => request.pathname === "/v1/session"));
    await page.locator("#signOut").click(); await page.locator("#notice").filter({ hasText: "signed out of the workbench" }).waitFor();
    assert.ok(provider.calls.some(call => call.path === "/auth/sign-out"));
  });
  await run("Authenticated unenrolled account requests workspace access", { membershipDenied: true }, async ({ page, requests }) => {
    await submit(page); await page.locator("#notice").filter({ hasText: "no active workspace membership" }).waitFor();
    assert.ok(!requests.some(request => ["/v1/cases", "/v1/dashboard"].includes(request.pathname)));
    assert.equal(await page.locator("#newCase").isDisabled(), true);
  });
  await run("Invalid password clears the password and supports retry", { passwordFailure: true }, async ({ page, provider }) => {
    await submit(page); await page.locator("#notice").filter({ hasText: "Check your email and password" }).waitFor();
    assert.equal(await page.locator("#loginPassword").inputValue(), "");
    assert.equal(provider.sentTokens.length, 0);
    assert.equal(await page.locator("#loginPassword").isDisabled(), false);
  });
  await run("Opaque provider session token cannot authorize the app", { opaqueToken: true }, async ({ page, requests }) => {
    await submit(page); await page.locator("#notice").filter({ hasText: "could not be verified" }).waitFor();
    assert.ok(!requests.some(request => request.pathname === "/v1/session"));
  });
  await run("Provider sign-out failure still clears local workspace", { signOutFailure: true }, async ({ page }) => {
    await submit(page); await page.locator("#detail h2").filter({ hasText: "Harness case A" }).waitFor();
    await page.locator("#signOut").click(); await page.locator("#notice").filter({ hasText: "provider sign-out failed" }).waitFor();
    assert.equal(await page.locator("#newCase").isDisabled(), true);
  });
  await run("Reload resumes provider cookie and obtains a fresh JWT", { signedIn: true }, async ({ page, provider }) => {
    await page.locator("#detail h2").filter({ hasText: "Harness case A" }).waitFor();
    await page.reload(); await page.locator("#detail h2").filter({ hasText: "Harness case A" }).waitFor();
    assert.equal(provider.sentTokens.length, 2);
    assert.notEqual(provider.sentTokens[0], provider.sentTokens[1]);
  });
  await run("Unregistered verified identity receives enrollment guidance", { membershipDenied: true, unregistered: true }, async ({ page }) => {
    await submit(page); await page.locator("#notice").filter({ hasText: "no active workspace membership" }).waitFor();
  });
  await run("Expired JWT clears workspace without persisting credentials", { expiresIn: 2 }, async ({ page }) => {
    await submit(page); await page.locator("#detail h2").filter({ hasText: "Harness case A" }).waitFor();
    await page.locator("#notice").filter({ hasText: "session expired" }).waitFor();
    assert.equal(await page.locator("#newCase").isDisabled(), true);
  });
  await run("Signup and verification never assign workspace membership", { signupUnverified: true }, async ({ page, provider, requests }) => {
    await page.getByRole("button", { name: "Create an account", exact: true }).click();
    await page.locator("#loginName").fill("Fictional test user"); await page.locator("#loginEmail").fill("browser-fixture@example.invalid"); await page.locator("#loginPassword").fill(password);
    await page.getByRole("button", { name: "Create account", exact: true }).click(); await page.locator("#verificationCode").waitFor();
    await page.getByRole("button", { name: "Resend verification email" }).click(); await page.locator("#notice").filter({ hasText: "new code or link" }).waitFor();
    await page.locator("#verificationCode").fill("123456"); await page.getByRole("button", { name: "Verify email", exact: true }).click();
    await page.locator("#notice").filter({ hasText: "Email verified" }).waitFor();
    assert.ok(provider.calls.some(call => call.path === "/auth/email-otp/verify-email")); assert.ok(!requests.some(request => request.pathname === "/v1/session"));
  });
  await run("Password recovery uses an emailed code without URL credentials", {}, async ({ page, provider }) => {
    await page.getByRole("button", { name: "Forgot password?" }).click(); await page.locator("#loginEmail").fill("browser-fixture@example.invalid");
    await page.getByRole("button", { name: "Send reset code", exact: true }).click(); await page.locator("#notice").filter({ hasText: "If the account exists" }).waitFor();
    assert.ok(provider.calls.some(call => call.path === "/auth/email-otp/request-password-reset"));
    await page.getByLabel("New password", { exact: true }).fill(password); await page.locator("#resetCode").fill("123456");
    await page.getByRole("button", { name: "Set new password", exact: true }).click(); await page.locator("#notice").filter({ hasText: "Password reset" }).waitFor();
    assert.ok(!page.url().includes("token="));
  });
  await run("Reset callback consumes URL credentials before config and resets password", { resetCallback: true, signedIn: true, resetToken: "synthetic-reset-credential", urlSuffix: "/?reset_password=true&token=synthetic-reset-credential" }, async ({ page, requests }) => {
    await page.getByLabel("New password", { exact: true }).waitFor(); assert.ok(!page.url().includes("synthetic-reset-credential"));
    assert.ok(!requests.some(request => request.pathname === "/v1/session"));
    await page.getByLabel("New password", { exact: true }).fill(password); await page.getByRole("button", { name: "Set new password", exact: true }).click();
    await page.locator("#notice").filter({ hasText: "Password reset" }).waitFor(); assert.equal(await page.locator("#loginPassword").inputValue(), "");
  });
  await run("Expired reset callback removes URL error and offers a fresh link", { resetCallback: true, urlSuffix: "/?reset_password=true&error=INVALID_TOKEN" }, async ({ page }) => {
    await page.locator("#notice").filter({ hasText: "reset link is invalid or expired" }).waitFor(); assert.ok(!page.url().includes("INVALID_TOKEN"));
    assert.equal(await page.getByRole("button", { name: "Send reset code", exact: true }).isVisible(), true);
  });
  await run("Invalid reset code clears secrets and permits a fresh code", { invalidOtp: true }, async ({ page }) => {
    await page.getByRole("button", { name: "Forgot password?" }).click(); await page.locator("#loginEmail").fill("browser-fixture@example.invalid"); await page.getByRole("button", { name: "Send reset code", exact: true }).click(); await page.locator("#resetCode").waitFor();
    await page.getByLabel("New password", { exact: true }).fill(password); await page.locator("#resetCode").fill("123456"); await page.getByRole("button", { name: "Set new password", exact: true }).click();
    await page.locator("#notice").filter({ hasText: "could not be completed" }).waitFor(); assert.equal(await page.locator("#resetCode").inputValue(), ""); assert.equal(await page.locator("#loginPassword").inputValue(), "");
    await page.getByRole("button", { name: "Back to sign in", exact: true }).click(); assert.equal(await page.locator("#loginPassword").isEnabled(), true);
  });
  await run("Neon sign-out from Evaluation exposes a reachable login form", {}, async ({ page }) => {
    await submit(page); await page.locator("#detail h2").filter({ hasText: "Harness case A" }).waitFor(); await page.locator('[data-view="evaluation"]').click(); await page.locator('#evalMode').waitFor(); await page.locator('#signOut').click();
    await page.locator('#notice').filter({hasText:'signed out of the workbench'}).waitFor(); assert.equal(await page.locator('#loginEmail').isVisible(),true);
  });
  await run("Signed-out mobile workspace prioritizes account access and truthful copy", {}, async ({ page }) => {
    await page.setViewportSize({width:375,height:900}); await page.evaluate(()=>scrollTo(0,0));
    assert.ok(await page.locator('#neonSignInForm').evaluate(el=>el.getBoundingClientRect().top<innerHeight),'Account form must be in the first screen');
    assert.doesNotMatch(await page.locator('.fixture-disclaimer').textContent(),/Synthetic retailer|No real order|Demo clock/);
  });
} finally {
  const report = await finishHarness(path.resolve(process.argv[2]), 15);
  await writeFile(path.resolve(process.argv[2]), JSON.stringify({ ...report, storage_checks: storage, external_identity_provider_tested: false }, null, 2) + "\n");
}
