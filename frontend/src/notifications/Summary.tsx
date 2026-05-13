import { Popover, Tag } from "antd";
import type { HTMLAttributes } from "react";
import {
  NotificationDeliveryHistory,
  NotificationDeliveryRecord,
  NotificationSettings,
  severityColor,
  severityLabel
} from "./types";

type Props = {
  settings: NotificationSettings;
  deliveryHistory: NotificationDeliveryHistory;
  isLoadingHistory: boolean;
};

export function Summary({ settings, deliveryHistory, isLoadingHistory }: Props) {
  const enabledWebhookCount = settings.webhooks.filter((webhook) => webhook.enabled).length;
  const enabledRules = settings.rules.filter((rule) => rule.enabled);
  const unroutedRules = settings.rules.filter((rule) => rule.targetWebhookIds.length === 0);
  const historyCount = isLoadingHistory ? "..." : `${deliveryHistory.total}`;

  return (
    <section className="panel result-panel notif-summary">
      <div className="panel-title-row">
        <div>
          <p className="eyebrow">Status</p>
          <h2>当前告警</h2>
        </div>
      </div>

      <div className="notif-summary-counts">
        <SummaryStat label="Webhook" value={`${enabledWebhookCount}/${settings.webhooks.length}`} hint="已启用 / 总数" />
        <SummaryStat label="启用规则" value={`${enabledRules.length}`} hint={`共 ${settings.rules.length} 条`} />
        <SummaryStat label="未路由" value={`${unroutedRules.length}`} hint="没有选 Webhook 的规则" tone={unroutedRules.length > 0 ? "warn" : "ok"} />
        <Popover
          trigger="click"
          placement="bottomRight"
          content={<HistoryPopup settings={settings} history={deliveryHistory} />}
        >
          <SummaryStat
            label="告警历史"
            value={historyCount}
            tone={deliveryHistory.items.some((item) => item.status === "failed") ? "warn" : "ok"}
            interactive
          />
        </Popover>
      </div>

    </section>
  );
}

function SummaryStat({
  label,
  value,
  hint,
  tone = "ok",
  interactive = false,
  ...interactiveProps
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "ok" | "warn";
  interactive?: boolean;
} & HTMLAttributes<HTMLElement>) {
  const content = (
    <>
      <span className="notif-stat-label">{label}</span>
      <strong className="notif-stat-value">{value}</strong>
      {hint ? <small className="notif-stat-hint">{hint}</small> : null}
    </>
  );
  if (interactive) {
    return (
      <button
        {...interactiveProps}
        className={`notif-stat notif-stat-button tone-${tone}`}
        type="button"
      >
        {content}
      </button>
    );
  }
  return (
    <div className={`notif-stat tone-${tone}`}>
      {content}
    </div>
  );
}

function HistoryPopup({
  settings,
  history
}: {
  settings: NotificationSettings;
  history: NotificationDeliveryHistory;
}) {
  const ruleNameById = new Map(settings.rules.map((rule) => [rule.id, rule.name || rule.signalKey]));
  const webhookNameById = new Map(settings.webhooks.map((webhook) => [webhook.id, webhook.name || webhook.id]));

  return (
    <div className="notif-history-popup">
      <div className="notif-history-popup-head">
        <strong>告警历史</strong>
        <span>{history.items.length}</span>
      </div>
      <div className="notif-history-list">
        {history.items.length === 0 ? (
          <div className="notif-history-empty">暂无历史告警</div>
        ) : (
          history.items.map((item) => (
            <HistoryRow
              key={item.deliveryId}
              item={item}
              ruleName={ruleNameById.get(item.ruleId) ?? item.ruleId}
              receiverName={webhookNameById.get(item.receiverId) ?? item.receiverId}
            />
          ))
        )}
      </div>
    </div>
  );
}

function HistoryRow({
  item,
  ruleName,
  receiverName
}: {
  item: NotificationDeliveryRecord;
  ruleName: string;
  receiverName: string;
}) {
  return (
    <article className="notif-history-row">
      <div className="notif-history-row-main">
        <strong>{ruleName}</strong>
        <span>{receiverName}</span>
      </div>
      <div className="notif-history-tags">
        <Tag color={severityColor(item.severity)}>{severityLabel(item.severity)}</Tag>
        <Tag color={statusColor(item.status)}>{statusLabel(item.status)}</Tag>
        <Tag>{triggerLabel(item.trigger)}</Tag>
      </div>
      <div className="notif-history-meta">
        <span>{formatHistoryTime(item.createdAt)}</span>
        {item.responseStatus !== null ? <span>HTTP {item.responseStatus}</span> : null}
        {item.errorMessage ? <span className="notif-history-error">{item.errorMessage}</span> : null}
      </div>
    </article>
  );
}

function statusColor(status: NotificationDeliveryRecord["status"]): string {
  if (status === "succeeded") return "green";
  if (status === "skipped") return "default";
  return "red";
}

function statusLabel(status: NotificationDeliveryRecord["status"]): string {
  if (status === "succeeded") return "成功";
  if (status === "skipped") return "跳过";
  return "失败";
}

function triggerLabel(trigger: NotificationDeliveryRecord["trigger"]): string {
  if (trigger === "test") return "测试";
  if (trigger === "recovery") return "恢复";
  return "规则";
}

function formatHistoryTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(date);
}
