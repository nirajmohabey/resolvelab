/* Browser OIDC code flow. Provider secrets and access tokens never enter persistent storage. */
import * as oauth from "oauth4webapi";

const PENDING_KEY = "resolvelab.oidc.pending.v1";
const MAX_AGE_MS = 10 * 60 * 1000;

function configuration(value) {
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
  const response = await fetch("/v1/auth/config", {
    credentials: "same-origin",
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok)
    throw new Error("Workspace sign-in is temporarily unavailable.");
  const config = configuration(await response.json());
  const current = new URL(location.href);
  if (!current.searchParams.has("code") && !current.searchParams.has("error"))
    return { config, token: null };
  const stored = sessionStorage.getItem(PENDING_KEY);
  sessionStorage.removeItem(PENDING_KEY);
  history.replaceState({}, "", new URL("/", location.origin));
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
