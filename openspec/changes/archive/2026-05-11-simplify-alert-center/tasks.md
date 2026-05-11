## 1. OpenSpec artifacts

- [x] 1.1 Create proposal, design, tasks
- [x] 1.2 Add orchestration-dashboard delta spec (REMOVED + MODIFIED)
- [x] 1.3 Validate change with `openspec validate simplify-alert-center --strict`

## 2. P0 — Backend model + service slimming

- [x] 2.1 `app/models/notification.py`: drop `NotificationRoutingPolicy`; drop `policy` from `NotificationSettings`; drop `recovery_threshold`, `warning_threshold`, `aggregation`, `evaluation_window_minutes` from `NotificationRule`; drop `mention_on_failure` from `NotificationWebhook`; expose `REMOVED_*_KEYS` constants
- [x] 2.2 `app/services/notification.py`: rewrite `_default_settings`, drop `_hydrate_legacy` and `DEFAULT_RULE_SIGNAL_KEYS`, default rules list is empty; add `reject_removed_keys(raw)` validator
- [x] 2.3 `app/services/notification_evaluator.py`: remove `is_quiet_hours`, recovery now uses "no longer breaches" check, drop `recovery_threshold` / `aggregation` / `evaluation_window` references; drop suppress action
- [x] 2.4 `app/main.py`: PUT `/notifications/config` now reads raw JSON, calls `reject_removed_keys` first, then constructs `NotificationSettings`; returns 422 with explicit field name on legacy payloads
- [x] 2.5 Update tests: `tests/test_notifications.py` covers reject-legacy-policy/rule/webhook, tolerate-legacy-on-read, empty-rules-default; `tests/test_notification_scheduler.py` rewritten without policy/aggregation/recoveryThreshold/quiet-hours; full suite green (90 passed)

## 3. P0 — Frontend simplification (combined with P1 module split)

- [x] 3.1 Default rules array is empty; placeholder webhook stays
- [x] 3.2 Removed "路由与降噪" section (no longer rendered)
- [x] 3.3 Removed "失败类消息提醒负责人" switch + `mentionOnFailure` field
- [x] 3.4 Rule timing collapsed to 2 fields: `检查频率` + `冷却时间`
- [x] 3.5 Removed `aggregation` select and `recoveryThreshold` / `warningThreshold` inputs
- [x] 3.6 `<select multiple>` replaced with vertical checkbox list (`.notif-webhook-checks`)
- [x] 3.7 Empty-state card "还没有告警规则" + 主 CTA "添加你关心的第一条告警"
- [x] 3.8 `storage.ts` hydrates only known keys, silently drops legacy `policy` / `recoveryThreshold` / `aggregation` / `evaluationWindowMinutes` / `mentionOnFailure`

## 4. P1 — Module split + summary rewrite

- [x] 4.1 New `frontend/src/notifications/` with `types.ts`, `defaults.ts`, `storage.ts`, `Panel.tsx`, `WebhookEditor.tsx`, `RuleEditor.tsx`, `Summary.tsx`
- [x] 4.2 `App.tsx`: import `NotificationPanel`; old 700-line `NotificationSettingsPanel` deleted; orphaned types + severity/operator helpers deleted (App.tsx trimmed 3843 → 2918 lines)
- [x] 4.3 Summary panel: counts (webhook enabled/total, enabled rules, unrouted rules) + per-rule runtime row + placeholder for upcoming deliveries integration
- [x] 4.4 New CSS namespace `notif-*` replaces the old `webhook-*` / `rule-*` / `notification-*` classes; old block removed; `prefers-reduced-motion` respected

## 5. P2 — Signal catalog consolidation

- [x] 5.1 3 groups → 2 groups: "账号 & 调度" merges 上游账号 + 平台 Key 健康/过期; "计费 & 用量" merges 用户余额 + quota + 订阅 + 运维突增
- [x] 5.2 All signal `key` values stable (account_invalid, account_rate_limited, ..., admin_usage_anomaly, platform_key_quota, ...) — backend collectors keep working

## 6. Verification

- [x] 6.1 `cd frontend && npm run build` clean (0 TS errors, no warnings)
- [x] 6.2 Backend tests pass: `pytest tests/` → 90 passed
- [x] 6.3 Agent-browser smoke test: empty-state renders, "添加第一条告警" CTA adds rule with 5 user-facing fields + checkbox webhook list; legacy localStorage payload (policy + recoveryThreshold + aggregation + mentionOnFailure) loads with extra keys silently dropped
- [x] 6.4 Backend boundary: `PUT /notifications/config` returns 422 with explicit field name when payload contains `policy`, `recoveryThreshold`, `warningThreshold`, `aggregation`, `evaluationWindowMinutes`, or `mentionOnFailure`; valid payload (no removed fields) round-trips correctly
- [x] 6.5 `openspec validate simplify-alert-center --strict` passes
- [ ] 6.6 Archive change
