/* Browser OIDC code flow. Provider secrets and access tokens never enter persistent storage. */
import * as oauth from "oauth4webapi";
import { createAuthClient } from "better-auth/client";
import { emailOTPClient, jwtClient } from "better-auth/client/plugins";

const PENDING_KEY = "resolvelab.oidc.pending.v1";
const MAX_AGE_MS = 10 * 60 * 1000;
let neonClient = null;
let resetToken = null;
let resetIssue = false;
const resetCallback = new URL(location.href);
if (resetCallback.searchParams.has("reset_password")) {
  const value = resetCallback.searchParams.get("token");
  resetToken = value && value.length <= 4096 ? value : null;
  resetIssue = !resetToken;
  resetCallback.searchParams.delete("token"); resetCallback.searchParams.delete("reset_password");
  resetCallback.searchParams.delete("error");
  history.replaceState({}, "", resetCallback);
}

function neon(value) {
  if (value?.mode !== "neon" || !value.configured || value.auth_base_url !== "/auth")
    throw new Error("Workspace sign-in is not configured.");
  return neonClient ??= createAuthClient({ baseURL: new URL("/auth", location.origin).href,
    plugins: [jwtClient(), emailOTPClient()], fetchOptions: { timeout: 15000, retry: 0 } });
}
async function neonAuthentication(config, credentials) {
  const client = neon(config);
  if (credentials) {
    const result = await client.signIn.email(credentials);
    if (result.error) throw new Error(result.error.status === 403
      ? "Verify your email before signing in. Contact the workspace owner if you need access."
      : "Sign-in failed. Check your email and password, then try again.");
  }
  const session = await client.getSession();
  if (session.error) throw new Error("Sign-in is temporarily unavailable. Try again.");
  if (!session.data?.session) return { config, token: null };
  // The opaque provider cookie-session token never authorizes an app request.
  const result = await client.token();
  const token = result.data?.token;
  if (result.error || typeof token !== "string" || token.split(".").length !== 3)
    throw new Error("Your sign-in could not be verified. Sign in again.");
  let expiresAt = null;
  try { const claims = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))); if (Number.isFinite(claims.exp)) expiresAt = claims.exp * 1000; } catch {}
  return { config, token, expiresAt };
}
export function signInWithPassword(config, credentials) { return neonAuthentication(configuration(config), credentials); }
export async function accountAction(config, action, values) {
  const provider = neon(configuration(config));
  const result = action === "signup" ? await provider.signUp.email({ email: values.email, password: values.password, name: values.name, callbackURL: location.origin + "/" })
    : action === "verify" ? await provider.emailOtp.verifyEmail({ email: values.email, otp: values.code })
    : action === "resend" ? await provider.emailOtp.sendVerificationOtp({ email: values.email, type: "email-verification" })
    : action === "reset-request" ? await provider.emailOtp.requestPasswordReset({ email: values.email })
    : action === "reset-code" ? await provider.emailOtp.resetPassword({ email: values.email, otp: values.code, password: values.password })
    : await provider.resetPassword({ newPassword: values.password, token: resetToken || "" });
  if (result.error) throw new Error(action === "reset-confirm" ? "The password could not be reset. Check the new password or request a fresh reset link." : "The request could not be completed. Check your details and try again.");
  if (action === "reset-confirm") { resetToken = null; resetIssue = false; }
  return action === "signup" && result.data?.user && !result.data.user.emailVerified;
}
export async function signOutAuthentication(config) {
  clearAuthentication();
  if (config?.mode === "neon") {
    const result = await neon(config).signOut();
    if (result.error) throw new Error("The workbench disconnected, but provider sign-out failed. Try signing out again.");
  }
}

function configuration(value) {
  if (value?.mode === "neon") {
    if (typeof value.configured !== "boolean" || value.auth_base_url !== "/auth")
      throw new Error("Workspace sign-in configuration is invalid.");
    return value;
  }
  if (value?.mode === "fixture") {
    if (value.local !== true || value.demo !== true || value.configured !== true)
      throw new Error("Fixture sign-in is not available in this environment.");
    return value;
  }
  if (value?.mode !== "oidc") throw new Error("Workspace sign-in configuration is invalid.");
  if (!value.configured) return value;
  for (const name of [
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "jwks_uri",
  ]) {
    const url = new URL(value[name]);
    if (url.protocol !== "https:" || url.username || url.password || url.hash)
      throw new Error("Workspace sign-in configuration is invalid.");
  }
  if (
    !value.client_id ||
    !["RS256", "ES256"].includes(value.algorithm) ||
    !String(value.scope).split(/\s+/).includes("openid")
  )
    throw new Error("Workspace sign-in configuration is incomplete.");
  return value;
}

