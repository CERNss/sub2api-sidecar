import { Tag } from "antd";
import { Plus } from "lucide-react";
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
};

export function WebhookEditor({ settings, selectedWebhookId, onSelect, onChange, onAdd }: Props) {
  const selected =
    settings.webhooks.find((webhook) => webhook.id === selectedWebhookId) ?? settings.webhooks[0];

  return (
    <section className="notif-section">
      <header className="notif-section-head">
        <div>
          <h3>Webhook 接收器</h3>
          <p>接收器只负责消息送到哪里。是否发送、阈值与频率由规则控制。</p>
        </div>
        <button className="button secondary compact" type="button" onClick={onAdd}>
          <Plus size={16} aria-hidden="true" />
          新增 Webhook
        </button>
      </header>

      <div className="notif-list">
        {settings.webhooks.map((webhook) => {
          const ruleCount = settings.rules.filter((rule) =>
            rule.targetWebhookIds.includes(webhook.id)
          ).length;
          return (
            <button
              key={webhook.id}
              className={`notif-list-row ${webhook.id === selected?.id ? "active" : ""}`}
              type="button"
              onClick={() => onSelect(webhook.id)}
            >
              <span className="notif-list-text">
                <strong>{webhook.name || "未命名 Webhook"}</strong>
                <small>{webhook.url || "未配置 URL"}</small>
              </span>
              <Tag color={webhook.enabled ? "green" : "default"}>{ruleCount} 条规则</Tag>
            </button>
          );
        })}
      </div>

      {selected ? (
        <div className="notif-form">
          <label className="notif-toggle">
            <input
              type="checkbox"
              checked={selected.enabled}
              onChange={(event) => onChange(selected.id, { enabled: event.target.checked })}
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
                value={selected.name}
                placeholder="Ops / Finance"
                onChange={(event) => onChange(selected.id, { name: event.target.value })}
              />
            </label>
            <label className="notif-field">
              <span>接收平台</span>
              <select
                value={selected.provider}
                onChange={(event) =>
                  onChange(selected.id, { provider: event.target.value as WebhookProvider })
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
              value={selected.url}
              placeholder="https://example.com/webhook"
              onChange={(event) => onChange(selected.id, { url: event.target.value })}
            />
          </label>

          <label className="notif-field">
            <span>Secret / 加签密钥</span>
            <input
              value={selected.secret}
              placeholder={webhookSecretHints[selected.provider]}
              onChange={(event) => onChange(selected.id, { secret: event.target.value })}
            />
          </label>
        </div>
      ) : null}
    </section>
  );
}
