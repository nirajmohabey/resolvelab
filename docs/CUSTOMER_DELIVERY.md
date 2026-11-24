# ResolveLab customer delivery exercise

**Engagement: simulated stakeholder exercise, 2026-11-23.** An automated agent plays the operator/reviewer role using fictional retailer fixtures. No outside customer or human acceptance reviewer participated. This is a delivery and handoff exercise around the existing app, not a client engagement, customer adoption study or measured business improvement.

The reviewed starting runtime is commit `1207948cc6684830fed29d9c73ca47ad38352120` (v0.3.0). This update reconciles documentation, adds fresh acceptance evidence and fixes an observed frontend Retry control defect. Backend Python, model weights, thresholds, holdout cases, APIs and hosting are unchanged. The [production record](PRODUCTION.md) describes the real Vercel/Neon deployment; the acceptance exercise below uses an isolated local PostgreSQL database and fixture identities, not the owner's hosted account.

## Discovery record

Answers below are agent-authored scenario assumptions, checked against the existing interface and source. They are not interview findings.

| Role | Responsibility in this exercise |
|---|---|
| Simulated support operator | Receive a request, inspect its verified context and evidence, resolve or hand off |
| Simulated approver | Independently review the exact cancellation proposal; approve or decline within permission and eligibility bounds |
| Project maintainer / workspace administrator | Review identity enrollment, policies, release configuration and incidents |
| External acceptance owner | Not appointed; required before claiming an external pilot |

**Problem.** A retailer support operator needs to answer an owned-order tracking question and prepare an eligible cancellation while avoiding unauthorized order disclosure or mutation. Unsupported, ambiguous and failed cases must remain inspectable for manual handling.

**Current steps.** Sign in with an enrolled identity; create or open a case; inspect customer/order context; resolve; inspect Policy evidence and Execution trace; supply missing information or hand off; an authorized approver reviews a cancellation separately. There is no real merchant integration in this exercise. Orders, policies and identities in the local acceptance database are synthetic.

Three desired outcomes:

1. Answer tracking only for the bound customer, with inspectable authorized policy evidence and no order mutation.
2. Stop an eligible cancellation at human approval; declining a proposal produces a visible handoff and no cancellation.
3. Preserve missing context and recoverable failures on the same case so the operator can understand and safely resume work.

**Access constraints.** Public signup does not grant workspace membership. An administrator must review the provider issuer/subject, active tenant membership, role and customer binding. The browser cannot establish order ownership by entering an order reference. Local fixture switching is a reproduction tool disabled in production. This exercise does not create real identities, contact customers, trigger paid generation or change a hosted database.

**Observed baseline.** A documentation audit at the pinned commit found CASE_STUDY describing hosted identity/backend as future work and RUNBOOK describing public identity/hosting as a future boundary, while PRODUCTION already documented the deployed implementation. DEMO did not clearly identify its supplied recording as an older local fixture run. Those three documents form the bounded baseline; there is no measured handling-time, adoption or business-savings baseline. The six acceptance checks characterize current behavior, not a before/after productivity experiment.

Open discovery questions for a real pilot: who may accept on the retailer's behalf, how trusted intake establishes each customer, what merchant and policy systems are authoritative, retention/deletion obligations, expected traffic, and who owns incident response. The current single reviewed customer binding must not be represented as a completed multi-customer merchant intake.

## Existing identity and data map

