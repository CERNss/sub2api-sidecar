## 1. Spec and config updates

- [x] 1.1 Modify the OpenSpec contract for `openai-oauth-provisioning` to require SQLite-backed persistence and automated tests.
- [x] 1.2 Add SQLite configuration and documentation updates for local setup and test execution.

## 2. Persistence implementation

- [x] 2.1 Add a SQLite flow store implementation with automatic schema initialization and lookup by `flow_id` and `state`.
- [x] 2.2 Switch the app wiring from the in-memory store to the SQLite-backed store.

## 3. Test coverage

- [x] 3.1 Add store tests that verify save, reload, and update behavior across separate SQLite store instances.
- [x] 3.2 Add API tests that verify start flow, OAuth callback completion, and error handling with mocked Sub2API admin responses.
- [x] 3.3 Run validation and test commands, then mark the completed tasks and archive the OpenSpec change.
