## 1. Storage And Models

- [x] 1.1 Add new operational data runtime settings for enabled state and optional expiration seconds.
- [x] 1.2 Deprecate the previous notification scheduler tick config path as a runtime source of truth.
- [x] 1.3 Add operational data sample, snapshot, and source-status models.
- [x] 1.4 Add SQLite tables and store methods for saving and reading latest operational samples and snapshots.
- [x] 1.5 Add tests for config parsing, sample persistence, and latest-sample ordering.

## 2. Sampling Pipeline

- [x] 2.1 Implement an operational data collector that fetches accounts, groups, users, current-day usage, and previous-day usage in deterministic order.
- [x] 2.2 Derive supported signal samples from the collected datasets without per-rule upstream calls.
- [x] 2.3 Persist per-source sampling status including success, error, item count, and timestamps.
- [x] 2.4 Add tests proving one collection refresh can serve multiple signal rules.

## 3. Evaluation Refactor

- [x] 3.1 Change scheduled notification ticks to refresh samples once and evaluate due rules from local samples.
- [x] 3.2 Change on-demand rule evaluation to refresh samples first and then evaluate from local samples.
- [x] 3.3 Preserve no-data, sustained-for, cooldown, recovery, and delivery behavior.

## 4. Observability And API

- [x] 4.1 Extend notification scheduler snapshots and response schema with sampling freshness and source status details.
- [x] 4.2 Update docs/config notes for the new operational data config and sampling/evaluation split.

## 5. Verification

- [x] 5.1 Validate the OpenSpec change.
- [x] 5.2 Run the full Python test suite.
