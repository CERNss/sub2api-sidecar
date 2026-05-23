import { makeDefaultSettings } from "./defaults";
import { apiUrl } from "../runtime";
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
  "jsonTemplate",
  "feishuCardTemplate",
  "url",
  "secret"
]);

const KNOWN_SETTINGS_KEYS = new Set<keyof NotificationSettings>(["webhooks", "rules"]);
const notificationSeverityValues = new Set(["info", "warning", "critical"]);
const notificationOperatorValues = new Set(["gt", "gte", "lt", "lte", "eq", "neq"]);
const webhookProviderValues = new Set(webhookProviderOptions.map((option) => option.value));
const webhookMethodValues = new Set(webhookMethodOptions.map((option) => option.value));
const webhookPayloadFieldValues = new Set(webhookPayloadFieldOptions.map((option) => option.value));

function assertKnownObjectKeys(
  source: ApiPayload,
  knownKeys: Set<string>,
  path: string
): void {
  const unknown = Object.keys(source).filter((key) => !knownKeys.has(key));
  if (unknown.length > 0) {
    throw new NotificationApiError(
      `${path} 包含不支持的字段：${unknown.join(", ")}`,
      0,
      { detail: `${path} unsupported field(s): ${unknown.join(", ")}` }
    );
  }
}

function requireString(source: ApiPayload, key: string, path: string): string {
  const value = source[key];
  if (typeof value !== "string") {
    throw new NotificationApiError(
      `${path}.${key} 必须是字符串`,
      0,
      { detail: `${path}.${key} must be a string` }
    );
  }
  return value;
}

function requireBoolean(source: ApiPayload, key: string, path: string): boolean {
  const value = source[key];
  if (typeof value !== "boolean") {
    throw new NotificationApiError(
      `${path}.${key} 必须是布尔值`,
      0,
      { detail: `${path}.${key} must be a boolean` }
    );
  }
  return value;
}

function requireNumber(source: ApiPayload, key: string, path: string): number {
  const value = source[key];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new NotificationApiError(
      `${path}.${key} 必须是数字`,
      0,
      { detail: `${path}.${key} must be a number` }
    );
  }
  return value;
}

function requireStringArray(source: ApiPayload, key: string, path: string): string[] {
  const value = source[key];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new NotificationApiError(
      `${path}.${key} 必须是字符串数组`,
      0,
      { detail: `${path}.${key} must be a string array` }
    );
  }
  return value;
}

function optionalObject(source: ApiPayload, key: string, path: string): Record<string, unknown> | null {
  const value = source[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new NotificationApiError(
      `${path}.${key} 必须是 JSON 对象`,
      0,
      { detail: `${path}.${key} must be a JSON object` }
    );
  }
  return value as Record<string, unknown>;
}

function requireEnum<T extends string>(
  value: string,
  allowed: Set<string>,
  path: string
): T {
  if (!allowed.has(value)) {
    throw new NotificationApiError(
      `${path} 的值不支持：${value}`,
      0,
      { detail: `${path} has unsupported value: ${value}` }
    );
  }
  return value as T;
}

async function requestNotificationJson<T>(
  url: string,
  options: RequestInit,
  fallbackMessage: string
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(url), {
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

function hydrateWebhook(raw: unknown, index: number): NotificationWebhook {
  if (!raw || typeof raw !== "object") {
    throw new NotificationApiError(
      `webhooks[${index}] 必须是对象`,
      0,
      { detail: `webhooks[${index}] must be an object` }
    );
  }
  const source = raw as ApiPayload;
  const path = `webhooks[${index}]`;
  assertKnownObjectKeys(source, KNOWN_WEBHOOK_KEYS as Set<string>, path);
  const provider = requireEnum<WebhookProvider>(
    requireString(source, "provider", path),
    webhookProviderValues,
    `${path}.provider`
  );
  const method = requireEnum<WebhookMethod>(
    requireString(source, "method", path),
    webhookMethodValues,
    `${path}.method`
  );
  const payloadFields = requireStringArray(source, "payloadFields", path).map((field) =>
    requireEnum<WebhookPayloadField>(field, webhookPayloadFieldValues, `${path}.payloadFields`)
  );
  return {
    id: requireString(source, "id", path),
    name: requireString(source, "name", path),
    enabled: requireBoolean(source, "enabled", path),
    provider,
    method: provider === "generic" ? method : "POST",
    payloadFields: payloadFields.length > 0 ? payloadFields : defaultWebhookPayloadFields,
    jsonTemplate: optionalObject(source, "jsonTemplate", path),
    feishuCardTemplate: optionalObject(source, "feishuCardTemplate", path),
    url: requireString(source, "url", path),
    secret: requireString(source, "secret", path)
  };
}

function hydrateRule(raw: unknown, index: number): NotificationRule {
  if (!raw || typeof raw !== "object") {
    throw new NotificationApiError(
      `rules[${index}] 必须是对象`,
      0,
      { detail: `rules[${index}] must be an object` }
    );
  }
  const source = raw as ApiPayload;
  const path = `rules[${index}]`;
  assertKnownObjectKeys(source, KNOWN_RULE_KEYS as Set<string>, path);
  const readIntervalMinutes = requireNumber(source, "readIntervalMinutes", path);
  const forMinutes = requireNumber(source, "forMinutes", path);
  const cooldownMinutes = requireNumber(source, "cooldownMinutes", path);
  if (readIntervalMinutes < 1 || forMinutes < 0 || cooldownMinutes < 0) {
    throw new NotificationApiError(
      `${path} 的时间配置不合法`,
      0,
      { detail: `${path} interval fields are out of range` }
    );
  }
  return {
    id: requireString(source, "id", path),
    name: requireString(source, "name", path),
    enabled: requireBoolean(source, "enabled", path),
    signalKey: requireString(source, "signalKey", path),
    severity: requireEnum<NotificationRule["severity"]>(
      requireString(source, "severity", path),
      notificationSeverityValues,
      `${path}.severity`
    ),
    operator: requireEnum<NotificationRule["operator"]>(
      requireString(source, "operator", path),
      notificationOperatorValues,
      `${path}.operator`
    ),
    threshold: requireString(source, "threshold", path),
    thresholdUnit: requireString(source, "thresholdUnit", path),
    readIntervalMinutes,
    forMinutes,
    cooldownMinutes,
    targetWebhookIds: requireStringArray(source, "targetWebhookIds", path),
    includeResolved: requireBoolean(source, "includeResolved", path),
    includeSnapshot: requireBoolean(source, "includeSnapshot", path)
  };
}

function hydrateSettings(raw: unknown): NotificationSettings {
  if (!raw || typeof raw !== "object") return makeDefaultSettings();
  const source = raw as ApiPayload;
  assertKnownObjectKeys(source, KNOWN_SETTINGS_KEYS as Set<string>, "settings");
  if (!Array.isArray(source.webhooks) || !Array.isArray(source.rules)) {
    throw new NotificationApiError(
      "告警配置必须包含 webhooks 和 rules 数组",
      0,
      { detail: "notification settings must contain webhooks and rules arrays" }
    );
  }
  const webhooks = source.webhooks.map(hydrateWebhook);
  const rules = source.rules.map(hydrateRule);
  if (webhooks.length === 0) return makeDefaultSettings();
  return { webhooks, rules };
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