| Boundary / fields | Source and transformation | Authority / failure behavior |
|---|---|---|
| Provider `iss`, `sub`, `aud`, `iat`, `exp` | Neon Managed Better Auth JWT; signature, trusted issuer/audience and time checks precede lookup | Provider proves identity. Browser-supplied tenant, role and customer claims grant no application permission. Invalid/unregistered identity is denied. |
| `external_identities`: issuer + external_subject -> tenant_id, actor_id, customer_subject | Administrator-reviewed enrollment using [register_identity.py](../scripts/register_identity.py); active membership is a precondition | Administrator establishes the trusted binding. Conflicting or revoked mappings are not silently replaced. |
| Active `memberships`: tenant_id + actor_id -> role | Rechecked by the API under tenant-scoped database context | Membership determines operator/approver/admin permissions. Deactivation denies future application checks. |
| Case: case_id, release_id, customer_subject, message, order_ref, version | [Case creation](../src/resolvelab/service.py) takes the customer from the authenticated principal and pins the active release | Operator may provide a claimed reference, not an ownership assertion. Context edits keep the customer and release and require explicit resolution again. |
| Order: tenant_id + order_ref, customer_subject, status, version, cancel_deadline, tracking_summary | Current `fixture_orders` lookup inside the authorized tenant; customer must match the case | Server verifies ownership. Missing/unverified reference requests information or escalates without disclosing a different customer's tracking data. |
| Evidence: published corpus, document access scope, chunk_id/body/locator | Permission and published-corpus filters precede ranking; saved response contains policy snapshots and version pins | Routing does not authorize evidence or actions. Missing required evidence stops automated handling. |
| Proposal: digest, expected case/order versions, tool policy, expires_at | Server prepares only the permitted `cancel_order` proposal | `approver`/`admin` must approve the exact still-current eligible proposal. Decline/staleness cannot execute it. |
| Action / receipt | Approval, fixture order change, receipt and trace commit in one database transaction | Connector is `local_fixture`. This does not guarantee atomicity or reconciliation for a hypothetical remote merchant effect. |

The existing [auth checks](../src/resolvelab/auth.py), [service handlers](../src/resolvelab/service.py) and [data dictionary](DATA_DICTIONARY.md) are implementation references. Deadlines use UTC; local acceptance fixes the fixture clock, while production uses the actual clock. Case/order integer versions and proposal digest/deadline detect stale state. No pagination, backfill or merchant rate-limit contract applies to this fixture connector. A real pilot needs its own data retention and deletion agreement; this exercise does not introduce one.

```mermaid
sequenceDiagram
    actor Operator
    participant Auth as Neon Auth
    participant API as ResolveLab API
    participant DB as Tenant-scoped PostgreSQL
    actor Approver
    Operator->>Auth: Sign in (hosted profile)
    Auth-->>Operator: Provider session and short-lived JWT
    Operator->>API: Create / resolve a case
    API->>API: Verify JWT identity and time bounds
    API->>DB: Resolve reviewed identity and active membership
    DB-->>API: Tenant, role, customer binding
    API->>DB: Pin case release; check owned order and authorized evidence
    alt Owned tracking
        API-->>Operator: Read-only answer, policy evidence, trace
    else Eligible cancellation
        API-->>Operator: Exact proposal awaiting approval; no mutation
        Approver->>API: Review exact proposal and approve / decline
        API->>DB: Recheck membership, ownership, versions, policy, deadline
        DB-->>API: Atomic local action receipt or recorded decline / stale denial
        API-->>Approver: Durable outcome
    else Unsupported, missing context or dependency failure
        API-->>Operator: Handoff, needs information or saved retryable failure
    end
```

The diagram describes the existing hosted identity path. The acceptance run substitutes signed local fixture identities for Neon sign-in, then exercises the real frontend, API and PostgreSQL service. It does not establish successful hosted owner sign-in or verification-email delivery.

## Acceptance record

The [before run](../artifacts/fde-retrofit-2026-11-23/before/summary.json) exercised the real frontend, API and PostgreSQL service: five of six cases passed. The recoverable failure exposed the Retry control defect described below. The [corrected run](../artifacts/fde-retrofit-2026-11-23/after/summary.json) passed all six cases, with no JavaScript page errors or unexpected API errors. The injected provider503 and a favicon404 remain explicitly recorded. No test outcome is inferred from the earlier 210-test suite.

