## 1. Backend Payload Contract

- [x] 1.1 Add a canonical alert payload builder with `alert`, `rule`, `signal`, and `snapshot` sections.
- [x] 1.2 Switch generic POST webhook delivery to send the canonical alert JSON.
- [x] 1.3 Keep generic GET field selection and add nested placeholder rendering.
- [x] 1.4 Keep legacy placeholder names available in template context.

## 2. Provider Rendering

- [x] 2.1 Add a default Feishu interactive card rendered from abstract placeholders.
- [x] 2.2 Render custom Feishu card templates from the same abstract context.
- [x] 2.3 Preserve Feishu and DingTalk signing behavior.
- [x] 2.4 Render Slack blocks, Discord embeds, DingTalk markdown, and WeCom markdown from the same abstract alert payload.

## 3. UI

- [x] 3.1 Show generic POST JSON payload preview instead of field selection.
- [x] 3.2 Show placeholder chips for Feishu card templates and generic GET URLs.
- [x] 3.3 Update the Feishu sample card to use abstract placeholders.
- [x] 3.4 Show the provider-native render shape for supported rich-message receivers.

## 4. Verification

- [ ] 4.1 Run targeted notification backend tests. Blocked locally because pytest and project Python dependencies are unavailable.
- [ ] 4.2 Run frontend TypeScript production build. Blocked locally because npm/project node_modules are unavailable.
- [x] 4.3 Run Python compile and diff whitespace checks.
