import {
  NotificationRule,
  NotificationSettings,
  NotificationWebhook,
  notificationSignalByKey
} from "./types";

export const DEFAULT_WEBHOOK_ID = "ops-default";

export function makeDefaultWebhook(): NotificationWebhook {
  return {
    id: DEFAULT_WEBHOOK_ID,
    name: "Ops Webhook",
    enabled: false,
    provider: "generic",
    url: "",
    secret: ""
  };
}

export function makeDefaultSettings(): NotificationSettings {
  return {
    webhooks: [makeDefaultWebhook()],
    rules: []
  };
}

export function makeRuleForSignal(signalKey: string, targetWebhookId: string): NotificationRule {
  const signal = notificationSignalByKey.get(signalKey);
  return {
    id: `rule-${signalKey}-${Date.now()}`,
    name: signal?.label ?? signalKey,
    enabled: true,
    signalKey,
    severity: signal?.defaultSeverity ?? "warning",
    operator: signal?.defaultOperator ?? "gte",
    threshold: signal?.defaultThreshold ?? "1",
    thresholdUnit: signal?.unit ?? "",
    readIntervalMinutes: signal?.defaultReadIntervalMinutes ?? 10,
    forMinutes: 5,
    cooldownMinutes: 60,
    targetWebhookIds: targetWebhookId ? [targetWebhookId] : [],
    includeResolved: true,
    includeSnapshot: true
  };
}
