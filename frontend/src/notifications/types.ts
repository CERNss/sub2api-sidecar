export type NotificationRuleOperator = "gt" | "gte" | "lt" | "lte" | "eq" | "neq";
export type NotificationSeverity = "info" | "warning" | "critical";
export type WebhookProvider = "generic" | "feishu" | "dingtalk" | "wecom" | "slack" | "discord";
export type WebhookMethod = "GET" | "POST";
export type WebhookPayloadField =
  | "rule_id"
  | "rule_name"
  | "signal_key"
  | "severity"
  | "summary"
  | "trigger"
  | "snapshot"
  | "occurred_at"
  | "name"
  | "enabled"
  | "signalKey"
  | "operator"
  | "threshold"
  | "thresholdUnit"
  | "readIntervalMinutes"
  | "forMinutes"
  | "cooldownMinutes"
  | "includeResolved"
  | "includeSnapshot";

export type NotificationSignal = {
  key: string;
  label: string;
  description: string;
  source: string;
  unit?: string;
  defaultThreshold?: string;
  defaultOperator?: NotificationRuleOperator;
  defaultSeverity?: NotificationSeverity;
  defaultReadIntervalMinutes?: number;
};

export type NotificationSignalGroup = {
  title: string;
  description: string;
  signals: NotificationSignal[];
};

export type NotificationWebhook = {
  id: string;
  name: string;
  enabled: boolean;
  provider: WebhookProvider;
  method: WebhookMethod;
  payloadFields: WebhookPayloadField[];
  jsonTemplate: Record<string, unknown> | null;
  feishuCardTemplate: Record<string, unknown> | null;
  url: string;
  secret: string;
};

export type NotificationRule = {
  id: string;
  name: string;
  enabled: boolean;
  signalKey: string;
  severity: NotificationSeverity;
  operator: NotificationRuleOperator;
  threshold: string;
  thresholdUnit: string;
  readIntervalMinutes: number;
  forMinutes: number;
  cooldownMinutes: number;
  targetWebhookIds: string[];
  includeResolved: boolean;
  includeSnapshot: boolean;
};

export type NotificationSettings = {
  webhooks: NotificationWebhook[];
  rules: NotificationRule[];
};

export type NotificationPlaceholder = {
  value: string;
  label: string;
  description: string;
};

export type NotificationDeliveryOutcome = {
  receiverId: string;
  provider: WebhookProvider;
  status: "succeeded" | "failed" | "skipped";
  attemptCount: number;
  responseStatus: number | null;
  errorMessage: string | null;
};

export type NotificationDeliveryRecord = {
  deliveryId: string;
  receiverId: string;
  ruleId: string;
  provider: WebhookProvider;
  severity: NotificationSeverity;
  trigger: "test" | "rule" | "recovery";
  status: "succeeded" | "failed" | "skipped";
  attemptIndex: number;
  responseStatus: number | null;
  errorMessage: string | null;
  payloadDigest: string;
  createdAt: string;
  updatedAt: string;
};

export type NotificationDeliveryHistory = {
  items: NotificationDeliveryRecord[];
  total: number;
};

export type NotificationTestResult = {
  ruleId: string;
  ruleName: string;
  outcomes: NotificationDeliveryOutcome[];
};

export const webhookProviderOptions: { value: WebhookProvider; label: string }[] = [
  { value: "generic", label: "通用 / JSON Webhook" },
  { value: "feishu", label: "飞书 / Lark 自定义机器人" },
  { value: "dingtalk", label: "钉钉自定义机器人" },
  { value: "wecom", label: "企业微信群机器人" },
  { value: "slack", label: "Slack Incoming Webhook" },
  { value: "discord", label: "Discord Webhook" }
];

export const webhookMethodOptions: { value: WebhookMethod; label: string }[] = [
  { value: "POST", label: "POST" },
  { value: "GET", label: "GET" }
];

export const defaultWebhookPayloadFields: WebhookPayloadField[] = [
  "rule_id",
  "rule_name",
  "signal_key",
  "severity",
  "summary",
  "trigger",
  "snapshot",
  "occurred_at"
];

