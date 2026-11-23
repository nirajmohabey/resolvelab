/** Direct official Better Auth client storage contract. Fictional provider only.
 * Signup is tested here as a dependency contract, not exposed as app enrollment.
 */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";
import { installProvider, password } from "./neon-provider-fixture.mjs";
assert.ok(process.argv[2], "Provide explicit scratch report path");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const hashes = async () => Object.fromEntries(await Promise.all(["package.json", "package-lock.json", "web/auth-client-src.js"].map(async file => [file, createHash("sha256").update(await readFile(path.join(root, file))).digest("hex")])));
const before = await hashes();
const bundle = await build({ stdin: { contents: `import {createAuthClient} from 'better-auth/client';import {jwtClient} from 'better-auth/client/plugins';window.provider=createAuthClient({baseURL:new URL('/auth',location.origin).href,plugins:[jwtClient()],fetchOptions:{timeout:15000,retry:0}});`, resolveDir: root }, bundle: true, write: false, platform: "browser", format: "esm" });
const server = createServer((request, response) => { response.setHeader("Content-Type", request.url === "/client.js" ? "text/javascript" : "text/html"); response.end(request.url === "/client.js" ? bundle.outputFiles[0].text : '<html lang="en"><title>Fictional auth client fixture</title><script type="module" src="/client.js"></script></html>'); });
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext(), page = await context.newPage(), errors = [];
page.on("pageerror", error => errors.push(error.message));
const fixture = await installProvider(page);
let checks, storage;
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`); await page.waitForFunction(() => !!window.provider);
  checks = await page.evaluate(async ({ password }) => {
    const provider = window.provider, credentials = { email: "browser-fixture@example.invalid", password };
    const signup = await provider.signUp.email({ ...credentials, name: "Fictional test account" });
    const firstLogout = await provider.signOut();
    const login = await provider.signIn.email(credentials);
    const profile = await provider.getSession();
    const first = await provider.token(), refreshed = await provider.token();
    const logout = await provider.signOut(), after = await provider.getSession();
    return { signup: !signup.error, signin: !login.error, profile_session_present: !!profile.data?.session,
      explicit_jwt_received: first.data?.token?.split(".").length === 3,
      refresh_returns_new_jwt: first.data?.token !== refreshed.data?.token,
      signout: !firstLogout.error && !logout.error, provider_session_cleared: !after.data?.session };
  }, { password });
  storage = await fixture.assertNoPersistence();
  assert.ok(Object.values(checks).every(Boolean)); assert.deepEqual(errors, []);
} finally {
  await browser.close(); await new Promise(resolve => server.close(resolve));
  const after = await hashes(), source_stable = JSON.stringify(before) === JSON.stringify(after);
  const report = { recorded_at: new Date().toISOString(), scope: "Official better-auth1.7.27 client and JWT plugin with fictional intercepted provider only. Signup does not create an actual account or application membership.", source_hashes: after, source_stable, checks, storage, page_errors: errors, passed: !!checks && Object.values(checks).every(Boolean) && source_stable && !errors.length };
  await mkdir(path.dirname(path.resolve(process.argv[2])), { recursive: true }); await writeFile(path.resolve(process.argv[2]), JSON.stringify(report, null, 2) + "\n");
  if (!report.passed) process.exitCode = 1;
  console.log(JSON.stringify({ passed: report.passed, checks, storage, source_stable }));
}
