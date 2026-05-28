import {
  NotificationRule,
  NotificationSettings,
  NotificationWebhook,
  defaultWebhookPayloadFields,
  makeEmptyAccountAlertWhitelist,
  makeEmptyGroupAlertWhitelist,
  notificationSignalByKey
} from "./types";

export const DEFAULT_WEBHOOK_ID = "ops-default";

export function makeDefaultWebhook(): NotificationWebhook {
  return {
    id: DEFAULT_WEBHOOK_ID,
    name: "Ops Webhook",
    enabled: false,
    provider: "generic",
    method: "POST",
    payloadFields: defaultWebhookPayloadFields,
    jsonTemplate: null,
    feishuCardTemplate: null,
    url: "",
    secret: ""
  };
}

export function makeDefaultSettings(): NotificationSettings {
  return {
    webhooks: [makeDefaultWebhook()],
    rules: [],
    account_alert_whitelist: makeEmptyAccountAlertWhitelist(),
    group_alert_whitelist: makeEmptyGroupAlertWhitelist()
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
