## Why

The deployment config still exposes runtime scheduler switches, provisioning assignment mode, and automatic-rotation tuning fields even though operational data is now the shared source for notifications, credit control, and automatic orchestration. This makes the runtime contract harder to understand, requires process restarts for operator switches, and lets separate modules drift back into their own polling settings.

## What Changes

- **BREAKING** Remove deployment-config support for `auto_rotation`, `credit_control`, `operational_data`, and `provisioning.assignment_mode` runtime settings entirely.
- Store provisioning runtime settings (`assignment_mode`) in SQLite and expose them through authenticated API/UI.
- Store operational-data runtime settings (`enabled`, optional `expiration`) in SQLite and expose them through authenticated API/UI.
- Store credit-control scheduler runtime settings (`enabled`) in SQLite and expose them through authenticated API/UI.
- Keep automatic-rotation execution enabled state and policy fields on the authenticated dynamic orchestration API/UI.
- Use one code-owned operational cadence of 60 seconds for background scheduler loops. Schedulers are process services; each tick reads current SQLite runtime settings before doing work.
- Route notification, credit-control, and automatic-orchestration read/decision inputs through the neutral operational-data snapshot/metric layer wherever upstream state is required. Upstream mutation APIs remain direct write operations.
- Update docs, examples, and tests so removed fields are absent rather than silently ignored.

## Capabilities

### New Capabilities

### Modified Capabilities

- `deployment-tooling`: The example deployment config no longer includes runtime scheduler sections.
- `group-rotation`: Automatic rotation background scheduling is governed by persisted runtime dynamic orchestration config, not deployment config.
- `orchestration-dashboard`: Operational data collection is a neutral shared runtime source with fixed internal cadence, persisted runtime settings, and no per-consumer deployment scheduler configuration.

## Impact

- Affected code: settings parsing, SQLite runtime settings, FastAPI runtime settings endpoints, scheduler startup/status, provisioning assignment lookup, notification expiration lookup, credit-control scheduling, rotation runtime defaults, UI settings controls, tests, README, and `config.example.yaml`.
- Affected config: removed sections include `auto_rotation`, `credit_control`, `operational_data`, and `provisioning`; removed env vars include their previous scheduler/policy/runtime equivalents.
- Affected operators: existing `config.yaml` files must remove those sections and configure runtime switches from the authenticated web UI/API.
