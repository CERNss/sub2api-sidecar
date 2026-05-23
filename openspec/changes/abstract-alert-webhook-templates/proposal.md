## Why

Webhook alerts currently expose delivery details as a loose set of fields. Operators have to guess which values a provider can render, and rich-message providers need templates or native message shapes that can safely reference alert state without coupling to every internal rule field.

## What Changes

- Introduce a stable abstract alert payload with `alert`, `rule`, `signal`, and `snapshot` sections.
- Render provider templates from the same placeholder context, while keeping legacy placeholder names available for existing templates.
- Make generic POST webhooks send the structured alert JSON directly.
- Make rich-message receivers render provider-native status messages by default: Feishu interactive cards, Slack blocks, Discord embeds, DingTalk markdown, and WeCom markdown.
- Keep Feishu's custom card JSON template editor because that provider exposes a configurable card schema in the current settings model.
- Update the UI to show the generic JSON payload, provider render shape, Feishu card template editor, and copyable placeholders.

## Capabilities

### Modified Capabilities
- `orchestration-dashboard`: webhook alert delivery and configuration now use a stable abstract alert payload and provider-specific rendering.

## Impact

- Affected code: notification delivery adapters, notification tests, notification receiver UI, and dashboard styles.
- Compatibility: existing saved receiver/rule documents remain valid; legacy `$rule_name`, `$severity`, `${snapshot.value}`, and `${snapshot.data...}` placeholders continue to render.
