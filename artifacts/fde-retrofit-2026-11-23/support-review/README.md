# Evidence-support review preparation

This is an **AI-assisted archive review**, prepared on November 23, 2026. No human reviewer or customer participated. It prepares a human study; it does not complete one. Human claim-support quality and reviewer disagreement remain **unmeasured**.

The review uses the saved, offline-deterministic outputs from the [frozen production-v0.3 v4 report](../../production-v0.3/evaluations/v4-blind-final/workflow-evaluation.json). It does not rerun the app, use a paid provider, change a route/threshold, or revise the original 139/160 workflow score.

## Frozen sample and findings

The retrospective selection takes the first ten IDs from each of two **expected-answerable** strata in the frozen fixture: owned-order tracking expected to resolve read-only, and eligible cancellation expected to await approval. Selection uses expected labels rather than observed success. The twenty IDs are V4-B001–V4-B010 and V4-B041–V4-B050. This selection is deterministic, was made after viewing the report, and is not preregistered, blind, random, or representative of production traffic. Related cases are not independent customer trials.

| Archive finding | Count | Meaning |
| --- | ---: | --- |
| Expected-answerable cases selected | 20 | Ten tracking; ten cancellation |
| Saved nonempty responses | 20/20 | Every selected case remains in the denominator |
| Bounded expected workflow completed | 19/20 | One already reported routing failure is retained |
| Missing output | 0/20 | A missing output would fail, rather than be dropped |
| Missing required answer | 1/20 | V4-B003 omits the required owned-order status |
| Material factual claims annotated | 30 | Includes the uncited scope claim and order facts |
| Claims supported by saved permitted policy | 10/30 | The exact-proposal approval condition |
| Order claims unverifiable from archived tool evidence | 19/30 | Nine tracking facts and ten eligibility facts |
| Scope claims contradicted by the frozen workflow rubric | 1/30 | The known V4-B003 `route_wrong` failure |

The archived report saves policy evidence, citations and proposal metadata, but **does not save the raw authorized order lookup result** for these cases. Seeded fixture values and the original runner assertions corroborate the nineteen order claims indirectly. They cannot replace a per-case observed, customer-authorized tool result. Those claims are therefore marked *insufficient archived evidence*, not false. No response qualifies as fully verified against all required primary evidence in this archive (`strict_support_given_archived_evidence`: 0/20); that is an **archive evidence-sufficiency result, not a factual-accuracy estimate or a new model benchmark**. `substantive_truth` remains unmeasured; the missing tool snapshots do not establish nineteen false claims.

The tracking ownership citation establishes when disclosure is allowed; it does not establish the current order status. The cancellation eligibility citation establishes required conditions; a pending proposal alone does not prove the actual ownership/status/deadline lookup. The ten approval-policy claims are directly supported by each response's cited, saved permitted `alpha-cancel-v1#approval` text. Citation IDs, source, corpus, locator and excerpt are checked against that response's own evidence snapshot. No other customer's or tenant's records are added to the packet.

V4-B003 says “This request is outside automated support.” Its frozen input asks what owned order A-102's record shows, and the rubric expects supported tracking. The missing answer and contradicted scope claim reproduce its already published failure; no code change is justified by this review alone. A fresh, consented review could decide which production failure is worth addressing and evaluate it on new cases.

## Review files

- [sample-manifest.json](sample-manifest.json): exact IDs, selection rule, eligible populations, semantic-group count and immutable source hashes.
- [review.json](review.json): all twenty original answers, per-case permitted policy snapshots, citations, saved proposals, indirect fixture context and thirty claim annotations.
- [human-review.csv](human-review.csv): thirty claim rows, with separate blank reviewer names/judgments, adjudication and completeness notes. The agent judgments are explicitly labeled.
- [verification.json](verification.json): existing report, dataset, seed fixture, model manifest, model bytes and original human-review queue hashes checked before and after preparation.
- [prepare_review.py](prepare_review.py): offline packet preparation; refuses unexpected sample/output forms or mismatched model/dataset hashes. Writes only in this directory.

## Human review and handoff

1. Have two actual reviewers independently read each full response and input, including any material claim omitted by the draft segmentation. Check helpfulness and completeness against the operator need as well as the bounded workflow rubric.
2. Use only the case's permitted published policy and customer-authorized order evidence. Preserve the evidence gap on these historical outputs. If obtaining new observed tool snapshots is required, run a separately identified evaluation in an isolated database and retain its new release/time/provenance; do not manufacture historical tool results or overwrite the original reports.
3. Label every material claim supported, contradicted or insufficient. Policy citation validity and current order facts must be scored separately. Record uncited claims, missing required facts and missing answers; keep every selected case in the response denominator.
4. Fill the two reviewers' names and independent judgments, then record disagreements and adjudication. Publish response-level support and completeness with exact denominators and examples. Leave disagreement unmeasured until actual independent judgments exist.

This packet contains synthetic retailer fixtures. It establishes no customer adoption, measured productivity savings, merchant integration, paid-provider quality, or human validation.
