## Context

Operational data collection already snapshots users, user usage, and API keys. Credit control reads those snapshots on demand for one selected window, while automatic rotation builds a small usage snapshot during each run. The new segment layer should sit between collection and consumers: it derives a latest per-user profile once, stores it, and lets balance management and rotation read the same facts.

## Approach

Add a `usage_segmentation` backend slice:

- `UserUsageSegmentRecord` model with user identity, group context, balance, API-key count, per-window usage values, normalized daily averages, trend ratios, runway days, segment label, reasons, and timestamps.
- `UsageSegmentationService` that reads latest operational snapshots (`users`, `user_usage`, `user_api_keys`) and upserts one segment record per user.
- `UsageSegmentationScheduler` that runs on the same operational cadence constant as other background jobs.
- PostgreSQL table `user_usage_segments` keyed by serialized user id, with indexes for segment and refresh ordering.
- Authenticated endpoints for list/status/manual refresh.

## Segment Rules

The first implementation uses explainable static thresholds:

- `heavy`: 30-day daily average `>= 5.0` or 7-day daily average `>= 7.0`
- `spike`: 5-hour dailyized usage is `>= 3x` the 30-day daily average and 5-hour usage is non-zero
- `active`: 30-day daily average `>= 1.0` or 7-day daily average `>= 1.5`
- `light`: any known usage below active thresholds
- `idle`: no positive known usage

These defaults are intentionally deterministic and cheap to compute. They are stored in each record's `reasons` and `metadata` so operators can audit the label without reverse engineering code.

## Consumer Integration

Credit control:

- Load latest segment records in `_load_user_snapshots`.
- Attach `usage_segment`, `usage_segment_label`, and `usage_profile` to each row.
- Allow `segment` filtering and include segment counts in aggregates.
- Render segment tags in the balance table and detail drawer.

Automatic rotation:

- `_build_usage_snapshot` first loads `UserUsageSegmentRecord`.
- For the configured rotation window, use `usage_by_window[window]` as `usage_value`.
- Include segment label, daily averages, trend ratios, and `usage_source=usage_segmentation` in rotation audit snapshots.
- Preserve the existing local user usage fallback when no segment record exists.

## Scheduling

Segmentation is refreshed independently from credit policy execution. Startup performs an initial best-effort refresh after operational data refresh. A background scheduler then refreshes on the same base cadence. The service reads local snapshots only, so it does not add new upstream API calls; freshness is bounded by the operational-data collection cadence.

## Data Model Sketch

`user_usage_segments`:

- `user_id_key` primary key
- `email`, `segment`, `observed_at`, `refreshed_at`
- `payload` JSON model dump
- `created_at`, `updated_at`

The payload remains the source of truth for full profile details, while columns make common list queries and indexes cheap.

## Decisions

- The segment layer stores only latest records in V1. Historical trends can be added later by changing persistence to append snapshots while keeping a latest table.
- Missing usage produces an `idle` segment instead of omitting the user, matching the balance-management principle that unknown fields should remain visible.
- Thresholds are code constants in V1, not runtime settings, to keep the data contract stable while operators validate the labels.
- Segmentation reads operational snapshots rather than Sub2API directly, avoiding a second collector and keeping scheduling responsibilities clear.
