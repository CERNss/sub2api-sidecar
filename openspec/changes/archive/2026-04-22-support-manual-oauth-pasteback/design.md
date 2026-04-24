## Context

The project previously assumed the OAuth provider could redirect directly back into the sidecar service for completion. The user clarified that this is impossible in the target design: the provider will land on a separate localhost callback target, and the user must manually paste that final URL back into the sidecar.

## Goals / Non-Goals

**Goals:**
- Make manual paste-back the only supported OAuth completion flow.
- Keep the original email-driven account naming and group binding behavior unchanged.
- Separate the provider redirect URI configuration from the sidecar app URL.
- Preserve SQLite-backed flow persistence and automated coverage for the new flow.

**Non-Goals:**
- Support automatic browser redirect completion inside the sidecar.
- Add browser automation for copying callback URLs.
- Change the underlying Sub2API orchestration semantics outside the completion handoff.

## Decisions

### 1. Replace direct callback completion with a JSON completion endpoint
The service will use `POST /provision/oauth/complete` as the completion API.

Why:
- The sidecar is not the redirect target.
- A JSON API aligns cleanly with frontend paste-back submission.
- It keeps completion logic explicit and testable.

Alternatives considered:
- Keep a direct callback route in parallel. Rejected because the user clarified that the project design does not support automatic redirect completion.

### 2. Parse the pasted callback URL server-side
The backend will accept the full pasted localhost callback URL and extract `code` and `state` from it.

Why:
- Users can copy the whole URL without manually extracting query parameters.
- Parsing centrally reduces user error and keeps the frontend simple.

Alternatives considered:
- Ask users to paste `code` and `state` separately. Rejected as more error-prone.

### 3. Configure the OAuth provider redirect URI independently
A dedicated `OPENAI_OAUTH_REDIRECT_URI` setting will control both OAuth URL generation and code exchange.

Why:
- The provider redirect target is not the same as the sidecar service URL.
- The same redirect URI must be used consistently during auth URL generation and code exchange.

Alternatives considered:
- Derive redirect URI from the sidecar app base URL. Rejected because it does not match the actual deployment topology.

## Risks / Trade-offs

- [Users paste an incomplete or wrong callback URL] → Validate server-side and return explicit parsing errors.
- [Redirect URI configuration drifts from the actual OAuth provider setup] → Expose the configured redirect URI clearly in both config and UI.
- [Manual copy/paste adds friction] → Keep the UI workflow short and deterministic so the user only copies one final URL.

## Migration Plan

1. Replace the direct completion assumption in the spec.
2. Add a completion endpoint that accepts the pasted callback URL.
3. Update the page to guide the user through the paste-back workflow.
4. Update tests to cover the new completion path.
5. Archive the OpenSpec change so the main spec reflects the new baseline.

Rollback strategy:
- Revert to the previous direct-callback implementation if this requirement changes again.

## Open Questions

- Should the future UI store recent pasted callback URLs temporarily for operator convenience, or remain fully stateless?
