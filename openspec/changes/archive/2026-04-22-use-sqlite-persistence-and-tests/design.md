## Context

The current implementation models flow persistence behind an interface but wires the application to an in-memory store. That was fine for a first local MVP, but it does not satisfy the practical requirement of resuming an OAuth callback after a process restart. The project also needs committed automated tests so the provisioning contract can evolve safely.

## Goals / Non-Goals

**Goals:**
- Replace default flow persistence with SQLite while preserving the store abstraction boundary.
- Ensure pending flows survive process restarts and can be used during OAuth callback completion.
- Add automated tests for the SQLite store and end-to-end HTTP orchestration behavior with mocked Sub2API admin responses.
- Keep runtime dependencies lightweight and local-friendly.

**Non-Goals:**
- Introduce a full ORM or async database stack.
- Add production-grade migration tooling beyond minimal schema initialization.
- Add browser automation for the manual OAuth step in this change.

## Decisions

### 1. Use stdlib `sqlite3` with a dedicated store implementation
A new `SQLiteFlowStore` will implement the existing `FlowStore` interface using Python's built-in `sqlite3` module.

Why:
- It avoids new heavy runtime dependencies.
- SQLite matches the user's persistence requirement and keeps local setup simple.
- The interface already exists, so the change stays localized.

Alternatives considered:
- Keep in-memory storage and add a warning. Rejected because it does not meet the durable persistence requirement.
- Introduce SQLAlchemy. Rejected as unnecessary weight for a single-table local service.

### 2. Persist the full flow payload as JSON plus indexed lookup columns
The SQLite table will store `flow_id`, `state`, `email`, `status`, timestamps, and the full serialized flow payload.

Why:
- It preserves flexible `Any` fields without fragile column modeling.
- It keeps lookup by `flow_id` and `state` efficient.
- It simplifies future schema evolution for this small service.

Alternatives considered:
- Map every field to its own typed column. Rejected because the flow payload contains flexible values and the extra complexity is not needed yet.

### 3. Keep singleton wiring but point it at SQLite by default
`get_flow_store()` will build a cached SQLite-backed store from environment-backed settings.

Why:
- The rest of the app already uses cached dependency builders.
- The application can initialize schema lazily when the store is first created.

Alternatives considered:
- Convert the whole app to an explicit app factory. Rejected for now because the current structure is sufficient and tests can still isolate caches.

### 4. Test HTTP behavior with mocked upstream requests and a temp SQLite database
The tests will use `pytest`, FastAPI's `TestClient`, and mocked `requests.Session.request` responses.

Why:
- This verifies the HTTP contract and orchestration logic without needing a real Sub2API server.
- A temporary SQLite file exercises the real persistence layer.
- The tests will cover both successful flows and failure cases.

Alternatives considered:
- Unit-test only the service layer. Rejected because route-level behavior and response shaping are part of the requirement.

## Risks / Trade-offs

- [SQLite file locking or path issues] → Initialize parent directories, keep operations small, and use one connection per store call.
- [Cached singletons leaking test state] → Clear settings/store caches between tests and inject a unique temp database path per run.
- [JSON payload persistence hides schema drift] → Store indexed lookup columns alongside the payload and validate back into `ProvisionFlow` on read.

## Migration Plan

1. Add `SQLITE_DB_PATH` configuration and a SQLite flow store implementation.
2. Switch application wiring from the in-memory store to the SQLite store.
3. Add tests for the store and the HTTP workflow.
4. Update docs and examples to describe SQLite-backed persistence and test commands.
5. Archive the OpenSpec change so the main spec reflects the new baseline.

Rollback strategy:
- Revert to the previous in-memory wiring if SQLite introduces blocking issues during local development.

## Open Questions

- Should future migrations keep using raw SQL or introduce a migration tool once more tables are added?
