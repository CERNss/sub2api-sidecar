## MODIFIED Requirements

### Requirement: Deliver outbound webhook payloads through provider adapters
The system SHALL deliver webhook payloads to operator-configured receivers using provider-specific payload formatting and signing. The system SHALL construct each outbound notification from a stable abstract alert payload containing `alert`, `rule`, `signal`, and `snapshot` sections.

#### Scenario: Generic provider posts an abstract JSON alert with optional HMAC signature
- **GIVEN** a receiver has provider `generic`
- **AND** the receiver method is `POST`
- **WHEN** the delivery worker sends a message to that receiver
- **THEN** the request is `POST` with JSON content type
- **THEN** the request body contains an object with `alert`, `rule`, `signal`, and `snapshot` keys
- **THEN** the `alert` section includes status, severity, summary, trigger, occurrence time, title, and provider color hint fields
- **THEN** the `rule` section includes the rule id, name, signal key, and threshold configuration
- **THEN** the `signal` section includes the signal key and latest value when present
- **THEN** the `snapshot` section contains the raw rule snapshot when snapshots are enabled
- **THEN** when the receiver has a non-empty secret, the request includes an HMAC-SHA256 signature header derived from the body and the secret

#### Scenario: Generic GET receiver can render abstract placeholders
- **GIVEN** a receiver has provider `generic`
- **AND** the receiver method is `GET`
- **WHEN** its URL contains placeholders such as `${alert.status}`, `${rule.name}`, or `${signal.value}`
- **THEN** the delivery worker replaces those placeholders from the abstract alert payload
- **THEN** legacy placeholders such as `$rule_name` and `$severity` remain supported
- **THEN** if the URL contains no placeholders, the configured query fields are appended as before

#### Scenario: Rich-message receivers use provider-native status rendering by default
- **GIVEN** a receiver has provider `feishu`, `slack`, `discord`, `dingtalk`, or `wecom`
- **WHEN** the delivery worker sends a message
- **THEN** the request body uses that provider's native structured message shape
- **THEN** Feishu uses an interactive card
- **THEN** Slack uses blocks
- **THEN** Discord uses embeds
- **THEN** DingTalk uses markdown
- **THEN** WeCom uses markdown
- **THEN** the rendered message includes status, severity, rule name, signal key, current value when present, scope when present, summary, and occurrence time from the abstract alert payload

#### Scenario: Feishu receivers may use custom card templates
- **GIVEN** a receiver has provider `feishu`
- **AND** a custom card template is configured
- **WHEN** the delivery worker sends a message
- **THEN** the card template is rendered from the same abstract placeholder context
- **THEN** legacy placeholders such as `$rule_name`, `${snapshot.value}`, and `${snapshot.data.low_users.0.name}` remain supported

### Requirement: Configure webhook alert receivers and routing
The React UI SHALL let an authenticated operator define webhook receivers and route operational alert rules to one or more receivers.

#### Scenario: Operator manages provider-specific alert payloads
- **GIVEN** the operator opens a webhook receiver editor
- **WHEN** the receiver provider is `generic` with method `POST`
- **THEN** the UI shows an example JSON payload containing `alert`, `rule`, `signal`, and `snapshot`
- **WHEN** the receiver provider is `generic` with method `GET`
- **THEN** the UI shows copyable placeholders that can be used in the URL
- **WHEN** the receiver provider supports rich-message rendering
- **THEN** the UI identifies the provider-native render shape
- **WHEN** the receiver provider is `feishu`
- **THEN** the UI also shows a JSON card template editor, a default template action, and copyable placeholders
