import { NotificationSettings } from "./types";

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
