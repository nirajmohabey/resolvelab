# ResolveLab dataset card — v4

## Source data and attribution

The routing source is [Bitext's customer-support dataset](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset), revision `430d1a89bd93bd1fa23c16f29dd53e73f0087443`. The publisher identifies it as hybrid synthetic data under **CDLA-Sharing-1.0**. The downloaded CSV contains **26,872 rows and 27 intents**, SHA-256 `6f81102b0100b97b8468eb04368033a23206bf1fde9d53500d5806ec1001a434`. Original CSV/card live in ignored `work/raw/`; URLs/checksums are in `artifacts/source_manifest.json`.

Only instruction text is a feature. `track_order` maps to tracking; `cancel_order` to cancellation; `contact_human_agent` to human handoff; all other intents map to unsupported. Source response templates are neither features nor authorized retailer facts. Source-intent slices remain available separately from mapped labels.

V4 uses Unicode normalization, common-contraction expansion, order-reference replacement, negation-aware processing, and explicit-human features. Exact duplicates are removed before label-blind character-shingle MinHash grouping. Detected near-duplicate components remain together in seeded stratified group folds. This leaves **23,443 source rows in 18,321 groups**: 14,066 training, 4,689 validation, and 4,688 test rows. Grouping is approximate and cannot prove that all semantic/template relatives were separated. Split rows, source identities, labels, and hashes are preserved in `artifacts/v4/source_membership-v2.jsonl`.

That membership file contains modified Bitext data and is distributed under CDLA-Sharing-1.0 with the prominent companion `artifacts/v4/NOTICE-data.md`. Modifications include normalization, deduplication, label mapping, and added groups/splits. See `THIRD_PARTY_NOTICES.md` and `LICENSES/CDLA-Sharing-1.0.txt`; the project code license does not replace third-party asset licenses.

## Domain development data

`fixtures/domain_development_v2.jsonl` contains **1,290 coding-agent-authored synthetic development rows**: 319 meaning families with four variants each, plus 14 explicitly marked earlier handbook core regressions. These cover shorthand, ordinary requests, human requests discussing other tasks, mixed unsupported actions, negation, and several order-reference formats. They are not real customer observations and have no independent human adjudication.

| Split | Rows | Assigned groups |
|---|---:|---:|
| Train | 650 | 173, including 14 core regression groups |
| Calibration | 256 | 64 |
| Selection validation | 256 | 64 |
| Untuned family test | 128 | 32 |

All variants of an authored family stay in one split. Features/models fit train only. Temperature fits calibration only. Architecture, regularization, blending, and acceptance threshold use selection validation. Family test does not select them. Generated polite/ID variants are correlated; row counts must not be presented as independent human examples.

`artifacts/domain_manifest-v2.json` records fixture SHA-256 `5d8e43bbb76b20accfd649aa60be7b4b9aa28b32f35c7bdbd3920f0936dbfe8f`, authoring, counts, and the seed. Historical audit exact overlaps were excluded by normalized equality except the explicitly older handbook core regression contract.

## Joint split audit

Within each collection, normalized duplicates and assigned groups do not cross splits. A later cross-collection audit found **three source-validation rows** identical to trained core regressions: normalized cancellation, tracking, and “where is order.” Raw reports remain unchanged. Excluding those three leaves selected source accepted error **20/435**, within the original 5% gate; the stored candidate ranking still selects the same parameters. All feasible candidates had already been required to pass those identical core features, so this sensitivity check does not fit or tune a new model.

The audit found **zero exact training overlaps** in source test, domain calibration, domain selection validation, domain family test, or the new 160-case final audit. These are exact/assigned-group checks, not a guarantee against every linguistic paraphrase. `artifacts/v4/split_audit.json` records the overlaps and selection sensitivity.

## Independent final workflow audit

`fixtures/v4_blind_v1.jsonl` contains **160 cases, 40/class, grouped into 87 authored semantic groups**. A different agent authored them without seeing v4 training/development data. They remained hidden from the training agent until `artifacts/v4/model_freeze.json` pinned weights/hash/threshold. No message, label, weight, or threshold changed after this final audit. Earlier v1–v3 80/100-case sets remain historical diagnostics; they are not advertised as untouched v4 tests.

The evaluator checks actual service/database paths, expected intent/disposition, required evidence/facts, handoffs, and forbidden order effects. All 160 cases, including failures and runner errors, remain in denominators. V4 matched 139 outcomes, with 4 wrong routes among 104 accepted and 1 unsupported acceptance among 40 unsupported cases. Counts and every failure are in `artifacts/v4/blind-final/`. Baseline/v3 use the same cases in `paired-baseline/` and `paired-v3/`.

Uncertainty uses a paired cluster bootstrap over the **87 semantic groups**, retaining each group's variants together and using the same resample for all models. It is not an estimate for a randomly sampled customer population. Separately authored by another agent does not mean independently reviewed by humans. No intervention impact or production accuracy is inferred.

The v0.2 source-only reruns preserved the same 139/95/76 paired workflow counts, the baseline fixture 19/20, and unchanged weights/thresholds. The separate deterministic invariant workload passed 20/20; all runs had zero unauthorized effects/runner errors and complete effect audits. Common source SHA-256 is `f3c9dc2df82b6333dc6aba15346c39d20b3612be236b9a1aef1f20a7b3b45445`. No dataset message, label or split changed. Prior v0.1 reports/cards/analysis are hash-preserved under `artifacts/v4/before-v0.2-hardening/`; older reports remain in `before-intake-binding/`. Release metadata comes from the actual DB-selected model/hash/corpus/retrieval pin, not a global manifest file. Reusing these disclosed frozen cases verifies serving compatibility; it does not create a fresh independent generalization estimate.

## Policies, embeddings, retrieval labels, and factual review

Retailer policies, orders, customers, clocks, and canaries are fictional. Bitext responses do not supply factual support. Main policy corpus versions are immutable. Dense features/ranking use Apache-2.0 `sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, quantized ONNX, 384 dimensions, 256-token truncation, attention-mask mean pooling and unit normalization. Training/serving v4 features both use independent one-example inference. Checksums and publisher card are retained; see `LICENSES/Apache-2.0.txt`.

`fixtures/retrieval_benchmark_v2.json` freezes 80 synthetic sections and 32 queries, split into 16 development and 16 test queries. Only 32 sections are permitted to the benchmark operator; other-tenant, admin-only and retired material are filtered before ranking. Relevant units are labeled policy sections, sometimes more than one per query. Development selects ranking configuration; test reports its fixed result. These are agent-authored relevance labels with **no independent human adjudication**.

Programmatic tests verify response schema, allowed tools, evidence provenance, required policy sections and agreement with authorized order facts. **Human claim-support review remains unavailable.** The CSV queues in `artifacts/v4/final-analysis/` have blank reviewer/adjudication columns. They must not be counted as completed factual review. A real review should inspect all material claims, including uncited claims and missing required facts, and record support/contradiction/insufficient evidence with a small independently double-reviewed overlap.
