## Why

The alert center shipped with the full Prometheus/Alertmanager mental model (双阈值滞回、aggregation、separate read interval vs. evaluation window、policy-level grouping、quiet hours、failure-mention 等)，但本项目监控的是布尔/单值离散信号（账号失效、余额低、quota 低），且没有时序库、没有 Alertmanager、没有"负责人"模型。结果是：

- 每条规则 12 个字段，普通用户区分不清 `evaluationWindow` 和 `forMinutes`。
- 默认 8 条规则预置，第一眼是"我得改吗"而不是"我能配什么"。
- `warningThreshold` 字段被读写却没有任何 UI 渲染 — 半成品。
- `mentionOnFailure` 没有任何"负责人"模型可挂。
- 路由 policy 段 (`groupBy` / `groupWait` / 全局 `repeatInterval` / `quietHours`) 与规则级 `cooldownMinutes` 重复抑制并存，且没有 Alertmanager 在中间真正消费这些字段。
- `<select multiple>` 是公认最差的多选 UX。
- `App.tsx` 单文件 3843 行，`NotificationSettingsPanel` 单组件 700+ 行。

## What Changes

**P0 — 模型瘦身**
- 移除字段：`policy.*`（整段路由/降噪/quiet hours）、`rule.recoveryThreshold`、`rule.warningThreshold`、`rule.aggregation`（始终视为 `latest`）、`rule.evaluationWindowMinutes`（与 `readIntervalMinutes` 合并）、`webhook.mentionOnFailure`。
- 简化时间字段：4 个（read / evaluation / for / cooldown） → 2 个（"每 N 分钟检查"+"冷却 N 分钟"）。
- 默认规则数量：8 → 0。新用户进入显示空状态引导，主动添加第一条。
- 多选目标 webhook：`<select multiple>` → checkbox 列表。

**P1 — 代码与摘要瘦身**
- 把 `NotificationSettingsPanel` 从 `App.tsx` 抽到 `frontend/src/notifications/` 目录。
- 右侧"当前配置"摘要从配置回显改为运行时状态（"X 条规则已启用 · Y 条正在告警 · Z 条最近静默"）。
- 删除"失败类提醒负责人"开关与对应模型字段。

**P2 — 信号瘦身**
- 信号分组从 3 组合并为更扁平的列表；"运维告警"与"账号失效/限流/重授"概念重叠，合并为单一 ops 分组。

**BREAKING**：localStorage 与 SQLite 中的旧 settings 在加载时丢弃被删除字段；旧字段 hydrate 路径删除。`PUT /notifications/config` 拒绝包含已移除字段的请求 → 客户端必须升级。

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `orchestration-dashboard`：删减 webhook alert 模型/UI/scheduler/evaluator 字段；简化 UI 表单；改写运行时摘要语义。

## Impact

- **Affected code**: `app/models/notification.py`, `app/services/notification.py`, `app/services/notification_evaluator.py`, `app/services/notification_delivery.py`, `app/services/notification_scheduler.py`, `frontend/src/App.tsx`, 新增 `frontend/src/notifications/*`
- **Affected docs**: `openspec/specs/orchestration-dashboard/spec.md`
- **Operational impact**：旧 saved config 被截断；首次访问的运营者看到空规则列表而非 8 条预置；告警表达力下降但匹配实际使用（布尔/单值信号）。
- **Out of scope**：不改 provider 投递格式、不改 `/notifications/test`、`/notifications/deliveries` 契约；不动 SQLite schema（沿用 JSON document 列，新代码只读/写收窄字段集）。
