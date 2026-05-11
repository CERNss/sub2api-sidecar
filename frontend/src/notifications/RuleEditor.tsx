import { Tag } from "antd";
import { Plus, Send } from "lucide-react";
import {
  NotificationRule,
  NotificationRuleOperator,
  NotificationSettings,
  NotificationSeverity,
  notificationSignalByKey,
  notificationSignalGroups,
  severityColor,
  severityLabel
} from "./types";

type Props = {
  settings: NotificationSettings;
  selectedRuleId: string;
  onSelectRule: (id: string) => void;
  onChangeRule: (id: string, partial: Partial<NotificationRule>) => void;
  onAddRule: () => void;
  onRemoveRule: () => void;
  onTest: () => void;
};

const OPERATORS: { value: NotificationRuleOperator; label: string }[] = [
  { value: "gte", label: "≥ 大于等于" },
  { value: "gt", label: "> 大于" },
  { value: "lte", label: "≤ 小于等于" },
  { value: "lt", label: "< 小于" },
  { value: "eq", label: "= 等于" },
  { value: "neq", label: "≠ 不等于" }
];

const SEVERITIES: NotificationSeverity[] = ["info", "warning", "critical"];

export function RuleEditor({
  settings,
  selectedRuleId,
  onSelectRule,
  onChangeRule,
  onAddRule,
  onRemoveRule,
  onTest
}: Props) {
  const selected = settings.rules.find((rule) => rule.id === selectedRuleId) ?? settings.rules[0];
  const selectedSignal = selected ? notificationSignalByKey.get(selected.signalKey) : null;

  if (settings.rules.length === 0) {
    return (
      <section className="notif-section">
        <header className="notif-section-head">
          <div>
            <h3>告警规则</h3>
            <p>选择你关心的运营信号，绑定到一个 Webhook 接收器。</p>
          </div>
        </header>
        <div className="notif-empty">
          <h4>还没有告警规则</h4>
          <p>添加一条规则来订阅你关心的运营信号。</p>
          <button className="button primary" type="button" onClick={onAddRule}>
            <Plus size={16} aria-hidden="true" />
            添加你关心的第一条告警
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="notif-section">
      <header className="notif-section-head">
        <div>
          <h3>告警规则</h3>
          <p>每条规则把一个信号绑定到一个或多个 Webhook 接收器。</p>
        </div>
        <button className="button secondary compact" type="button" onClick={onAddRule}>
          <Plus size={16} aria-hidden="true" />
          新增规则
        </button>
      </header>

      <div className="notif-rule-layout">
        <div className="notif-list">
          {settings.rules.map((rule) => {
            const signal = notificationSignalByKey.get(rule.signalKey);
            return (
              <button
                key={rule.id}
                className={`notif-list-row ${rule.id === selected?.id ? "active" : ""}`}
                type="button"
                onClick={() => onSelectRule(rule.id)}
              >
                <span className="notif-list-text">
                  <strong>{rule.name || signal?.label || "未命名规则"}</strong>
                  <small>
                    {signal?.label ?? rule.signalKey} · 每 {rule.readIntervalMinutes} 分钟 · {rule.targetWebhookIds.length} 个 Webhook
                  </small>
                </span>
                <Tag color={rule.enabled ? severityColor(rule.severity) : "default"}>
                  {rule.enabled ? severityLabel(rule.severity) : "停用"}
                </Tag>
              </button>
            );
          })}
        </div>

        {selected ? (
          <div className="notif-form notif-rule-form">
            <header className="notif-rule-head">
              <div>
                <h4>{selected.name || selectedSignal?.label || "告警规则"}</h4>
                <p>{selectedSignal?.description ?? "选择信号类型后配置阈值。"}</p>
              </div>
              <label className="notif-mini-toggle">
                <input
                  type="checkbox"
                  checked={selected.enabled}
                  onChange={(event) => onChangeRule(selected.id, { enabled: event.target.checked })}
                />
                启用
              </label>
            </header>

            <div className="notif-grid-2">
              <label className="notif-field">
                <span>规则名称</span>
                <input
                  value={selected.name}
                  onChange={(event) => onChangeRule(selected.id, { name: event.target.value })}
                />
              </label>
              <label className="notif-field">
                <span>信号类型</span>
                <select
                  value={selected.signalKey}
                  onChange={(event) => {
                    const signal = notificationSignalByKey.get(event.target.value);
                    onChangeRule(selected.id, {
                      signalKey: event.target.value,
                      name: signal?.label ?? selected.name,
                      threshold: signal?.defaultThreshold ?? selected.threshold,
                      thresholdUnit: signal?.unit ?? "",
                      operator: signal?.defaultOperator ?? selected.operator,
                      severity: signal?.defaultSeverity ?? selected.severity,
                      readIntervalMinutes:
                        signal?.defaultReadIntervalMinutes ?? selected.readIntervalMinutes
                    });
                  }}
                >
                  {notificationSignalGroups.map((group) => (
                    <optgroup label={group.title} key={group.title}>
                      {group.signals.map((signal) => (
                        <option value={signal.key} key={signal.key}>
                          {signal.label}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>
            </div>

            <div className="notif-grid-3">
              <label className="notif-field">
                <span>条件</span>
                <select
                  value={selected.operator}
                  onChange={(event) =>
                    onChangeRule(selected.id, {
                      operator: event.target.value as NotificationRuleOperator
                    })
                  }
                >
                  {OPERATORS.map((op) => (
                    <option key={op.value} value={op.value}>
                      {op.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="notif-field">
                <span>阈值</span>
                <input
                  value={selected.threshold}
                  onChange={(event) => onChangeRule(selected.id, { threshold: event.target.value })}
                />
                <small>{selected.thresholdUnit || "按信号类型定义"}</small>
              </label>
              <label className="notif-field">
                <span>严重等级</span>
                <select
                  value={selected.severity}
                  onChange={(event) =>
                    onChangeRule(selected.id, { severity: event.target.value as NotificationSeverity })
                  }
                >
                  {SEVERITIES.map((sev) => (
                    <option key={sev} value={sev}>
                      {severityLabel(sev)}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="notif-grid-2">
              <label className="notif-field">
                <span>检查频率</span>
                <input
                  type="number"
                  min={1}
                  value={selected.readIntervalMinutes}
                  onChange={(event) =>
                    onChangeRule(selected.id, {
                      readIntervalMinutes: Math.max(1, Number(event.target.value) || 1)
                    })
                  }
                />
                <small>每 N 分钟读取一次</small>
              </label>
              <label className="notif-field">
                <span>冷却时间</span>
                <input
                  type="number"
                  min={0}
                  value={selected.cooldownMinutes}
                  onChange={(event) =>
                    onChangeRule(selected.id, {
                      cooldownMinutes: Math.max(0, Number(event.target.value) || 0)
                    })
                  }
                />
                <small>触发后静默 N 分钟</small>
              </label>
            </div>

            <div className="notif-field">
              <span>目标 Webhook</span>
              <div className="notif-webhook-checks">
                {settings.webhooks.map((webhook) => {
                  const checked = selected.targetWebhookIds.includes(webhook.id);
                  return (
                    <label key={webhook.id} className="notif-webhook-check">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => {
                          const next = event.target.checked
                            ? Array.from(new Set([...selected.targetWebhookIds, webhook.id]))
                            : selected.targetWebhookIds.filter((id) => id !== webhook.id);
                          onChangeRule(selected.id, { targetWebhookIds: next });
                        }}
                      />
                      <span>
                        <strong>{webhook.name || "未命名 Webhook"}</strong>
                        <small>
                          {webhook.enabled ? "已启用" : "未启用"} · {webhook.url || "未配置 URL"}
                        </small>
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>

            <div className="notif-grid-2">
              <label className="notif-toggle">
                <input
                  type="checkbox"
                  checked={selected.includeResolved}
                  onChange={(event) =>
                    onChangeRule(selected.id, { includeResolved: event.target.checked })
                  }
                />
                <span>
                  <strong>发送恢复通知</strong>
                  <small>异常恢复时再发一次。</small>
                </span>
              </label>
              <label className="notif-toggle">
                <input
                  type="checkbox"
                  checked={selected.includeSnapshot}
                  onChange={(event) =>
                    onChangeRule(selected.id, { includeSnapshot: event.target.checked })
                  }
                />
                <span>
                  <strong>附带数据快照</strong>
                  <small>消息中包含当前值与来源。</small>
                </span>
              </label>
            </div>

            <div className="notif-actions">
              <button className="button secondary" type="button" onClick={onTest}>
                <Send size={16} aria-hidden="true" />
                发送测试
              </button>
              <button className="button tertiary" type="button" onClick={onRemoveRule}>
                删除规则
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