function protocol(config) {
  const server = {
    issuer: config.issuer,
    authorization_endpoint: config.authorization_endpoint,
    token_endpoint: config.token_endpoint,
    jwks_uri: config.jwks_uri,
    code_challenge_methods_supported: ["S256"],
    id_token_signing_alg_values_supported: [config.algorithm],
  };
  const client = {
    client_id: config.client_id,
    token_endpoint_auth_method: "none",
    id_token_signed_response_alg: config.algorithm,
  };
  const allowed = new Set([config.token_endpoint, config.jwks_uri]);
  const options = {
    signal: () => AbortSignal.timeout(10_000),
    [oauth.customFetch]: (url, init) => {
      if (!allowed.has(String(url)))
        throw new Error("Unexpected identity endpoint.");
      return fetch(url, {
        ...init,
        redirect: "error",
        credentials: "omit",
        cache: "no-store",
      });
    },
  };
  return { server, client, options };
}

export async function readAuthentication() {
  const current = new URL(location.href);
  const callback = current.searchParams.has("code") || current.searchParams.has("error");
  let stored = null;
  if (callback) {
    // Consume callback credentials before a configuration request can fail.
    try {
      stored = sessionStorage.getItem(PENDING_KEY);
    } finally {
      try { sessionStorage.removeItem(PENDING_KEY); }
      finally { history.replaceState({}, "", new URL("/", location.origin)); }
    }
  }
  const response = await fetch("/v1/auth/config", {
    credentials: "same-origin",
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok)
    throw new Error("Workspace sign-in is temporarily unavailable.");
  const config = configuration(await response.json());
  if (config.mode === "neon") {
    if (callback) throw new Error("Start a new workspace sign-in request.");
    if (resetToken) return { config, token: null, resetPending: true };
    if (resetIssue) return { config, token: null, resetIssue: true };
    if (!config.configured) return { config, token: null };
    try { return await neonAuthentication(config); }
    catch {
      return { config, token: null, authenticationError: "Existing sign-in could not be checked. Sign in again or try later." };
    }
  }
  if (!callback) return { config, token: null };
  try {
    const pending = stored ? JSON.parse(stored) : null;
    if (
      config.mode !== "oidc" ||
      !config.configured ||
      !pending ||
      typeof pending.createdAt !== "number" ||
      !Number.isFinite(pending.createdAt) ||
      ["state", "nonce", "verifier", "issuer", "clientId", "redirectUri"].some(
        (key) => typeof pending[key] !== "string" || !pending[key].length,
      ) ||
      Date.now() - pending.createdAt > MAX_AGE_MS ||
      Date.now() < pending.createdAt ||
      pending.issuer !== config.issuer ||
      pending.clientId !== config.client_id ||
      pending.redirectUri !== new URL("/", location.origin).href
    )
      throw new Error("Expired sign-in.");
    const { server, client, options } = protocol(config);
    const parameters = oauth.validateAuthResponse(
      server,
      client,
      current,
      pending.state,
    );
    const response = await oauth.authorizationCodeGrantRequest(
      server,
      client,
      oauth.None(),
      parameters,
      pending.redirectUri,
      pending.verifier,
      options,
    );
    const result = await oauth.processAuthorizationCodeResponse(
      server,
      client,
      response,
      { expectedNonce: pending.nonce, requireIdToken: true },
    );
    await oauth.validateApplicationLevelSignature(server, response, options);
    if (String(result.token_type).toLowerCase() !== "bearer")
      throw new Error("Unsupported session.");
    return {
      config,
      token: result.access_token,
      expiresAt: Number.isFinite(result.expires_in)
        ? Date.now() + result.expires_in * 1000
        : null,
    };
  } catch {
    throw new Error(
      "Sign-in could not be completed. Start a new sign-in request.",
    );
  }
}

export async function beginSignIn(value) {
  const config = configuration(value);
  if (config.mode !== "oidc" || !config.configured)
    throw new Error("Sign-in is not configured for this workspace.");
  const pending = {
    createdAt: Date.now(),
    issuer: config.issuer,
    clientId: config.client_id,
    redirectUri: new URL("/", location.origin).href,
    verifier: oauth.generateRandomCodeVerifier(),
    state: oauth.generateRandomState(),
    nonce: oauth.generateRandomNonce(),
  };
  const target = new URL(config.authorization_endpoint);
  target.searchParams.set("client_id", config.client_id);
  target.searchParams.set("redirect_uri", pending.redirectUri);
  target.searchParams.set("response_type", "code");
  target.searchParams.set("scope", config.scope);
  target.searchParams.set("state", pending.state);
  target.searchParams.set("nonce", pending.nonce);
  target.searchParams.set(
    "code_challenge",
    await oauth.calculatePKCECodeChallenge(pending.verifier),
  );
  target.searchParams.set("code_challenge_method", "S256");
  if (config.audience) target.searchParams.set("audience", config.audience);
  sessionStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  location.assign(target.href);
}

export function clearAuthentication() {
  sessionStorage.removeItem(PENDING_KEY);
}
