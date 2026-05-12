import { Tag } from "antd";
import { ChevronDown, Plus, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import {
  NotificationSettings,
  NotificationWebhook,
  WebhookProvider,
  webhookProviderOptions,
  webhookSecretHints
} from "./types";

type Props = {
  settings: NotificationSettings;
  selectedWebhookId: string;
  onSelect: (id: string) => void;
  onChange: (id: string, partial: Partial<NotificationWebhook>) => void;
  onAdd: () => void;
  onRemove: (id: string) => void;
  renderSaveAction: (scope: string) => ReactNode;
};

export function WebhookEditor({
  settings,
  selectedWebhookId,
  onSelect,
  onChange,
  onAdd,
  onRemove,
  renderSaveAction
}: Props) {
  return (
    <section className="panel notif-section notif-island">
      <header className="notif-section-head">
        <div>
          <h3>Webhook 接收器</h3>
        </div>
        <button className="button secondary compact" type="button" onClick={onAdd}>
          <Plus size={16} aria-hidden="true" />
          新增 Webhook
        </button>
      </header>

      <div className="notif-webhook-cards">
        {settings.webhooks.map((webhook) => {
          const ruleCount = settings.rules.filter((rule) =>
            rule.targetWebhookIds.includes(webhook.id)
          ).length;
          const expanded = webhook.id === selectedWebhookId;
          return (
            <article
              key={webhook.id}
              className={`notif-webhook-card ${expanded ? "expanded" : ""}`}
            >
              <button
                className="notif-webhook-card-trigger"
                type="button"
                aria-expanded={expanded}
                onClick={() => onSelect(expanded ? "" : webhook.id)}
              >
                <span className="notif-list-text">
                  <strong>{webhook.name || "未命名 Webhook"}</strong>
                  <small>{webhook.url || "未配置 URL"}</small>
                </span>
                <span className="notif-webhook-card-meta">
                  <Tag color={webhook.enabled ? "green" : "default"}>{ruleCount} 条规则</Tag>
                  <ChevronDown className="notif-card-chevron" size={16} aria-hidden="true" />
                </span>
              </button>

              {expanded ? (
                <div className="notif-form notif-webhook-card-body">
                  <label className="notif-toggle">
                    <input
                      type="checkbox"
                      checked={webhook.enabled}
                      onChange={(event) => onChange(webhook.id, { enabled: event.target.checked })}
                    />
                    <span>
                      <strong>启用</strong>
                      <small>启用后才会真正发送消息。</small>
                    </span>
                  </label>

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
                          onChange(webhook.id, { provider: event.target.value as WebhookProvider })
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
