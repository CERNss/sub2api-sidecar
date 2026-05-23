## Design

The delivery layer now treats provider formatting as a rendering step over one canonical alert object:

- `alert`: delivery status, severity, summary, trigger, title, occurrence time, and color hint.
- `rule`: rule identity and threshold/cadence configuration.
- `signal`: signal key, latest value, and optional scoped target labels.
- `snapshot`: the raw sample snapshot, when enabled.

Generic POST sends this object directly. Generic GET and provider templates render placeholders from the same object. The renderer also overlays the previous flat field names into the template context so saved templates using `$rule_name`, `$severity`, `$threshold`, or `${snapshot.data...}` do not break.

Rich-message providers use their native structured shape instead of plain text where supported:

- Feishu: interactive card, with optional custom card JSON rendered recursively.
- Slack: blocks, with header, summary, fields, and timestamp context.
- Discord: embeds, with color, title, summary, fields, and timestamp.
- DingTalk: markdown.
- WeCom: markdown.

A placeholder that occupies the entire JSON string preserves the source value type, so arrays and numbers remain arrays and numbers.

The UI intentionally keeps provider editing narrow: generic POST shows the fixed JSON object, generic GET keeps query-field selection for backward compatibility, all non-generic providers show their render shape, and Feishu exposes the card template editor plus copyable placeholders.
