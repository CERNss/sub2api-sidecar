## 1. Group Usage Data Substrate

- [x] 1.1 Add Pydantic models for group usage records and refresh summaries
- [x] 1.2 Add Sub2API client support for dashboard group usage stats
- [x] 1.3 Extend operational data collection to snapshot group usage for 1d/7d/30d and local 5h group logs
- [x] 1.4 Add PostgreSQL persistence for latest group usage records
- [x] 1.5 Implement `GroupUsageService` to derive per-window group usage profiles from snapshots

## 2. APIs And Scheduling

- [x] 2.1 Add authenticated group usage list and refresh APIs
- [x] 2.2 Wire group usage refresh into startup/scheduler cadence beside user segmentation
- [x] 2.3 Include group usage refresh status in scheduler/status responses where useful

## 3. Dynamic Orchestration Refactor

- [x] 3.1 Refactor automatic rotation load calculation to prefer persisted group usage records
- [x] 3.2 Select move candidates by overloaded source group, underloaded target group, and user usage segment weight
- [x] 3.3 Preserve dry-run, cooldown, rollback, and fallback behavior
- [x] 3.4 Add balancing metadata to automatic run records for auditability

## 4. Verification

- [x] 4.1 Add store tests for group usage persistence
- [x] 4.2 Add service tests for group usage profile calculation
- [x] 4.3 Add API tests for group usage endpoints
- [x] 4.4 Add automatic rotation tests proving group-level balancing uses persisted group usage
- [x] 4.5 Run OpenSpec validation, backend tests, and frontend build
