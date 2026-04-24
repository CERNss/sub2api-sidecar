## 1. Contract updates

- [x] 1.1 Modify the OpenSpec contract for `openai-oauth-provisioning` to require a single manual paste-back completion flow.
- [x] 1.2 Document the dedicated OAuth provider redirect URI configuration and updated operator workflow.

## 2. Backend implementation

- [x] 2.1 Replace direct callback completion assumptions with a `POST /provision/oauth/complete` endpoint that parses pasted callback URLs.
- [x] 2.2 Ensure start flow and OAuth code exchange both use the configured external localhost redirect URI.

## 3. Frontend and verification

- [x] 3.1 Update the HTML page to guide users through click -> paste-back completion.
- [x] 3.2 Update tests to cover start flow, paste-back completion, and malformed callback input.
- [x] 3.3 Run validation and test commands, then archive the OpenSpec change.
