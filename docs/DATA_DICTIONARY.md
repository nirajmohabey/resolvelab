# Persistence grains

All timestamps are timezone-aware UTC instants; PostgreSQL may display its session offset. Proposal hashes normalize timestamps to UTC. UUID identities are internal; order references belong to a tenant.

| Table | Grain / key | Meaning |
|---|---|---|
| tenants | tenant_id | Fixture retailer organization |
| memberships | tenant, actor | Active role resolved from verified identity |
| corpus_versions | tenant, corpus | Published immutable policy version and embedding metadata |
| documents | tenant, corpus, document | Versioned policy source and access scope |
| chunks | tenant, chunk | Policy section with locator, FTS vector and 384-dimensional embedding |
| model_versions | tenant, model | Artifact URI/hash, source/split hashes, threshold and metrics |
| releases | tenant, release | Exact model/corpus/prompt/tool-policy combination |
| active_releases | tenant | Pointer used only when creating a new case |
| fixture_orders | tenant, order_ref | Owned current order snapshot with positive version |
| cases | tenant, case | Authenticated intake context, message, state, pinned release and version |
| trace_events | tenant, trace | One structured stage outcome; no hidden reasoning transcript |
| proposals | tenant, proposal | Exact cancellable snapshot and digest, expiry, state |
| proposal_evidence | tenant, proposal, chunk | Policy sections supporting a proposed effect |
| approvals | tenant, proposal | One approval of the exact digest by a current approver |
| actions | tenant, action; unique proposal | One local connector outcome, coupled to approval |
| handoffs | tenant, case | One internal escalation, not a sent email |
| idempotency_keys | tenant, actor, operation, key | Original canonical request hash and persisted result |
| evaluation_runs | tenant, evaluation | Mode, pinned release, dataset, state, lease and metrics |

Messages are bounded to 6,000 characters and HTTP bodies to 32 KiB. List pagination is capped at 100. Unknown cost remains null. Routing score, retrieval score and workflow success have different denominators.
