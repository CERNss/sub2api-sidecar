## 1. Persistence

- [x] 1.1 Add Pydantic models that mirror the frontend `NotificationWebhook`, `NotificationRule`, `NotificationRoutingPolicy`, and `NotificationSettings` shapes
- [x] 1.2 Add SQLite tables for the single-row notification config blob and append-only delivery audit log; initialize schema on startup
- [x] 1.3 Tolerate legacy receiver-only configuration on load by generating default rules routed to the first receiver

## 2. Configuration APIs

- [x] 2.1 Add `GET /notifications/config` returning saved configuration or sensible defaults
- [x] 2.2 Add `PUT /notifications/config` validating receiver ids, rule target webhook ids, and severity/operator/aggregation enums before saving
- [x] 2.3 Reject unauthenticated callers with the same auth contract as other dashboard APIs

## 3. Delivery worker and provider adapters

- [x] 3.1 Add provider adapters that build payload, headers, and signing for `generic`, `feishu`, `dingtalk`, `wecom`, `slack`, and `discord`
- [x] 3.2 Add a delivery client that respects per-request timeout, retries transient failures with exponential backoff, and never touches receivers that are disabled or have an empty URL
- [x] 3.3 Persist a delivery audit row for every send attempt with provider, receiver id, rule id, severity, status code, error, and outbound payload digest

## 4. Test action and audit inspection

- [x] 4.1 Add `POST /notifications/test` that loads the saved config, validates a target rule has at least one enabled receiver with a non-empty URL, sends a labelled test payload, and returns per-receiver delivery results
- [x] 4.2 Add `GET /notifications/deliveries` for inspecting recent delivery attempts with optional limit
- [x] 4.3 Reject test requests for unknown rule ids or rules without enabled receivers

## 5. Verification

- [x] 5.1 Add tests for config CRUD, legacy receiver-only tolerance, and validation errors
- [x] 5.2 Add tests for each provider adapter payload/header shape and HMAC signing where applicable
- [x] 5.3 Add tests for delivery retry, audit recording, and the `/notifications/test` and `/notifications/deliveries` endpoints
