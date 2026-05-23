import { Modal, Popconfirm, Select, Tag } from "antd";
import { ChevronDown, Clipboard, Eraser, Eye, FileJson, Plus, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import {
  genericWebhookPayloadExample,
  notificationPlaceholders,
  NotificationSettings,
  NotificationWebhook,
  WebhookPayloadField,
  WebhookMethod,
  WebhookProvider,
  webhookMethodOptions,
  webhookPayloadFieldOptions,
  webhookProviderOptions,
  webhookSecretHints
} from "./types";

type Props = {
  settings: NotificationSettings;
  selectedWebhookId: string;
  onSelect: (id: string) => void;
  onChange: (id: string, partial: Partial<NotificationWebhook>) => void;
  onToggleEnabled: (id: string, enabled: boolean) => void;
  onAdd: () => void;
  onRemove: (id: string) => void;
  savingWebhookToggleId: string;
  renderSaveAction: (scope: string) => ReactNode;
};

const FEISHU_SAMPLE_CARD_TEMPLATE: Record<string, unknown> = {
  config: { wide_screen_mode: true },
  header: {
    template: "${alert.color}",
    title: {
      tag: "plain_text",
      content: "${alert.title}"
    }
  },
  elements: [
    {
      tag: "div",
      fields: [
        {
          is_short: true,
          text: {
            tag: "lark_md",
            content: "**状态**\n${alert.status_label}"
          }
        },
        {
          is_short: true,
          text: {
            tag: "lark_md",
            content: "**等级**\n${alert.severity_label}"
          }
        },
        {
          is_short: true,
          text: {
            tag: "lark_md",
            content: "**规则**\n${rule.name}"
          }
        },
        {
          is_short: true,
          text: {
            tag: "lark_md",
            content: "**信号**\n${signal.key}"
          }
        },
        {
          is_short: true,
          text: {
            tag: "lark_md",
            content: "**当前值**\n${signal.value}"
          }
        },
        {
          is_short: true,
          text: {
            tag: "lark_md",
            content: "**范围**\n${signal.scope_label}"
          }
        }
      ]
    },
    { tag: "hr" },
    {
      tag: "div",
      text: {
        tag: "lark_md",
        content: "**摘要**\n${alert.summary}"
      }
    }
  ]
};

const GENERIC_SAMPLE_JSON_TEMPLATE: Record<string, unknown> = {
  status: "${alert.status}",
  severity: "${alert.severity}",
  title: "${alert.title}",
  summary: "${alert.summary}",
  rule: {
    id: "${rule.id}",
    name: "${rule.name}",
    threshold: "${rule.threshold}",
    operator: "${rule.operator}"
  },
  signal: {
    key: "${signal.key}",
    value: "${signal.value}",
    scope: "${signal.scope_label}"
  },
  snapshot: "${snapshot}"
};

function stringifyTemplate(template: Record<string, unknown> | null): string {
  return template ? JSON.stringify(template, null, 2) : "";
}

function stringifyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

type PreviewField = {
  label: string;
  value: string;
};

const previewFields: PreviewField[] = [
  { label: "状态", value: previewValue(genericWebhookPayloadExample.alert.status_label) },
  { label: "等级", value: previewValue(genericWebhookPayloadExample.alert.severity_label) },
  { label: "规则", value: previewValue(genericWebhookPayloadExample.rule.name) },
  { label: "信号", value: previewValue(genericWebhookPayloadExample.signal.key) },
  { label: "当前值", value: previewValue(genericWebhookPayloadExample.signal.value) },
  { label: "范围", value: previewValue(genericWebhookPayloadExample.signal.scope_label) }
];

const previewContext = {
  title: previewValue(genericWebhookPayloadExample.alert.title),
  summary: previewValue(genericWebhookPayloadExample.alert.summary),
  occurredAt: previewValue(genericWebhookPayloadExample.alert.occurred_at),
  color: previewValue(genericWebhookPayloadExample.alert.color),
  fields: previewFields
};

function previewValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function previewObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function previewPath(path: string): unknown {
  return path.split(".").reduce<unknown>((current, part) => {
    if (current === null || current === undefined) return undefined;
    if (Array.isArray(current)) {
      const index = Number(part);
      return Number.isInteger(index) ? current[index] : undefined;
    }
    if (typeof current === "object") {
      return (current as Record<string, unknown>)[part];
    }
    return undefined;
  }, genericWebhookPayloadExample);
}

function renderPreviewTemplate(value: unknown): unknown {
  if (typeof value === "string") {
    const fullMatch = value.match(/^\s*\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}\s*$/);
    if (fullMatch) {
      const replacement = previewPath(fullMatch[1]);
      return replacement === undefined ? "" : replacement;
    }
    return value.replace(/\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}/g, (_match, path: string) =>
      previewValue(previewPath(path))
    );
  }
  if (Array.isArray(value)) {
    return value.map((item) => renderPreviewTemplate(item));
  }
  const object = previewObject(value);
  if (object) {
    return Object.fromEntries(
      Object.entries(object).map(([key, entry]) => [key, renderPreviewTemplate(entry)])
    );
  }
  return value;
}

