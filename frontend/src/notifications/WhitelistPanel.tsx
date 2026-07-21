import { Select } from "antd";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  AccountAlertWhitelist,
  GroupAlertWhitelist,
  NotificationSettings
} from "./types";
import {
  WhitelistOption,
  getNotificationApiErrorMessage,
  loadAccountWhitelistOptions,
  loadGroupWhitelistOptions,
  loadHealthAlertWhitelist,
  loadProxyWhitelistOptions,
  saveHealthAlertWhitelist
} from "./api";
import { notifyError, notifySuccess } from "../notify";

type Props = {
  settings: NotificationSettings;
  onChangeAccountWhitelist: (partial: Partial<AccountAlertWhitelist>) => void;
  onChangeGroupWhitelist: (partial: Partial<GroupAlertWhitelist>) => void;
  renderSaveAction: (scope: string) => ReactNode;
};

// Keep stored ids selectable even if the account/group was deleted upstream.
function withStoredValues(options: WhitelistOption[], values: string[]): WhitelistOption[] {
  const known = new Set(options.map((option) => option.value));
  const extras = values
    .filter((value) => value && !known.has(value))
    .map((value) => ({ value, label: value }));
  return [...options, ...extras];
}

export function WhitelistPanel({
  settings,
  onChangeAccountWhitelist,
  onChangeGroupWhitelist,
  renderSaveAction
}: Props) {
  const account = settings.account_alert_whitelist;
  const group = settings.group_alert_whitelist;

  const [accountOptions, setAccountOptions] = useState<WhitelistOption[]>([]);
  const [groupOptions, setGroupOptions] = useState<WhitelistOption[]>([]);
  const [proxyOptions, setProxyOptions] = useState<WhitelistOption[]>([]);
  const [proxyMuted, setProxyMuted] = useState<string[]>([]);
  const [evictionMuted, setEvictionMuted] = useState<string[]>([]);
  const [savingScope, setSavingScope] = useState<"proxy" | "account" | "">("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [accounts, groups, proxies, proxyList, evictionList] = await Promise.all([
          loadAccountWhitelistOptions(),
          loadGroupWhitelistOptions(),
          loadProxyWhitelistOptions(),
          loadHealthAlertWhitelist("proxy"),
          loadHealthAlertWhitelist("account")
        ]);
        if (cancelled) return;
        setAccountOptions(accounts);
        setGroupOptions(groups);
        setProxyOptions(proxies);
        setProxyMuted(proxyList);
        setEvictionMuted(evictionList);
      } catch (error) {
        if (!cancelled) {
          notifyError(getNotificationApiErrorMessage(error, "加载账号 / 分组列表失败"));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  // These two live in the proxy/account health services, not in the notification
  // config, so they cannot ride along with this panel's save button — they are
  // written straight through on change instead.
  async function persistHealthWhitelist(scope: "proxy" | "account", ids: string[]) {
    const previous = scope === "proxy" ? proxyMuted : evictionMuted;
    const apply = scope === "proxy" ? setProxyMuted : setEvictionMuted;
    apply(ids);
    setSavingScope(scope);
    try {
      const saved = await saveHealthAlertWhitelist(scope, ids);
      apply(saved);
      notifySuccess(scope === "proxy" ? "代理免告警名单已保存" : "账号驱逐免告警名单已保存");
    } catch (error) {
      apply(previous);
      notifyError(getNotificationApiErrorMessage(error, "保存免告警名单失败"));
    } finally {
      setSavingScope("");
    }
  }

  return (
    <section className="panel notif-section notif-island">
      <header className="notif-section-head notif-whitelist-head">
        <div>
          <h3>告警白名单</h3>
        </div>
        <div className="notif-section-actions">{renderSaveAction("whitelist")}</div>
      </header>

      <div className="notif-whitelist-fields">
        <label className="notif-field">
          <span>账号白名单{account.ids.length > 0 ? ` (${account.ids.length})` : ""}</span>
          <Select
            mode="multiple"
            className="notif-webhook-select"
            value={account.ids}
            placeholder="选择要排除的账号"
            optionFilterProp="label"
            maxTagCount="responsive"
            loading={loading}
            options={withStoredValues(accountOptions, account.ids)}
            onChange={(values) =>
              onChangeAccountWhitelist({ ids: values as string[] })
            }
            notFoundContent={loading ? "加载中…" : "暂无账号"}
          />
          <small>选中的账号即使失效也不会触发「账号失效」告警</small>
        </label>
        <label className="notif-field">
          <span>分组白名单{group.ids.length > 0 ? ` (${group.ids.length})` : ""}</span>
          <Select
            mode="multiple"
            className="notif-webhook-select"
            value={group.ids}
            placeholder="选择要排除的分组"
            optionFilterProp="label"
            maxTagCount="responsive"
            loading={loading}
            options={withStoredValues(groupOptions, group.ids)}
            onChange={(values) => onChangeGroupWhitelist({ ids: values as string[] })}
            notFoundContent={loading ? "加载中…" : "暂无分组"}
          />
          <small>选中的分组即使容量跑满也不会触发「分组容量满载」告警</small>
        </label>
        <label className="notif-field">
          <span>代理白名单{proxyMuted.length > 0 ? ` (${proxyMuted.length})` : ""}</span>
          <Select
            mode="multiple"
            className="notif-webhook-select"
            value={proxyMuted}
            placeholder="选择要排除的代理"
            optionFilterProp="label"
            maxTagCount="responsive"
            loading={loading || savingScope === "proxy"}
            options={withStoredValues(proxyOptions, proxyMuted)}
            onChange={(values) => void persistHealthWhitelist("proxy", values as string[])}
            notFoundContent={loading ? "加载中…" : "暂无代理"}
          />
          <small>选中的代理判死也不会告警，仍照常探活和搬迁账号（改动即时生效）</small>
        </label>
        <label className="notif-field">
          <span>驱逐白名单{evictionMuted.length > 0 ? ` (${evictionMuted.length})` : ""}</span>
          <Select
            mode="multiple"
            className="notif-webhook-select"
            value={evictionMuted}
            placeholder="选择要排除的账号"
            optionFilterProp="label"
            maxTagCount="responsive"
            loading={loading || savingScope === "account"}
            options={withStoredValues(accountOptions, evictionMuted)}
            onChange={(values) => void persistHealthWhitelist("account", values as string[])}
            notFoundContent={loading ? "加载中…" : "暂无账号"}
          />
          <small>选中的账号被健康巡检驱逐时不会告警，驱逐动作照常执行（改动即时生效）</small>
        </label>
      </div>
    </section>
  );
}
