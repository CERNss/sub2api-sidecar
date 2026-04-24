## Why

The current implementation keeps flow state in memory, which means a process restart can orphan pending OAuth callbacks and break the manual handoff workflow. The project also lacks committed automated tests for the contract we just established, so SQLite-backed persistence and test coverage need to become part of the primary requirement rather than optional follow-up work.

## What Changes

- **BREAKING** Replace the default in-memory flow persistence requirement with SQLite-backed persistence for provisioning flows.
- Modify the provisioning spec so OAuth callbacks can continue using stored flow context after service restarts.
- Add configuration and initialization requirements for the SQLite database path.
- Add an explicit requirement for automated tests that cover the store and HTTP orchestration flow.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `openai-oauth-provisioning`: require SQLite as the default durable flow store and require automated tests for the provisioning workflow.

## Impact

- Affects `app/config.py`, `app/main.py`, `app/stores/*`, README setup instructions, environment configuration, and dependency definitions.
- Adds test infrastructure and test cases for store behavior and HTTP orchestration.
- Updates the main OpenSpec contract for the existing `openai-oauth-provisioning` capability.