| ID | Current interface scenario | Expected outcome | Actual / reviewer result |
|---|---|---|---|
| UAT-01 | Owned-order tracking | Read-only tracking with policy evidence; no action or order change | **Pass**: resolved read-only, one policy citation, zero actions. [Screen](../artifacts/fde-retrofit-2026-11-23/after/AC01-tracking-evidence.png) |
| UAT-02 | Eligible cancellation | Awaiting approval with exact proposal; no order mutation | **Pass**: one pending proposal; operator has no approval control; zero actions. [Screen](../artifacts/fde-retrofit-2026-11-23/after/AC02-awaiting-approval.png) |
| UAT-03 | Declined approval | Fixed decline reason, visible escalation; no cancellation | **Pass**: rejected proposal, escalated case, receipt `order_changed=false`, zero actions. [Screen](../artifacts/fde-retrofit-2026-11-23/after/AC03-declined.png) |
| UAT-04 | Unsupported request | Support escalation with no executable proposal | **Pass**: out-of-scope refund escalated, no proposal/action. [Screen](../artifacts/fde-retrofit-2026-11-23/after/AC04-unsupported.png) |
| UAT-05 | Missing order information | Needs information; save reference on the same case; explicit resolve verifies ownership | **Pass**: same case/customer/release; explicit second resolution succeeds. [Screen](../artifacts/fde-retrofit-2026-11-23/after/AC05-context-resolved.png) |
| UAT-06 | Recoverable provider failure | Failed attempt/trace preserved; Retry resolves the same case after dependency recovery | **Pass**: failed v2 -> explicit Retry -> resolved v3; old error trace retained, zero actions. [Screen](../artifacts/fde-retrofit-2026-11-23/after/AC06-recovered.png) |

Six checks use five distinct synthetic cases because eligible cancellation and its decline are one lifecycle. The [isolation record](../artifacts/fde-retrofit-2026-11-23/after/isolation.json) verifies unchanged order snapshots, zero actions, an untouched native workspace configuration and cleanup of the owned database/processes. A preceding replay was interrupted by a Windows test-process shutdown timeout; it is retained separately in the [evidence index](../artifacts/fde-retrofit-2026-11-23/README.md), rather than counted as an application pass.

## Observed friction -> smallest fix -> replay

**Friction FDE-01 (agent-run simulated operator review):** following CASE_STUDY and RUNBOOK could lead a reviewer to think they must provision hosting/identity again; the supplied recording could also be confused with the current hosted interface. Reproduction: read those documents at the pinned source commit alongside PRODUCTION, then try to determine whether to enroll an identity or build an identity integration.

**Root cause:** older local-release narrative remained in the main handoff documents after the Vercel/Neon release. **Correction:** reconcile [CASE_STUDY](CASE_STUDY.md), [RUNBOOK](RUNBOOK.md) and [DEMO](DEMO.md); distinguish current hosted access, historical local footage, synthetic data and unmeasured work; link this packet from the entry points. No speculative app rewrite is justified by this finding.

Replay checks: verify the hosted enrollment instructions agree, the video remains labeled v0.2.1 local, the optional generator is described as a constrained boundary, historical metrics retain their provenance, and local document links resolve. The separate fresh acceptance run checks that the current app still follows the documented six-case workflow. This is documentation feedback from an automated simulated reviewer, not a reported customer issue or measured usability improvement.

**Friction FDE-02 (observed during actual UI acceptance):** a failed provider attempt saves a nonempty response with `provider_unavailable` and a generate/error trace. After repairing the dependency, the Resolution tab offers no Retry button. The before run preserves the [failed response screen](../artifacts/fde-retrofit-2026-11-23/before/AC06-failure-summary.png) and [failure trace](../artifacts/fde-retrofit-2026-11-23/before/AC06-failure-trace.png). The preloaded older fixture failure had an empty resolution and therefore did not expose this branch.

**Root cause and bounded correction:** [renderDetail in app.js](../web/app.js) renders the original Retry button only when the case has no resolution. A genuine provider failure stores a resolution. The patch shows an authorized Retry control for that saved failed state, retains the failed response and trace, and uses the existing `resolveSelected` handler with its case version, idempotency and session guards. Its error path also refreshes the inbox and dashboard so the saved Failed state agrees with the detail view. The correction does not retry automatically, grant read-only reviewers a mutation or bypass server authorization. All six real local UI/API/PostgreSQL cases passed on replay. The [eight focused intercepted-API checks](../artifacts/fde-retrofit-2026-11-23/retry-regression/after/results.json) also passed, including operator/admin, evaluator read-only, duplicate prevention, current-version retry and disconnect. One operator check uses a 375px mobile viewport with no horizontal overflow. [Original focused failures](../artifacts/fde-retrofit-2026-11-23/retry-regression/before/results.json) remain visible; the new check is included in the existing CI workflow.

