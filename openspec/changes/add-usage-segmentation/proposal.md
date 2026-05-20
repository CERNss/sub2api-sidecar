## Why

Balance management and automatic orchestration both need the same view of user consumption, but today each path reads a single selected usage window on demand. Operators need a persisted, periodically refreshed user usage profile that turns `5h`, `1d`, `7d`, and `30d` history into stable user segments for cohort targeting and rotation decisions.

## What Changes

- Add a shared user usage segmentation capability backed by PostgreSQL.
- Periodically refresh every user's usage profile from the existing operational user, API-key, and usage snapshots.
- Compute normalized metrics including per-window consumption, daily averages, short/long trend ratios, balance runway, API-key presence, and a segment label.
- Expose authenticated APIs to read the latest segment records and scheduler status.
- Surface each user's segment in the `余额管理` user list/detail responses.
- Make automatic rotation read the persisted segmentation snapshot for its configured usage window while preserving its current balancing behavior when a segment record is missing.

## Capabilities

### New Capabilities
- `usage-segmentation`: Persisted user consumption profiling, segment classification, scheduler refresh behavior, and read APIs.

### Modified Capabilities
- `credit-control`: Include usage segment metadata in user summaries and allow balance management to consume the shared profile.
- `group-rotation`: Automatic usage-based rotation reads the shared segmentation snapshot as its usage basis when available.

## Impact

- Affected backend: new Pydantic models, Postgres persistence, segmentation service, scheduler, authenticated API routes, and integration in credit-control and rotation services.
- Affected frontend: balance management user types/table/detail display for segment metadata.
- Affected tests: store persistence, segmentation calculation, credit-control responses, rotation usage-source behavior, scheduler status.
- Data impact: new durable table for latest per-user segment records; no upstream Sub2API mutation behavior changes.
