import { makeDefaultSettings } from "./defaults";
import {
  NotificationDeliveryHistory,
  NotificationDeliveryRecord,
  NotificationDeliveryOutcome,
  NotificationRule,
  NotificationSettings,
  NotificationTestResult,
  NotificationWebhook,
  WebhookPayloadField,
  WebhookMethod,
  WebhookProvider,
  defaultWebhookPayloadFields,
  webhookMethodOptions,
  webhookPayloadFieldOptions,
  webhookProviderOptions
} from "./types";

type ApiPayload = Record<string, unknown>;

export class NotificationApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

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
  "method",
  "payloadFields",
  "url",
  "secret"
]);

async function requestNotificationJson<T>(
  url: string,
  options: RequestInit,
  fallbackMessage: string
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers
      }
    });
  } catch (error) {
    throw new NotificationApiError(
      `${fallbackMessage}：无法连接后端服务，请确认 API 服务正在运行。`,
      0,
      { detail: error instanceof Error ? error.message : fallbackMessage }
    );
  }
  const payload = await readNotificationPayload(response, fallbackMessage);

  if (!response.ok) {
    const detail = makeNotificationErrorMessage(response, payload, fallbackMessage);
    throw new NotificationApiError(detail, response.status, payload);
  }

  return payload as T;
}

async function readNotificationPayload(
  response: Response,
  fallbackMessage: string
): Promise<unknown> {
  const text = await response.text().catch(() => "");
  if (!text.trim()) {
    return { detail: fallbackMessage };
  }
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text.trim() };
  }
}

function getPayloadDetail(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const source = payload as ApiPayload;
  if (typeof source.detail === "string") return source.detail;
  if (Array.isArray(source.detail)) {
    const first = source.detail.find((item) => item && typeof item === "object") as
      | ApiPayload
      | undefined;
    if (first) {
      const path = Array.isArray(first.loc) ? first.loc.join(".") : "";
      const message = typeof first.msg === "string" ? first.msg : "";
      return [path, message].filter(Boolean).join(": ");
    }
  }
  if (typeof source.message === "string") return source.message;
  return "";
}

function makeNotificationErrorMessage(
  response: Response,
  payload: unknown,
  fallbackMessage: string
): string {
  const detail = getPayloadDetail(payload);
  if (detail && detail !== fallbackMessage) {
    return detail;
  }
  if (response.status === 500 && response.headers.get("Content-Type")?.startsWith("text/plain")) {
    return `${fallbackMessage}：后端服务不可用或代理失败，请确认 API 服务正在 127.0.0.1:8000 运行。`;
  }
  return `${fallbackMessage}：服务返回 HTTP ${response.status}`;
}

function hydrateWebhook(raw: unknown, index: number): NotificationWebhook | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Partial<NotificationWebhook>;
  const out: Partial<NotificationWebhook> = {};
  for (const key of KNOWN_WEBHOOK_KEYS) {
    if (key in source) out[key] = source[key] as never;
  }
  const provider: WebhookProvider =
    out.provider && webhookProviderOptions.some((option) => option.value === out.provider)
      ? out.provider
      : "generic";
  const method: WebhookMethod =
    out.method && webhookMethodOptions.some((option) => option.value === out.method)
      ? out.method
      : "POST";
  const validPayloadFields = new Set(webhookPayloadFieldOptions.map((option) => option.value));
  const payloadFields = Array.isArray(out.payloadFields)
    ? out.payloadFields.filter((field): field is WebhookPayloadField =>
        typeof field === "string" && validPayloadFields.has(field as WebhookPayloadField)
      )
    : defaultWebhookPayloadFields;
  return {
    id: out.id || `webhook-${index + 1}`,
    name: out.name || `Webhook ${index + 1}`,
    enabled: Boolean(out.enabled),
    provider,
    method: provider === "generic" ? method : "POST",
    payloadFields: payloadFields.length > 0 ? payloadFields : defaultWebhookPayloadFields,
    url: out.url || "",
    secret: out.secret || ""
  };
}

function hydrateRule(raw: unknown, index: number): NotificationRule | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Partial<NotificationRule>;
  if (!source.signalKey || typeof source.signalKey !== "string") return null;
  const out: Partial<NotificationRule> = {};
  for (const key of KNOWN_RULE_KEYS) {
    if (key in source) out[key] = source[key] as never;
  }
  const readIntervalMinutes = Number(out.readIntervalMinutes);
  const forMinutes = Number(out.forMinutes);
  const cooldownMinutes = Number(out.cooldownMinutes);
  return {
    id: out.id || `rule-${index + 1}`,
    name: out.name || source.signalKey,
    enabled: out.enabled ?? true,
    signalKey: source.signalKey,
    severity: out.severity || "warning",
    operator: out.operator || "gte",
    threshold: out.threshold ?? "",
    thresholdUnit: out.thresholdUnit || "",
    readIntervalMinutes: Number.isFinite(readIntervalMinutes) && readIntervalMinutes >= 1
      ? readIntervalMinutes
      : 10,
    forMinutes: Number.isFinite(forMinutes) && forMinutes >= 0 ? forMinutes : 5,
    cooldownMinutes: Number.isFinite(cooldownMinutes) && cooldownMinutes >= 0
      ? cooldownMinutes
      : 60,
    targetWebhookIds: Array.isArray(out.targetWebhookIds) ? out.targetWebhookIds : [],
    includeResolved: out.includeResolved ?? true,
    includeSnapshot: out.includeSnapshot ?? true
  };
}

