import { Tag } from "antd";
import { ChevronDown, Plus, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import {
  NotificationSettings,
  NotificationWebhook,
  WebhookMethod,
  WebhookProvider,
  webhookMethodOptions,
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
                          onChange(webhook.id, { method: event.target.value as WebhookMethod })
                        }
                      >
                        {webhookMethodOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <label className="notif-field">
                    <span>Webhook URL</span>
                    <input
                      type="url"
                      value={webhook.url}
                      placeholder="https://example.com/webhook"
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

                  <div className="notif-actions notif-webhook-actions">
                    <span className="notif-action-note">
                      {ruleCount > 0
                        ? `删除后会从 ${ruleCount} 条规则中移除。`
                        : "删除后不会影响现有规则。"}
                    </span>
                    <div className="notif-item-actions">
                      {renderSaveAction(`webhook:${webhook.id}`)}
                      <button className="button danger compact" type="button" onClick={() => onRemove(webhook.id)}>
                        <Trash2 size={16} aria-hidden="true" />
                        删除 Webhook
                      </button>
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