export const webhookPayloadFieldOptions: { value: WebhookPayloadField; label: string }[] = [
  { value: "rule_id", label: "规则 ID" },
  { value: "rule_name", label: "规则名称" },
  { value: "signal_key", label: "信号类型" },
  { value: "severity", label: "严重等级" },
  { value: "summary", label: "摘要" },
  { value: "trigger", label: "触发类型" },
  { value: "snapshot", label: "数据快照" },
  { value: "occurred_at", label: "发生时间" },
  { value: "name", label: "配置: 名称" },
  { value: "enabled", label: "配置: 启用" },
  { value: "signalKey", label: "配置: 信号类型" },
  { value: "operator", label: "配置: 条件" },
  { value: "threshold", label: "配置: 阈值" },
  { value: "thresholdUnit", label: "配置: 阈值单位" },
  { value: "readIntervalMinutes", label: "配置: 检查频率" },
  { value: "forMinutes", label: "配置: 持续时间" },
  { value: "cooldownMinutes", label: "配置: 冷却时间" },
  { value: "includeResolved", label: "配置: 恢复通知" },
  { value: "includeSnapshot", label: "配置: 数据快照" }
];

export const notificationPlaceholders: NotificationPlaceholder[] = [
  { value: "${alert.status}", label: "状态", description: "firing / resolved / test" },
  { value: "${alert.status_label}", label: "状态文案", description: "告警触发 / 告警恢复" },
  { value: "${alert.severity}", label: "等级", description: "critical / warning / info" },
  { value: "${alert.severity_label}", label: "等级文案", description: "Critical / Warning / Info" },
  { value: "${alert.summary}", label: "摘要", description: "本次告警摘要" },
  { value: "${alert.occurred_at}", label: "发生时间", description: "ISO 时间" },
  { value: "${rule.id}", label: "规则 ID", description: "告警规则 id" },
  { value: "${rule.name}", label: "规则名称", description: "告警规则名称" },
  { value: "${rule.threshold}", label: "阈值", description: "规则阈值" },
  { value: "${rule.operator}", label: "条件", description: "比较操作符" },
  { value: "${signal.key}", label: "信号类型", description: "例如 user_balance_low" },
  { value: "${signal.value}", label: "当前值", description: "最近一次采样值" },
  { value: "${signal.scope_label}", label: "范围", description: "用户、账号或分组名称" },
  { value: "${snapshot.value}", label: "快照值", description: "兼容旧模板路径" },
  { value: "${snapshot.data.low_users.0.name}", label: "明细示例", description: "快照数组路径" }
];

export const genericWebhookPayloadExample = {
  alert: {
    status: "firing",
    status_label: "告警触发",
    severity: "critical",
    severity_label: "Critical",
    summary: "Rule '账号失效' firing: value 2 >= threshold 1",
    trigger: "rule",
    occurred_at: "2026-05-23T10:30:00+08:00",
    title: "告警触发 - 账号失效",
    color: "red"
  },
  rule: {
    id: "rule-account-invalid",
    name: "账号失效",
    signalKey: "account_invalid",
    operator: "gte",
    threshold: "1",
    thresholdUnit: "accounts",
    readIntervalMinutes: 2,
    forMinutes: 5,
    cooldownMinutes: 30
  },
  signal: {
    key: "account_invalid",
    value: 2,
    scope_key: "",
    scope_label: ""
  },
  snapshot: {
    trigger: "rule",
    value: 2,
    data: {
      invalid_accounts: [
        { id: "acct_01", name: "primary-openai", error_message: "token expired" }
      ]
    }
  }
};

export const webhookRenderShapeLabels: Record<WebhookProvider, string> = {
  generic: "JSON Payload",
  feishu: "飞书交互卡片",
  dingtalk: "钉钉 Markdown",
  wecom: "企微 Markdown",
  slack: "Slack Blocks",
  discord: "Discord Embeds"
};

export const webhookSecretHints: Record<WebhookProvider, string> = {
  generic: "可选，用于签名或鉴权",
  feishu: "可选，飞书加签密钥（HMAC-SHA256）",
  dingtalk: "可选，钉钉加签密钥",
  wecom: "通常留空（key 已包含在 URL 中）",
  slack: "可选，签名/鉴权 header",
  discord: "通常留空"
};

