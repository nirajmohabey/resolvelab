# ResolveLab model card

The active release is **domain-hybrid-v3**, a trained four-class router for a fictional retailer. It predicts `track_order`, `cancel_request`, `human_handoff`, or `out_of_scope`. A score is a routing score, not a factual-correctness guarantee, customer identity, or authorization. Deterministic ownership, tenant, evidence, state, deadline, and approval checks control disclosures and effects.

## Active model and frozen inputs

V3 combines **50% word/character TF-IDF logistic probabilities and 50% frozen MiniLM dense-feature logistic probabilities**, with **C=100** and a validation-selected **0.725 acceptance threshold**. Unsupported predictions escalate regardless of score. The model artifact is `artifacts/router-domain-v3.joblib`, SHA-256 `6856ace9b0eed25a2d818f6002fa023caa29e8c1199cdb9c5d648ccc217e8829`. The artifact, threshold, and manifest were frozen before the fresh 100-case final audit. No parameter was changed after that audit.

TF-IDF features combine word unigrams/bigrams and character-within-word three-to-five-grams, fitted on training text only. Logistic regression uses seed 42. The second classifier consumes 384-dimensional embeddings from pinned `sentence-transformers/all-MiniLM-L6-v2` ONNX weights. The embedding model is frozen rather than fine-tuned. Neither source response text nor retailer policies enter routing features.

Training joins grouped Bitext source training rows with **560 new domain development rows from 140 authored meaning families**. Source examples receive class-balanced sample weights scaled by 0.005; domain examples receive class-balanced weight 1. This downweights the source's much larger unsupported class while retaining its breadth. All variants of an authored family stay in one split. Source and domain validation alone choose source strength, C, probability blend, and threshold. The predeclared rule maximizes correctly accepted supported domain cases with at most 5% accepted routing error, at most 5% domain unsupported false acceptance, and at most 5% source unsupported false acceptance. Ties prefer fewer wrong domain accepts, better source coverage, and then the simpler model. Raw selection candidates and split checks are in `artifacts/domain_training_report.json`.

This chose 92/96 supported domain validation cases accepted, zero wrongly accepted validation cases, and zero unsupported domain false acceptances. Source validation had 421 correctly accepted supported cases and two wrong accepts. Validation results are selection evidence, not final performance estimates.

## Measured routing results

| Frozen workload | N | Active v3 macro-F1 | Context |
|---|---:|---:|---|
| Grouped Bitext source test | 4,766 | 0.9416 | Synthetic source-distribution test; retained unchanged |
| Held-out domain meaning families | 112 | 0.9821 | 28 families; 84 supported accepted, zero wrong accepts |
| Fresh separately authored final audit | 100 | 0.8366 | 83/100 correct routes; 44 accepted; zero wrong accepted routes |

The final actual-service workflow succeeded on **74/100 cases**, with **26 failures**, **zero runner errors**, and **zero unauthorized effects** across 100/100 completed effect audits. All **59/59** cases labeled as requiring escalation escalated safely. Routing accuracy was **83/100**. Unsupported false acceptance was **0/25**, while only **44/100** messages passed the supported confidence gate with zero wrong accepted routes. This conservative coverage leaves useful requests unresolved automatically.

These small, agent-authored synthetic sets do not establish real-customer accuracy or a universal safety guarantee. `artifacts/v3-blind-final/` contains the corrected-label v1.1 actual-service report; its dataset SHA-256 is `f51ebcc14b2bdf39778cfa8c6b01188a5f51142b615144f0cd7533314769bb85`. The original 71/100 report remains in `artifacts/v3-blind-original-labels/`. Three wrong-owner A-103 disposition labels changed from `needs_info` to `escalated` to match the original AI-E07 handbook contract. No message, routing label, model weight, or threshold changed. Routing quality and workflow success use separate denominators; failures and runner errors remain in those denominators.

Macro-F1 is the mean of four class F1 values with zero-division set to zero. Confusion matrices, supports, log loss, reliability bins, and acceptance/error counts are saved with each report. Raw probabilities are not post-hoc calibrated. The reliability plot in `artifacts/domain_calibration.png` contrasts the source and held-out domain-family tests; a high score must not be interpreted as a support-resolution probability.

**Remaining limitation:** the router still escalates useful requests, especially terse or unfamiliar phrasing. V3 can accept natural language such as `Where is my order?` and `Please cancel my order`, while the terse command `Track A-100.` may route as unsupported and `Cancel A-100.` can fall below threshold. Mixed supported/unsupported requests also remain difficult. The system preserves an inspectable case and support handoff when it abstains. It is a bounded portfolio demonstration, not a replacement for a production support team.

## Baselines and domain adaptation evidence

