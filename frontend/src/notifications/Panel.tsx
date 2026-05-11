import { FormEvent, useState } from "react";
import { Save } from "lucide-react";
import { WebhookEditor } from "./WebhookEditor";
import { RuleEditor } from "./RuleEditor";
import { Summary } from "./Summary";
import { makeDefaultWebhook, makeRuleForSignal } from "./defaults";
import { loadSettings, persistSettings } from "./storage";
import {
  NotificationRule,
  NotificationSettings,
  NotificationWebhook,
  notificationSignals
} from "./types";

type StatusTone = "idle" | "info" | "success" | "error";
type Status = { message: string; tone: StatusTone };

const emptyStatus: Status = { message: "", tone: "idle" };

export function NotificationPanel() {
  const [settings, setSettings] = useState<NotificationSettings>(() => loadSettings());
  const [selectedWebhookId, setSelectedWebhookId] = useState(
    () => settings.webhooks[0]?.id ?? ""
  );
  const [selectedRuleId, setSelectedRuleId] = useState(() => settings.rules[0]?.id ?? "");
  const [status, setStatus] = useState<Status>(emptyStatus);

  function updateWebhook(id: string, partial: Partial<NotificationWebhook>) {
    setSettings((current) => ({
      ...current,
      webhooks: current.webhooks.map((webhook) =>
        webhook.id === id ? { ...webhook, ...partial } : webhook
      )
    }));
  }

  function addWebhook() {
    const next: NotificationWebhook = {
      ...makeDefaultWebhook(),
      id: `webhook-${Date.now()}`,
      name: `Webhook ${settings.webhooks.length + 1}`
    };
    setSettings((current) => ({ ...current, webhooks: [...current.webhooks, next] }));
    setSelectedWebhookId(next.id);
  }

  function updateRule(id: string, partial: Partial<NotificationRule>) {
    setSettings((current) => ({
      ...current,
      rules: current.rules.map((rule) => (rule.id === id ? { ...rule, ...partial } : rule))
    }));
  }

  function addRule() {
    const defaultSignal = notificationSignals[0];
    if (!defaultSignal) return;
    const target = settings.webhooks[0]?.id ?? "";
    const rule = makeRuleForSignal(defaultSignal.key, target);
    setSettings((current) => ({ ...current, rules: [...current.rules, rule] }));
    setSelectedRuleId(rule.id);
  }

  function removeSelectedRule() {
    if (!selectedRuleId) return;
    setSettings((current) => {
      const remaining = current.rules.filter((rule) => rule.id !== selectedRuleId);
      return { ...current, rules: remaining };
    });
    setSelectedRuleId((current) => {
      const remaining = settings.rules.filter((rule) => rule.id !== current);
      return remaining[0]?.id ?? "";
    });
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    persistSettings(settings);
    setStatus({ message: "已保存到浏览器本地。", tone: "success" });
  }

  function sendTest() {
    const rule = settings.rules.find((r) => r.id === selectedRuleId);
    if (!rule) {
      setStatus({ message: "请先选中一条规则。", tone: "error" });
      return;
    }
    const targets = settings.webhooks.filter((webhook) =>
      rule.targetWebhookIds.includes(webhook.id)
    );
    if (targets.length === 0) {
      setStatus({ message: "当前规则没有选中任何 Webhook。", tone: "error" });
      return;
    }
    if (targets.some((webhook) => !webhook.enabled || !webhook.url.trim())) {
      setStatus({
        message: "目标 Webhook 中存在未启用或未填写 URL 的接收器。",
        tone: "error"
      });
      return;
    }
    setStatus({
      message: `${rule.name} 测试消息已准备好，将发送到 ${targets.length} 个 Webhook。`,
      tone: "info"
    });
  }

  return (
    <div className="notification-workspace">
      <form className="panel form-panel notif-panel" onSubmit={save}>
        <div className="panel-title-row">
          <div>
            <p className="eyebrow">Alert Center</p>
            <h2>告警中心</h2>
          </div>
        </div>

        <WebhookEditor
          settings={settings}
          selectedWebhookId={selectedWebhookId}
          onSelect={setSelectedWebhookId}
          onChange={updateWebhook}
          onAdd={addWebhook}
        />

        <RuleEditor
          settings={settings}
          selectedRuleId={selectedRuleId}
          onSelectRule={setSelectedRuleId}
          onChangeRule={updateRule}
          onAddRule={addRule}
          onRemoveRule={removeSelectedRule}
          onTest={sendTest}
        />

        <div className="notif-actions notif-save-row">
          <button className="button primary" type="submit">
            <Save size={16} aria-hidden="true" />
            保存设置
          </button>
          {status.message ? (
            <span className={`notif-status tone-${status.tone}`} role={status.tone === "error" ? "alert" : "status"}>
              {status.message}
            </span>
          ) : null}
        </div>
      </form>

      <Summary settings={settings} />
    </div>
  );
}
