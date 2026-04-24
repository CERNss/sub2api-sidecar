## 1. Backend orchestration

- [x] 1.1 Add FastAPI routes for `/`, `POST /provision/start`, `GET /provision/oauth/callback`, and `/health`.
- [x] 1.2 Implement `ProvisioningService` to create groups, create users, bind user groups, generate OAuth URLs, complete callbacks, and update flow status.
- [x] 1.3 Add structured flow models and error handling for validation, downstream admin failures, and callback failures.

## 2. Integration boundaries

- [x] 2.1 Create a centralized `Sub2APIClient` with `x-api-key` auth, environment-backed configuration, and compatibility parsing for uncertain upstream payloads.
- [x] 2.2 Add a replaceable flow store interface with an in-memory implementation for the MVP.

## 3. Operator workflow and docs

- [x] 3.1 Create a minimal HTML page that starts provisioning, renders the API response, and exposes the manual OAuth link.
- [x] 3.2 Add `.env.example`, dependency definitions, and README instructions for local setup and manual testing.
- [x] 3.3 Verify the implemented flow with syntax checks and mock-backed orchestration validation.
