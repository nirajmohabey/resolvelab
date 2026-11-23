# ResolveLab model card — v4

**Published default: `domain-router-v4`, an operator-reviewed support workbench using fictional retailer data.** The trained router, permission-filtered retrieval, scoped local tools, approval transaction, and evaluation are implemented. Cancellation always requires an authorized human to approve an exact proposal. Measured routing errors remain visible; this release does not establish unattended production quality.

## Final paired audit

All releases ran on the same frozen **160 cases across 87 authored semantic groups**, with deterministic drafts and the actual PostgreSQL/service paths. Each class has 40 cases. The test author did not see v4 development data; messages stayed hidden until the artifact and threshold were frozen. No training, threshold tuning, or label editing followed this audit.

| Release | Workflow success | Routing macro-F1 | Supported accepted | Wrong accepted | Unsupported accepted |
|---|---:|---:|---:|---:|---:|
| Source TF-IDF v1 | 76/160 | 0.4638 | 40/160 | 3/40 | 1/40 |
| Domain v3 | 95/160 | 0.8691 | 54/160 | 1/54 | 1/40 |
| Scope + intent v4 | **139/160** | **0.8705** | **104/160** | **4/104** | **1/40** |

V4 produced **21 workflow failures**, zero runner errors, and zero unauthorized effects in **160/160 completed effect audits**. Safe escalation was **95/98**. Its weakest class is human handoff: **28/40 recall**, versus 35/40 tracking and 37/40 cancellation. Unsupported recall was 39/40. Every failure remains in its denominator.

The principal improvement over v3 is **accepted workflow coverage**, not a large raw classification improvement. Macro-F1 changed only from 0.8691 to 0.8705; accepted coverage rose from 33.75% to 65%. Accepted routing error rose from 1/54 to 4/104. On this workload, v4 matched 44 more outcomes than v3: **+27.5 percentage points**, with a paired group-bootstrap interval of **+17.7 to +37.5 points**. The difference versus v1 was +39.375 points, interval +30.0 to +47.4. These intervals resample the 87 authored groups 5,000 times; they show this synthetic audit's group sensitivity, not uncertainty about representative customer traffic.

V4's observed workflow rate was 86.875%, with a group-bootstrap interval of approximately 80.9–92.3%. Its accepted-error interval was approximately 0.8–8.1%. Zero unauthorized effects in these fixtures is bounded evidence, not a universal safety proof. The paired comparison evaluates whole releases, including their retrieval configurations; separate ranking measurements isolate retrieval behavior.

The v0.2 source-only verification reproduced these counts and unchanged model/threshold hashes across all four comparison runs. The deterministic invariant workload also passed **20/20**, with zero effects/errors and complete effect audits. All five reports use source SHA-256 `f3c9dc2df82b6333dc6aba15346c39d20b3612be236b9a1aef1f20a7b3b45445`. Reports, artifact hashes and case failures are in `artifacts/v4/{blind-final,paired-v3,paired-baseline}/`; `final_source_verification.json` records the common source and invariant report. Prior v0.1 reports/cards/analysis are preserved under `artifacts/v4/before-v0.2-hardening/` with verified archive hashes; older measurements remain under `before-intake-binding/`. This rerun verifies hardened serving behavior on existing frozen cases; it is not a new independent model evaluation. Grouped analysis and the paired figure are in `artifacts/v4/final-analysis/`.

## Model and scope decisions

The learned architecture has a **binary supported-scope classifier** and a **three-class supported-intent classifier**. Both use word/character TF-IDF logistic regression and frozen MiniLM dense-feature logistic regression, blended 50/50. Logistic regularization is **C=100**, source sample-weight scale **0.1**, and a temperature **0.7510562** is fitted on a separate calibration split. The validation-selected acceptance threshold is **0.425**.

A versioned semantic transform normalizes order references, expands common contractions, preserves explicit negation, and adds a human-request feature. Training and serving use the same normalized text and **individual-example ONNX inference**, avoiding dynamic-quantization changes caused by different batch composition. A serving/offline check on 128 untouched domain-family test rows found **zero intent or acceptance mismatches**; maximum score difference was 1.12e-7 within a declared 1e-6 floating-point tolerance.

The `explicit-scope-v1` guard rejects recognized unsupported topics and negated automatic actions. It only denies scope; it cannot grant data access or authorize a tool. The final audit contained **27 scope-rule decisions**, distinct from 133 learned decisions. A rule decision is labeled `score_source=scope_rule`; its numeric decision score is **not a calibrated model probability** and is excluded from probability-calibration analysis.

The guard over-denied **six contextual human requests**, labeling them unsupported. They still escalated, but failed the required intent/reason rubric. Other errors included supported requests routed out of scope, two cancellations routed to tracking, one human request routed to cancellation, and one unsupported request routed to tracking. The latter four were accepted. These are concrete limitations; neither rules nor thresholds were changed to hide them after the final audit.

The immutable artifact is `artifacts/router-domain-v4.joblib`, SHA-256 `4978ea5c17100409db15be570fe9985581eb86402acb451aa638aeafedfa57e2`. The pre-test freeze, release metadata, and activation rationale are retained in `artifacts/v4/`. V1/v3 named artifacts and releases remain available for rollback. New registrations do not automatically change an existing database's active pointer.

## Development protocol and split audit

