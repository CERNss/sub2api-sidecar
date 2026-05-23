## 1. Backend Existing-Account Path

- [x] 1.1 Add Sub2API client support for building and sending an existing OAuth account configuration update.
- [x] 1.2 Change provisioning start to detect matching existing OAuth accounts before OAuth URL generation.
- [x] 1.3 Persist completed flows and events when existing accounts are configured on start.
- [x] 1.4 Extend provisioning response schemas with status, oauth_required, and optional oauth_account_id/oauth_url.

## 2. Frontend And Docs

- [x] 2.1 Update OAuth provisioning UI to show completed-on-start status and disable OAuth callback controls when no OAuth handoff is required.
- [x] 2.2 Document the existing-account short-circuit behavior in README.

## 3. Verification

- [x] 3.1 Add backend tests for existing-account start path, credential preservation, account config update, binding, and no OAuth URL generation.
- [x] 3.2 Run focused backend tests for provisioning start/complete.
- [x] 3.3 Run frontend typecheck/build.
