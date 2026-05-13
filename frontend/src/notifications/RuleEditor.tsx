import { Select, Tag } from "antd";
import { Copy, Download, Plus, Send } from "lucide-react";
import type { ReactNode } from "react";
import { downloadJson, makeRuleExport, makeRuleTemplate, makeRuleTemplateJson, toJsonFileSlug } from "./ruleExport";
import {
  NotificationRule,
  NotificationRuleOperator,
  NotificationSettings,
  NotificationSeverity,
  notificationSignalByKey,
  notificationSignalGroups,
  operatorLabel,
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
  onStatus: (message: string, tone: "info" | "success" | "error") => void;
  renderSaveAction: (scope: string) => ReactNode;
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
  onTest,
  onStatus,
  renderSaveAction
}: Props) {
  const selected = settings.rules.find((rule) => rule.id === selectedRuleId) ?? settings.rules[0] ?? null;
  const selectedSignal = selected ? notificationSignalByKey.get(selected.signalKey) : null;
  const selectedTargets = selected
    ? settings.webhooks.filter((webhook) => selected.targetWebhookIds.includes(webhook.id))
    : [];

  function updateTargetWebhooks(webhookIds: string[]) {
    if (!selected) return;
    onChangeRule(selected.id, { targetWebhookIds: Array.from(new Set(webhookIds)) });
  }

  async function copySelectedRuleTemplate() {
    if (!selected) return;
    const json = makeRuleTemplateJson(selected);
    try {
      await window.navigator.clipboard.writeText(json);
      onStatus("规则 JSON 模板已复制。", "success");
    } catch {
      onStatus("复制失败，可以直接选中下方 JSON 手动复制。", "error");
    }
  }

  function exportSelectedRuleTemplate() {
    if (!selected) return;
    downloadJson(`notification-rule-${toJsonFileSlug(selected.name || selected.signalKey)}.json`, {
      schemaVersion: 1,
      kind: "sub2api.notification.rule-template",
      exportedAt: new Date().toISOString(),
      template: makeRuleTemplate(selected)
    });
    onStatus("当前规则模板已导出。", "success");
  }

  function exportAllRules() {
    downloadJson("sub2api-notification-rules.json", makeRuleExport(settings.rules, settings.webhooks));
    onStatus("全部规则 JSON 已导出。", "success");
  }

  if (settings.rules.length === 0) {
    return (
      <section className="panel notif-section notif-island notif-rule-island">
        <header className="notif-section-head">
          <div>
            <h3>告警规则</h3>
          </div>
        </header>
        <div className="notif-empty">
          <h4>还没有告警规则</h4>
          <button className="button primary" type="button" onClick={onAddRule}>
            <Plus size={16} aria-hidden="true" />
            添加你关心的第一条告警
          </button>
        </div>
      </section>
    );
  }

  if (!selected) {
    return null;
  }

  return (
    <section className="panel notif-section notif-island notif-rule-island">
      <header className="notif-section-head">
        <div>
          <h3>告警规则</h3>
        </div>
        <div className="notif-section-actions">
          <button className="button secondary compact" type="button" onClick={onAddRule}>
            <Plus size={16} aria-hidden="true" />
            新增规则
          </button>
          <button className="button tertiary compact" type="button" onClick={exportAllRules}>
            <Download size={16} aria-hidden="true" />
            导出规则 JSON
          </button>
        </div>
      </header>

      <div className="notif-rule-layout">
        <aside className="notif-rule-list-pane" aria-label="告警规则列表">
          <div className="notif-rule-list-head">
            <span>规则列表</span>
            <strong>{settings.rules.length}</strong>
          </div>
          <div className="notif-rule-cards">
            {settings.rules.map((rule) => {
              const signal = notificationSignalByKey.get(rule.signalKey);
              const targetCount = settings.webhooks.filter((webhook) =>
                rule.targetWebhookIds.includes(webhook.id)
              ).length;
              const active = rule.id === selected.id;
              return (
                <article
                  key={rule.id}
                  className={`notif-rule-card ${active ? "expanded" : ""}`}
                >
                  <button
                    className="notif-rule-card-trigger"
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => onSelectRule(rule.id)}
                  >
                    <span className="notif-list-text">
                      <strong>{rule.name || signal?.label || "未命名规则"}</strong>
                      <small>
                        {operatorLabel(rule.operator)} {rule.threshold}
                        {rule.thresholdUnit ? ` ${rule.thresholdUnit}` : ""} · 每 {rule.readIntervalMinutes} 分钟 · {targetCount} 个接收器
                      </small>
                    </span>
                    <span className="notif-rule-card-meta">
                      <Tag color={rule.enabled ? severityColor(rule.severity) : "default"}>
                        {rule.enabled ? severityLabel(rule.severity) : "停用"}
                      </Tag>
                    </span>
                  </button>
                </article>
              );
            })}
          </div>
        </aside>

        <div className="notif-rule-editor-pane">
          <div className="notif-form notif-rule-form">
            <header className="notif-rule-head">
              <div>
                <h4>{selected.name || selectedSignal?.label || "告警规则"}</h4>
                <p>{selectedSignal?.description ?? "选择信号类型后配置阈值。"}</p>
                <div className="notif-rule-meta">
                  <Tag color={severityColor(selected.severity)}>{severityLabel(selected.severity)}</Tag>
                  <span>{selectedTargets.length} 个接收器</span>
                  <span>每 {selected.readIntervalMinutes} 分钟检查</span>
                </div>
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

            <div className="notif-rule-section">
              <div className="notif-rule-section-title">
                <span>1</span>
                <div>
                  <h5>规则来源</h5>
                  <p>选择要监听的运营信号。</p>
                </div>
              </div>
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
            </div>

            <div className="notif-rule-section">
              <div className="notif-rule-section-title">
                <span>2</span>
                <div>
                  <h5>触发条件</h5>
                  <p>达到阈值后按冷却时间抑制重复消息。</p>
                </div>
              </div>
              <div className="notif-rule-trigger-grid">
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
            </div>

            <div className="notif-rule-section">
              <div className="notif-rule-section-title">
                <span>3</span>
                <div>
                  <h5>发送目标</h5>
                  <p>{selectedTargets.length > 0 ? `已选择 ${selectedTargets.length} 个接收器。` : "当前规则还没有发送目标。"}</p>
                </div>
              </div>
              {settings.webhooks.length > 0 ? (
                <Select
                  className="notif-webhook-select"
                  mode="multiple"
                  value={selected.targetWebhookIds}
                  placeholder="选择 Webhook 接收器"
                  optionFilterProp="label"
                  maxTagCount="responsive"
                  onChange={updateTargetWebhooks}
                  options={settings.webhooks.map((webhook) => ({
                    value: webhook.id,
                    label: webhook.name || "未命名 Webhook",
                    title: webhook.url || "未配置 URL"
                  }))}
                />
              ) : (
                <div className="notif-target-empty">
                  <strong>还没有 Webhook 接收器</strong>
                  <span>先在上方新增 Webhook，再把规则发送到对应接收器。</span>
                </div>
              )}
            </div>

            <div className="notif-rule-section">
              <div className="notif-rule-section-title">
                <span>4</span>
                <div>
                  <h5>消息内容</h5>
                  <p>控制恢复消息与数据快照。</p>
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
            </div>

            <div className="notif-rule-section">
              <div className="notif-rule-section-title">
                <span>5</span>
                <div>
                  <h5>规则 JSON</h5>
                  <p>这段是可复用规则模板，不包含当前 Webhook 绑定。</p>
                </div>
              </div>
              <div className="notif-json-card">
                <pre>{makeRuleTemplateJson(selected)}</pre>
                <div className="notif-json-actions">
                  <button className="button secondary compact" type="button" onClick={() => void copySelectedRuleTemplate()}>
                    <Copy size={16} aria-hidden="true" />
                    复制模板
                  </button>
                  <button className="button tertiary compact" type="button" onClick={exportSelectedRuleTemplate}>
                    <Download size={16} aria-hidden="true" />
                    导出单条
                  </button>
                </div>
              </div>
            </div>

            <div className="notif-actions notif-rule-actions">
              <button className="button secondary" type="button" onClick={onTest}>
                <Send size={16} aria-hidden="true" />
                发送测试
              </button>
              <div className="notif-item-actions">
                {renderSaveAction(`rule:${selected.id}`)}
                <button className="button danger" type="button" onClick={onRemoveRule}>
                  删除规则
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
