## Context

The current dynamic orchestration path builds loads by summing candidate user usage inside the current rotation pool. That is useful when no group aggregate exists, but it misses upstream group-level consumption that may include all requests already attributed to a group. Sub2API exposes `/api/v1/admin/dashboard/groups`, which returns per-group requests, tokens, and cost for date ranges, so the sidecar can store a stable latest group usage record beside user segmentation.

## Approach

Add a `group_usage` backend slice:

- `GroupUsageSegmentRecord` model with group identity, per-window usage values, daily averages, ratios, member counts, load source metadata, and timestamps.
- `GroupUsageService` that reads latest operational snapshots for groups, users, user usage, and group usage distribution, then upserts one latest record per group.
- PostgreSQL table `group_usage_segments` keyed by serialized group id, with list/count helpers and indexes.
- Authenticated APIs for list and manual refresh.
- Automatic rotation planner that first loads persisted group records for selected pool groups and only falls back to summing user usage candidates for missing group loads.

## Group Usage Windows

- `1d`, `7d`, and `30d` come from upstream dashboard group stats.
- `5h` is derived from local `usage_logs_current_day` grouped by `group_id`, matching the user 5h logic.
- Each record stores `usage_by_window`, `daily_average_by_window`, `baseline_window`, `baseline_daily_average`, `short_term_ratio`, and `medium_term_ratio`.

## Rotation Planning

Automatic rotation uses:

- Group current load: `GroupUsageSegmentRecord.usage_by_window[configured_window]`, falling back to summed candidate usage when absent.
- Candidate move weight: `UserUsageSegmentRecord.usage_by_window[configured_window]`, falling back to existing user usage snapshot when absent.
- Target selection: choose overloaded source groups and underloaded target groups from selected rotation pool groups, then move the highest-impact eligible user only when the simulated move improves the max/min spread by at least `improvement_delta` and remains outside `imbalance_epsilon`.

The planner records metadata including `group_load_source`, before/after load summaries, source and target group loads, decision type, and user segment.

## Decisions

- Store latest group records in V1, not historical snapshots, matching the first user segmentation implementation.
- Treat missing group usage as a fallback condition, not a hard failure, so existing dynamic orchestration still runs.
- Keep the balancing metric as `actual_cost` by default because it reflects billed usage and aligns with the current group dashboard data.
- Keep pool eligibility unchanged: only selected exclusive non-subscription rotation groups can receive dynamic moves.
