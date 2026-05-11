import { Tag } from "antd";
import {
  NotificationSettings,
  notificationSignalByKey,
  severityColor,
  severityLabel
} from "./types";

type Props = {
  settings: NotificationSettings;
};

export function Summary({ settings }: Props) {
  const enabledWebhookCount = settings.webhooks.filter((webhook) => webhook.enabled).length;
  const enabledRules = settings.rules.filter((rule) => rule.enabled);
  const unroutedRules = settings.rules.filter((rule) => rule.targetWebhookIds.length === 0);

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
      </div>

      <div className="notif-summary-list">
        {enabledRules.length === 0 ? (
          <div className="notif-summary-placeholder">
            暂无启用的规则。添加一条规则后这里会显示运行时状态。
          </div>
        ) : (
          enabledRules.map((rule) => {
            const signal = notificationSignalByKey.get(rule.signalKey);
            return (
              <div className="notif-summary-row" key={rule.id}>
                <div>
                  <strong>{rule.name || signal?.label || rule.signalKey}</strong>
                  <small>
                    {signal?.source ?? "—"} · 每 {rule.readIntervalMinutes} 分钟
                  </small>
                </div>
                <Tag color={severityColor(rule.severity)}>{severityLabel(rule.severity)}</Tag>
              </div>
            );
          })
        )}
      </div>

      <p className="notif-summary-foot">
        运行时投递记录（最近一次触发、失败原因）将在后端 deliveries API 接入 UI 后显示在此处。
      </p>
    </section>
  );
}

function SummaryStat({
  label,
  value,
  hint,
  tone = "ok"
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "ok" | "warn";
}) {
  return (
    <div className={`notif-stat tone-${tone}`}>
      <span className="notif-stat-label">{label}</span>
      <strong className="notif-stat-value">{value}</strong>
      {hint ? <small className="notif-stat-hint">{hint}</small> : null}
    </div>
  );
}
