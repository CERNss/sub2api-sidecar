## 1. Shared segmentation backend

- [x] 1.1 Add Pydantic models for user usage segment records, refresh summaries, and scheduler settings/status
- [x] 1.2 Add PostgreSQL persistence for upserting, retrieving, listing, and aggregating latest segment records
- [x] 1.3 Implement `UsageSegmentationService` to derive per-window usage, daily averages, ratios, runway, labels, and reasons from operational snapshots
- [x] 1.4 Implement `UsageSegmentationScheduler` with startup/manual refresh support and status snapshots

## 2. APIs and consumers

- [x] 2.1 Add authenticated usage segmentation list, refresh, and scheduler-status APIs
- [x] 2.2 Wire the scheduler into FastAPI lifespan after operational-data refresh and stop it on shutdown
- [x] 2.3 Extend credit-control user snapshots, filters, responses, and aggregates with segment metadata
- [x] 2.4 Extend automatic rotation usage snapshots to prefer persisted segmentation and fall back to existing local usage snapshots

## 3. Frontend

- [x] 3.1 Add usage segment fields to React types and render segment tags in the balance table
- [x] 3.2 Add segment filtering and segment counts to the balance management view
- [x] 3.3 Show usage profile details in the user detail drawer

## 4. Verification

- [x] 4.1 Add store tests for usage segment persistence
- [x] 4.2 Add service tests for segmentation labels and metrics
- [x] 4.3 Add API tests for credit-control segment responses and rotation usage-source integration
- [x] 4.4 Run OpenSpec validation, backend tests, and frontend build