The initial TF-IDF classifier and keyword/majority baselines use the same grouped source split. Their source-test macro-F1 was **0.9968 trained**, **0.5671 keyword**, and **0.2382 majority**. An initial separately authored 80-case audit exposed severe source-to-domain mismatch: trained macro-F1 **0.4735**, keyword **0.6001**, and majority **0.1000**. Only 19/60 supported requests were accepted. This is why the source score is not the headline product claim.

A validation-selected 75/25 TF-IDF/dense v2 blend did not fix that gap. A second held-out 80-case workflow audit measured routing macro-F1 **0.5186** and accuracy **42/80**. Both observed 80-case sets were then retired as historical diagnostics before v3 development. Neither audit's messages nor labels were fitted or used to select v3 parameters. Their observed failure motivated the new domain data, so they are not advertised as fresh v3 holdouts. The new 100-case final audit was authored by another agent and kept hidden from the training agent until v3's artifact and threshold were frozen. No further tuning followed.

The source-to-domain gap is consistent with synthetic templates, narrow source-intent mapping, and domain language mismatch. This is an explanation to investigate, not a causal finding. V3 trades some source-distribution macro-F1 for better domain handling. All named v1/v2 artifacts and raw reports remain available; the better-looking source metric was not substituted for the final audit.

## Reproduction and local resources

`python scripts/train_all.py` validates shipped frozen artifacts and their checksums. `python scripts/train_all.py --rebuild` executes fetch, grouped source baseline, dense challenger, domain selection, measurement, and plots. Its training commands never fit on final audit data. Changed rebuilds receive a content-addressed artifact filename, preserving older release-pinned files. `artifacts/model_manifest.json` identifies the selected artifact; register it with the explicit seed/release workflow. Never load a user-uploaded joblib or pickle file.

The full rebuild command was executed in an isolated source/input copy. Selected parameters and both source/domain test macro-F1 values reproduced exactly, while the original final artifact remained unchanged. Serialized joblib bytes differed between builds, so numeric reproducibility is claimed rather than byte-identical serialization. `artifacts/rebuild_verification.json` records those comparisons and both hashes.

`scripts/benchmark_routing.py` measured each router in a separate fresh CPU process on the same 80 historical diagnostic messages. Active v3 warm p50/p95 was **2.85/3.52 ms**, cold load plus first prediction **277.9 ms**, peak process RSS **193.1 MiB**, and process CPU time including cold load **0.781 s** for 81 predictions. The TF-IDF baseline was **0.91/1.09 ms**, cold **61.0 ms**, and peak RSS **134.2 MiB**. Interpreter/library memory is included. These single local measurements exclude database, retrieval, generation, and provider latency. Raw values and scope are in `artifacts/routing_latency.json`; no cloud throughput or cost is inferred.

## Retrieval and response boundaries

Retrieval filters organization, selected published corpus, and document access in SQL before ranking. PostgreSQL full-text search is the lexical baseline. Dense search uses exact cosine ranking of the small filtered corpus. Hybrid ranking sums `1/(60+rank)` across lexical and dense lists. Stored dimension and model revision must match the 384-dimensional configured encoder. Pinned tokenizer and ONNX checksums are verified before loading. The local model truncates to 256 tokens, applies attention-mask mean pooling, and unit normalizes.

On 16 frozen, agent-labeled synthetic queries against the actual nonowner runtime database role, policy-section recall@1 was **0.375 lexical**, **0.9375 dense**, and **0.6875 hybrid**. Recall@2 was **0.625**, **1.000**, and **0.9375**. Hybrid did not beat dense. Recall@4 was 1.0 for all modes because the permitted corpus has only four policy sections; that is a completeness sanity check. `artifacts/retrieval_report.json` preserves every ranking, corpus/query hashes, runtime role, and warm latency. This tiny corpus and unadjudicated labels do not estimate broad search quality.

The default offline generation adapter returns the deterministic structured draft unchanged and makes no paid calls. The optional OpenAI Responses adapter uses a strict bounded answer schema; the permitted answer is an enum and must be transcribed verbatim. It cannot change intent, citations, tool arguments, or tenant. The service separately checks the exact answer, schema, required evidence, and cited chunk provenance. This is a deliberately bounded provider integration, not general free-form answering. Paid live-provider evaluation has not been run and its token cost remains unavailable.

## Release and evidence control

Cases pin model artifact/hash/version/threshold, corpus, prompt, and tool policy. Rollback changes the active release pointer while retaining prior artifacts and case evidence. Runtime checks reject missing, altered, or mismatched artifacts. Restores and local action correctness are established by separate database-backed checks, not classification scores. Keep workflow, security, routing, retrieval, and response-support results distinct.

`artifacts/model_metadata_correction.json` records a metadata-only repair: a baseline feature description inherited C=4, while the actual v3 bundle and selection report already used C=100. The original evaluation-time manifest is preserved as `model_manifest-v3-frozen.json`; current metadata now describes the actual v3 pipeline. Artifact SHA, threshold, predictions, and datasets were unchanged.