function hydrateSettings(raw: unknown): NotificationSettings {
  if (!raw || typeof raw !== "object") return makeDefaultSettings();
  const source = raw as Partial<NotificationSettings>;
  const webhooks = Array.isArray(source.webhooks)
    ? source.webhooks.map(hydrateWebhook).filter(Boolean)
    : [];
  const rules = Array.isArray(source.rules) ? source.rules.map(hydrateRule).filter(Boolean) : [];
  if (webhooks.length === 0) return makeDefaultSettings();
  return {
    webhooks: webhooks as NotificationWebhook[],
    rules: rules as NotificationRule[]
  };
}

function hydrateDeliveryOutcome(raw: unknown): NotificationDeliveryOutcome | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as ApiPayload;
  const provider = typeof source.provider === "string" ? source.provider : "generic";
  const status = typeof source.status === "string" ? source.status : "failed";
  return {
    receiverId: String(source.receiver_id ?? ""),
    provider: webhookProviderOptions.some((option) => option.value === provider)
      ? provider as WebhookProvider
      : "generic",
    status: status === "succeeded" || status === "skipped" ? status : "failed",
    attemptCount: Number(source.attempt_count ?? 0),
    responseStatus: source.response_status === null || source.response_status === undefined
      ? null
      : Number(source.response_status),
    errorMessage: typeof source.error_message === "string" ? source.error_message : null
  };
}

function hydrateDeliveryRecord(raw: unknown): NotificationDeliveryRecord | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as ApiPayload;
  const provider = typeof source.provider === "string" ? source.provider : "generic";
  const severity = typeof source.severity === "string" ? source.severity : "warning";
  const trigger = typeof source.trigger === "string" ? source.trigger : "rule";
  const status = typeof source.status === "string" ? source.status : "failed";
  return {
    deliveryId: String(source.delivery_id ?? ""),
    receiverId: String(source.receiver_id ?? ""),
    ruleId: String(source.rule_id ?? ""),
    provider: webhookProviderOptions.some((option) => option.value === provider)
      ? provider as WebhookProvider
      : "generic",
    severity: severity === "info" || severity === "critical" ? severity : "warning",
    trigger: trigger === "test" || trigger === "recovery" ? trigger : "rule",
    status: status === "succeeded" || status === "skipped" ? status : "failed",
    attemptIndex: Number(source.attempt_index ?? 0),
    responseStatus: source.response_status === null || source.response_status === undefined
      ? null
      : Number(source.response_status),
    errorMessage: typeof source.error_message === "string" ? source.error_message : null,
    payloadDigest: String(source.payload_digest ?? ""),
    createdAt: String(source.created_at ?? ""),
    updatedAt: String(source.updated_at ?? "")
  };
}

function hydrateTestResult(raw: unknown): NotificationTestResult {
  if (!raw || typeof raw !== "object") {
    return { ruleId: "", ruleName: "", outcomes: [] };
  }
  const source = raw as ApiPayload;
  return {
    ruleId: String(source.rule_id ?? ""),
    ruleName: String(source.rule_name ?? ""),
    outcomes: Array.isArray(source.outcomes)
      ? source.outcomes.map(hydrateDeliveryOutcome).filter(Boolean) as NotificationDeliveryOutcome[]
      : []
  };
}

function hydrateDeliveryHistory(raw: unknown): NotificationDeliveryHistory {
  if (!raw || typeof raw !== "object") {
    return { items: [], total: 0 };
  }
  const source = raw as ApiPayload;
  const items = Array.isArray(source.items)
    ? source.items.map(hydrateDeliveryRecord).filter(Boolean) as NotificationDeliveryRecord[]
    : [];
  const total = Number(source.total ?? items.length);
  return {
    items,
    total: Number.isFinite(total) ? total : items.length
  };
}

export function getNotificationApiErrorMessage(error: unknown, fallbackMessage: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallbackMessage;
}

export async function loadNotificationSettings(): Promise<NotificationSettings> {
  const payload = await requestNotificationJson<unknown>(
    "/notifications/config",
    { method: "GET" },
    "加载告警配置失败"
  );
  return hydrateSettings(payload);
}

export async function loadNotificationDeliveries(limit = 50): Promise<NotificationDeliveryHistory> {
  const payload = await requestNotificationJson<unknown>(
    `/notifications/deliveries?limit=${encodeURIComponent(String(limit))}`,
    { method: "GET" },
    "加载告警历史失败"
  );
  return hydrateDeliveryHistory(payload);
}

export async function sendNotificationTest(ruleId: string): Promise<NotificationTestResult> {
  const payload = await requestNotificationJson<unknown>(
    "/notifications/test",
    {
      method: "POST",
      body: JSON.stringify({ rule_id: ruleId })
    },
    "发送测试消息失败"
  );
  return hydrateTestResult(payload);
}

export async function saveNotificationSettings(
  settings: NotificationSettings
): Promise<NotificationSettings> {
  const payload = await requestNotificationJson<unknown>(
    "/notifications/config",
    {
      method: "PUT",
      body: JSON.stringify(settings)
    },
    "保存告警配置失败"
  );
  return hydrateSettings(payload);
}
