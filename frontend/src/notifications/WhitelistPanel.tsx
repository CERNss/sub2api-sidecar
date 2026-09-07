import { Select, Tag } from "antd";
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
  normalizedPlatform,
  saveHealthAlertWhitelist
} from "./api";
import { notifyError, notifySuccess } from "../notify";

type Props = {
  settings: NotificationSettings;
  platform: string;
  onChangeAccountWhitelist: (partial: Partial<AccountAlertWhitelist>) => void;
  onChangeGroupWhitelist: (partial: Partial<GroupAlertWhitelist>) => void;
  renderSaveAction: (scope: string) => ReactNode;
};

// Keep stored ids selectable even if the account/group was deleted upstream.
function withStoredValues(options: WhitelistOption[], values: string[]): WhitelistOption[] {
  const known = new Set(options.map((option) => option.value));
  const extras = values
    .filter((value) => value && !known.has(value))
    .map((value) => ({ value, label: value, platform: null }));
  return [...options, ...extras];
}

// Account / group selectors follow the app-wide platform: the dropdown only offers
// entries of that platform (plus the ones whose platform is unknown), while ids that
// were stored under another platform stay selected — and say so — so nothing silently
// drops out of a whitelist the operator cannot see any more.
function withPlatformScope(
  options: WhitelistOption[],
  values: string[],
  platform: string
): WhitelistOption[] {
  const scope = normalizedPlatform(platform);
  const all = withStoredValues(options, values);
  if (!scope) return all;
  const selected = new Set(values.filter(Boolean));
  return all
    .filter(
      (option) =>
        option.platform === null || option.platform === scope || selected.has(option.value)
    )
    .map((option) =>
      option.platform && option.platform !== scope
        ? { ...option, label: `${option.label} · ${option.platform}` }
        : option
    );
}

export function WhitelistPanel({
  settings,
  platform,
  onChangeAccountWhitelist,
  onChangeGroupWhitelist,
  renderSaveAction
}: Props) {
  const account = settings.account_alert_whitelist;
  const group = settings.group_alert_whitelist;
  const scope = normalizedPlatform(platform);

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
          {scope ? (
            <p className="notif-whitelist-scope">
              当前平台 <Tag>{scope}</Tag>
            </p>
          ) : null}
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
            options={withPlatformScope(accountOptions, account.ids, platform)}
            onChange={(values) =>
              onChangeAccountWhitelist({ ids: values as string[] })
            }
            notFoundContent={loading ? "加载中…" : scope ? `平台 ${scope} 上暂无账号` : "暂无账号"}
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
            options={withPlatformScope(groupOptions, group.ids, platform)}
            onChange={(values) => onChangeGroupWhitelist({ ids: values as string[] })}
            notFoundContent={loading ? "加载中…" : scope ? `平台 ${scope} 上暂无分组` : "暂无分组"}
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
          <small>选中的代理判死也不会告警，仍照常探活和搬迁账号（代理不分平台，改动即时生效）</small>
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
            options={withPlatformScope(accountOptions, evictionMuted, platform)}
            onChange={(values) => void persistHealthWhitelist("account", values as string[])}
            notFoundContent={loading ? "加载中…" : scope ? `平台 ${scope} 上暂无账号` : "暂无账号"}
          />
          <small>选中的账号被健康巡检驱逐时不会告警，驱逐动作照常执行（改动即时生效）</small>
        </label>
      </div>
    </section>
  );
}
