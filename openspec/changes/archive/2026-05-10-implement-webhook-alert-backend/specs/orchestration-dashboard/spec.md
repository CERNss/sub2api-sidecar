## ADDED Requirements

### Requirement: Persist webhook alert configuration server-side
The system SHALL expose authenticated APIs that read and write the full webhook alert center configuration document to durable local storage.

#### Scenario: Operator reads notification configuration
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator requests `GET /notifications/config`
- **THEN** the system returns the saved `webhooks`, `rules`, and `policy` document
- **THEN** the response redacts every receiver `secret` value
- **THEN** if no configuration has been saved yet, the system returns a sensible default with at least one disabled placeholder receiver

#### Scenario: Operator saves notification configuration
- **GIVEN** the operator has a valid admin session
- **WHEN** the operator submits a configuration document to `PUT /notifications/config`
- **THEN** the system validates that every rule `targetWebhookIds` value references an existing receiver id
- **THEN** the system validates severity, operator, aggregation, and provider enum values
- **THEN** the system rejects the request with a client error response when validation fails
- **THEN** the system persists the validated document so subsequent `GET /notifications/config` returns the same shape

#### Scenario: Legacy receiver-only configuration is tolerated
- **GIVEN** an existing saved document contains receivers but no rules or routing policy
- **WHEN** the system loads the document
- **THEN** the system synthesizes default rules routed to the first receiver
- **THEN** the system synthesizes a default routing policy
- **THEN** the synthesized configuration is returned to the caller without losing receivers

#### Scenario: Unauthenticated callers cannot read or write configuration
- **GIVEN** a caller has no valid admin session, access-key header, or bearer token
- **WHEN** the caller calls `GET /notifications/config` or `PUT /notifications/config`
- **THEN** the system returns an authentication error
- **THEN** the system does not return or modify configuration

### Requirement: Deliver outbound webhook payloads through provider adapters
The system SHALL deliver webhook payloads to operator-configured receivers using provider-specific payload formatting and signing.

#### Scenario: Generic provider posts a JSON payload with optional HMAC signature
- **GIVEN** a receiver has provider `generic`
- **WHEN** the delivery worker sends a message to that receiver
- **THEN** the request is `POST` with JSON content type and the message rendered as a JSON object
- **THEN** when the receiver has a non-empty secret, the request includes an HMAC-SHA256 signature header derived from the body and the secret

#### Scenario: Feishu and dingtalk receivers use their documented signing schemes
- **GIVEN** a feishu receiver with a non-empty secret
- **WHEN** the delivery worker sends a message
- **THEN** the request body includes a `timestamp` and `sign` field computed from `timestamp + "\n" + secret` using HMAC-SHA256 base64 per Feishu's custom bot documentation
- **GIVEN** a dingtalk receiver with a non-empty secret
- **WHEN** the delivery worker sends a message
- **THEN** the request URL includes `timestamp` and `sign` query parameters computed from `timestamp + "\n" + secret` using HMAC-SHA256 base64 per DingTalk's custom bot documentation

#### Scenario: Slack, discord, and wecom receivers use their native payload shapes
- **GIVEN** a slack receiver
- **WHEN** the delivery worker sends a message
- **THEN** the request body uses Slack's `{"text": ...}` shape
- **GIVEN** a discord receiver
- **WHEN** the delivery worker sends a message
- **THEN** the request body uses Discord's `{"content": ...}` shape
- **GIVEN** a wecom receiver
- **WHEN** the delivery worker sends a message
- **THEN** the request body uses WeCom's `{"msgtype": "text", "text": {"content": ...}}` shape

#### Scenario: Disabled receivers and empty URLs never receive deliveries
- **GIVEN** a receiver is disabled or has an empty URL
- **WHEN** the delivery worker is asked to send a message to that receiver
- **THEN** the system records a skipped delivery audit row
- **THEN** the system does not perform any outbound HTTP request

#### Scenario: Transient delivery failures are retried with backoff
- **GIVEN** the receiver returns a transient HTTP failure
- **WHEN** the delivery worker handles the response
- **THEN** the system retries the request a bounded number of times with exponential backoff
- **THEN** the audit log records each attempt with its `attempt_index`, status, and error
- **THEN** the final outcome is reported as the delivery result

### Requirement: Inspect delivery audit trail and exercise test deliveries
The system SHALL let operators trigger a real test delivery for a saved rule and inspect recent delivery attempts.

#### Scenario: Operator runs a real test delivery for a saved rule
- **GIVEN** an authenticated operator
- **AND** a saved rule whose `targetWebhookIds` reference at least one enabled receiver with a non-empty URL
- **WHEN** the operator calls `POST /notifications/test` with that rule id
- **THEN** the system loads the saved configuration
- **THEN** the system builds a test message labelled with the rule name, severity, and signal key
- **THEN** the system delivers the test message through every targeted enabled receiver using the provider adapter
- **THEN** the response includes the per-receiver outcome with status, attempt count, and error message when applicable

#### Scenario: Test request is rejected when no receiver can be delivered to
- **GIVEN** the requested rule has no enabled receivers, all referenced receivers have empty URLs, or the rule id does not exist
- **WHEN** the operator calls `POST /notifications/test`
- **THEN** the system returns a client error response with a human-readable reason
- **THEN** the system does not perform any outbound HTTP request

#### Scenario: Operator inspects recent deliveries
- **GIVEN** at least one delivery attempt has been audited
- **WHEN** the operator calls `GET /notifications/deliveries`
- **THEN** the system returns recent delivery audit rows ordered by creation time descending
- **THEN** each row includes provider, receiver id, rule id, severity, status, error, attempt index, trigger type, and timestamp
- **THEN** the response respects an optional `limit` query parameter
