## Why
Operational snapshots are collected on a fixed cadence and shared by rotation, balance control, usage segmentation, and group usage decisions. That is efficient for dashboard reads, but it can be unsafe for mutating operations: a scheduler or operator action may execute against stale local data after Sub2API has already changed.

## What Changes
- Add a forced operational-data refresh path for mutation workflows.
- Refresh derived usage segmentation and group usage views immediately after the raw Sub2API snapshot refresh.
- Invoke the forced refresh before real rotation, key transfer, API key creation, rollback, and credit adjustment/recharge execution.
- Keep previews and dry-runs read-only so operators can inspect the current local view without triggering an upstream collection.

## Impact
- Mutating operations may perform extra Sub2API reads before writing.
- If the forced refresh cannot complete cleanly, the mutation is rejected instead of continuing with stale data.
- Existing periodic collection remains unchanged for dashboard and alerting reads.