function previewTone(color: string): string {
  if (color === "red" || color === "green" || color === "blue") return color;
  if (color === "orange" || color === "yellow" || color === "gold") return "orange";
  return "blue";
}

function previewMarkdownText(text: string): ReactNode {
  return text.split("\n").map((line, lineIndex) => (
    <span key={`${line}-${lineIndex}`}>
      {line.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part, partIndex) =>
        part.startsWith("**") && part.endsWith("**") ? (
          <strong key={`${part}-${partIndex}`}>{part.slice(2, -2)}</strong>
        ) : (
          <span key={`${part}-${partIndex}`}>{part}</span>
        )
      )}
    </span>
  ));
}

function previewFeishuText(value: unknown): string {
  const object = previewObject(value);
  if (object && "content" in object) return previewValue(object.content);
  return previewValue(value);
}

function renderFeishuElement(element: unknown, index: number): ReactNode {
  const object = previewObject(element);
  if (!object) return null;
  const tag = previewValue(object.tag);
  if (tag === "hr") return <div key={index} className="notif-preview-divider" />;

  const fields = Array.isArray(object.fields) ? object.fields : [];
  if (fields.length > 0) {
    return (
      <div key={index} className="notif-preview-fields">
        {fields.map((field, fieldIndex) => {
          const fieldObject = previewObject(field);
          return (
            <div key={fieldIndex} className="notif-preview-field">
              {previewMarkdownText(previewFeishuText(fieldObject?.text))}
            </div>
          );
        })}
      </div>
    );
  }

  if ("text" in object) {
    return (
      <p key={index} className="notif-preview-md">
        {previewMarkdownText(previewFeishuText(object.text))}
      </p>
    );
  }

  return (
    <pre key={index} className="notif-preview-json-snippet">
      {stringifyJson(object)}
    </pre>
  );
}

function WebhookMessagePreview({ webhook }: { webhook: NotificationWebhook }) {
  if (webhook.provider === "generic") {
    const requestPreview =
      webhook.method === "GET"
        ? {
            method: "GET",
            url: webhook.url || "https://example.com/test",
            queryFields: webhook.payloadFields,
            payload: genericWebhookPayloadExample
          }
        : renderPreviewTemplate(webhook.jsonTemplate ?? genericWebhookPayloadExample);
    return <pre className="notif-payload-preview">{stringifyJson(requestPreview)}</pre>;
  }

  if (webhook.provider === "feishu") {
    const card = previewObject(
      renderPreviewTemplate(webhook.feishuCardTemplate ?? FEISHU_SAMPLE_CARD_TEMPLATE)
    );
    const header = previewObject(card?.header);
    const title = previewFeishuText(previewObject(header?.title)?.content ?? header?.title);
    const tone = previewTone(previewValue(header?.template ?? previewContext.color));
    const elements = Array.isArray(card?.elements) ? card.elements : [];

    return (
      <div className={`notif-message-preview notif-feishu-preview tone-${tone}`}>
        <div className="notif-feishu-header">
          <strong>{title || previewContext.title}</strong>
        </div>
        <div className="notif-feishu-body">
          {elements.length > 0 ? elements.map(renderFeishuElement) : (
            <p className="notif-preview-md">{previewContext.summary}</p>
          )}
        </div>
      </div>
    );
  }

  if (webhook.provider === "slack") {
    return (
      <div className="notif-message-preview notif-slack-preview">
        <div className="notif-slack-header">{previewContext.title}</div>
        <p>{previewContext.summary}</p>
        <div className="notif-preview-fields">
          {previewContext.fields.map((field) => (
            <div key={field.label} className="notif-preview-field">
              <strong>{field.label}</strong>
              <span>{field.value}</span>
            </div>
          ))}
        </div>
        <small>发生时间：{previewContext.occurredAt}</small>
      </div>
    );
  }

  if (webhook.provider === "discord") {
    return (
      <div className="notif-message-preview notif-discord-preview">
        <div>
          <strong>{previewContext.title}</strong>
          <p>{previewContext.summary}</p>
        </div>
        <div className="notif-preview-fields">
          {previewContext.fields.map((field) => (
            <div key={field.label} className="notif-preview-field">
              <strong>{field.label}</strong>
              <span>{field.value}</span>
            </div>
          ))}
        </div>
        <small>{previewContext.occurredAt}</small>
      </div>
    );
  }

  return (
    <div className="notif-message-preview notif-markdown-preview">
      <h4>{previewContext.title}</h4>
      <p>{previewContext.summary}</p>
      {previewContext.fields.map((field) => (
        <p key={field.label}>
          <strong>{field.label}</strong>：{field.value}
        </p>
      ))}
      <small>发生时间：{previewContext.occurredAt}</small>
    </div>
  );
}