## Operator handoff

1. **Get access.** Use the [hosted sign-in page](https://resolvelab-blush.vercel.app) with an existing verified account. Ask the workspace administrator to review and enroll its exact issuer/subject, role and customer binding using the [production enrollment guidance](PRODUCTION.md#authentication-and-enrollment). Signup alone is insufficient. Do not share passwords/codes or enable production fixture login. A signup/code delivery problem belongs to the administrator/provider investigation; provider acceptance of a send request is not proof of inbox delivery.
2. **Operate.** Open/create a case, check its bound context, resolve, then inspect Policy evidence and Execution trace. A needs-information case stays open while a reference is supplied and verified on explicit resolution. Read an awaiting-approval result as a proposal, not an executed cancellation. An enrolled approver independently checks the exact proposal and can decline with a recorded reason.
3. **Interpret a failure.** For `failed`, inspect the preserved generate/validation trace, repair the dependency, refresh and use Retry. For unsupported or unverified ownership, hand off for manual support review. For stale approval, refresh and follow the server instruction to prepare a new case after review; never force the old proposal or re-resolve an ineligible awaiting-approval case. The [runbook](RUNBOOK.md) covers current-version/idempotency requirements and bounded queue recovery.
4. **Escalate.** The workspace administrator/project maintainer owns access, policy configuration, dependency outages and release incidents in this portfolio deployment. A real pilot must name a reachable person and backup before acceptance; no staffed support SLA exists here. Do not include credentials or private customer text in a public issue.
5. **Pause / roll back.** Stop disputed work and retain the case/trace. An administrator can deactivate the membership/identity mapping for application denial on the next request. For a model regression, verify the retained immutable release and evaluation evidence, then use the existing reviewed release-pointer procedure in [RUNBOOK](RUNBOOK.md#inspect-and-recover); existing cases keep their pinned release. The examples are local fixture commands, not authorization to run fixture resets or owner credentials against production. No hosted rollback was executed for this documentation exercise.
6. **Accept a real pilot.** Have the appointed human acceptance owner run these six cases in its authorized environment, confirm ownership and failure escalation, agree retention/quotas/rollback, and record actual sign-off. This simulated run is a technical readiness exercise, not that sign-off.

## Separate answer-support review

The [AI-assisted archive-review packet](../artifacts/fde-retrofit-2026-11-23/support-review/README.md) freezes twenty saved expected-answerable outputs and thirty material claim annotations. Nineteen of twenty retain the expected bounded workflow; the already disclosed V4-B003 routing error remains visible. Ten claims have direct permitted-policy support; nineteen order claims lack the archived raw authorized lookup needed for full verification; one scope claim contradicts the frozen supported-workflow rubric. Missing archived evidence is not proof those order facts are false.

All twenty saved outputs are nonempty, but none has complete primary evidence for every order-related claim in this archive. That 0/20 archive evidence-sufficiency result is not model factual accuracy. The [human worksheet](../artifacts/fde-retrofit-2026-11-23/support-review/human-review.csv) leaves actual reviewer judgments and adjudication blank; human support quality and agreement remain unmeasured. The original report, fixture and model hashes are unchanged. This retrospective selected sample is not random, blind or a new held-out benchmark. No model tuning or paid-provider run was performed.

## Acceptance boundary

The intended decision is limited to a consistent current delivery story, a reproducible simulated six-case workflow, a documented feedback correction and an operator handoff. External acceptance, hosted owner sign-in, email delivery, business savings, human answer-support review, merchant effects and paid-provider benchmarks remain unclaimed unless independently observed and recorded.