Bitext instructions map `track_order`, `cancel_order`, and `contact_human_agent` to the three supported classes; every other source intent maps to unsupported. Source response templates never become retailer policies or model input. V4 normalizes/deduplicates 26,872 source rows to **23,443**, groups approximate lexical/template variants before splitting, and fits features only on training rows.

New domain development data has **319 authored meaning families and 1,290 rows**, including 14 explicitly marked older handbook regression cases. Its splits contain 650 training, 256 calibration, 256 selection-validation, and 128 family-test rows. All variants of a family stay together. Hyperparameters and thresholds use development validation; temperature uses calibration; test families and new blind cases do not select parameters.

The predeclared candidate gate required all 14 known core regressions to route correctly and accepted supported ones, at most 2% domain-validation accepted error/OOD acceptance, at most 2% source-validation OOD acceptance, and at most 5% source-validation accepted error. V4 passed those gates. The known 20-case live-model workflow now passes **20/20**, versus v1 **19/20**. These known regressions are development evidence, not a fresh generalization estimate.

A joint audit found three exact source-validation overlaps with the explicitly trained core regressions. Excluding them tightens source accepted error from 20/438 to **20/435**, still below 5%; re-evaluating the stored candidate ranking leaves the selected parameters unchanged. There were **zero exact training overlaps** in source test, domain calibration/validation/test, or the fresh 160-case audit. The raw selection report is preserved alongside `split_audit.json`. Approximate grouping cannot guarantee removal of every semantic paraphrase.

Untuned domain-family test macro-F1 was **0.9370 on 128 rows**; source-test macro-F1 was **0.9817 on 4,688 rows**. Learned-route test calibration, excluding deterministic rules, improved ten-bin ECE from **0.0612 to 0.0392** after temperature fitting on separate calibration families. Calibration uses small synthetic sets and does not turn a routing score into a probability of successful customer resolution.

## Retrieval and generation

The new release pins **dense MiniLM retrieval (`dense-minilm-v1`)**. Tenant, published-corpus, and document-access filtering happen in SQL before ranking. Exact 384-dimensional cosine ranking is adequate for the small corpus. Encoder/tokenizer revisions and checksums are pinned.

v0.2 adds admission limits without changing ranking or model parameters: **64 sections across all access scopes, 16 KiB per body, and 256 KiB total body bytes per corpus**. Titles are limited to 512 UTF-8 bytes; source labels and locators to 256 bytes. New writes/publication and serving validate the pinned finite, unit-normalized 384-vectors. Serving reads at most 65 candidate metadata/rows and rejects overflow; it does not silently discard required evidence. Malformed historical array shapes are not transferred to the client. Draft changes and publication share a parent-row lock and MVCC fence; stale Repeatable Read contenders fail serialization. Published/retired evidence remains immutable. The optional provider path also bounds evidence and response bytes. These are reliability controls, not evidence of better model generalization or cloud capacity.

A harder actual-database benchmark contains **80 physical sections, 32 permitted operator sections, and 32 queries** split 16 development/16 test. It includes multi-section answers, near-topic distractors, admin-only content, an actually existing second tenant's canary, and retired policy versions. Dense ranking was selected on development queries. Held-out section recall@3 was **0.84375**, recall@5 **1.0**, MRR@10 **0.8094**, and warm p95 approximately **10.14 ms**. OR-token lexical recall@3 was 0.8125; OR hybrid was 0.78125. Legacy AND matching performed poorly because long queries produced zero-score matches. Hybrid did not beat dense here. Labels are agent-authored and lack independent human adjudication. Ranking quality does not establish factual answer support.

The default generator deterministically renders an approved bounded answer. The optional OpenAI adapter uses a strict enum-bounded answer schema and cannot invent tools, change citations, or bypass policy. Paid-provider quality, token cost, and free-form generation performance have not been measured. Human claim-support review remains **unavailable**, with an unfilled review queue provided; deterministic schema/citation/fact checks are reported separately.

## Resources and reproduction

In the saved local CPU run on the same 80 diagnostic messages, v4 warm routing p50/p95 was **2.67/3.18 ms**, cold model load plus first query after library imports **331 ms**, peak process RSS **196.0 MiB**, and process CPU time **0.859 s** for 81 predictions. V3 p95/RSS was 3.07 ms/193.1 MiB; v1 was 1.15 ms/134.6 MiB. These are isolated fresh-process measurements, not cloud capacity or monetary cost estimates. Full fixture-workflow latency additionally includes database/service assertions and excludes fixture reset time.

`python scripts/train_all.py` verifies shipped artifacts. `--rebuild` executes all training stages in an isolated work copy, compares selected parameters/metrics, and preserves serving manifests and DB pointers. The complete rebuild passed: selected architecture, regularization, blend, threshold and source/family macro-F1 reproduced; temperature differed by about 1.5e-7. Serialization bytes can differ, so actual hashes identify builds. Evidence is in `artifacts/rebuild-runs/390355c7da28/report.json`.

The model is approved as this **operator-reviewed synthetic release** because development/core and authorization/effect gates passed. Unattended real-merchant use needs independently reviewed domain data, stronger human-request handling, lower accepted-error risk, live-provider evaluation if enabled, and deployment-specific verification. Future releases should report actual reviewed customer-domain measurements and deployment cost.
