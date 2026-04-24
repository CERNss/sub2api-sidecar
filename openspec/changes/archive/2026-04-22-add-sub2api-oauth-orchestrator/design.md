## Context

This repository implements a lightweight FastAPI service that orchestrates a Sub2API-managed OpenAI OAuth flow. The workflow is split between automated server-side admin API calls and a deliberate manual handoff for the actual OAuth authorization step. The implementation must preserve the entry email as the canonical identity, because the business flow cannot rely on provider-returned email data in the callback.

The codebase is intentionally small: a FastAPI app, a thin HTML frontend, a `requests`-based admin client, and an in-memory flow store. Even so, the change crosses multiple layers and depends on an external admin API with some path and payload uncertainty, which makes the design worth capturing explicitly.

## Goals / Non-Goals

**Goals:**
- Keep controllers thin by moving orchestration into a service layer.
- Centralize all Sub2API admin API behavior and compatibility handling in one client class.
- Preserve the original flow email through start and callback, especially when creating the OpenAI OAuth account.
- Make flow persistence swappable so Redis or database storage can be introduced later without rewriting the orchestration logic.
- Provide a minimal operator-facing HTML page that can run locally without a frontend framework.

**Non-Goals:**
- Implement persistent storage in this change.
- Implement automated browser OAuth or Playwright-based flow completion.
- Guarantee exact Sub2API endpoint names for every deployment; instead, isolate the adjustments to one client module.
- Add production deployment, authentication, or advanced frontend UX.

## Decisions

### 1. Use a service layer for orchestration
The design places workflow logic in `ProvisioningService` instead of the FastAPI route handlers.

Why:
- The workflow spans multiple dependent steps and state transitions.
- A service makes it easier to keep the controller focused on HTTP concerns.
- Future changes like retries, compensation, or alternate stores belong here.

Alternatives considered:
- Put all orchestration directly in route handlers. Rejected because it would make the controller hard to test and extend.

### 2. Isolate uncertain Sub2API API details in `Sub2APIClient`
The client owns endpoint candidates, request payload construction, header configuration, response parsing, and compatibility comments.

Why:
- The upstream admin API may vary by deployment.
- The controller and service should depend on semantic operations like `create_group` and `exchange_openai_code`, not on raw paths and payload keys.
- Future adjustments stay local to one module.

Alternatives considered:
- Scatter raw `requests` calls across the service. Rejected because it couples orchestration logic to protocol details and makes later adjustments error-prone.

### 3. Use the flow store as the source of truth for callback identity
The callback resolves a flow by `state` and then uses `flow.email` for account creation.

Why:
- The business requirement explicitly says the entry email is authoritative.
- OAuth provider data may be absent, inconsistent, or intentionally ignored.
- This keeps the identity contract deterministic.

Alternatives considered:
- Infer the account name from the OAuth exchange response. Rejected because it violates the stated requirement.

### 4. Start with in-memory storage behind an interface
The implementation uses `FlowStore` plus `InMemoryFlowStore`.

Why:
- The initial scope is local, minimal, and fast to run.
- The abstraction keeps the upgrade path to Redis/DB clean.
- The interface boundary is small and centered around flow lifecycle operations.

Alternatives considered:
- Introduce Redis immediately. Rejected to avoid unnecessary setup and operational weight for a minimal local service.

### 5. Keep the frontend as a server-rendered static HTML page with `fetch`
The operator page is a simple HTML template with a small amount of inline JavaScript.

Why:
- The requirement explicitly asks for a minimal frontend.
- This keeps the local startup path simple and dependency-light.
- The page only needs to collect email, call one API, render JSON, and surface an OAuth link.

Alternatives considered:
- Add a frontend framework. Rejected as unnecessary complexity for the feature scope.

## Risks / Trade-offs

- [Sub2API path or payload mismatch] → Keep candidate paths and response extraction logic inside the admin client and document that future fixes should stay there.
- [In-memory flow state is lost on restart] → Accept for local MVP; preserve a clean store interface so Redis/DB can be added later.
- [Partial downstream provisioning failure] → Log each step clearly and fail fast with explicit error responses so operators can see where the workflow stopped.
- [Manual OAuth step depends on user action] → Surface the OAuth URL clearly in both the API response and the HTML page.

## Migration Plan

1. Keep the current lightweight implementation as the local baseline.
2. Use this spec as the source of truth for future refactors.
3. When persistent storage is needed, add a new `FlowStore` implementation and swap the dependency wiring.
4. When real Sub2API API details differ, update only the admin client path and parsing definitions.

Rollback strategy:
- Revert the small service if needed; there is no data migration in this change because storage is in-memory only.

## Open Questions

- Which exact Sub2API admin API paths and payload keys are authoritative in the target environment?
- Does the upstream account-creation API support direct group binding, or should binding always remain a separate step?
- Should the project eventually add automated end-to-end tests for the browser handoff and callback flow?
