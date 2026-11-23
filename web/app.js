import {
  readAuthentication,
  beginSignIn,
  clearAuthentication,
} from "./auth-client.js";
("use strict");
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const labels = {
  new: "New",
  triaged: "Triaged",
  needs_info: "Needs information",
  draft_ready: "Draft ready",
  awaiting_approval: "Awaiting approval",
  executing: "Executing",
  resolved: "Resolved",
  escalated: "Escalated",
  failed: "Failed",
};
const state = {
  sessionGeneration: 0,
  caseSelectionRequest: 0,
  evaluationReportRequest: 0,
  inboxRequest: 0,
  evaluationViewRequest: 0,
  releaseViewRequest: 0,
  createRequest: 0,
  evaluationQueueRequest: 0,
  pendingCreate: null,
  inlineKeys: new Map(),
  inlineOperation: null,
  pendingEvaluation: null,
  creating: false,
  evaluationQueuing: false,
  evaluationSelection: null,
  token: null,
  principal: null,
  cases: [],
  selected: null,
  selectingId: null,
  filter: "all",
  query: "",
  inboxOffset: 0,
  inboxLimit: 25,
  inboxTotal: 0,
  inboxLoading: false,
  tab: "summary",
  releases: [],
  evaluations: [],
};
class SessionChanged extends Error {
  constructor() {
    super("The workspace session changed.");
  }
}
function checkSession(generation, token) {
  if (state.sessionGeneration !== generation || state.token !== token)
    throw new SessionChanged();
}
const badge = (s) =>
  `<span class="badge ${esc(s)}">${esc(labels[s] || s)}</span>`;
const short = (s) =>
  String(s || "")
    .slice(0, 8)
    .toUpperCase();
