# Two-minute reviewer walkthrough

The supplied WebM is an actual automated browser recording, without narration. Use this script for a longer live explanation. The retailer, orders, policies and customer subjects are fictional.

| Time | Show | Explain |
|---|---|---|
| 0:00–0:20 | Alpha Operator inbox; pending cancellation | An operator receives a case tied to verified customer context. The routing score does not grant permission. |
| 0:20–0:40 | Policy evidence and execution trace | Tenant/published-corpus/access filters precede ranking. The case preserves evidence and release versions. |
| 0:40–1:00 | Switch to Alpha Approver and approve | Exact proposal hash, case/order versions, owner, deadline and role are checked again. Approval, order change and one action receipt commit together. |
| 1:00–1:15 | Failed provider case and Retry resolution | The case and error stage survive an outage. A retry uses a current version and a new key. |
| 1:15–1:30 | Beta Operator | Tenant isolation is enforced in direct API/database tests, not just navigation. |
| 1:30–1:50 | Evaluation lab | Show 20/20 deterministic invariants separately from 74/100 independent workflow success. Explain the 26 conservative-routing failures. |
| 1:50–2:00 | Release registry and saved restore report | Models are hash-pinned; rollback changes only new cases. A real disposable restore verified tracking and approval. |

For transaction depth, open `tests/test_security_and_transactions.py`: independent connections and a barrier race two approvers 25 times. Before/after-commit faults verify rollback or persisted idempotent replay. These are deterministic simulated crash boundaries, not a claim that an operating-system process was killed at every boundary.

To reset the demo, run `scripts/local.ps1 reset`, then `python scripts/populate_demo.py`. Do not run a fixture reset against a non-project database. Tests/evaluations use separate configured databases.

For model rollback evidence run `python scripts/verify_rollback.py`. For an actual restored backup check run `python scripts/backup_check.py`. Both save machine-readable results; the restore check writes only to its new disposable database.
