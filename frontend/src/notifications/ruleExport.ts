import { NotificationRule, NotificationWebhook } from "./types";

export type NotificationRuleTemplate = Omit<NotificationRule, "id" | "targetWebhookIds">;

type ExportedRule = {
  template: NotificationRuleTemplate;
  localBindings: {
    targetWebhookIds: string[];
    targetWebhooks: Array<{
      id: string;
      name: string;
      provider: string;
      enabled: boolean;
    }>;
  };
};

export type NotificationRuleExport = {
  schemaVersion: 1;
  kind: "sub2api.notification.rules";
  exportedAt: string;
  rules: ExportedRule[];
};

export function makeRuleTemplate(rule: NotificationRule): NotificationRuleTemplate {
  const { id: _id, targetWebhookIds: _targetWebhookIds, ...template } = rule;
  return template;
}

export function makeRuleExport(
  rules: NotificationRule[],
  webhooks: NotificationWebhook[]
): NotificationRuleExport {
  const webhookById = new Map(webhooks.map((webhook) => [webhook.id, webhook]));
  return {
    schemaVersion: 1,
    kind: "sub2api.notification.rules",
    exportedAt: new Date().toISOString(),
    rules: rules.map((rule) => ({
      template: makeRuleTemplate(rule),
      localBindings: {
        targetWebhookIds: rule.targetWebhookIds,
        targetWebhooks: rule.targetWebhookIds.flatMap((id) => {
          const webhook = webhookById.get(id);
          return webhook
            ? [
                {
                  id: webhook.id,
                  name: webhook.name,
                  provider: webhook.provider,
                  enabled: webhook.enabled
                }
              ]
            : [];
        })
      }
    }))
  };
}

export function makeRuleTemplateJson(rule: NotificationRule): string {
  return JSON.stringify(makeRuleTemplate(rule), null, 2);
}

export function downloadJson(filename: string, payload: unknown): void {
  if (typeof window === "undefined") return;
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = window.URL.createObjectURL(blob);
  const link = window.document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.URL.revokeObjectURL(url);
}

export function toJsonFileSlug(value: string): string {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "rule";
}
