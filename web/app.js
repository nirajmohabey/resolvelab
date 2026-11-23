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
  pendingCreate: null,
  token: null,
  principal: null,
  cases: [],
  selected: null,
  filter: "all",
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
let expiryTimer;
async function applySession(token, principal, organization) {
  const generation = ++state.sessionGeneration;
  state.pendingCreate = null;
  const previous = state.selected,
    previousTenant = state.principal?.tenant_id;
  state.token = token;
  state.principal = principal;
  const fixture = state.authConfig?.mode === "fixture";
  $("#apiReference").hidden = !fixture;
  $(".build-label").textContent = fixture ? "v0.1 · Fixture retailer" : "v0.1 · Verified workspace";
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
    previous && previousTenant === principal.tenant_id
      ? { case_id: previous.case_id }
      : null;
  state.tab = "summary";
  $("#detail").innerHTML =
    '<div class="empty"><h3>Loading organization cases…</h3></div>';
  $$("[data-view]").forEach((button) => (button.disabled = false));
  await refresh();
  checkSession(generation, token);
  $("#newCase").disabled = !["operator", "approver", "admin"].includes(
    principal.role,
  );
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
  state.cases = [];
  state.releases = [];
  state.evaluations = [];
  state.pendingCreate = null;
  $("#apiReference").hidden = true;
  $(".build-label").textContent = "v0.1 · Verified workspace";
  $("#actor").hidden = true;
  $(".identity-label").hidden = true;
  $("#signOut").hidden = true;
  $("#signIn").hidden = false;
  $("#signIn").disabled = !(
    state.authConfig?.mode === "oidc" && state.authConfig?.configured
  );
  $("#newCase").disabled = true;
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
  state.sessionGeneration++;
  try {
    const result = await api("/v1/demo/session", {
      method: "POST",
      body: { actor: $("#actor").value },
    });
    await applySession(
      result.access_token || result.token,
      result.principal,
      result.organization,
    );
  } catch (error) {
    if (error instanceof SessionChanged) return;
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
$("#signIn").onclick = () =>
  beginSignIn(state.authConfig).catch((error) => notify(error.message, true));
$("#signOut").onclick = () =>
  requireSignIn("This workbench session is disconnected.");
async function refresh() {
  const result = await api("/v1/cases?limit=100");
  state.cases = result.items || [];
  renderList();
  const stats = await api("/v1/dashboard");
  const counts = stats.states || {};
  $("#statTotal").textContent = stats.total_cases ?? state.cases.length;
  $("#statApproval").textContent = counts.awaiting_approval || 0;
  $("#statResolved").textContent = counts.resolved || 0;
  $("#statEscalated").textContent = counts.escalated || 0;
  $("#navCount").textContent = stats.total_cases ?? state.cases.length;
  if (state.selected) await selectCase(state.selected.case_id);
  else if (state.cases.length) await selectCase(state.cases[0].case_id);
  else
    $("#detail").innerHTML =
      '<div class="welcome-state"><div class="eyebrow">CONTEXT BEFORE ACTION</div><h2>Your inbox is ready.</h2><p>Create a new support case to get started.</p></div>';
}
function renderList() {
  const query = $("#search").value.toLowerCase();
  const cases = state.cases.filter(
    (c) =>
      (state.filter === "all" || c.state === state.filter) &&
      `${c.message} ${c.order_ref || ""} ${c.case_id}`
        .toLowerCase()
        .includes(query),
  );
  $("#caseCount").textContent = state.cases.length;
  $("#caseList").innerHTML = cases.length
    ? cases
        .map(
          (c) =>
            `<button class="case-row ${state.selected?.case_id === c.case_id ? "selected" : ""}" data-case="${esc(c.case_id)}"><div class="case-meta"><span>CASE ${short(c.case_id)}</span><span>${new Date(c.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></div><h3>${esc(c.message)}</h3><div class="case-bottom">${badge(c.state)}<span class="order-ref">${esc(c.order_ref || "No reference")}</span></div></button>`,
        )
        .join("")
    : '<div class="empty"><span class="empty-icon">▤</span><h3>No cases here yet.</h3><p>Create a case, or adjust your filter.</p></div>';
  $$("[data-case]").forEach(
    (b) => (b.onclick = () => selectCase(b.dataset.case)),
  );
}
async function selectCase(id) {
  const request = ++state.caseSelectionRequest;
  try {
    const selected = await api(`/v1/cases/${id}`);
    if (request !== state.caseSelectionRequest) return;
    state.selected = selected;
    renderList();
    renderDetail();
  } catch (e) {
    if (e instanceof SessionChanged) return;
    if (request !== state.caseSelectionRequest) return;
    notify(e.message, true);
  }
}
function renderDetail() {
  const c = state.selected;
  if (!c) return;
  const resolution = c.resolution;
  $("#detail").innerHTML =
    `<div class="detail-head"><div class="detail-topline"><span class="case-id">CASE ${short(c.case_id)}</span>${badge(c.state)}</div><h2>${esc(c.message)}</h2><div class="detail-subtitle"><span>Customer ${esc(c.customer_subject)}</span><span>Order ${esc(c.order_ref || "not provided")}</span><span>Version ${c.version}</span></div></div><div class="detail-tabs"><button class="${state.tab === "summary" ? "active" : ""}" data-tab="summary">Resolution</button><button class="${state.tab === "evidence" ? "active" : ""}" data-tab="evidence">Policy evidence</button><button class="${state.tab === "trace" ? "active" : ""}" data-tab="trace">Execution trace</button></div><div class="detail-body" id="detailBody"></div>`;
  $$("[data-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        state.tab = b.dataset.tab;
        renderDetail();
      }),
  );
  if (state.tab === "evidence") {
    loadEvidence(c);
    return;
  }
  if (state.tab === "trace") {
    loadTrace(c);
    return;
  }
  if (!resolution) {
    $("#detailBody").innerHTML =
      `<div class="resolution-empty"><h3>${c.state === "failed" ? "A dependency needs attention." : "Ready for a closer look."}</h3><p>${c.state === "failed" ? "The case and trace are preserved. You can retry resolution with the current version." : "Run the routing and evidence workflow. A cancellation will stop at a proposal for human approval."}</p><button class="button primary" id="resolveCase">${c.state === "failed" ? "Retry resolution" : "Resolve this case"}</button></div>`;
    $("#resolveCase").onclick = resolveSelected;
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
  if (c.actions?.length)
    html += `<div class="answer-card"><div class="section-label">LOCAL ACTION RECEIPT</div><p>Cancellation completed. One durable action was recorded.</p><div class="compact-code">${esc(c.actions[0].action_id)}</div></div>`;
  html += `<div class="section-label">RELEASE SNAPSHOT</div><div class="compact-code">${esc(c.release?.model_version || c.release?.model_label || "Versioned routing")} · ${esc(c.release?.corpus_version || c.release?.corpus_label || "policy-v1")}<br>Release ${esc(c.release_id)}</div>`;
  $("#detailBody").innerHTML = html;
  $("#inspectEvidence").onclick = () => {
    state.tab = "evidence";
    renderDetail();
  };
  if ($("#approveProposal")) $("#approveProposal").onclick = () => approve(p);
}
async function resolveSelected() {
  const button = $("#resolveCase");
  setBusy(button, true, "Checking route and evidence…");
  try {
    await api(`/v1/cases/${state.selected.case_id}/resolve`, {
      method: "POST",
      body: { expected_case_version: state.selected.version },
    });
    await refresh();
    notify(
      "Resolution saved. Evidence and decision stages are ready to inspect.",
    );
  } catch (e) {
    if (e instanceof SessionChanged) return;
    notify(e.message, true);
    await selectCase(state.selected.case_id);
  } finally {
    setBusy(button, false);
  }
}
async function approve(p) {
  if (
    !confirm(
      `Approve the cancellation of ${p.order_ref || state.selected.order_ref}? This changes the local fixture order.`,
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
    await selectCase(state.selected.case_id);
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
  $("#caseDialog").showModal();
  $("#formError").hidden = true;
  $("#message").focus();
}
async function createCase(event) {
  event.preventDefault();
  setBusy($("#createSubmit"), true, "Saving…");
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
    if (!c.case_id) throw new Error("The server response was incomplete. Retry to check the saved case.");
    if (state.pendingCreate?.key === key) state.pendingCreate = null;
    $("#caseDialog").close();
    $("#caseForm").reset();
    state.selected = c;
    state.tab = "summary";
    await refresh();
    notify(
      "Case created with verified customer context. Run resolution to inspect its route.",
    );
  } catch (e) {
    if (e instanceof SessionChanged) return;
    $("#formError").textContent = e.message;
    $("#formError").hidden = false;
  } finally {
    setBusy($("#createSubmit"), false);
  }
}
async function evaluationView() {
  const generation = state.sessionGeneration, token = state.token;
  const target = $("#evaluationContent");
  target.innerHTML =
    '<div class="content-card"><p>Loading measured results…</p></div>';
  try {
    const [releases, evaluations] = await Promise.all([
      api("/v1/releases"),
      api("/v1/evaluations"),
    ]);
    state.releases = releases.items || [];
    state.evaluations = evaluations.items || [];
    let saved = null;
    try {
      const r = await fetch("/static/reports/summary.json");
      if (r.ok) saved = await r.json();
    } catch {}
    checkSession(generation, token);
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
    $$("[data-evaluation-report]").forEach(
      (button) =>
        (button.onclick = () =>
          showEvaluationReport(button.dataset.evaluationReport)),
    );
  } catch (e) {
    if (e instanceof SessionChanged) return;
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
  const button = $("#queueEvaluation");
  setBusy(button, true, "Queuing…");
  try {
    for (const release_id of new Set([
      $("#evalRelease").value,
      $("#evalReleaseB").value,
    ]))
      await api("/v1/evaluations", {
        method: "POST",
        body: {
          release_id,
          dataset_key: "fixture-v1",
          mode: $("#evalMode").value,
        },
      });
    notify(
      "Evaluation queued in the isolated worker database. Refresh results after the worker completes.",
    );
    await evaluationView();
  } catch (e) {
    if (e instanceof SessionChanged) return;
    notify(e.message, true);
  } finally {
    setBusy(button, false);
  }
}
async function releasesView() {
  try {
    const result = await api("/v1/releases");
    $("#releaseContent").innerHTML =
      `<div class="content-card"><h2>Release registry</h2><p>Published artifacts are immutable. Rollback changes the active pointer for new cases; existing cases retain their original release.</p><div class="table-wrap"><table><thead><tr><th>Model</th><th>Policy corpus</th><th>Prompt / tool policy</th><th>Release</th><th>State</th></tr></thead><tbody>${result.items.map((r) => `<tr><td>${esc(r.model_version)}<div class="muted">${esc(r.algorithm)}</div></td><td>${esc(r.corpus_version)}</td><td>${esc(r.prompt_version)}<br>${esc(r.tool_policy_version)}</td><td><code>${esc(r.release_id)}</code></td><td>${r.active ? '<span class="release-active">Active</span>' : "Retained"}</td></tr>`).join("")}</tbody></table></div></div><div class="comparison-grid"><div class="content-card"><div class="eyebrow">RECOVERY CONTRACT</div><h2 style="margin-top:12px">Change the pointer. Keep the evidence.</h2><p>The administrative rollback command accepts an existing evaluated release. It does not rewrite past cases or delete model artifacts.</p><p>See the repository runbook for the exact local command and the backup restoration check.</p></div><div class="content-card"><div class="eyebrow">PROVENANCE</div><h2 style="margin-top:12px">Four versions per decision</h2><p>Model artifact, policy corpus, prompt, and deterministic tool policy are pinned together. A probability never grants permission to act.</p></div></div>`;
  } catch (e) {
    if (e instanceof SessionChanged) return;
    $("#releaseContent").innerHTML =
      `<div class="content-card"><div class="form-error">${esc(e.message)}</div></div>`;
  }
}
function changeView(view) {
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  $$("[data-view]").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view),
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
      state.filter = b.dataset.filter;
      $$("[data-filter]").forEach((x) => x.classList.toggle("active", x === b));
      renderList();
    }),
);
$("#search").oninput = renderList;
$("#actor").onchange = login;
$("#refresh").onclick = () => refresh().catch((e) => notify(e.message, true));
$("#refreshInbox").onclick = $("#refresh").onclick;
$("#newCase").onclick = openNew;
$("#welcomeCase").onclick = openNew;
$("#closeDialog").onclick = () => $("#caseDialog").close();
$("#cancelDialog").onclick = $("#closeDialog").onclick;
$("#caseForm").onsubmit = createCase;
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
      $("#orderRef").value = b.dataset.example === "handoff" ? "" : ref;
    }),
);
bootstrapAuthentication();