export const notificationSignalGroups: NotificationSignalGroup[] = [
  {
    title: "账号 & 调度",
    description: "AI 上游账号健康、上游平台 Key 与调度容量。",
    signals: [
      { key: "account_invalid", label: "账号失效", description: "status、error_message、expires_at。", source: "account usage", unit: "accounts", defaultThreshold: "1", defaultOperator: "gte", defaultSeverity: "critical", defaultReadIntervalMinutes: 5 },
      { key: "account_rate_limited", label: "限流/过载", description: "rate_limited_at、reset_at、overload_until。", source: "account usage", unit: "accounts", defaultThreshold: "1", defaultOperator: "gte", defaultSeverity: "warning", defaultReadIntervalMinutes: 5 },
      { key: "account_reauth_needed", label: "需重授/验证/疑似封禁", description: "needs_verify、needs_reauth、is_banned、error_code。", source: "account usage", unit: "accounts", defaultThreshold: "1", defaultOperator: "gte", defaultSeverity: "critical", defaultReadIntervalMinutes: 10 },
      { key: "account_capacity_high", label: "并发/RPM 接近上限", description: "concurrency、rpm、max_sessions。", source: "account usage", unit: "% used", defaultThreshold: "85", defaultOperator: "gte", defaultSeverity: "warning", defaultReadIntervalMinutes: 5 },
      { key: "account_capacity_full", label: "账号容量满载", description: "当前容量达到总容量，例如 5 / 5。", source: "account usage", unit: "accounts", defaultThreshold: "1", defaultOperator: "gte", defaultSeverity: "warning", defaultReadIntervalMinutes: 1 },
      { key: "group_capacity_full", label: "分组容量满载", description: "分组内账号容量汇总达到总容量，例如 12 / 12。", source: "account usage", unit: "groups", defaultThreshold: "1", defaultOperator: "gte", defaultSeverity: "warning", defaultReadIntervalMinutes: 1 },
      { key: "platform_key_health", label: "Key 有效性", description: "isValid、status、可用模型异常。", source: "/v1/usage", defaultThreshold: "1", defaultOperator: "eq", defaultSeverity: "critical", defaultReadIntervalMinutes: 5 },
      { key: "platform_key_expiry", label: "Key 即将过期", description: "expires_at、days_until_expiry。", source: "/v1/usage", unit: "days", defaultThreshold: "7", defaultOperator: "lte", defaultSeverity: "warning", defaultReadIntervalMinutes: 60 }
    ]
  },
  {
    title: "计费 & 用量",
    description: "余额、quota、订阅、用量突增与运维告警。",
    signals: [
      { key: "user_balance_low", label: "用户余额低", description: "balance、通知阈值。", source: "user routes", unit: "USD", defaultThreshold: "5", defaultOperator: "lte", defaultSeverity: "warning", defaultReadIntervalMinutes: 30 },
      { key: "account_quota_low", label: "Quota/Credits 低", description: "总/日/周 quota、AI credits。", source: "account usage", unit: "% remaining", defaultThreshold: "15", defaultOperator: "lte", defaultSeverity: "warning", defaultReadIntervalMinutes: 15 },
      { key: "platform_key_quota", label: "Key 额度低", description: "quota、rate limits、remaining。", source: "/v1/usage", unit: "% remaining", defaultThreshold: "20", defaultOperator: "lte", defaultSeverity: "warning", defaultReadIntervalMinutes: 10 },
      { key: "subscription_usage", label: "订阅用量/过期", description: "日/周/月用量、限额。", source: "/v1/usage", unit: "% used", defaultThreshold: "85", defaultOperator: "gte", defaultSeverity: "warning", defaultReadIntervalMinutes: 30 },
      { key: "admin_cost_spike", label: "成本突增", description: "今日总成本相对昨日总成本的涨幅。", source: "usage stats", unit: "% change", defaultThreshold: "150", defaultOperator: "gte", defaultSeverity: "critical", defaultReadIntervalMinutes: 5 },
      { key: "admin_error_spike", label: "错误突增", description: "今日 usage log 错误数相对昨日错误数的涨幅。", source: "usage logs", unit: "% change", defaultThreshold: "150", defaultOperator: "gte", defaultSeverity: "critical", defaultReadIntervalMinutes: 5 }
    ]
  }
];

export const notificationSignals: NotificationSignal[] = notificationSignalGroups.flatMap(
  (group) => group.signals
);

export const notificationSignalByKey = new Map(
  notificationSignals.map((signal) => [signal.key, signal])
);

export function severityLabel(severity: NotificationSeverity): string {
  if (severity === "critical") return "Critical";
  if (severity === "warning") return "Warning";
  return "Info";
}

export function severityColor(severity: NotificationSeverity): string {
  if (severity === "critical") return "red";
  if (severity === "warning") return "gold";
  return "blue";
}

export function operatorLabel(operator: NotificationRuleOperator): string {
  const labels: Record<NotificationRuleOperator, string> = {
    gt: ">",
    gte: "≥",
    lt: "<",
    lte: "≤",
    eq: "=",
    neq: "≠"
  };
  return labels[operator];
}
