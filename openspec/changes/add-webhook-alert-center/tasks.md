## 1. OpenSpec artifacts

- [x] 1.1 Create proposal for webhook alert center
- [x] 1.2 Add orchestration-dashboard delta spec for notification settings
- [x] 1.3 Add design document covering receiver/rule/routing model
- [x] 1.4 Add implementation task checklist

## 2. Frontend alert-center UI

- [x] 2.1 Add top-level `通知设置` navigation beside orchestration and OAuth provisioning
- [x] 2.2 Add typed notification signal catalog for platform key, user, admin, and AI upstream account information classes
- [x] 2.3 Add webhook receiver list and receiver editor
- [x] 2.4 Add alert rule list and rule editor with signal, threshold, operator, aggregation, severity, read interval, evaluation window, sustained duration, repeat interval, recovery, snapshot, and target webhook controls
- [x] 2.5 Add routing/noise controls for grouping, group wait, repeat interval, and quiet hours
- [x] 2.6 Add localStorage persistence and compatibility for older receiver-only local config
- [x] 2.7 Add summary panel for receivers, enabled rules, signal coverage, and routing details
- [x] 2.8 Add responsive CSS for the alert-center workspace

## 3. Verification

- [x] 3.1 Run frontend TypeScript/Vite build
- [x] 3.2 Remove stale receiver-level signal-selection UI and CSS after moving to rule-based configuration
- [ ] 3.3 Validate OpenSpec change with `openspec validate add-webhook-alert-center --json`

## 4. Future backend implementation

- [ ] 4.1 Add authenticated notification configuration APIs backed by SQLite
- [ ] 4.2 Add alert signal collectors keyed by the UI `signalKey` values
- [ ] 4.3 Add scheduler/evaluator for read interval, evaluation window, aggregation, thresholds, recovery, and sustained duration
- [ ] 4.4 Add webhook delivery worker with signing, retry, grouping, quiet-hours, repeat interval, and delivery audit history
- [ ] 4.4.1 Add per-provider sender adapters (generic JSON, feishu/lark, dingtalk, wecom, slack, discord) covering payload schema, signing scheme, and mention semantics
- [ ] 4.5 Add backend/API tests for configuration validation, rule evaluation, delivery routing, and failure handling
