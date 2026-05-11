export type NotificationRuleOperator = "gt" | "gte" | "lt" | "lte" | "eq" | "neq";
export type NotificationSeverity = "info" | "warning" | "critical";
export type WebhookProvider = "generic" | "feishu" | "dingtalk" | "wecom" | "slack" | "discord";

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

export const webhookProviderOptions: { value: WebhookProvider; label: string }[] = [
  { value: "generic", label: "通用 / 自定义 JSON" },
  { value: "feishu", label: "飞书 / Lark 自定义机器人" },
  { value: "dingtalk", label: "钉钉自定义机器人" },
  { value: "wecom", label: "企业微信群机器人" },
  { value: "slack", label: "Slack Incoming Webhook" },
  { value: "discord", label: "Discord Webhook" }
];

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
      { key: "admin_usage_anomaly", label: "成本/错误突增", description: "usage log、错误率、上游错误。", source: "admin routes", unit: "% change", defaultThreshold: "150", defaultOperator: "gte", defaultSeverity: "critical", defaultReadIntervalMinutes: 5 }
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