async function copyPlaceholder(value: string) {
  await navigator.clipboard?.writeText(value);
}

export function WebhookEditor({
  settings,
  selectedWebhookId,
  onSelect,
  onChange,
  onToggleEnabled,
  onAdd,
  onRemove,
  savingWebhookToggleId,
  renderSaveAction
}: Props) {
  const [jsonTemplateDrafts, setJsonTemplateDrafts] = useState<Record<string, string>>({});
  const [jsonTemplateErrors, setJsonTemplateErrors] = useState<Record<string, string>>({});
  const [feishuTemplateDrafts, setFeishuTemplateDrafts] = useState<Record<string, string>>({});
  const [feishuTemplateErrors, setFeishuTemplateErrors] = useState<Record<string, string>>({});
  const [templateDialogWebhookId, setTemplateDialogWebhookId] = useState("");

  function syncGetUrlTemplate(url: string, previousFields: WebhookPayloadField[], nextFields: WebhookPayloadField[]): string {
    const addedFields = nextFields.filter((field) => !previousFields.includes(field));
    const removedFields = previousFields.filter((field) => !nextFields.includes(field));
    const baseUrl = ensureUrlBase(url);
    if (!baseUrl) return url;
    const parsed = splitUrlTemplate(baseUrl);
    const nextQueryParts = parsed.query ? parsed.query.split("&").filter(Boolean) : [];
    for (const field of removedFields) {
      const template = `${field}=$${field}`;
      const index = nextQueryParts.findIndex((part) => part === template);
      if (index >= 0) nextQueryParts.splice(index, 1);
    }
    for (const field of addedFields) {
      if (!nextQueryParts.some((part) => part.split("=")[0] === field)) {
        nextQueryParts.push(`${field}=$${field}`);
      }
    }
    const query = nextQueryParts.length > 0 ? `?${nextQueryParts.join("&")}` : "";
    return `${parsed.base}${query}${parsed.hash}`;
  }

  function ensureUrlBase(url: string): string {
    const trimmed = url.trim();
    if (trimmed) return trimmed;
    return "https://example.com/test";
  }

  function splitUrlTemplate(url: string): { base: string; query: string; hash: string } {
    const hashIndex = url.indexOf("#");
    const beforeHash = hashIndex >= 0 ? url.slice(0, hashIndex) : url;
    const hash = hashIndex >= 0 ? url.slice(hashIndex) : "";
    const queryIndex = beforeHash.indexOf("?");
    if (queryIndex < 0) {
      return { base: beforeHash, query: "", hash };
    }
    return {
      base: beforeHash.slice(0, queryIndex),
      query: beforeHash.slice(queryIndex + 1),
      hash
    };
  }

  function updateWebhookPayloadFields(webhook: NotificationWebhook, values: WebhookPayloadField[]) {
    const partial: Partial<NotificationWebhook> = { payloadFields: values };
    if (webhook.provider === "generic" && webhook.method === "GET") {
      partial.url = syncGetUrlTemplate(webhook.url, webhook.payloadFields, values);
    }
    onChange(webhook.id, partial);
  }

  function updateWebhookMethod(webhook: NotificationWebhook, method: WebhookMethod) {
    const partial: Partial<NotificationWebhook> = { method };
    if (webhook.provider === "generic" && method === "GET") {
      partial.url = syncGetUrlTemplate(webhook.url, [], webhook.payloadFields);
    }
    onChange(webhook.id, partial);
  }

  function updateJsonTemplate(webhook: NotificationWebhook, value: string) {
    setJsonTemplateDrafts((current) => ({ ...current, [webhook.id]: value }));
    if (!value.trim()) {
      setJsonTemplateErrors((current) => ({ ...current, [webhook.id]: "" }));
      onChange(webhook.id, { jsonTemplate: null });
      return;
    }
    try {
      const parsed = JSON.parse(value) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setJsonTemplateErrors((current) => ({ ...current, [webhook.id]: "JSON 模板必须是对象。" }));
        return;
      }
      setJsonTemplateErrors((current) => ({ ...current, [webhook.id]: "" }));
      onChange(webhook.id, { jsonTemplate: parsed as Record<string, unknown> });
    } catch {
      setJsonTemplateErrors((current) => ({ ...current, [webhook.id]: "JSON 格式不正确，修好后才会保存。" }));
    }
  }

  function applyGenericSampleTemplate(webhook: NotificationWebhook) {
    const text = stringifyTemplate(GENERIC_SAMPLE_JSON_TEMPLATE);
    setJsonTemplateDrafts((current) => ({ ...current, [webhook.id]: text }));
    setJsonTemplateErrors((current) => ({ ...current, [webhook.id]: "" }));
    onChange(webhook.id, { jsonTemplate: GENERIC_SAMPLE_JSON_TEMPLATE });
  }

  function clearJsonTemplate(webhook: NotificationWebhook) {
    setJsonTemplateDrafts((current) => ({ ...current, [webhook.id]: "" }));
    setJsonTemplateErrors((current) => ({ ...current, [webhook.id]: "" }));
    onChange(webhook.id, { jsonTemplate: null });
  }

  function updateFeishuTemplate(webhook: NotificationWebhook, value: string) {
    setFeishuTemplateDrafts((current) => ({ ...current, [webhook.id]: value }));
    if (!value.trim()) {
      setFeishuTemplateErrors((current) => ({ ...current, [webhook.id]: "" }));
      onChange(webhook.id, { feishuCardTemplate: null });
      return;
    }
    try {
      const parsed = JSON.parse(value) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setFeishuTemplateErrors((current) => ({ ...current, [webhook.id]: "飞书卡片必须是 JSON 对象。" }));
        return;
      }
      setFeishuTemplateErrors((current) => ({ ...current, [webhook.id]: "" }));
      onChange(webhook.id, { feishuCardTemplate: parsed as Record<string, unknown> });
    } catch {
      setFeishuTemplateErrors((current) => ({ ...current, [webhook.id]: "JSON 格式不正确，修好后才会保存。" }));
    }
  }

  function applyFeishuSampleTemplate(webhook: NotificationWebhook) {
    const text = stringifyTemplate(FEISHU_SAMPLE_CARD_TEMPLATE);
    setFeishuTemplateDrafts((current) => ({ ...current, [webhook.id]: text }));
    setFeishuTemplateErrors((current) => ({ ...current, [webhook.id]: "" }));
    onChange(webhook.id, { feishuCardTemplate: FEISHU_SAMPLE_CARD_TEMPLATE });
  }

  function clearFeishuTemplate(webhook: NotificationWebhook) {
    setFeishuTemplateDrafts((current) => ({ ...current, [webhook.id]: "" }));
    setFeishuTemplateErrors((current) => ({ ...current, [webhook.id]: "" }));
    onChange(webhook.id, { feishuCardTemplate: null });
  }

  function renderTemplateDialog(webhook: NotificationWebhook) {
    const showPlaceholders =
      webhook.provider === "feishu" || webhook.provider === "generic";

    return (
      <Modal
        open={templateDialogWebhookId === webhook.id}
        title={`${webhook.name || "Webhook"} · 模板与预览`}
        width={980}
        footer={
          <div className="notif-template-modal-footer">
            {renderSaveAction(`webhook:${webhook.id}`)}
          </div>
        }
        className="notif-template-modal"
        onCancel={() => setTemplateDialogWebhookId("")}
      >
        <div className="notif-template-modal-body">
          <section className="notif-template-pane">
            {webhook.provider === "feishu" ? (
              <div className="notif-template-block">
                <div className="notif-template-toolbar">
                  <span>飞书消息卡片</span>
                  <div>
                    <button className="button secondary compact" type="button" onClick={() => applyFeishuSampleTemplate(webhook)}>
                      <FileJson size={16} aria-hidden="true" />
                      示例模版
                    </button>
                    <button className="button tertiary compact" type="button" onClick={() => clearFeishuTemplate(webhook)}>
                      <Eraser size={16} aria-hidden="true" />
                      清空
                    </button>
                  </div>
                </div>
                <textarea
                  className="notif-template-textarea"
                  value={feishuTemplateDrafts[webhook.id] ?? stringifyTemplate(webhook.feishuCardTemplate)}
                  placeholder={stringifyTemplate(FEISHU_SAMPLE_CARD_TEMPLATE)}
                  spellCheck={false}
                  onChange={(event) => updateFeishuTemplate(webhook, event.target.value)}
                />
                <small className={`notif-template-hint ${feishuTemplateErrors[webhook.id] ? "tone-error" : ""}`}>
                  {feishuTemplateErrors[webhook.id] || "留空时使用内置状态卡片；卡片 JSON 中可以使用下方占位符。"}
                </small>
              </div>
            ) : null}

            {webhook.provider === "generic" && webhook.method === "POST" ? (
              <div className="notif-template-block">
                <div className="notif-template-toolbar">
                  <span>通用 JSON 模板</span>
                  <div>
                    <button className="button secondary compact" type="button" onClick={() => applyGenericSampleTemplate(webhook)}>
                      <FileJson size={16} aria-hidden="true" />
                      示例模版
                    </button>
                    <button className="button tertiary compact" type="button" onClick={() => clearJsonTemplate(webhook)}>
                      <Eraser size={16} aria-hidden="true" />
                      清空
                    </button>
                  </div>
                </div>
                <textarea
                  className="notif-template-textarea"
                  value={jsonTemplateDrafts[webhook.id] ?? stringifyTemplate(webhook.jsonTemplate)}
                  placeholder={stringifyTemplate(GENERIC_SAMPLE_JSON_TEMPLATE)}
                  spellCheck={false}
                  onChange={(event) => updateJsonTemplate(webhook, event.target.value)}
                />
                <small className={`notif-template-hint ${jsonTemplateErrors[webhook.id] ? "tone-error" : ""}`}>
                  {jsonTemplateErrors[webhook.id] || "留空时发送默认告警 JSON；模板 JSON 中可以使用下方占位符。"}
                </small>
              </div>
            ) : null}

            {webhook.provider === "generic" && webhook.method === "GET" ? (
              <small className="notif-template-hint">
                URL 可以使用 <code>{"${alert.status}"}</code>、<code>{"${rule.name}"}</code>、<code>{"${signal.value}"}</code> 这类占位符；没有模板时会把 Query 字段追加到 URL。
              </small>
            ) : null}

            {showPlaceholders ? (
              <div className="notif-placeholder-panel">
                <div>
                  <strong>可用占位符</strong>
                  <small>点击占位符复制。</small>
                </div>
                <div className="notif-placeholder-list">
                  {notificationPlaceholders.map((item) => (
                    <button
                      key={item.value}
                      className="notif-placeholder-chip"
                      type="button"
                      title={item.description}
                      onClick={() => void copyPlaceholder(item.value)}
                    >
                      <Clipboard size={13} aria-hidden="true" />
                      <span>{item.value}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </section>

          <section className="notif-preview-pane">
            <div className="notif-template-toolbar">
              <span>实时预览</span>
              <div>
                <Tag color="blue">示例数据</Tag>
              </div>
            </div>
            <WebhookMessagePreview webhook={webhook} />
          </section>
        </div>
      </Modal>
    );
  }

  return (
    <section className="panel notif-island webhook-island">
      <header className="webhook-island-head">
        <div>
          <h3>Webhook 接收器</h3>
        </div>
        <button className="button secondary compact" type="button" onClick={onAdd}>
          <Plus size={16} aria-hidden="true" />
          新增 Webhook
        </button>
      </header>

      <div className="webhook-list">
        {settings.webhooks.map((webhook) => {
          const ruleCount = settings.rules.filter((rule) =>
            rule.targetWebhookIds.includes(webhook.id)
          ).length;
          const expanded = webhook.id === selectedWebhookId;
          return (
            <article
              key={webhook.id}
              className={`webhook-card ${expanded ? "expanded" : ""}`}
            >
              <button
                className="webhook-card-trigger"
                type="button"
                aria-expanded={expanded}
                onClick={() => onSelect(expanded ? "" : webhook.id)}
              >
                <span className="webhook-card-text">
                  <strong>{webhook.name || "未命名 Webhook"}</strong>
                  <small>{webhook.url || "未配置 URL"}</small>
                </span>
                <span className="webhook-card-meta">
                  <label
                    className="webhook-switch"
                    aria-label={`${webhook.enabled ? "停用" : "启用"} ${webhook.name || "Webhook"}`}
                    onClick={(event) => event.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={webhook.enabled}
                      disabled={savingWebhookToggleId === webhook.id}
                      onChange={(event) => onToggleEnabled(webhook.id, event.target.checked)}
                    />
                    <span aria-hidden="true" />
                  </label>
                  <Tag color={webhook.enabled ? "green" : "default"}>{ruleCount} 条规则</Tag>
                  <ChevronDown className="webhook-card-chevron" size={16} aria-hidden="true" />
                </span>
              </button>

              {expanded ? (
                <div className="webhook-card-body">
                  <div className="notif-grid-2">
                    <label className="notif-field">
                      <span>名称</span>
                      <input
                        value={webhook.name}
                        placeholder="Ops / Finance"
                        onChange={(event) => onChange(webhook.id, { name: event.target.value })}
                      />
                    </label>
                    <label className="notif-field">
                      <span>接收平台</span>
                      <select
                        value={webhook.provider}
                        onChange={(event) =>
                          onChange(webhook.id, {
                            provider: event.target.value as WebhookProvider,
                            method: event.target.value === "generic" ? webhook.method : "POST"
                          })
                        }
                      >
                        {webhookProviderOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  {webhook.provider === "generic" ? (
                    <div className="notif-grid-2">
                      <label className="notif-field">
                        <span>请求方式</span>
                        <select
                          value={webhook.method}
                          onChange={(event) =>
                            updateWebhookMethod(webhook, event.target.value as WebhookMethod)
                          }
                        >
                          {webhookMethodOptions.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      {webhook.method === "GET" ? (
                        <label className="notif-field">
                          <span>Query 字段</span>
                          <Select
                            mode="multiple"
                            className="notif-webhook-select"
                            value={webhook.payloadFields}
                            optionFilterProp="label"
                            maxTagCount="responsive"
                            onChange={(values) =>
                              updateWebhookPayloadFields(webhook, values as WebhookPayloadField[])
                            }
                            options={webhookPayloadFieldOptions}
                          />
                        </label>
                      ) : null}
                    </div>
                  ) : null}

                  <label className="notif-field">
                    <span>Webhook URL</span>
                    <input
                      type="url"
                      value={webhook.url}
                      placeholder={webhook.method === "GET" ? "https://example.com/test?severity=$severity" : "https://example.com/webhook"}
                      onChange={(event) => onChange(webhook.id, { url: event.target.value })}
                    />
                  </label>

                  <label className="notif-field">
                    <span>Secret / 加签密钥</span>
                    <input
                      value={webhook.secret}
                      placeholder={webhookSecretHints[webhook.provider]}
                      onChange={(event) => onChange(webhook.id, { secret: event.target.value })}
                    />
                  </label>

                  <div className="notif-template-entry">
                    <div>
                      <strong>消息模板与预览</strong>
                      <small>示例模板、占位符和渲染效果放在弹窗里维护。</small>
                    </div>
                    <button className="button secondary compact" type="button" onClick={() => setTemplateDialogWebhookId(webhook.id)}>
                      <Eye size={16} aria-hidden="true" />
                      打开
                    </button>
                  </div>

                  <div className="notif-actions notif-webhook-actions">
                    <span className="notif-action-note">
                      {ruleCount > 0
                        ? `删除后会从 ${ruleCount} 条规则中移除。`
                        : "删除后不会影响现有规则。"}
                    </span>
                    <div className="notif-item-actions">
                      {renderSaveAction(`webhook:${webhook.id}`)}
                      <Popconfirm
                        title="删除 Webhook？"
                        description={ruleCount > 0 ? `会从 ${ruleCount} 条规则中移除。` : "删除后保存才会写入数据库。"}
                        okText="确认删除"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                        onConfirm={() => onRemove(webhook.id)}
                      >
                        <button className="button danger compact" type="button">
                          <Trash2 size={16} aria-hidden="true" />
                          删除 Webhook
                        </button>
                      </Popconfirm>
                    </div>
                  </div>
                </div>
              ) : null}
              {renderTemplateDialog(webhook)}
            </article>
          );
        })}
      </div>
    </section>
  );
}
