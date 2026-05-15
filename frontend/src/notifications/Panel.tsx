import { FormEvent, useEffect, useState } from "react";
import { LoaderCircle, Save } from "lucide-react";
import { WebhookEditor } from "./WebhookEditor";
import { RuleEditor } from "./RuleEditor";
import { Summary } from "./Summary";
import { makeDefaultWebhook, makeRuleForSignal } from "./defaults";
import {
  getNotificationApiErrorMessage,
  loadNotificationDeliveries,
  loadNotificationSettings,
  saveNotificationSettings,
  sendNotificationTest
} from "./api";
import {
  NotificationDeliveryHistory,
  NotificationRule,
  NotificationSettings,
  NotificationTestResult,
  NotificationWebhook,
  notificationSignals
} from "./types";

type StatusTone = "idle" | "info" | "success" | "error";
type Status = { message: string; tone: StatusTone };
type Props = {
  onAuthExpired: (error: unknown, setStatus?: (status: Status) => void) => boolean;
};

const emptyStatus: Status = { message: "", tone: "idle" };
const emptyDeliveryHistory: NotificationDeliveryHistory = { items: [], total: 0 };

export function NotificationPanel({ onAuthExpired }: Props) {
  const [settings, setSettings] = useState<NotificationSettings>(() => ({
    webhooks: [makeDefaultWebhook()],
    rules: []
  }));
  const [selectedWebhookId, setSelectedWebhookId] = useState("");
  const [selectedRuleId, setSelectedRuleId] = useState("");
  const [status, setStatus] = useState<Status>(emptyStatus);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [savingWebhookToggleId, setSavingWebhookToggleId] = useState("");
  const [saveScope, setSaveScope] = useState("");
  const [testResult, setTestResult] = useState<NotificationTestResult | null>(null);
  const [deliveryHistory, setDeliveryHistory] =
    useState<NotificationDeliveryHistory>(emptyDeliveryHistory);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);

  function applySettings(next: NotificationSettings) {
    setSettings(next);
    setSelectedWebhookId((current) =>
      current && next.webhooks.some((webhook) => webhook.id === current) ? current : ""
    );
    setSelectedRuleId((current) =>
      current && next.rules.some((rule) => rule.id === current) ? current : ""
    );
  }

  useEffect(() => {
    let cancelled = false;
    async function loadFromDatabase() {
      setIsLoading(true);
      setIsLoadingHistory(true);
      try {
        const loaded = await loadNotificationSettings();
        if (cancelled) return;
        applySettings(loaded);
        setStatus({ message: "已从数据库加载告警配置。", tone: "success" });
        try {
          const history = await loadNotificationDeliveries(50);
          if (!cancelled) setDeliveryHistory(history);
        } catch (error) {
          if (!cancelled && !onAuthExpired(error, setStatus)) {
            setStatus({
              message: getNotificationApiErrorMessage(error, "加载告警历史失败"),
              tone: "error"
            });
          }
        }
      } catch (error) {
        if (cancelled) return;
        if (!onAuthExpired(error, setStatus)) {
          setStatus({
            message: getNotificationApiErrorMessage(error, "加载告警配置失败"),
            tone: "error"
          });
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
          setIsLoadingHistory(false);
        }
      }
    }
    void loadFromDatabase();
    return () => {
      cancelled = true;
    };
  }, []);

  async function refreshDeliveryHistory() {
    setIsLoadingHistory(true);
    try {
      const history = await loadNotificationDeliveries(50);
      setDeliveryHistory(history);
    } catch (error) {
      if (!onAuthExpired(error, setStatus)) {
        setStatus({
          message: getNotificationApiErrorMessage(error, "刷新告警历史失败"),
          tone: "error"
        });
      }
    } finally {
      setIsLoadingHistory(false);
    }
  }

  function updateWebhook(id: string, partial: Partial<NotificationWebhook>) {
    setSettings((current) => ({
      ...current,
      webhooks: current.webhooks.map((webhook) =>
        webhook.id === id ? { ...webhook, ...partial } : webhook
      )
    }));
  }

  async function toggleWebhookEnabled(id: string, enabled: boolean) {
    const nextSettings: NotificationSettings = {
      ...settings,
      webhooks: settings.webhooks.map((webhook) =>
        webhook.id === id ? { ...webhook, enabled } : webhook
      )
    };
    setSettings(nextSettings);
    setSavingWebhookToggleId(id);
    setSaveScope(`webhook-toggle:${id}`);
    setStatus({ message: enabled ? "正在启用 Webhook。" : "正在停用 Webhook。", tone: "info" });
    try {
      const saved = await saveNotificationSettings(nextSettings);
      applySettings(saved);
      setStatus({ message: enabled ? "Webhook 已启用。" : "Webhook 已停用。", tone: "success" });
    } catch (error) {
      setSettings(settings);
      if (!onAuthExpired(error, setStatus)) {
        setStatus({
          message: getNotificationApiErrorMessage(error, "保存 Webhook 开关失败"),
          tone: "error"
        });
      }
    } finally {
      setSavingWebhookToggleId("");
    }
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

  function removeWebhook(id: string) {
    if (!id) return;
    setSettings((current) => {
      const remainingWebhooks = current.webhooks.filter((webhook) => webhook.id !== id);
      return {
        ...current,
        webhooks: remainingWebhooks,
        rules: current.rules.map((rule) => ({
          ...rule,
          targetWebhookIds: rule.targetWebhookIds.filter((webhookId) => webhookId !== id)
        }))
      };
    });
    setSelectedWebhookId((current) => {
      if (current !== id) return current;
      return "";
    });
    setStatus({ message: "Webhook 已删除，保存后会写入数据库。", tone: "success" });
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
    const ruleId = selectedRuleId || settings.rules[0]?.id || "";
    if (!ruleId) return;
    setSettings((current) => {
      const remaining = current.rules.filter((rule) => rule.id !== ruleId);
      return { ...current, rules: remaining };
    });
    setSelectedRuleId("");
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSaving(true);
    setStatus({ message: "正在保存到数据库。", tone: "info" });
    try {
      const saved = await saveNotificationSettings(settings);
      applySettings(saved);
      setStatus({ message: "已保存到数据库。", tone: "success" });
    } catch (error) {
      if (!onAuthExpired(error, setStatus)) {
        setStatus({
          message: getNotificationApiErrorMessage(error, "保存告警配置失败"),
          tone: "error"
        });
      }
    } finally {
      setIsSaving(false);
    }
  }

  async function sendTest() {
    const rule = settings.rules.find((r) => r.id === selectedRuleId) ?? settings.rules[0];
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
    setIsTesting(true);
    setTestResult(null);
    setStatus({
      message: `正在发送 ${targets.length} 个 Webhook。`,
      tone: "info"
    });
    try {
      const saved = await saveNotificationSettings(settings);
      applySettings(saved);
      const result = await sendNotificationTest(rule.id);
      setTestResult(result);
      void refreshDeliveryHistory();
      const successCount = result.outcomes.filter((outcome) => outcome.status === "succeeded").length;
      const failedCount = result.outcomes.filter((outcome) => outcome.status === "failed").length;
      const skippedCount = result.outcomes.filter((outcome) => outcome.status === "skipped").length;
      if (failedCount > 0) {
        setStatus({
          message: `测试发送失败 ${failedCount} 个，成功 ${successCount} 个。`,
          tone: "error"
        });
      } else {
        setStatus({
          message: `测试发送完成：成功 ${successCount} 个${skippedCount > 0 ? `，跳过 ${skippedCount} 个` : ""}。`,
          tone: "success"
        });
      }
    } catch (error) {
      if (!onAuthExpired(error, setStatus)) {
        setStatus({
          message: getNotificationApiErrorMessage(error, "发送测试消息失败"),
          tone: "error"
        });
      }
    } finally {
      setIsTesting(false);
    }
  }

  function renderSaveAction(scope: string) {
    return (
      <>
        <button
          className="button primary compact"
          type="submit"
          disabled={isLoading || isSaving}
          onClick={() => setSaveScope(scope)}
        >
          {isSaving && saveScope === scope ? (
            <LoaderCircle className="spin" size={16} aria-hidden="true" />
          ) : (
            <Save size={16} aria-hidden="true" />
          )}
          {isSaving && saveScope === scope ? "保存中" : "保存设置"}
        </button>
        {status.message && saveScope === scope ? (
          <span className={`notif-status tone-${status.tone}`} role={status.tone === "error" ? "alert" : "status"}>
            {status.message}
          </span>
        ) : null}
      </>
    );
  }

  return (
    <form className="notification-workspace" onSubmit={save}>
      <div className="notif-webhook-area">
        <WebhookEditor
          settings={settings}
          selectedWebhookId={selectedWebhookId}
          onSelect={setSelectedWebhookId}
          onChange={updateWebhook}
          onToggleEnabled={toggleWebhookEnabled}
          onAdd={addWebhook}
          onRemove={removeWebhook}
          savingWebhookToggleId={savingWebhookToggleId}
          renderSaveAction={renderSaveAction}
        />
      </div>

      <div className="notif-summary-area">
        <Summary
          settings={settings}
          deliveryHistory={deliveryHistory}
          isLoadingHistory={isLoadingHistory}
        />
      </div>

      <div className="notif-rules-area">
        <RuleEditor
          settings={settings}
          selectedRuleId={selectedRuleId}
          onSelectRule={setSelectedRuleId}
          onChangeRule={updateRule}
          onAddRule={addRule}
          onRemoveRule={removeSelectedRule}
          onTest={() => void sendTest()}
          isTesting={isTesting}
          testResult={testResult}
          onStatus={(message, tone) => setStatus({ message, tone })}
          renderSaveAction={renderSaveAction}
        />
      </div>
    </form>
  );
}
