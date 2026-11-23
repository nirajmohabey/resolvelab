# Release verification

Measured on Windows 11, Python 3.12.14, PostgreSQL 17.12 and the committed dependency lock. All data is synthetic. See `artifacts/release_manifest.json` for report hashes, environment, numerators and limits.

| Check | Executed result |
|---|---|
| Real PostgreSQL suite | 64 passed; includes 25 repeated two-connection approval races |
| Supplied deterministic workflows | 20/20 passed; 0 runner errors; 0 unauthorized order effects; 20/20 effect audits |
| Final independent synthetic workflow | 74/100 passed; 26 retained failures; 0 runner errors; 0 unauthorized effects; 100/100 effect audits |
| Final independent routing | 83/100 accuracy; macro-F1 0.8366; 44/100 accepted, 0/44 accepted errors; 0/25 OOD false accepts |
| Browser workflow | 9 checks passed; desktop 1440×1000 and mobile 390×844; no JavaScript errors |
| Source/model rebuild | Selected parameters and measured metrics reproduced in an isolated copy; serialized bytes are not claimed identical |
| Retrieval | Lexical/dense/hybrid measured separately on 16 labeled synthetic queries |
| Restore | Actual pg_dump/pg_restore; restored original order snapshot checked, then tracking/approval verified on a disposable probe |
| Provider adapter | Mock-tested strict structured contract; no paid-provider benchmark |
| Hosted CI / Docker | Jobs submitted, but GitHub prevented runners from starting due to an account restriction. No clean-Linux or Docker execution result is claimed |
| Public cloud hosting | Not provisioned; the recorded local demonstration is the release fallback |

The 100-case evaluation was authored by a separate agent before v3 was frozen. Three ownership disposition labels were corrected against original case AI-E07 after scoring; no message, intent, model weight or threshold changed. Both label versions and the original 71/100 report are retained with provenance. The corrected contract score is 74/100. This remains an agent-authored synthetic evaluation, not independent human validation or real-customer accuracy.

The main remaining product limitation is excessive escalation on unfamiliar wording. A safe refusal is still a workflow failure when the case should be resolvable. This release keeps those failures visible instead of presenting deterministic fixture success as broad model quality.
