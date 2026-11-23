# ResolveLab workbench v0.2

The workbench connects a verified customer context, order checks, policy evidence, and human review. The included retailer, orders, and customer records are synthetic. See the [local setup](../README.md) and [production configuration](PRODUCTION.md) for the supported runtime modes.

## Sign in and find a case

In the local fixture, choose an identity such as **Alpha · Operator** or **Alpha · Approver**. Changing identity immediately clears the previous workspace while the new session is established. A failed fixture connection offers **Reconnect workspace**. Configured OIDC sessions use the provider sign-in flow and server-maintained memberships; fixture examples and identity switching are hidden there.

Search matches the message, order reference, or case ID across the tenant's cases, rather than only the loaded page. Search is limited to 200 characters. Press Enter to submit immediately, or pause briefly after typing. The **All cases**, **Needs approval**, and **Escalated** filters combine with the search. Previous/Next controls show 25 cases at a time and display the filtered total.

Selecting another case removes the previous action surface while the new case loads. The selected case has three detail tabs: **Resolution**, **Policy evidence**, and **Execution trace**. Keyboard users can move between tabs with Left/Right arrows, Home, and End. Evaluators can inspect cases without being offered write actions.

## Create and resolve

Choose **New case**, enter a customer message, and optionally supply an order reference. The message must contain non-whitespace text and fit within 6,000 characters; an order reference is limited to 100 characters. Customer identity comes from the verified server context and cannot be edited in this form.

While creation is pending, the submitted fields and close/cancel controls are disabled. A failed or incomplete response leaves the form available for retry. Retrying the same unchanged creation within the current session reuses its request key. A session change clears pending keys and private form state.

Choose **Resolve this case** to run the bounded workflow:

- Tracking returns a read-only result for a verified order.
- An eligible cancellation becomes a proposal awaiting human approval.
- Missing order information keeps the case in **Needs information**.
- Unsupported requests and explicit human requests are handed off.

The routing score does not authorize an action. Inspect the policy evidence, case version, and release snapshot alongside the response.

## Add a missing order reference

An operator, approver, or administrator can use **Add order reference** on a case in **Needs information**. Enter the reference and choose **Save order reference**.

This updates only the claimed order reference. The verified customer and pinned release remain unchanged, the case version advances, and its current resolution is cleared. Saving a reference does not establish ownership or eligibility. The operator then explicitly chooses **Resolve this case** to run those checks; saving never resolves automatically.

The request uses the displayed case ID/version and retains its logical request key after a failed response. Navigating to another case while it is pending does not retarget the update. A stale-version error requires loading the current case and reviewing its state before submitting another update.

## Approve or decline a cancellation proposal

Only an approver or administrator is offered the review controls.

**Approve cancellation** confirms a state-changing action. The backend checks the exact proposal, current case/order versions, permissions, ownership, and cancellation policy again. The committed result includes an action receipt.

**Decline proposal** requires one of four fixed review reasons:

- Policy or evidence concern
- Customer asked not to proceed
- Needs manual review
- Other review concern

There is no free-text reason field. The confirmation explains that the case will be escalated and the decision recorded. The decline operation does not execute a cancellation. A rejected proposal is no longer offered for approval in the escalated case.

Decline requests capture the proposal digest and case version. Failed replies retain the same request key for an unchanged retry. Pending review updates disable conflicting inline controls; case or session changes cannot redirect a pending decision.

## Review evaluations and releases

The Evaluation lab separates deterministic workflow regressions from trained routing measurements. An authorized evaluator can compare retained releases using one captured dataset/mode configuration. Comparison jobs run through a worker against the dedicated evaluation database, separately from the inbox database. A partial retry reuses each release's request key instead of duplicating a previously queued job.

The case-level report dialog rejects obsolete responses when another report is opened. Evaluation history currently lists the latest 100 runs; history pagination is deferred. The Release registry describes retained immutable artifacts and the active pointer. Existing cases retain their pinned release.

## Verification and artifacts

The live browser suite requires a disposable synthetic fixture instance with API and worker running and A-100 pending. It creates cases, attaches a reference, declines a proposal, and later approves a cancellation in that fixture. It records video, screenshots, source hashes, and the completed journey count.

```powershell
node scripts/browser-check.mjs
node scripts/oidc-browser-check.mjs
node scripts/accessibility.mjs
```

The isolated browser regressions intercept API responses and do not modify a database:

```powershell
node scripts/ui-race-check.mjs
node scripts/workbench-feature-check.mjs
node scripts/auth-callback-failure-check.mjs
node scripts/operator-followup-check.mjs
```

Results are saved under `artifacts/browser`, `artifacts/security`, and `artifacts/accessibility`. The follow-up mock journeys also inspect both new forms at a 375px width and run targeted axe scans. These checks are bounded fixture evidence; automated scans and mocked identity-provider tests do not establish complete accessibility conformance or real provider interoperability.
