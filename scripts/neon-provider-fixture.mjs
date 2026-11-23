/** Fictional provider transport for browser regressions. No external IdP. */
import assert from "node:assert/strict";
export const config = { mode: "neon", configured: true, auth_base_url: "/auth", local: false, demo: false, fixture_login_enabled: false, identities: [] };
export const password = "synthetic-browser-password";
export const opaque = "synthetic-opaque-provider-session";
export async function installProvider(page, options = {}) {
  const calls = [], sentTokens = [], applicationBearers = [];
  let signedIn = options.signedIn ?? false, releaseSignIn, observedSignIn;
  const signInObserved = new Promise(resolve => { observedSignIn = resolve; });
  const signInGate = new Promise(resolve => { releaseSignIn = resolve; });
  const token = () => ["eyJhbGciOiJFZERTQSJ9", Buffer.from(JSON.stringify({ sub: "synthetic-user", exp: Math.floor(Date.now() / 1000) + (options.expiresIn ?? 600), refresh: sentTokens.length })).toString("base64url"), "synthetic-signature"].join(".");
  const user = { id: "synthetic-user", email: "browser-fixture@example.invalid", name: "Fictional test user", emailVerified: !options.signupUnverified, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
  await page.addInitScript(() => {
    window.__credentialWrites = [];
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) { window.__credentialWrites.push(String(value)); return set.call(this, key, value); };
    if (window.BroadcastChannel) {
      const post = BroadcastChannel.prototype.postMessage;
      BroadcastChannel.prototype.postMessage = function (value) { window.__credentialWrites.push(JSON.stringify(value)); return post.call(this, value); };
    }
  });
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    const bearer = request.headers().authorization;
    if (path.startsWith("/v1/") && bearer) applicationBearers.push(bearer);
  });
  await page.route("**/auth/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (!pathname.startsWith("/auth/")) return route.fallback();
    calls.push({ path: pathname, method: route.request().method() });
    const send = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (pathname === "/auth/sign-in/email") {
      observedSignIn(); if (options.holdSignIn) await signInGate;
      if (options.passwordFailure) return send({ code: "INVALID_EMAIL_OR_PASSWORD", message: "Invalid credentials" }, 401);
      signedIn = true; return send({ token: opaque, user, redirect: false });
    }
    if (pathname === "/auth/sign-up/email") {
      const body = JSON.parse(route.request().postData()); assert.ok(!("role" in body) && !("tenant_id" in body), "Signup must not assign app permissions");
      signedIn = !options.signupUnverified; return send({ token: opaque, user });
    }
    if (pathname === "/auth/email-otp/send-verification-otp") {
      const body = JSON.parse(route.request().postData()); assert.deepEqual(body, {email: "browser-fixture@example.invalid", type: "email-verification"}); return send({success: true});
    }
    if (pathname === "/auth/send-verification-email") return send({ status: true });
    if (pathname === "/auth/email-otp/verify-email") { user.emailVerified = true; return send({ status: true, user }); }
    if (pathname === "/auth/email-otp/request-password-reset") return send({ success: true });
    if (pathname === "/auth/email-otp/reset-password") {
      const body = JSON.parse(route.request().postData()); assert.equal(body.otp, "123456"); assert.ok(body.password.length >= 8);
      if (options.invalidOtp) return send({ code: "INVALID_OTP", message: "Invalid code" }, 400);
      return send({ success: true });
    }
    if (pathname === "/auth/request-password-reset") {
      const body = JSON.parse(route.request().postData());
      assert.equal(new URL(body.redirectTo).origin, new URL(route.request().url()).origin);
      assert.equal(new URL(body.redirectTo).searchParams.get("reset_password"), "true");
      return send({ status: true });
    }
    if (pathname === "/auth/reset-password") {
      const body = JSON.parse(route.request().postData());
      if (options.resetToken) assert.equal(body.token, options.resetToken);
      assert.ok(body.newPassword.length >= 8); return send({ status: true });
    }
    if (pathname === "/auth/get-session") {
      if (options.sessionFailure) return send({code:"SESSION_UNAVAILABLE"},options.sessionFailure);
      return send(signedIn ? { user, session: { id: "synthetic-session", userId: user.id, token: opaque, expiresAt: new Date(Date.now() + 3600000).toISOString(), createdAt: user.createdAt, updatedAt: user.updatedAt } } : null);
    }
    if (pathname === "/auth/token") {
      if (options.tokenFailure) return send({code:"TOKEN_UNAVAILABLE"},options.tokenFailure);
      if (!signedIn) return send({ code: "UNAUTHORIZED" }, 401);
      const value = options.opaqueToken ? opaque : token(); sentTokens.push(value); return send({ token: value });
    }
    if (pathname === "/auth/sign-out") {
      if (options.signOutFailure) return send({ code: "SERVICE_UNAVAILABLE" }, 503);
      signedIn = false; return send({ success: true });
    }
    return send({ code: "UNEXPECTED_PROVIDER_ROUTE" }, 404);
  });
  return { calls, sentTokens, signInObserved, releaseSignIn,
    async assertNoPersistence() {
      const values = await page.evaluate(() => [...window.__credentialWrites, ...Object.values(localStorage), ...Object.values(sessionStorage)]);
      for (const secret of [password, opaque, "123456", options.resetToken, ...sentTokens].filter(Boolean)) assert.ok(values.every(value => !String(value).includes(secret)), "Credentials must never enter browser storage or broadcast payloads");
      assert.ok(applicationBearers.every(value => sentTokens.some(token => value === `Bearer ${token}`) && value !== `Bearer ${opaque}`), "Only explicit JWT-endpoint tokens may authorize application requests");
      return { writes_examined: values.length, password_persisted: false, opaque_session_persisted: false, jwt_persisted: false };
    } };
}