function notify(message, error = false) {
  const el = $("#notice");
  el.textContent = message;
  el.className = "notice" + (error ? " error" : "");
  el.hidden = false;
}
async function api(path, options = {}) {
  const generation = state.sessionGeneration, token = state.token;
  if (!token && path !== "/v1/demo/session") throw new SessionChanged();
  const headers = {
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
    ...options.headers,
  };
  if (options.method && options.method !== "GET")
    headers["Idempotency-Key"] = options.key || crypto.randomUUID();
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch (error) {
    checkSession(generation, token);
    throw error;
  }
  let result;
  try {
    result = await response.json();
  } catch {
    result = {};
  }
  if (!result || typeof result !== "object" || Array.isArray(result)) result = {};
  checkSession(generation, token);
  if (!response.ok) {
    if (
      response.status === 401 &&
      state.authConfig?.mode === "oidc" &&
      state.token
    ) {
      requireSignIn("Your session expired. Sign in again.");
      throw new SessionChanged();
    }
    const err = result.error || result.detail || result;
    throw new Error(
      typeof err === "string"
        ? err
        : err.message || err.code || `Request failed (${response.status})`,
    );
  }
  return result;
}
function setBusy(button, busy, label) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = label || "Working…";
    button.disabled = true;
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.disabled = false;
  }
}
function canWrite() {
  return !!state.token && ["operator", "approver", "admin"].includes(state.principal?.role);
}
function setCreating(busy) {
  state.creating = busy;
  setBusy($("#createSubmit"), busy, "Saving…");
  $("#caseForm").setAttribute("aria-busy", String(busy));
  $$("#caseForm input, #caseForm textarea, #caseForm button").forEach(button => {
    button.disabled = busy || !canWrite();
  });
  $("#newCase").disabled = busy || !canWrite();
}
function resetInboxCriteria() {
  clearTimeout(searchTimer);
  state.query = "";
  state.filter = "all";
  state.inboxOffset = 0;
  $("#search").value = "";
  syncFilterButtons();
}
function syncFilterButtons() {
  $$("[data-filter]").forEach(button => {
    const active = button.dataset.filter === state.filter;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}
function renderPagination() {
  $("#previousPage").disabled = !state.token || state.inboxLoading || state.inboxOffset === 0;
  $("#nextPage").disabled = !state.token || state.inboxLoading || state.inboxOffset + state.cases.length >= state.inboxTotal;
  $("#pageStatus").textContent = state.inboxLoading ? "Loading cases…" : state.inboxTotal
    ? `${state.inboxOffset + 1}–${state.inboxOffset + state.cases.length} of ${state.inboxTotal} matching cases`
    : "No matching cases";
  $("#caseList").setAttribute("aria-busy", String(state.inboxLoading));
}
let expiryTimer;
let searchTimer;
async function applySession(token, principal, organization, preferredCaseId = null) {
  const generation = ++state.sessionGeneration;
  state.pendingCreate = null;
  state.inlineKeys.clear();
  state.inlineOperation = null;
  state.pendingEvaluation = null;
  state.evaluationQueuing = false;
  state.evaluationQueueRequest++;
  state.createRequest++;
  const previous = state.selected,
    previousTenant = state.principal?.tenant_id;
  state.token = token;
  state.principal = principal;
  setCreating(false);
  $$("#search, [data-filter], #refresh, #refreshInbox, #refreshEvaluations").forEach(control => control.disabled = false);
  const fixture = state.authConfig?.mode === "fixture";
  $("#apiReference").hidden = !fixture;
  $(".build-label").textContent = fixture ? "v0.2 · Fixture retailer" : "v0.2 · Verified workspace";
  $("#customerContextNote").textContent = fixture ? "Customer identity comes from the signed fixture session." : "Customer identity comes from the verified server session.";
  $(".examples").hidden = !fixture;
  const name =
    organization?.display_name ||
    (fixture
      ? principal.tenant_id.startsWith("1111")
        ? "Alpha Retail"
        : "Beta Retail"
      : "Support workspace");
  $("#tenantName").innerHTML =
    esc(name) +
    "<small>" +
    (fixture ? "Fixture workspace" : "Verified workspace") +
    "</small>";
  $(".workspace-icon").textContent = name.slice(0, 1).toUpperCase();
  $("#avatar").textContent = principal.role.slice(0, 2).toUpperCase();
  $("#signIn").hidden = true;
  $("#signOut").hidden = fixture;
  $("#actor").hidden = !fixture;
  $(".identity-label").hidden = !fixture;
  $("#environmentTag").textContent = fixture ? "LOCAL" : "OIDC";
  $("#responseMode").textContent = fixture
    ? "Fixture workflow"
    : "Verified session";
  $(".fixture-disclaimer").textContent = fixture
    ? "Synthetic retailer · Demo clock: November 23, 2026, 12:00 UTC · No real order, payment, or email is changed."
    : "Verified customer context · Policy, ownership, current state, and approval checks control every action.";
  state.selected =
    preferredCaseId ? { case_id: preferredCaseId } : previous && previousTenant === principal.tenant_id
      ? { case_id: previous.case_id }
      : null;
  state.tab = "summary";
  $("#detail").innerHTML =
    '<div class="empty"><h3>Loading organization cases…</h3></div>';
  $$("[data-view]").forEach((button) => (button.disabled = false));
  await refresh();
  checkSession(generation, token);
  setCreating(false);
  if (!$("#view-evaluation").hidden) await evaluationView();
  checkSession(generation, token);
  if (!$("#view-releases").hidden) await releasesView();
  checkSession(generation, token);
  $("#notice").hidden = true;
}
function requireSignIn(message = "Sign in to your support workspace.") {
  state.sessionGeneration++;
  clearTimeout(expiryTimer);
  clearAuthentication();
  state.token = null;
  state.principal = null;
  state.selected = null;
  state.selectingId = null;
  state.cases = [];
  state.releases = [];
  state.evaluations = [];
  state.pendingCreate = null;
  state.inlineKeys.clear();
  state.inlineOperation = null;
  state.pendingEvaluation = null;
  state.evaluationQueuing = false;
  state.evaluationSelection = null;
  state.evaluationQueueRequest++;
  state.createRequest++;
  state.inboxRequest++;
  state.evaluationViewRequest++;
  state.releaseViewRequest++;
  state.inboxTotal = 0;
  state.inboxLoading = false;
  resetInboxCriteria();
  setCreating(false);
  renderPagination();
  $("#apiReference").hidden = true;
  $(".build-label").textContent = "v0.2 · Verified workspace";
  $("#actor").hidden = true;
  $(".identity-label").hidden = true;
  $("#signOut").hidden = true;
  $("#signIn").hidden = false;
  $("#signIn").textContent = "Sign in";
  $("#signIn").disabled = !(
    state.authConfig?.mode === "oidc" && state.authConfig?.configured
  );
  $("#newCase").disabled = true;
  $$("#search, [data-filter], #refresh, #refreshInbox, #refreshEvaluations").forEach(control => control.disabled = true);
  $$("[data-view]").forEach((button) => (button.disabled = true));
  $("#tenantName").innerHTML =
    "Support workspace<small>Sign-in required</small>";
  $("#environmentTag").textContent = "OIDC";
  $("#responseMode").textContent = "Sign-in required";
  [
    "#statTotal",
    "#statApproval",
    "#statResolved",
    "#statEscalated",
    "#navCount",
  ].forEach((id) => ($(id).textContent = "—"));
  $("#caseList").innerHTML =
    '<div class="empty">Sign in to view your organization’s cases.</div>';
  $("#caseCount").textContent = "—";
  $("#detail").innerHTML =
    '<div class="welcome-state"><div class="eyebrow">VERIFIED ACCESS</div><h2>Sign in to continue.</h2><p>Your organization and permissions are verified by the server.</p></div>';
  $("#evaluationContent").textContent = "";
  $("#releaseContent").textContent = "";
  $("#evaluationDetails").textContent = "";
  $("#caseForm").reset();
  if ($("#caseDialog").open) $("#caseDialog").close();
  if ($("#evaluationDialog").open) $("#evaluationDialog").close();
  notify(message);
}
async function login() {
  if (state.authConfig?.mode !== "fixture") return;
  const actor = $("#actor").value;
  const previous = { tenant: state.principal?.tenant_id, caseId: state.selected?.case_id || state.selectingId,
    query: state.query, filter: state.filter, offset: state.inboxOffset };
  requireSignIn("Connecting the selected fixture identity…");
  $("#actor").hidden = false;
  $(".identity-label").hidden = false;
  $("#signIn").hidden = true;
  $("#environmentTag").textContent = "LOCAL";
  $("#responseMode").textContent = "Connecting workspace";
  $(".build-label").textContent = "v0.2 · Fixture retailer";
  try {
    const result = await api("/v1/demo/session", {
      method: "POST",
      body: { actor },
    });
    if (previous.tenant === result.principal.tenant_id) {
      state.query = previous.query;
      state.filter = previous.filter;
      state.inboxOffset = previous.offset;
      $("#search").value = previous.query;
      syncFilterButtons();
    }
    await applySession(
      result.access_token || result.token,
      result.principal,
      result.organization,
      previous.tenant === result.principal.tenant_id ? previous.caseId : null,
    );
  } catch (error) {
    if (error instanceof SessionChanged) return;
    $("#signIn").hidden = false;
    $("#signIn").disabled = false;
    $("#signIn").textContent = "Reconnect workspace";
    notify(error.message, true);
  }
}
async function bootstrapAuthentication() {
  try {
    const result = await readAuthentication();
    state.authConfig = result.config;
    if (result.config.mode === "fixture") {
      await login();
      return;
    }
    if (!result.token) {
      requireSignIn(
        result.config.configured
          ? "Sign in with your organization’s identity provider."
          : result.config.message ||
              "Workspace sign-in has not been configured.",
      );
      return;
    }
    state.token = result.token;
    state.sessionGeneration++;
    const session = await api("/v1/session");
    await applySession(result.token, session.principal, session.organization);
    if (result.expiresAt)
      expiryTimer = setTimeout(
        () => requireSignIn("Your session expired. Sign in again to continue."),
        Math.max(0, result.expiresAt - Date.now()),
      );
  } catch (error) {
    if (error instanceof SessionChanged) return;
    try {
      state.authConfig = (await readAuthentication()).config;
    } catch {}
    requireSignIn(error.message);
  }
}
$("#signIn").onclick = () => state.authConfig?.mode === "fixture" ? login() :
  beginSignIn(state.authConfig).catch((error) => notify(error.message, true));
$("#signOut").onclick = () =>
  requireSignIn("This workbench session is disconnected.");
async function refresh({ resetSelection = false, preserveCaseId = null } = {}) {
  const request = ++state.inboxRequest;
  const selectedId = preserveCaseId || (resetSelection ? null : state.selected?.case_id || state.selectingId);
  if (resetSelection) {
    state.selected = null;
    state.selectingId = null;
    state.caseSelectionRequest++;
    state.tab = "summary";
    $("#detail").innerHTML = '<div class="empty"><h3>Loading matching cases…</h3></div>';
  }
  const selectionRequest = state.caseSelectionRequest;
  const params = new URLSearchParams({ limit: String(state.inboxLimit), offset: String(state.inboxOffset) });
  if (state.filter !== "all") params.set("state", state.filter);
  if (state.query) params.set("q", state.query);
  state.inboxLoading = true;
  renderPagination();
  const [caseResult, dashboardResult] = await Promise.allSettled([
    api(`/v1/cases?${params}`), api("/v1/dashboard"),
  ]);
  if (request !== state.inboxRequest) return;
  try {
    if (caseResult.status === "rejected") throw caseResult.reason;
    const result = caseResult.value;
    if (!Array.isArray(result.items)) throw new Error("The server returned an incomplete inbox. Refresh to try again.");
    state.cases = result.items;
    state.inboxTotal = Number.isInteger(result.total) && result.total >= 0 ? result.total : state.inboxOffset + result.items.length;
    if (!state.cases.length && state.inboxOffset > 0 && state.inboxTotal > 0) {
      const lastOffset = Math.floor((state.inboxTotal - 1) / state.inboxLimit) * state.inboxLimit;
      if (lastOffset !== state.inboxOffset) {
        state.inboxOffset = lastOffset;
        return refresh({ resetSelection: true });
      }
    }
    renderList();
    if (dashboardResult.status === "fulfilled") {
      const stats = dashboardResult.value, counts = stats.states || {};
      $("#statTotal").textContent = stats.total_cases ?? state.inboxTotal;
      $("#statApproval").textContent = counts.awaiting_approval || 0;
      $("#statResolved").textContent = counts.resolved || 0;
      $("#statEscalated").textContent = counts.escalated || 0;
      $("#navCount").textContent = stats.total_cases ?? state.inboxTotal;
    } else if (!(dashboardResult.reason instanceof SessionChanged)) {
      notify("Cases loaded, but dashboard totals are unavailable. Refresh to retry.", true);
    }
    if (selectionRequest !== state.caseSelectionRequest) return;
    const nextId = preserveCaseId || (state.cases.some(c => c.case_id === selectedId) ? selectedId : state.cases[0]?.case_id);
    if (nextId) await selectCase(nextId);
    else {
      state.selected = null;
      state.selectingId = null;
      $("#detail").innerHTML = '<div class="welcome-state"><h2>No matching cases.</h2><p>Adjust your search or filter to find another case.</p></div>';
    }
  } catch (error) {
    if (error instanceof SessionChanged || request !== state.inboxRequest) return;
    state.cases = [];
    state.inboxTotal = 0;
    state.selected = null;
    state.selectingId = null;
    $("#caseList").innerHTML = `<div class="empty"><h3>Cases are unavailable.</h3><p>${esc(error.message)}</p><button class="button secondary" id="retryInbox">Retry inbox</button></div>`;
    $("#detail").innerHTML = '<div class="empty"><h3>Waiting for the inbox.</h3><p>Retry to load a verified case before taking action.</p></div>';
    $("#retryInbox").onclick = () => refresh({ resetSelection: true });
  } finally {
    if (request === state.inboxRequest) {
      state.inboxLoading = false;
      renderPagination();
    }
  }
}
function renderList() {
  const cases = state.cases;
  $("#caseCount").textContent = state.inboxTotal;
  $("#caseList").innerHTML = cases.length
    ? cases
        .map(
          (c) =>
            `<button class="case-row ${(state.selectingId || state.selected?.case_id) === c.case_id ? "selected" : ""}" data-case="${esc(c.case_id)}" aria-pressed="${(state.selectingId || state.selected?.case_id) === c.case_id}"><div class="case-meta"><span>CASE ${short(c.case_id)}</span><span>${new Date(c.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></div><h3>${esc(c.message)}</h3><div class="case-bottom">${badge(c.state)}<span class="order-ref">${esc(c.order_ref || "No reference")}</span></div></button>`,
        )
        .join("")
    : '<div class="empty"><span class="empty-icon">▤</span><h3>No cases here yet.</h3><p>Create a case, or adjust your filter.</p></div>';
  $$("[data-case]").forEach(
    (b) => (b.onclick = () => selectCase(b.dataset.case)),
  );
}
async function selectCase(id) {
  const request = ++state.caseSelectionRequest;
  state.selected = null;
  state.selectingId = id;
  renderList();
  $("#detail").innerHTML = '<div class="empty" role="status"><h3>Loading the selected case…</h3></div>';
  try {
    const selected = await api(`/v1/cases/${id}`);
    if (request !== state.caseSelectionRequest) return;
    state.selected = selected;
    state.selectingId = null;
    renderList();
    renderDetail();
  } catch (e) {
    if (e instanceof SessionChanged) return;
    if (request !== state.caseSelectionRequest) return;
    $("#detail").innerHTML = `<div class="empty"><h3>This case could not be loaded.</h3><p>${esc(e.message)}</p><button class="button secondary" id="retryCase">Retry case</button></div>`;
    $("#retryCase").onclick = () => selectCase(id);
    notify(e.message, true);
  }
}
function renderDetail() {
  const c = state.selected;
  if (!c) return;
  const resolution = c.resolution;
  $("#detail").innerHTML =
    `<div class="detail-head"><div class="detail-topline"><span class="case-id">CASE ${short(c.case_id)}</span>${badge(c.state)}</div><h2>${esc(c.message)}</h2><div class="detail-subtitle"><span>Customer ${esc(c.customer_subject)}</span><span>Order ${esc(c.order_ref || "not provided")}</span><span>Version ${c.version}</span></div></div><div class="detail-tabs" role="tablist" aria-label="Case details">${[["summary", "Resolution"], ["evidence", "Policy evidence"], ["trace", "Execution trace"]].map(([tab, label]) => `<button id="tab-${tab}" class="${state.tab === tab ? "active" : ""}" data-tab="${tab}" role="tab" aria-selected="${state.tab === tab}" aria-controls="detailBody" tabindex="${state.tab === tab ? 0 : -1}">${label}</button>`).join("")}</div><div class="detail-body" id="detailBody" role="tabpanel" aria-labelledby="tab-${state.tab}" tabindex="0"></div>`;
  $$("[data-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        activateDetailTab(b.dataset.tab);
      }),
  );
  $(".detail-tabs").onkeydown = event => {
    const tabs = ["summary", "evidence", "trace"];
    const index = tabs.indexOf(state.tab);
    const next = event.key === "ArrowRight" ? tabs[(index + 1) % tabs.length]
      : event.key === "ArrowLeft" ? tabs[(index + tabs.length - 1) % tabs.length]
      : event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[2] : null;
    if (next) { event.preventDefault(); activateDetailTab(next); }
  };
  if (state.tab === "evidence") {
    loadEvidence(c);
    return;
  }
  if (state.tab === "trace") {
    loadTrace(c);
    return;
  }
  if (!resolution) {
    const canResolve = canWrite() && ["new", "triaged", "needs_info", "draft_ready", "failed"].includes(c.state);
    $("#detailBody").innerHTML =
      `<div class="resolution-empty"><h3>${c.state === "failed" ? "A dependency needs attention." : canResolve ? "Ready for a closer look." : "Case available for inspection."}</h3><p>${c.state === "failed" ? "The case and trace are preserved. An authorized operator can retry resolution with the current version." : "Inspect the permitted evidence and execution trace for this case."}</p>${canResolve ? `<button class="button primary" id="resolveCase">${c.state === "failed" ? "Retry resolution" : "Resolve this case"}</button>` : '<p class="role-hint">Resolution requires an authorized operator and an eligible case state.</p>'}</div>`;
    if ($("#resolveCase")) $("#resolveCase").onclick = resolveSelected;
    $("#detailBody").insertAdjacentHTML("beforeend", contextForm(c));
    bindInlineForms(c);
    return;
  }
  const r = resolution;
  const scopeRule = r.routing_result?.score_source === "scope_rule";
  const scoreText = scopeRule ? "Scope decision" : c.routing_score != null ? (100 * c.routing_score).toFixed(1) + "% routing score" : "Score unavailable";
  let html = `<div class="section-label">${scopeRule ? "SCOPE CHECK" : "TRAINED ROUTING"}</div><div class="routing-row"><span class="intent-label">${esc((c.intent || r.intent || "unavailable").replaceAll("_", " "))}</span><span class="score">${esc(scoreText)}</span></div><p class="score-caveat">${scopeRule ? "A versioned scope rule requires human review. This decision is not a model probability." : "A routing score measures intent confidence. It does not establish factual correctness or permission."}</p><div class="section-label">CUSTOMER RESPONSE</div><div class="answer-card"><p>${esc(r.answer)}</p><div class="answer-meta">${r.citations?.length ? `◈ ${r.citations.length} permitted policy section${r.citations.length === 1 ? "" : "s"} cited` : "Human review or additional information required"} <button class="text-button" id="inspectEvidence">Inspect evidence</button></div></div>`;
  if (r.missing_information?.length)
    html += `<p class="muted">Needed: ${esc(r.missing_information.join(", "))}</p>`;
  const p = c.proposals?.find((p) => p.state === "pending") || r.proposal;
  if (p && c.state === "awaiting_approval") {
    html += `<div class="action-card"><h3>◇ Cancellation proposed</h3><p>Order ${esc(p.order_ref || c.order_ref)} is eligible under the frozen policy. Preparing this proposal has not changed the order.</p><div class="action-grid"><div><span>ORDER VERSION</span><strong>v${p.expected_order_version}</strong></div><div><span>PROPOSAL DIGEST</span><strong>${esc((p.proposal_sha256 || "").slice(0, 16))}…</strong></div></div>${["approver", "admin"].includes(state.principal?.role) ? '<div class="action-footer"><small>Approval checks the exact proposal and current order again.</small><button class="button primary" id="approveProposal">Approve cancellation</button></div>' : `<div class="role-hint">${state.authConfig?.mode === "fixture" ? "Switch to Alpha · Approver to review and approve this proposal." : "An authorized approver must review this proposal."}</div>`}</div>`;
  }
  if (p && c.state === "awaiting_approval" && ["approver", "admin"].includes(state.principal?.role)) html += declineForm();
  if (c.state === "escalated" && c.proposals?.some(proposal => proposal.state === "rejected")) html += '<div class="answer-card"><div class="section-label">REVIEW DECISION</div><p>The proposal was declined and the case was escalated for human review. The decline operation did not execute a cancellation.</p></div>';
  html += contextForm(c);
  if (c.actions?.length)
    html += `<div class="answer-card"><div class="section-label">LOCAL ACTION RECEIPT</div><p>Cancellation completed. One durable action was recorded.</p><div class="compact-code">${esc(c.actions[0].action_id)}</div></div>`;
  html += `<div class="section-label">RELEASE SNAPSHOT</div><div class="compact-code">${esc(c.release?.model_version || c.release?.model_label || "Versioned routing")} · ${esc(c.release?.corpus_version || c.release?.corpus_label || "policy-v1")}<br>Release ${esc(c.release_id)}</div>`;
  $("#detailBody").innerHTML = html;
  $("#inspectEvidence").onclick = () => {
    activateDetailTab("evidence");
  };
  if ($("#approveProposal")) $("#approveProposal").onclick = () => approve(p);
  bindInlineForms(c, p);
}
const declineReasons = {
  policy_concern: "Policy or evidence concern",
  customer_requested: "Customer asked not to proceed",
  needs_manual_review: "Needs manual review",
  other: "Other review concern",
};
function contextForm(c) {
  if (c.state !== "needs_info" || !canWrite()) return "";
  return `<form id="contextForm" class="inline-review"><h3>Add order reference</h3><p class="muted">Update this case's claimed order reference. Its verified customer and pinned release stay unchanged. Save first, then explicitly run resolution.</p><label for="contextOrderRef">Order reference</label><input id="contextOrderRef" required maxlength="100" value="${esc(c.order_ref || "")}" autocomplete="off"><div class="inline-review-footer"><button class="button primary" id="saveOrderRef" type="submit">Save order reference</button><span data-inline-status class="muted" role="status"></span></div><p id="contextError" class="form-error" role="alert" hidden></p></form>`;
}
function declineForm() {
  return `<form id="declineForm" class="inline-review"><h3>Decline for human review</h3><p class="muted">Declining escalates this case and records the review decision. This operation does not execute a cancellation.</p><label for="declineReason">Review reason</label><select id="declineReason" required><option value="" disabled selected>Choose a reason</option>${Object.entries(declineReasons).map(([code, label]) => `<option value="${code}">${esc(label)}</option>`).join("")}</select><div class="inline-review-footer"><button class="button secondary" id="declineProposal" type="submit">Decline proposal</button><span data-inline-status class="muted" role="status"></span></div><p id="declineError" class="form-error" role="alert" hidden></p></form>`;
}
function bindInlineForms(c, p) {
  if ($("#contextForm")) {
    $("#contextForm").onsubmit = event => saveOrderReference(event, c);
    $("#contextOrderRef").oninput = () => $("#contextOrderRef").setCustomValidity("");
  }
  if ($("#declineForm")) $("#declineForm").onsubmit = event => declineProposal(event, c, p);
  syncInlineControls();
}
function syncInlineControls() {
  const busy = !!state.inlineOperation;
  ["#contextForm", "#declineForm"].forEach(id => {
    const form = $(id);
    if (!form) return;
    const allowed = id === "#contextForm" ? canWrite() : !!state.token && ["approver", "admin"].includes(state.principal?.role);
    form.setAttribute("aria-busy", String(busy));
    [...form.querySelectorAll("input, select, button")].forEach(control => control.disabled = busy || !allowed);
    form.querySelector("[data-inline-status]").textContent = busy ? "Saving a case update. Please wait." : "";
  });
  if ($("#resolveCase")) $("#resolveCase").disabled = busy || !canWrite();
  if ($("#approveProposal")) $("#approveProposal").disabled = busy || !["approver", "admin"].includes(state.principal?.role);
}
function inlineKey(kind, c, body) {
  const fingerprint = JSON.stringify({ kind, case_id: c.case_id, body });
  if (!state.inlineKeys.has(fingerprint)) state.inlineKeys.set(fingerprint, crypto.randomUUID());
  return { fingerprint, key: state.inlineKeys.get(fingerprint) };
}
function inlineError(c, id, error) {
  const target = currentDetail(c, "summary") ? $(id) : null;
  if (target) { target.textContent = error.message; target.hidden = false; }
  else notify(`Case ${short(c.case_id)}: ${error.message}`, true);
}
async function saveOrderReference(event, c) {
  event.preventDefault();
  if (!currentDetail(c, "summary") || c.state !== "needs_info" || !canWrite() || state.inlineOperation) return;
  const orderRef = $("#contextOrderRef").value.trim();
  if (!orderRef) { $("#contextOrderRef").setCustomValidity("Enter an order reference."); $("#contextOrderRef").reportValidity(); return; }
  const body = { expected_case_version: c.version, order_ref: orderRef };
  const pending = inlineKey("context", c, body);
  const operation = { caseId: c.case_id, generation: state.sessionGeneration, token: state.token };
  state.inlineOperation = operation;
  $("#contextError").hidden = true;
  syncInlineControls();
  try {
    const updated = await api(`/v1/cases/${c.case_id}/context`, { method: "POST", key: pending.key, body });
    checkSession(operation.generation, operation.token);
    if (updated.case_id !== c.case_id || !Number.isInteger(updated.version) || updated.version <= c.version || updated.state !== "needs_info" || updated.order_ref !== orderRef) throw new Error("The saved-context response was incomplete. Retry to check the same update.");
    state.inlineKeys.delete(pending.fingerprint);
    if ((state.selected?.case_id || state.selectingId) === c.case_id) await refresh({ preserveCaseId: c.case_id });
    checkSession(operation.generation, operation.token);
    notify(`Case ${short(c.case_id)}: order reference saved. Explicitly run resolution to continue.`);
  } catch (error) {
    if (error instanceof SessionChanged) return;
    inlineError(c, "#contextError", error);
  } finally {
    if (state.inlineOperation === operation) { state.inlineOperation = null; syncInlineControls(); }
  }
}
async function declineProposal(event, c, p) {
  event.preventDefault();
  if (!currentDetail(c, "summary") || c.state !== "awaiting_approval" || !p || !["approver", "admin"].includes(state.principal?.role) || state.inlineOperation) return;
  const reason = $("#declineReason").value;
  if (!Object.hasOwn(declineReasons, reason)) return;
  if (!confirm(`Decline the cancellation proposal for ${p.order_ref || c.order_ref}? Reason: ${declineReasons[reason]}. This escalates the case for human review and records the decision. This operation does not execute a cancellation.`)) return;
  const body = { proposal_sha256: p.proposal_sha256, expected_case_version: c.version, reason_code: reason };
  const pending = inlineKey("decline", c, body);
  const operation = { caseId: c.case_id, generation: state.sessionGeneration, token: state.token };
  state.inlineOperation = operation;
  $("#declineError").hidden = true;
  syncInlineControls();
  try {
    const result = await api(`/v1/proposals/${p.proposal_id}/reject`, { method: "POST", key: pending.key, body });
    checkSession(operation.generation, operation.token);
    if (result.case_id !== c.case_id || result.proposal_id !== p.proposal_id || result.state !== "rejected" || result.case_state !== "escalated" || !Number.isInteger(result.case_version) || result.case_version <= c.version || result.order_changed !== false) throw new Error("The review response was incomplete. Retry to check the same decision.");
    state.inlineKeys.delete(pending.fingerprint);
    if ((state.selected?.case_id || state.selectingId) === c.case_id) await refresh({ preserveCaseId: c.case_id });
    checkSession(operation.generation, operation.token);
    notify(`Case ${short(c.case_id)}: proposal declined and escalated for human review. The decline did not execute a cancellation.`);
  } catch (error) {
    if (error instanceof SessionChanged) return;
    inlineError(c, "#declineError", error);
  } finally {
    if (state.inlineOperation === operation) { state.inlineOperation = null; syncInlineControls(); }
  }
}
function activateDetailTab(tab) {
  state.tab = tab;
  renderDetail();
  $(`[data-tab="${tab}"]`)?.focus();
}
async function resolveSelected() {
  const selected = state.selected;
  if (!selected || !canWrite() || state.inlineOperation) return;
  const button = $("#resolveCase");
  setBusy(button, true, "Checking route and evidence…");
  try {
    await api(`/v1/cases/${selected.case_id}/resolve`, {
      method: "POST",
      body: { expected_case_version: selected.version },
    });
    await refresh();
    notify(
      "Resolution saved. Evidence and decision stages are ready to inspect.",
    );
  } catch (e) {
    if (e instanceof SessionChanged) return;
    notify(e.message, true);
    if ((state.selected?.case_id || state.selectingId) === selected.case_id) await selectCase(selected.case_id);
  } finally {
    setBusy(button, false);
  }
}
async function approve(p) {
  const selected = state.selected;
  if (!selected || !["approver", "admin"].includes(state.principal?.role) || state.inlineOperation) return;
  if (
    !confirm(
      `Approve the cancellation of ${p.order_ref || selected.order_ref}? ${state.authConfig?.mode === "fixture" ? "This changes the local fixture order." : "This applies the approved cancellation."}`,
    )
  )
    return;
  const button = $("#approveProposal");
  setBusy(button, true, "Approving…");
  try {
    await api(`/v1/proposals/${p.proposal_id}/approve`, {
      method: "POST",
      body: {
        proposal_sha256: p.proposal_sha256,
        expected_case_version: p.expected_case_version,
        expected_order_version: p.expected_order_version,
      },
    });
    await refresh();
    notify(
      "Cancellation completed. The approval, order change, and action receipt committed together.",
    );
  } catch (e) {
    if (e instanceof SessionChanged) return;
    notify(e.message, true);
    if ((state.selected?.case_id || state.selectingId) === selected.case_id) await selectCase(selected.case_id);
  } finally {
    setBusy(button, false);
  }
}
function currentDetail(c, tab) {
  return state.selected?.case_id === c.case_id && state.selected?.version === c.version && state.tab === tab ? $("#detailBody") : null;
}
async function loadEvidence(c) {
  $("#detailBody").innerHTML =
    '<p class="muted">Loading permitted evidence…</p>';
  try {
    const result = await api(`/v1/cases/${c.case_id}/evidence`);
    const target = currentDetail(c, "evidence");
    if (!target) return;
    const items = result.items || result.evidence || result.citations || [];
    target.innerHTML =
      `<div class="section-label">PERMISSION-FILTERED POLICY EVIDENCE</div><p class="muted">Organization, published corpus, and access scope are checked before ranking.</p>${items.length ? items.map((e) => `<article class="evidence-card"><h3>${esc(e.title || e.source_label || "Retail policy")} · ${esc(e.locator)}</h3><p>${esc(e.body || e.excerpt)}</p><code>${esc(e.chunk_id)}</code></article>`).join("") : '<div class="empty">No permitted evidence is available for this case.</div>'}`;
  } catch (e) {
    if (e instanceof SessionChanged) return;
    const target = currentDetail(c, "evidence");
    if (!target) return;
    target.innerHTML =
      `<div class="form-error">${esc(e.message)}</div>`;
  }
}
async function loadTrace(c) {
  $("#detailBody").innerHTML = '<p class="muted">Loading structured trace…</p>';
  try {
    const result = await api(`/v1/cases/${c.case_id}/trace`);
    const target = currentDetail(c, "trace");
    if (!target) return;
    const items = result.events || result.items || [];
    target.innerHTML =
      `<div class="section-label">DECISION STAGES</div><p class="muted">Structured outcomes and evidence references. Customer text is excluded from logs.</p>${
        items.length
          ? items
              .map(
                (e) =>
                  `<div class="trace-row"><span class="trace-symbol ${e.outcome === "error" ? "error" : ""}">${e.outcome === "ok" ? "✓" : e.outcome === "error" ? "!" : "·"}</span><div><strong>${esc(e.stage)}</strong> <span class="badge">${esc(e.outcome)}</span><p>${esc(
                    Object.entries(e.details || {})
                      .map(
                        ([k, v]) =>
                          `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`,
                      )
                      .join(" · "),
                  )}</p></div><small>${e.duration_ms != null ? Number(e.duration_ms).toFixed(1) + " ms" : "—"}</small></div>`,
              )
              .join("")
          : '<div class="empty">The trace begins when this case is routed.</div>'
      }`;
  } catch (e) {
    if (e instanceof SessionChanged) return;
    const target = currentDetail(c, "trace");
    if (!target) return;
    target.innerHTML =
      `<div class="form-error">${esc(e.message)}</div>`;
  }
}
function openNew() {
  if (!canWrite() || state.creating) return;
  $("#caseDialog").showModal();
  $("#formError").hidden = true;
  $("#message").focus();
}
async function createCase(event) {
  event.preventDefault();
  if (!canWrite() || state.creating) return;
  if (!$("#message").value.trim()) {
    $("#message").setCustomValidity("Enter a customer message, not only spaces.");
    $("#message").reportValidity();
    return;
  }
  const request = ++state.createRequest;
  setCreating(true);
  try {
    const body = {
      message: $("#message").value,
      order_ref: $("#orderRef").value.trim() || null,
    };
    const fingerprint = JSON.stringify(body);
    if (state.pendingCreate?.fingerprint !== fingerprint)
      state.pendingCreate = { fingerprint, key: crypto.randomUUID() };
    const key = state.pendingCreate.key;
    const c = await api("/v1/cases", {
      method: "POST",
      key,
      body,
    });
    if (typeof c.case_id !== "string" || !c.case_id) throw new Error("The server response was incomplete. Retry to check the saved case.");
    if (state.pendingCreate?.key === key) state.pendingCreate = null;
    $("#caseDialog").close();
    $("#caseForm").reset();
    state.selected = c;
    state.tab = "summary";
    resetInboxCriteria();
    await refresh();
    notify(
      "Case created with verified customer context. Run resolution to inspect its route.",
    );
  } catch (e) {
    if (e instanceof SessionChanged) return;
    $("#formError").textContent = e.message;
    $("#formError").hidden = false;
  } finally {
    if (request === state.createRequest) setCreating(false);
  }
}
async function evaluationView() {
  const request = ++state.evaluationViewRequest;
  const generation = state.sessionGeneration, token = state.token;
  const target = $("#evaluationContent");
  target.innerHTML =
    '<div class="content-card"><p>Loading measured results…</p></div>';
  try {
    const [releases, evaluations] = await Promise.all([
      api("/v1/releases"),
      api("/v1/evaluations"),
    ]);
    if (request !== state.evaluationViewRequest) return;
    state.releases = releases.items || [];
    state.evaluations = evaluations.items || [];
    let saved = null;
    try {
      const r = await fetch("/static/reports/summary.json");
      if (r.ok) saved = await r.json();
    } catch {}
    checkSession(generation, token);
    if (request !== state.evaluationViewRequest) return;
    const comparisonBaseline = state.releases.find((release) => release.model_version === "tfidf-logistic-v1")?.release_id || state.releases.find((release) => !release.active)?.release_id;
    const w = saved?.workflow?.metrics;
    const model = saved?.model;
    target.innerHTML = `<div class="comparison-grid"><div class="content-card"><div class="eyebrow">DETERMINISTIC REGRESSIONS</div><h2 style="margin-top:12px">Workflow invariants</h2><p>Frozen fixture adapters test permissions, evidence, stale proposals, recovery, and duplicate effects. They do not measure trained routing accuracy.</p><div class="result-metrics"><div><strong>${w ? w.workflow_success.numerator + "/" + w.workflow_success.denominator : "—"}</strong><span>Invariant cases passed</span></div><div><strong>${w ? w.unauthorized_effect_count : "—"}</strong><span>Unauthorized effects observed</span></div><div><strong>${w ? w.runner_errors : "—"}</strong><span>Runner errors</span></div></div><a class="report-link" href="/static/reports/workflow-evaluation.html" target="_blank" rel="noopener">Inspect case-level report ↗</a></div><div class="content-card"><div class="eyebrow">TRAINED MODEL · SOURCE HOLDOUT</div><h2 style="margin-top:12px">Routing is a separate measure</h2><p>Grouped synthetic source tests can overestimate performance on ordinary customer language. Inspect the independent set alongside source metrics.</p><div class="result-metrics"><div><strong>${model?.source_macro_f1 != null ? (100 * model.source_macro_f1).toFixed(1) + "%" : "—"}</strong><span>Source macro-F1</span></div><div><strong>${model?.independent_macro_f1 != null ? (100 * model.independent_macro_f1).toFixed(1) + "%" : "—"}</strong><span>Independent synthetic macro-F1</span></div><div><strong>${model?.independent_count ?? "—"}</strong><span>Independent cases</span></div></div><p><strong>Independent workflow: ${model?.independent_workflow_success ? model.independent_workflow_success.numerator + " / " + model.independent_workflow_success.denominator : "Unavailable"} outcomes matched.</strong> Abstentions and incorrect outcomes remain failures.</p><a class="report-link" href="/static/reports/independent-evaluation.html" target="_blank" rel="noopener">Inspect all independent outcomes ↗</a><a class="report-link" href="/static/reports/model_report.json" target="_blank" rel="noopener">Inspect model measurements ↗</a></div></div><div class="content-card"><h2>Compare immutable releases</h2><p>Queue the same frozen workflow dataset for two releases. The worker runs in its own database, preserving the inbox. Live-model mode uses the trained router with deterministic drafts.</p><div class="run-form"><select id="evalRelease" aria-label="Release">${state.releases.map((r) => `<option value="${esc(r.release_id)}" ${r.release_id === comparisonBaseline ? "selected" : ""}>${esc(r.model_version)} · ${esc(r.corpus_version)}</option>`).join("")}</select><select id="evalReleaseB" aria-label="Comparison release">${state.releases.map((r) => `<option value="${esc(r.release_id)}" ${r.active ? "selected" : ""}>${esc(r.model_version)} · ${esc(r.corpus_version)}</option>`).join("")}</select><select id="evalMode" aria-label="Evaluation mode"><option value="live_model">Trained routing + fixture workflow</option><option value="deterministic_invariants">Deterministic invariants</option></select><button class="button primary" id="queueEvaluation" ${["evaluator", "admin"].includes(state.principal?.role) ? "" : "disabled"}>Compare releases</button></div>${["evaluator", "admin"].includes(state.principal?.role) ? "" : '<p class="muted" style="margin-top:12px">Select Alpha · Evaluator to queue a comparison.</p>'}<div class="table-wrap"><table><thead><tr><th>Release</th><th>Mode</th><th>State</th><th>Passed / total</th><th>Errors</th></tr></thead><tbody>${
      state.evaluations.length
        ? state.evaluations
            .map((e) => {
              let m = e.metrics?.workflow_success;
              return `<tr><td><code>${short(e.release_id)}</code></td><td>${esc(e.mode)}</td><td>${badge(e.state)}</td><td>${m ? `${m.numerator} / ${m.denominator}` : "Unavailable"}</td><td>${e.metrics?.runner_errors ?? "—"}${e.state === "completed" ? `<br><button class="text-button" data-evaluation-report="${esc(e.evaluation_id)}">Inspect report</button>` : ""}</td></tr>`;
            })
            .join("")
        : '<tr><td colspan="5">No queued runs. Saved regression evidence is available above.</td></tr>'
    }</tbody></table></div></div>`;
    $("#queueEvaluation").onclick = queueEvaluation;
    syncEvaluationControls();
    ["#evalRelease", "#evalReleaseB", "#evalMode"].forEach(id => {
      $(id).onchange = rememberEvaluationSelection;
    });
    const roleHint = target.querySelector(".run-form + p");
    if (roleHint && state.authConfig?.mode !== "fixture") roleHint.textContent = "An authorized evaluator is required to queue this benchmark.";
    $$("[data-evaluation-report]").forEach(
      (button) =>
        (button.onclick = () =>
          showEvaluationReport(button.dataset.evaluationReport)),
    );
  } catch (e) {
    if (e instanceof SessionChanged) return;
    if (request !== state.evaluationViewRequest) return;
    target.innerHTML = `<div class="content-card"><div class="form-error">${esc(e.message)}</div></div>`;
  }
}
async function showEvaluationReport(id) {
  const request = ++state.evaluationReportRequest;
  const dialog = $("#evaluationDialog"),
    target = $("#evaluationDetails");
  target.innerHTML =
    '<p class="muted">Loading permitted case-level results…</p>';
  dialog.showModal();
  try {
    const report = await api(`/v1/evaluations/${id}/report`);
    if (request !== state.evaluationReportRequest || !dialog.open) return;
    const result = report.metrics.workflow_success;
    target.innerHTML = `<p class="muted">${esc(report.model_version || "Release-pinned model")} · ${esc(report.mode)} · ${esc(report.generated_at)}</p><div class="result-metrics"><div><strong>${result.numerator} / ${result.denominator}</strong><span>Expected workflows completed</span></div><div><strong>${report.metrics.runner_errors}</strong><span>Runner errors</span></div><div><strong>${report.metrics.unauthorized_effect_count}</strong><span>Unauthorized effects observed</span></div></div><label class="report-filter"><input type="checkbox" id="onlyEvaluationFailures" checked> Show only failures</label><div id="evaluationCases"></div>`;
    const render = () => {
      const cases = (report.cases || []).filter(
        (item) => !$("#onlyEvaluationFailures").checked || !item.passed,
      );
      $("#evaluationCases").innerHTML = cases.length
        ? cases
            .map(
              (item) =>
                `<article class="evaluation-case"><div class="evaluation-case-head"><strong>${esc(item.id)}</strong>${item.passed ? '<span class="badge resolved">Passed</span>' : '<span class="badge failed">Failed</span>'}<span>${esc(item.failure_category || "all checks passed")}</span></div><p>${esc(item.error || item.runner_error || (item.checks || []).join(" · "))}</p><details><summary>Inspect structured response and checks</summary><pre>${esc(JSON.stringify({ checks: item.checks, response: item.observed }, null, 2))}</pre></details></article>`,
            )
            .join("")
        : '<p class="muted">No failures in this run. Clear the filter to inspect successful cases.</p>';
    };
    $("#onlyEvaluationFailures").onchange = render;
    render();
  } catch (error) {
    if (error instanceof SessionChanged) return;
    if (request !== state.evaluationReportRequest || !dialog.open) return;
    target.innerHTML = `<p class="form-error">${esc(error.message)}</p>`;
  }
}
$("#closeEvaluationDialog").onclick = () => {
  state.evaluationReportRequest++;
  $("#evaluationDialog").close();
};
$("#evaluationDialog").addEventListener("cancel", () => state.evaluationReportRequest++);
async function queueEvaluation() {
  if (state.evaluationQueuing || !["evaluator", "admin"].includes(state.principal?.role)) return;
  rememberEvaluationSelection();
  const selection = state.evaluationSelection;
  const releaseIds = [...new Set([selection.release, selection.comparison])];
  const mode = selection.mode;
  const fingerprint = JSON.stringify({ releaseIds, mode });
  if (state.pendingEvaluation?.fingerprint !== fingerprint) state.pendingEvaluation = {
    fingerprint, keys: Object.fromEntries(releaseIds.map(id => [id, crypto.randomUUID()])),
  };
  const pending = state.pendingEvaluation;
  const request = ++state.evaluationQueueRequest;
  state.evaluationQueuing = true;
  const button = $("#queueEvaluation");
  setBusy(button, true, "Queuing…");
  syncEvaluationControls();
  try {
    for (const release_id of releaseIds) {
      const queued = await api("/v1/evaluations", {
        method: "POST",
        key: pending.keys[release_id],
        body: {
          release_id,
          dataset_key: "fixture-v1",
          mode,
        },
      });
      if (!queued.evaluation_id) throw new Error("The evaluation response was incomplete.");
    }
    if (state.pendingEvaluation === pending) state.pendingEvaluation = null;
    notify(
      "Evaluation queued in the isolated worker database. Refresh results after the worker completes.",
    );
    await evaluationView();
  } catch (e) {
    if (e instanceof SessionChanged) return;
    notify(`${e.message} Retry the same comparison to check previously queued jobs.`, true);
  } finally {
    if (request === state.evaluationQueueRequest) {
      state.evaluationQueuing = false;
      setBusy(button, false);
      syncEvaluationControls();
    }
  }
}
function rememberEvaluationSelection() {
  if (!$("#evalRelease")) return;
  state.evaluationSelection = { release: $("#evalRelease").value, comparison: $("#evalReleaseB").value, mode: $("#evalMode").value };
}
function syncEvaluationControls() {
  const button = $("#queueEvaluation");
  if (!button) return;
  if (state.evaluationSelection) {
    [["#evalRelease", "release"], ["#evalReleaseB", "comparison"], ["#evalMode", "mode"]].forEach(([id, key]) => {
      const control = $(id), value = state.evaluationSelection[key];
      if ([...control.options].some(option => option.value === value)) control.value = value;
    });
  }
  button.disabled = state.evaluationQueuing || !["evaluator", "admin"].includes(state.principal?.role);
  button.textContent = state.evaluationQueuing ? "Queuing…" : "Compare releases";
  ["#evalRelease", "#evalReleaseB", "#evalMode"].forEach(id => $(id).disabled = state.evaluationQueuing);
}
async function releasesView() {
  const request = ++state.releaseViewRequest;
  try {
    const result = await api("/v1/releases");
    if (request !== state.releaseViewRequest) return;
    $("#releaseContent").innerHTML =
      `<div class="content-card"><h2>Release registry</h2><p>Published artifacts are immutable. Rollback changes the active pointer for new cases; existing cases retain their original release.</p><div class="table-wrap"><table><thead><tr><th>Model</th><th>Policy corpus</th><th>Prompt / tool policy</th><th>Release</th><th>State</th></tr></thead><tbody>${result.items.map((r) => `<tr><td>${esc(r.model_version)}<div class="muted">${esc(r.algorithm)}</div></td><td>${esc(r.corpus_version)}</td><td>${esc(r.prompt_version)}<br>${esc(r.tool_policy_version)}</td><td><code>${esc(r.release_id)}</code></td><td>${r.active ? '<span class="release-active">Active</span>' : "Retained"}</td></tr>`).join("")}</tbody></table></div></div><div class="comparison-grid"><div class="content-card"><div class="eyebrow">RECOVERY CONTRACT</div><h2 style="margin-top:12px">Change the pointer. Keep the evidence.</h2><p>The administrative rollback command accepts an existing evaluated release. It does not rewrite past cases or delete model artifacts.</p><p>See the repository runbook for the exact local command and the backup restoration check.</p></div><div class="content-card"><div class="eyebrow">PROVENANCE</div><h2 style="margin-top:12px">Four versions per decision</h2><p>Model artifact, policy corpus, prompt, and deterministic tool policy are pinned together. A probability never grants permission to act.</p></div></div>`;
  } catch (e) {
    if (e instanceof SessionChanged) return;
    if (request !== state.releaseViewRequest) return;
    $("#releaseContent").innerHTML =
      `<div class="content-card"><div class="form-error">${esc(e.message)}</div></div>`;
  }
}
function changeView(view) {
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  $$("[data-view]").forEach((b) =>
    { b.classList.toggle("active", b.dataset.view === view);
      if (b.dataset.view === view) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); },
  );
  $("#pageLabel").textContent = {
    inbox: "Case inbox",
    evaluation: "Evaluation lab",
    releases: "Release registry",
  }[view];
  if (view === "evaluation") evaluationView();
  if (view === "releases") releasesView();
}
$$("[data-view]").forEach(
  (b) => (b.onclick = () => changeView(b.dataset.view)),
);
$$("[data-filter]").forEach(
  (b) =>
    (b.onclick = () => {
      clearTimeout(searchTimer);
      state.filter = b.dataset.filter;
      state.query = $("#search").value.trim();
      state.inboxOffset = 0;
      syncFilterButtons();
      refresh({ resetSelection: true });
    }),
);
function searchInbox() {
  clearTimeout(searchTimer);
  state.query = $("#search").value.trim().slice(0, 200);
  state.inboxOffset = 0;
  refresh({ resetSelection: true });
}
$("#search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(searchInbox, 250); };
$("#search").onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); searchInbox(); } };
$("#previousPage").onclick = () => {
  if (state.inboxLoading || state.inboxOffset === 0) return;
  state.inboxOffset = Math.max(0, state.inboxOffset - state.inboxLimit);
  refresh({ resetSelection: true });
};
$("#nextPage").onclick = () => {
  if (state.inboxLoading || state.inboxOffset + state.cases.length >= state.inboxTotal) return;
  state.inboxOffset += state.inboxLimit;
  refresh({ resetSelection: true });
};
$("#actor").onchange = login;
$("#refresh").onclick = () => refresh().catch((e) => notify(e.message, true));
$("#refreshInbox").onclick = $("#refresh").onclick;
$("#newCase").onclick = openNew;
$("#welcomeCase").onclick = openNew;
$("#closeDialog").onclick = () => { if (!state.creating) $("#caseDialog").close(); };
$("#cancelDialog").onclick = $("#closeDialog").onclick;
$("#caseForm").onsubmit = createCase;
$("#caseDialog").addEventListener("cancel", event => { if (state.creating) event.preventDefault(); });
$("#message").oninput = () => $("#message").setCustomValidity("");
$("#refreshEvaluations").onclick = evaluationView;
$$("[data-example]").forEach(
  (b) =>
    (b.onclick = () => {
      const beta = state.principal?.tenant_id.startsWith("2222");
      const ref = beta ? "B-100" : "A-100";
      const messages = {
        track: "Where is my order?",
        cancel: "Please cancel my order.",
        handoff: "I would like to contact a human agent.",
      };
      $("#message").value = messages[b.dataset.example];
      $("#message").setCustomValidity("");
      $("#orderRef").value = b.dataset.example === "handoff" ? "" : ref;
    }),
);
bootstrapAuthentication();
