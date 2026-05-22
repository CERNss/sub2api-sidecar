import { Popconfirm, Select, Tag } from "antd";
import { ChevronDown, Eraser, FileJson, Plus, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import {
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
    template: "red",
    title: {
      tag: "plain_text",
      content: "🚨 云账号余额告警"
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
            content: "**账号名称**\n${snapshot.data.low_users.0.name}"
          }
        },
        {
          is_short: true,
          text: {
            tag: "lark_md",
            content: "**云厂商**\n${snapshot.data.low_users.0.provider}"
          }
        },
        {
          is_short: true,
          text: {
            tag: "lark_md",
            content: "**当前余额**\n${snapshot.data.min_balance}"
          }
        },
        {
          is_short: true,
          text: {
            tag: "lark_md",
            content: "**本月剩余额预估**\n${snapshot.data.month_remaining_estimate}"
          }
        },
        {
          is_short: false,
          text: {
            tag: "lark_md",
            content: "**资金缺口**\n${snapshot.data.funding_gap}"
          }
        }
      ]
    },
    { tag: "hr" },
    {
      tag: "div",
      text: {
        tag: "lark_md",
        content: "共 ${snapshot.data.low_user_count} 个账号余额不足以覆盖本月剩余消耗（参考上月同期），请及时充值"
      }
    }
  ]
};

function stringifyTemplate(template: Record<string, unknown> | null): string {
  return template ? JSON.stringify(template, null, 2) : "";
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
  const [feishuTemplateDrafts, setFeishuTemplateDrafts] = useState<Record<string, string>>({});
  const [feishuTemplateErrors, setFeishuTemplateErrors] = useState<Record<string, string>>({});

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

                  <div className="notif-grid-2">
                    <label className="notif-field">
                      <span>请求方式</span>
                      <select
                        value={webhook.method}
                        disabled={webhook.provider !== "generic"}
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
                    <label className="notif-field">
                      <span>{webhook.method === "GET" ? "Query 字段" : "JSON 字段"}</span>
                      <Select
                        mode="multiple"
                        className="notif-webhook-select"
                        value={webhook.payloadFields}
                        disabled={webhook.provider !== "generic"}
                        optionFilterProp="label"
                        maxTagCount="responsive"
                        onChange={(values) =>
                          updateWebhookPayloadFields(webhook, values as WebhookPayloadField[])
                        }
                        options={webhookPayloadFieldOptions}
                      />
                    </label>
                  </div>

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
                        {feishuTemplateErrors[webhook.id] || "留空时发送普通文本；可用 $rule_name、${snapshot.value}、${snapshot.data.low_users.0.name} 等占位符。"}
                      </small>
                    </div>
                  ) : null}

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
            </article>
          );
        })}
      </div>
    </section>
  );
}
