## 1. Spec And Config Contract

- [x] 1.1 Document that deployment config accepts no `auto_rotation`, `credit_control`, or `operational_data` runtime sections in OpenSpec, README, and `config.example.yaml`.
- [x] 1.2 Remove deployment parsing for auto-rotation, credit-control, and operational-data runtime settings and their old environment variables.
- [x] 1.3 Reject removed runtime sections and fields with clear startup configuration errors.

## 2. Runtime Settings And Scheduling

- [x] 2.1 Add PostgreSQL-backed runtime settings for operational data (`enabled`, `collect_interval_seconds`, optional `expiration`, optional `retention_seconds`, optional `max_storage_mb`) and credit control (`enabled`).
- [x] 2.2 Add authenticated API endpoints and frontend controls for operational data and credit-control runtime settings.
- [x] 2.3 Start operational data, automatic-rotation, and credit-control schedulers without deployment enable gates; operational data reads its runtime collection interval.
- [x] 2.4 Make each scheduler tick read persisted runtime settings and skip work when disabled.
- [x] 2.5 Keep automatic-rotation policy defaults independent from deployment config.

## 3. Shared Operational Data Reads

- [x] 3.1 Extend operational data collection to persist per-user usage and per-user API-key snapshots needed by credit control and automatic rotation.
- [x] 3.2 Update credit-control read paths to use local operational-data snapshots for user lists, filters, details, and policy target resolution.
- [x] 3.3 Update automatic-rotation read paths to use local operational-data snapshots for groups, user assignment sync, and usage-load calculation.

## 4. Verification

- [x] 4.1 Update config, scheduler, API, store, and frontend tests for the runtime settings contract.
- [x] 4.2 Search the repository and remove stale documentation/config examples for removed deployment runtime settings.
- [x] 4.3 Run OpenSpec validation, backend tests, and frontend build.
