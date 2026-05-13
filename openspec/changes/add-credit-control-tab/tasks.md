## 1. Backend API and upstream client

- [x] 1.1 Confirm the Sub2API admin balance mutation endpoint and payload semantics
- [ ] 1.2 Add Sub2API client methods for credit summary, usage/consumption lookup, and additive balance mutation
- [ ] 1.3 Add Pydantic schemas for credit summaries, manual adjustments, recharge policies, previews, runs, and audits
- [ ] 1.4 Add authenticated credit-control list/detail APIs with filtering and pagination
- [ ] 1.5 Add authenticated manual adjustment APIs for single-user and cohort targets

## 2. Persistence and automatic recharge

- [ ] 2.1 Add SQLite storage for recharge policies and recharge run/audit records
- [ ] 2.2 Implement policy CRUD with validation for amount, schedule, timezone, and target scope
- [ ] 2.3 Implement target-scope resolution and preview
- [ ] 2.4 Implement scheduler execution for due one-time and recurring policies
- [ ] 2.5 Add occurrence deduplication so the same scheduled recharge cannot run twice
- [ ] 2.6 Redact sensitive values before responses and audit persistence

## 3. Frontend

- [ ] 3.1 Add `余额管理` to top-level navigation and safe operator routes
- [ ] 3.2 Build the all-user balance/consumption table with filters, refresh, aggregates, and states
- [ ] 3.3 Build user detail drawer with balance, consumption, group context, API key usage, and recent audit entries
- [ ] 3.4 Build manual balance adjustment workflow with preview/confirmation and per-user results
- [ ] 3.5 Build automatic recharge policy list/editor with one-time and recurring schedules
- [ ] 3.6 Build automatic recharge preview and run history/audit views

## 4. Verification

- [ ] 4.1 Add backend tests for authenticated credit summary and filtering
- [ ] 4.2 Add backend tests for manual adjustment validation, success, partial failure, and audit records
- [ ] 4.3 Add backend tests for policy CRUD, preview, scheduled execution, and deduplication
- [ ] 4.4 Add frontend build and focused UI tests where the project already supports them
- [ ] 4.5 Run backend test suite and frontend build
