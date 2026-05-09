## 1. Provisioning model

- [ ] 1.1 Update flow models and API schemas so `user_id` is optional or absent for OAuth pre-provisioning flows
- [ ] 1.2 Update SQLite persistence and migrations so existing rows continue to load while new flows do not require `user_id`
- [ ] 1.3 Update provisioning response serialization to return account/group scoped flow data

## 2. Provisioning execution

- [ ] 2.1 Remove Sub2API user creation and user-group binding from `POST /provision/start`
- [ ] 2.2 Keep dedicated group creation and OAuth URL generation using the submitted external OAuth account email
- [ ] 2.3 Ensure `POST /provision/oauth/complete` creates and binds the OpenAI OAuth account to the stored `group_id` without user-system side effects
- [ ] 2.4 Update provisioning timeline events to record group creation, OAuth handoff, account creation, and account binding without user creation events

## 3. Dashboard and API presentation

- [ ] 3.1 Update `GET /provision/flows` and `GET /provision/flows/{flow_id}` contracts and handlers so flow summaries/details do not require `user_id`
- [ ] 3.2 Update React UI labels and flow tables to present `email` as external OAuth account email
- [ ] 3.3 Ensure the existing key -> user -> group orchestration workspace remains scoped to upstream users, keys, and groups

## 4. Tests and documentation

- [ ] 4.1 Update mocked Sub2API expectations so OAuth pre-provisioning tests fail if user creation or user binding is called
- [ ] 4.2 Add or update tests for start flow, OAuth completion, flow listing, flow detail, and dashboard rendering without `user_id`
- [ ] 4.3 Update README or operator-facing docs to describe the email as an external OAuth account identifier
- [ ] 4.4 Validate OpenSpec and run the affected automated test suites
