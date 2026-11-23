# v0.3 production verification evidence

The current backend cookie-fix verification is `checks/auth-cookie-fix/native-full.source-proof.json`: 210 tests passed. Subsequent frontend/Node-QA and documentation changes are separated in `cloud/resolvelab/current-runtime-component.json`; whole-repository byte equality after those changes is not claimed.

`cloud/manifest.json` binds the reviewed public cloud packet. Its report separates real provider/enrollment/browser checks, internally initiated hosted jobs, mocked frontend checks and application-schema recovery. Owner identifiers, email addresses, subject digests and session/credential material are omitted. Synthetic tenant/source/job/build identifiers are retained as operational provenance.

The frozen `evaluations/` reports retain their pre-cookie source hash and unchanged disclosed 139/95/76-of-160, 19/20 baseline and 20/20 invariant outcomes. Their component comparability is documented in `checks/auth-cookie-fix/ml-component-attestation.json`; they are not relabeled as fresh executions on a later whole-backend hash. Weights, thresholds and evaluation inputs were unchanged.

`backup/` records the local 22-table exported-snapshot restore with tracking/approval. `cloud/recovery/` records separate actual Neon 18.7 application-schema restores, including their scope exclusions. Neither establishes provider PITR/full-account recovery or an uptime/RPO/RTO guarantee.

Historical checkpoints remain visible: `checks/intermediate-before-final-ui/` retains the initial missing-report test failure and frontend drift; `checks/intermediate-before-backup-cleanup/` retains the earlier 196-test pass. `checks/native-full.*` records the later 199-test pre-cookie pass. `checks/neon-cookie-before.xml` retains the three reproduced 503 cookie failures; `checks/neon-cookie-after.xml` records the 28 focused post-fix passes. The temporary missing-report failure's cause was not established; subsequent full suites passed.

Both unexpected hosted OrderFlow dbt failures are retained in `cloud/orderflow/hosted-batch.json`, alongside the three final expected outcomes. All original proof files remain unchanged. `security/` retains raw static/advisory findings; the cloud packet includes reviewed summaries. Zero observed errors/advisories/high-severity findings in a scoped check is not a zero-vulnerability or capacity guarantee.

`publication-privacy-scan.json` and `publication-manifest.json` bind the whole exported tree. Credential scanning is limited to known private variants and token/email patterns; it is not a universal leak guarantee. No private dumps or credential directories are published.
