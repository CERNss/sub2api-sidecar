## Why

Dynamic orchestration should balance total usage across rotation groups, not only redistribute users by each user's latest consumption. We now have persisted user usage segmentation and a real upstream group usage distribution, so the balancing layer can operate from shared local data instead of recomputing partial views during each run.

## What Changes

- Add a persisted group usage data substrate sourced from Sub2API dashboard group usage distribution and local 5h usage logs.
- Refresh user and group usage profiles on the operational-data cadence and expose read APIs for group usage records.
- Refactor automatic rotation planning to use persisted group usage loads as the source of truth and persisted user usage segments as the predicted move weight.
- Preserve dry-run, audit, rollback, cooldown, pool constraints, and fallback behavior when a group usage record is missing.

## Capabilities

### New Capabilities
- `group-usage`: Persisted group usage profiling, latest group load records, refresh behavior, and read APIs.

### Modified Capabilities
- `usage-segmentation`: Clarify that user segmentation is a shared substrate consumed by group balancing.
- `group-rotation`: Dynamic orchestration balances group-level usage loads using shared group/user usage records.

## Impact

- Affected backend: Sub2API client dashboard group stats wrapper, operational data collection, new Pydantic models, PostgreSQL persistence, group usage service/scheduler wiring, authenticated group usage APIs, and rotation planning logic.
- Affected frontend: optional display of group usage/balancing metadata in dynamic orchestration run records if already surfaced by existing run views.
- Affected tests: group usage persistence/service/API tests and automatic rotation tests for group-level balancing decisions.
- Data impact: new durable latest table for per-group usage profiles; no upstream mutation behavior changes beyond existing `replace-group` execution.
