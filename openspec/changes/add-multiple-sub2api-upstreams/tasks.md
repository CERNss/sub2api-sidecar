## 1. Backend Configuration And Registry

- [x] 1.1 Add upstream config dataclass, parsing, validation, and default-upstream compatibility.
- [x] 1.2 Add Sub2API client registry lookup by `upstream_id` while preserving default client callers.
- [x] 1.3 Add sanitized upstream list schema and authenticated `GET /api/upstreams`.
- [x] 1.4 Add tests for legacy single-upstream config and multi-upstream config validation.

## 2. API And Flow Plumbing

- [x] 2.1 Add `upstream_id` to provisioning request/response schemas and `ProvisionFlow`.
- [x] 2.2 Start OAuth provisioning flows against selected upstream and persist the selected id.
- [x] 2.3 Complete OAuth flows against the upstream stored on the flow.
- [x] 2.4 Add `upstream_id` query support to orchestration users, groups, accounts, and user API keys APIs.
- [x] 2.5 Add backend tests proving selected upstream URLs/keys are used and unknown upstream ids are rejected.

## 3. Frontend And Docs

- [x] 3.1 Load upstream metadata in the React app.
- [x] 3.2 Add upstream selector to orchestration discovery and reload/clear selections on switch.
- [x] 3.3 Add upstream selector to OAuth provisioning start.
- [x] 3.4 Update `config.example.yaml`, `.env.example`, and README configuration notes.

## 4. Verification

- [x] 4.1 Run focused backend tests for config, provisioning, and orchestration discovery.
- [x] 4.2 Run frontend build or typecheck.
