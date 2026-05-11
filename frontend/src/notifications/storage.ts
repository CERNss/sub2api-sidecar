import {
  NotificationRule,
  NotificationSettings,
  NotificationWebhook,
  webhookProviderOptions
} from "./types";
import { makeDefaultSettings } from "./defaults";

const STORAGE_KEY = "sub2api-notification-settings";

const KNOWN_RULE_KEYS = new Set<keyof NotificationRule>([
  "id",
  "name",
  "enabled",
  "signalKey",
  "severity",
  "operator",
  "threshold",
  "thresholdUnit",
  "readIntervalMinutes",
  "forMinutes",
  "cooldownMinutes",
  "targetWebhookIds",
  "includeResolved",
  "includeSnapshot"
]);

const KNOWN_WEBHOOK_KEYS = new Set<keyof NotificationWebhook>([
  "id",
  "name",
  "enabled",
  "provider",
  "url",
  "secret"
]);

function hydrateWebhook(raw: any, index: number): NotificationWebhook | null {
  if (!raw || typeof raw !== "object") return null;
  const out: any = {};
  for (const key of KNOWN_WEBHOOK_KEYS) {
    if (key in raw) out[key] = raw[key];
  }
  return {
    id: out.id || `webhook-${index + 1}`,
    name: out.name || `Webhook ${index + 1}`,
    enabled: Boolean(out.enabled),
    provider: webhookProviderOptions.some((option) => option.value === out.provider)
      ? out.provider
      : "generic",
    url: out.url || "",
    secret: out.secret || ""
  };
}

function hydrateRule(raw: any, index: number): NotificationRule | null {
  if (!raw || typeof raw !== "object") return null;
  if (!raw.signalKey || typeof raw.signalKey !== "string") return null;
  const out: any = {};
  for (const key of KNOWN_RULE_KEYS) {
    if (key in raw) out[key] = raw[key];
  }
  return {
    id: out.id || `rule-${index + 1}`,
    name: out.name || out.signalKey,
    enabled: out.enabled ?? true,
    signalKey: out.signalKey,
    severity: out.severity || "warning",
    operator: out.operator || "gte",
    threshold: out.threshold ?? "",
    thresholdUnit: out.thresholdUnit || "",
    readIntervalMinutes: Number(out.readIntervalMinutes) || 10,
    forMinutes: Number(out.forMinutes) ?? 5,
    cooldownMinutes: Number(out.cooldownMinutes) ?? 60,
    targetWebhookIds: Array.isArray(out.targetWebhookIds) ? out.targetWebhookIds : [],
    includeResolved: out.includeResolved ?? true,
    includeSnapshot: out.includeSnapshot ?? true
  };
}

export function loadSettings(): NotificationSettings {
  if (typeof window === "undefined") return makeDefaultSettings();
  const saved = window.localStorage.getItem(STORAGE_KEY);
  if (!saved) return makeDefaultSettings();
  try {
    const parsed = JSON.parse(saved);
    const webhooks = Array.isArray(parsed?.webhooks)
      ? (parsed.webhooks.map(hydrateWebhook).filter(Boolean) as NotificationWebhook[])
      : [];
    const rules = Array.isArray(parsed?.rules)
      ? (parsed.rules.map(hydrateRule).filter(Boolean) as NotificationRule[])
      : [];
    if (webhooks.length === 0) return makeDefaultSettings();
    return { webhooks, rules };
  } catch {
    return makeDefaultSettings();
  }
}

export function persistSettings(settings: NotificationSettings): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
}
