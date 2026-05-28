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
  loadGroupWhitelistOptions
} from "./api";
import { notifyError } from "../notify";

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
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [accounts, groups] = await Promise.all([
          loadAccountWhitelistOptions(),
          loadGroupWhitelistOptions()
        ]);
        if (cancelled) return;
        setAccountOptions(accounts);
        setGroupOptions(groups);
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
      </div>
    </section>
  );
}
