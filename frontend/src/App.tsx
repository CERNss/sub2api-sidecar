import {
  ClipboardCheck,
  EyeOff,
  ExternalLink,
  Eye,
  ListChecks,
  LockKeyhole,
  LogIn,
  LoaderCircle,
  LogOut,
  Play,
  Plus,
  RefreshCw,
  Save,
  Search,
  Send,
  TimerReset,
  UserRound
} from "lucide-react";
import type { ReactNode } from "react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button as AntButton,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  Input,
  List,
  Modal,
  Segmented as AntSegmented,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography
} from "antd";
import type { RefSelectProps } from "antd/es/select";
import {
  ApiOutlined,
  ArrowDownOutlined,
  BranchesOutlined,
  ClusterOutlined,
  KeyOutlined,
  NodeIndexOutlined,
  ReloadOutlined,
  SendOutlined,
  SyncOutlined,
  UserOutlined,
  QuestionCircleOutlined
} from "@ant-design/icons";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type ReactFlowInstance
} from "@xyflow/react";

type UiConfig = {
  app_title: string;
  auth_username: string;
  oauth_redirect_uri: string;
  current_user: string | null;
};

type ApiPayload = Record<string, unknown>;

type StatusTone = "idle" | "info" | "success" | "error";

type StatusState = {
  message: string;
  tone: StatusTone;
};

type ProvisionStartPayload = ApiPayload & {
  flow_id?: string;
  oauth_url?: string;
  oauth_redirect_uri?: string;
};

type FlowStatusFilter = "" | "pending_oauth" | "completed" | "failed";
type AssignmentModeFilter = "" | "dedicated" | "managed_pool";
type OperatorView = "orchestration" | "provision" | "notification";
type OrchestrationTab = "manual" | "dynamic";

type ProvisionFlowSummary = {
  flow_id: string;
  email: string;
  user_id: unknown;
  group_id: unknown;
  assignment_mode: string;
  status: string;
  account_name: string;
  oauth_account_id: unknown | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

type ProvisionEvent = {
  event_id: string;
  flow_id: string;
  event_type: string;
  status: string;
  message: string;
  details: Record<string, unknown> | null;
  created_at: string;
};

type ProvisionFlowDetail = ProvisionFlowSummary & {
  success: boolean;
  state: string;
  assignment_reason: string | null;
  oauth_url: string | null;
  oauth_redirect_uri: string;
  oauth_exchange_payload: Record<string, unknown> | null;
  events: ProvisionEvent[];
};

type ProvisionFlowsPayload = ApiPayload & {
  items: ProvisionFlowSummary[];
  total: number;
  limit: number;
  offset: number;
};

type OrchestrationMode = "replace_group" | "api_key";

type OrchestrationUser = {
  user_id: unknown;
  email: string;
  name: string | null;
  username: string | null;
  display_name: string | null;
  status: string | null;
  current_group_id: unknown | null;
  current_group_name: string | null;
  local_group_id: unknown | null;
  local_group_name: string | null;
  has_local_assignment: boolean;
};

type OrchestrationUsersPayload = ApiPayload & {
  items: OrchestrationUser[];
  total: number;
};

type OrchestrationGroup = {
  group_id: unknown;
  name: string;
  group_kind: string | null;
  platform: string | null;
  status: string | null;
  is_exclusive: boolean;
  is_subscription: boolean;
  rotation_supported: boolean;
  unsupported_reason: string | null;
};

type GroupSelectOption = {
  value: string;
  label: string;
  searchText: string;
  groupIdText: string;
  disabled?: boolean;
};

type UserSelectOption = {
  value: string;
  label: string;
  searchText: string;
  emailText: string;
};

type OrchestrationGroupsPayload = ApiPayload & {
  items: OrchestrationGroup[];
  total: number;
};

type OrchestrationApiKey = {
  key_id: unknown;
  name: string | null;
  group_id: unknown | null;
  group_name: string | null;
  status: string | null;
  usage_5h: number | null;
  usage_1d: number | null;
  usage_7d: number | null;
};

type OrchestrationApiKeysPayload = ApiPayload & {
  items: OrchestrationApiKey[];
  total: number;
};

type RotationExecutionPayload = ApiPayload & {
  run_id?: string | null;
  run_kind?: string | null;
  tag?: string | null;
  user_id?: unknown;
  email?: string;
  key_id?: unknown;
  source_group_id?: unknown | null;
  target_group_id?: unknown | null;
  trigger_type?: string;
  status?: string;
  reason?: string;
  migrated_keys?: number;
  metadata?: Record<string, unknown> | null;
};

type AutoRotationRunPayload = ApiPayload & {
  run_id?: string | null;
  run_kind?: string | null;
  tag?: string | null;
  status?: string | null;
  window?: string;
  dry_run?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  synced?: Record<string, number>;
  config?: Record<string, unknown>;
  dead_band_skipped?: boolean;
  planned?: RotationExecutionPayload[];
  moved?: RotationExecutionPayload[];
  skipped?: RotationExecutionPayload[];
  failed?: RotationExecutionPayload[];
  rollback_status?: string | null;
  rollback_results?: RotationExecutionPayload[];
  rollback_reason?: string | null;
};

type AutoRotationRunsPayload = ApiPayload & {
  items: AutoRotationRunPayload[];
  total: number;
};

type RunRecordsPanelProps = {
  className?: string;
  onAuthExpired: (error: unknown, setStatus?: (status: StatusState) => void) => boolean;
  refreshSignal?: number;
  onStatus?: (status: StatusState) => void;
};

type RunReasonSummary = {
  reason: string;
  count: number;
};
type NotificationRuleOperator = "gt" | "gte" | "lt" | "lte" | "eq" | "neq";
type NotificationRuleAggregation = "latest" | "avg" | "max" | "min" | "sum";
type NotificationSeverity = "info" | "warning" | "critical";

type NotificationSignal = {
  key: string;
  label: string;
  description: string;
  source: string;
  unit?: string;
  defaultThreshold?: string;
  defaultOperator?: NotificationRuleOperator;
  defaultAggregation?: NotificationRuleAggregation;
  defaultSeverity?: NotificationSeverity;
  defaultReadIntervalMinutes?: number;
  defaultEvaluationWindowMinutes?: number;
};

type NotificationSignalGroup = {
  title: string;
  description: string;
  signals: NotificationSignal[];
};

type WebhookProvider = "generic" | "feishu" | "dingtalk" | "wecom" | "slack" | "discord";

type NotificationWebhook = {
  id: string;
  name: string;
  enabled: boolean;
  provider: WebhookProvider;
  url: string;
  secret: string;
  mentionOnFailure: boolean;
};

const webhookProviderOptions: { value: WebhookProvider; label: string }[] = [
  { value: "generic", label: "通用 / 自定义 JSON" },
  { value: "feishu", label: "飞书 / Lark 自定义机器人" },
  { value: "dingtalk", label: "钉钉自定义机器人" },
  { value: "wecom", label: "企业微信群机器人" },
  { value: "slack", label: "Slack Incoming Webhook" },
  { value: "discord", label: "Discord Webhook" }
];

const webhookSecretHints: Record<WebhookProvider, string> = {
  generic: "可选，用于签名或鉴权",
  feishu: "可选，飞书加签密钥（HMAC-SHA256）",
  dingtalk: "可选，钉钉加签密钥",
  wecom: "通常留空（key 已包含在 URL 中）",
  slack: "可选，签名/鉴权 header",
  discord: "通常留空"
};

type NotificationRule = {
  id: string;
  name: string;
  enabled: boolean;
  signalKey: string;
  severity: NotificationSeverity;
  operator: NotificationRuleOperator;
  threshold: string;
  warningThreshold: string;
  recoveryThreshold: string;
  thresholdUnit: string;
  aggregation: NotificationRuleAggregation;
  readIntervalMinutes: number;
  evaluationWindowMinutes: number;
  forMinutes: number;
  cooldownMinutes: number;
  targetWebhookIds: string[];
  includeResolved: boolean;
  includeSnapshot: boolean;
};

type NotificationRoutingPolicy = {
  groupBy: "signal" | "source" | "severity";
  groupWaitMinutes: number;
  repeatIntervalMinutes: number;
  quietHoursEnabled: boolean;
  quietHoursStart: string;
  quietHoursEnd: string;
};

type NotificationSettings = {
  webhooks: NotificationWebhook[];
  rules: NotificationRule[];
  policy: NotificationRoutingPolicy;
};
const notificationSignalGroups: NotificationSignalGroup[] = [
  {
    title: "平台 API Key",
    description: "平台 Key、额度、窗口限额、余额、模型和 Key 自身用量。",
    signals: [
      { key: "platform_key_health", label: "Key 有效性", description: "isValid、status、可用模型异常。", source: "/v1/usage", defaultThreshold: "1", defaultOperator: "eq", defaultAggregation: "latest", defaultSeverity: "critical", defaultReadIntervalMinutes: 5, defaultEvaluationWindowMinutes: 5 },
      { key: "platform_key_quota", label: "Key 额度低", description: "quota、rate limits、remaining 接近阈值。", source: "/v1/usage", unit: "% remaining", defaultThreshold: "20", defaultOperator: "lte", defaultAggregation: "min", defaultSeverity: "warning", defaultReadIntervalMinutes: 10, defaultEvaluationWindowMinutes: 30 },
      { key: "platform_key_expiry", label: "Key 即将过期", description: "expires_at、days_until_expiry。", source: "/v1/usage", unit: "days", defaultThreshold: "7", defaultOperator: "lte", defaultAggregation: "latest", defaultSeverity: "warning", defaultReadIntervalMinutes: 60, defaultEvaluationWindowMinutes: 60 },
      { key: "subscription_usage", label: "订阅用量/过期", description: "日/周/月用量、限额、订阅到期。", source: "/v1/usage", unit: "% used", defaultThreshold: "85", defaultOperator: "gte", defaultAggregation: "max", defaultSeverity: "warning", defaultReadIntervalMinutes: 30, defaultEvaluationWindowMinutes: 120 },
      { key: "api_key_usage_spike", label: "Key 用量突增", description: "requests、tokens、cost、rpm、tpm、模型统计。", source: "/v1/usage", unit: "% change", defaultThreshold: "150", defaultOperator: "gte", defaultAggregation: "max", defaultSeverity: "critical", defaultReadIntervalMinutes: 10, defaultEvaluationWindowMinutes: 60 }
    ]
  },
  {
    title: "普通用户",
    description: "用户资料、余额、订阅、API Keys、使用趋势和公告状态。",
    signals: [
      { key: "user_balance_low", label: "用户余额低", description: "balance、通知阈值、额外通知邮箱。", source: "user routes", unit: "USD", defaultThreshold: "5", defaultOperator: "lte", defaultAggregation: "latest", defaultSeverity: "warning", defaultReadIntervalMinutes: 30, defaultEvaluationWindowMinutes: 30 },
      { key: "user_api_key_state", label: "用户 Key 状态", description: "用户 API Key 状态、quota、过期、IP 规则。", source: "user routes", defaultThreshold: "1", defaultOperator: "gte", defaultAggregation: "sum", defaultSeverity: "warning", defaultReadIntervalMinutes: 30, defaultEvaluationWindowMinutes: 60 },
      { key: "user_usage_summary", label: "用户使用摘要", description: "usage stats、trend、model stats、Key usage。", source: "user routes", unit: "% change", defaultThreshold: "120", defaultOperator: "gte", defaultAggregation: "max", defaultSeverity: "info", defaultReadIntervalMinutes: 60, defaultEvaluationWindowMinutes: 240 },
      { key: "user_subscription", label: "用户订阅摘要", description: "活跃订阅、progress、summary。", source: "user routes", unit: "% used", defaultThreshold: "85", defaultOperator: "gte", defaultAggregation: "max", defaultSeverity: "warning", defaultReadIntervalMinutes: 60, defaultEvaluationWindowMinutes: 240 }
    ]
  },
  {
    title: "管理员运营",
    description: "Dashboard、用户/分组/渠道/支付/运维告警等后台数据。",
    signals: [
      { key: "admin_dashboard", label: "运营 Dashboard", description: "总请求、成本、趋势、模型和分组统计。", source: "admin routes", unit: "% change", defaultThreshold: "100", defaultOperator: "gte", defaultAggregation: "max", defaultSeverity: "info", defaultReadIntervalMinutes: 15, defaultEvaluationWindowMinutes: 60 },
      { key: "admin_usage_anomaly", label: "成本/错误突增", description: "usage log、错误率、上游错误、请求详情。", source: "admin routes", unit: "% change", defaultThreshold: "150", defaultOperator: "gte", defaultAggregation: "max", defaultSeverity: "critical", defaultReadIntervalMinutes: 5, defaultEvaluationWindowMinutes: 30 },
      { key: "admin_group_channel", label: "分组和渠道监控", description: "分组限额、渠道绑定、监控失败。", source: "admin routes", unit: "failures", defaultThreshold: "1", defaultOperator: "gte", defaultAggregation: "sum", defaultSeverity: "warning", defaultReadIntervalMinutes: 5, defaultEvaluationWindowMinutes: 15 },
      { key: "admin_payment", label: "支付/退款", description: "支付成功、失败、退款、订单状态。", source: "admin routes", unit: "events", defaultThreshold: "1", defaultOperator: "gte", defaultAggregation: "sum", defaultSeverity: "info", defaultReadIntervalMinutes: 10, defaultEvaluationWindowMinutes: 30 },
      { key: "admin_ops_alert", label: "运维告警", description: "并发、实时流量、告警规则、系统日志。", source: "admin routes", unit: "events", defaultThreshold: "1", defaultOperator: "gte", defaultAggregation: "sum", defaultSeverity: "critical", defaultReadIntervalMinutes: 5, defaultEvaluationWindowMinutes: 15 }
    ]
  },
  {
    title: "AI 上游账号",
    description: "账号状态、调度、限流、会话窗口、quota 和上游平台用量。",
    signals: [
      { key: "account_invalid", label: "账号失效", description: "status、error_message、expires_at。", source: "account usage", unit: "accounts", defaultThreshold: "1", defaultOperator: "gte", defaultAggregation: "sum", defaultSeverity: "critical", defaultReadIntervalMinutes: 5, defaultEvaluationWindowMinutes: 10 },
      { key: "account_rate_limited", label: "限流/过载", description: "rate_limited_at、reset_at、overload_until。", source: "account usage", unit: "accounts", defaultThreshold: "1", defaultOperator: "gte", defaultAggregation: "sum", defaultSeverity: "warning", defaultReadIntervalMinutes: 5, defaultEvaluationWindowMinutes: 15 },
      { key: "account_quota_low", label: "Quota/Credits 低", description: "总/日/周 quota、AI credits、通知阈值。", source: "account usage", unit: "% remaining", defaultThreshold: "15", defaultOperator: "lte", defaultAggregation: "min", defaultSeverity: "warning", defaultReadIntervalMinutes: 15, defaultEvaluationWindowMinutes: 60 },
      { key: "account_reauth_needed", label: "需重授/验证/疑似封禁", description: "needs_verify、needs_reauth、is_banned、error_code。", source: "account usage", unit: "accounts", defaultThreshold: "1", defaultOperator: "gte", defaultAggregation: "sum", defaultSeverity: "critical", defaultReadIntervalMinutes: 10, defaultEvaluationWindowMinutes: 30 },
      { key: "account_capacity_high", label: "并发/RPM/会话接近上限", description: "concurrency、rpm、max_sessions、active_sessions。", source: "account usage", unit: "% used", defaultThreshold: "85", defaultOperator: "gte", defaultAggregation: "max", defaultSeverity: "warning", defaultReadIntervalMinutes: 5, defaultEvaluationWindowMinutes: 15 }
    ]
  }
];
const notificationSignals = notificationSignalGroups.flatMap((group) => group.signals);
const notificationSignalByKey = new Map(notificationSignals.map((signal) => [signal.key, signal]));
const defaultNotificationSignalKeys = notificationSignalGroups.flatMap((group) =>
  group.signals
    .filter((signal) =>
      [
        "platform_key_quota",
        "platform_key_expiry",
        "user_balance_low",
        "admin_usage_anomaly",
        "account_invalid",
        "account_rate_limited",
        "account_quota_low",
        "account_reauth_needed"
      ].includes(signal.key)
    )
    .map((signal) => signal.key)
);
const defaultNotificationSettings: NotificationSettings = {
  webhooks: [
    {
      id: "ops-default",
      name: "Ops Webhook",
      enabled: false,
      provider: "generic",
      url: "",
      secret: "",
      mentionOnFailure: true
    }
  ],
  rules: defaultNotificationSignalKeys.map((signalKey) => {
    const signal = notificationSignalByKey.get(signalKey);
    return {
      id: `rule-${signalKey}`,
      name: signal?.label ?? signalKey,
      enabled: true,
      signalKey,
      severity: signal?.defaultSeverity ?? "warning",
      operator: signal?.defaultOperator ?? "gte",
      threshold: signal?.defaultThreshold ?? "1",
      warningThreshold: signal?.defaultThreshold ?? "1",
      recoveryThreshold: "",
      thresholdUnit: signal?.unit ?? "",
      aggregation: signal?.defaultAggregation ?? "latest",
      readIntervalMinutes: signal?.defaultReadIntervalMinutes ?? 10,
      evaluationWindowMinutes: signal?.defaultEvaluationWindowMinutes ?? 30,
      forMinutes: 5,
      cooldownMinutes: 60,
      targetWebhookIds: ["ops-default"],
      includeResolved: true,
      includeSnapshot: true
    };
  }),
  policy: {
    groupBy: "severity",
    groupWaitMinutes: 2,
    repeatIntervalMinutes: 120,
    quietHoursEnabled: false,
    quietHoursStart: "22:00",
    quietHoursEnd: "08:00"
  }
};

type RotationPoolCandidate = {
  group_id: unknown;
  name: string;
  group_kind: string | null;
  platform: string | null;
  status: string | null;
  is_exclusive: boolean;
  is_subscription: boolean;
  rotation_supported: boolean;
  unsupported_reason: string | null;
  selected: boolean;
  rotation_selected: boolean;
  landing_selected: boolean;
  priority: number | null;
  landing_priority: number | null;
};

type RotationPoolCandidatesPayload = ApiPayload & {
  items: RotationPoolCandidate[];
};

type AutoRotationConfig = {
  enabled: boolean;
  auto_assign_new_users: boolean;
  cooldown_minutes: number;
  usage_window: "5h" | "1d" | "7d" | "30d";
  usage_thresholds: number[];
  imbalance_epsilon: number;
  improvement_delta: number;
  schedule_source_group_ids: unknown[];
};

type AutoRotationConfigPayload = ApiPayload & {
  config: AutoRotationConfig;
  landing_pool?: RotationPoolCandidate[];
  rotation_pool?: RotationPoolCandidate[];
};

const emptyStatus: StatusState = { message: "", tone: "idle" };
const graphTopY = 42;
const graphRowGap = 126;
const graphNodeMinGap = 116;
const usageWindowOptions = [
  { label: "最近 5 小时", value: "5h" },
  { label: "最近 1 天", value: "1d" },
  { label: "最近 7 天", value: "7d" },
  { label: "最近 30 天", value: "30d" }
] as const;
const operatorViewPaths: Record<OperatorView, string> = {
  orchestration: "/orchestration/manual",
  provision: "/provision",
  notification: "/notifications"
};
const orchestrationTabPaths: Record<OrchestrationTab, string> = {
  manual: "/orchestration/manual",
  dynamic: "/orchestration/dynamic"
};

class ApiError extends Error {
  status: number;
  payload: ApiPayload;

  constructor(message: string, status: number, payload: ApiPayload) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

async function requestJson<T extends ApiPayload>(
  url: string,
  options: RequestInit,
  fallbackMessage: string
): Promise<T> {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers
    }
  });
  const payload = (await response.json().catch(() => ({
    detail: fallbackMessage
  }))) as ApiPayload;

  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : fallbackMessage;
    throw new ApiError(detail, response.status, payload);
  }

  return payload as T;
}

function getErrorMessage(error: unknown, fallbackMessage: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallbackMessage;
}

function formatPayload(payload: ApiPayload | null): string {
  if (!payload) {
    return "";
  }
  return JSON.stringify(payload, null, 2);
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(date);
}

function unknownToText(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function runKindLabel(run: AutoRotationRunPayload): string {
  if (run.run_kind === "manual") {
    return run.tag === "manual_api_key" ? "手动 Key" : "手动用户";
  }
  return run.dry_run ? "动态预览" : "动态执行";
}

function runKindColor(run: AutoRotationRunPayload): string {
  if (run.run_kind === "manual") {
    return "gold";
  }
  return run.dry_run ? "blue" : "green";
}

function runCounts(run: AutoRotationRunPayload) {
  return {
    planned: run.planned?.length ?? 0,
    moved: run.moved?.length ?? 0,
    skipped: run.skipped?.length ?? 0,
    failed: run.failed?.length ?? 0
  };
}

function idValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function average(values: number[], fallback: number) {
  if (values.length === 0) {
    return fallback;
  }
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function spreadVerticalPositions(items: Array<{ id: string; desiredY: number }>, minGap = graphNodeMinGap) {
  const positions = new Map<string, number>();
  const sortedItems = [...items].sort((first, second) => first.desiredY - second.desiredY || first.id.localeCompare(second.id));
  let nextY = graphTopY;
  sortedItems.forEach((item) => {
    const y = Math.max(item.desiredY, nextY);
    positions.set(item.id, y);
    nextY = y + minGap;
  });
  return positions;
}

function userDisplayName(user: OrchestrationUser): string {
  const email = user.email.trim();
  const fallbackName = email ? email.split("@", 1)[0] : unknownToText(user.user_id);
  const candidates = [user.display_name, user.username, user.name]
    .map((value) => (value ?? "").trim())
    .filter(Boolean);
  const distinctName = candidates.find((value) => value.toLowerCase() !== email.toLowerCase());
  return distinctName || fallbackName || "用户";
}

function userEmailText(user: OrchestrationUser): string {
  return user.email.trim() || "未提供 email";
}

function buildUserOption(user: OrchestrationUser): UserSelectOption {
  const displayName = userDisplayName(user);
  const emailText = userEmailText(user);
  const userIdText = unknownToText(user.user_id);
  return {
    value: idValue(user.user_id),
    label: displayName,
    searchText: `${displayName} ${emailText} ${userIdText}`,
    emailText
  };
}

function UserIdentity({ name, email }: { name: ReactNode; email: ReactNode }) {
  return (
    <div className="user-option">
      <span className="user-option-name">{name}</span>
      <span className="user-option-email">{email}</span>
    </div>
  );
}

function renderUserOption(option: { label?: ReactNode; data: UserSelectOption }) {
  const userOption = option.data;
  return <UserIdentity name={option.label} email={userOption.emailText} />;
}

function buildGroupOption(group: OrchestrationGroup, disabled = false): GroupSelectOption {
  const groupIdText = unknownToText(group.group_id);
  const label = group.name || groupIdText;
  return {
    value: idValue(group.group_id),
    label,
    searchText: `${label} ${groupIdText}`,
    groupIdText,
    disabled
  };
}

function renderGroupOption(option: { label?: ReactNode; data: GroupSelectOption }) {
  const groupOption = option.data;
  return (
    <div className="group-option">
      <span className="group-option-name">{option.label}</span>
      {groupOption?.groupIdText ? <span className="group-option-id">ID {groupOption.groupIdText}</span> : null}
    </div>
  );
}

function apiKeyGroupIdText(key: OrchestrationApiKey): string {
  return idValue(key.group_id) || "-";
}

function resolveKnownId(value: string, knownValues: unknown[]): unknown {
  const known = knownValues.find((item) => idValue(item) === value);
  return known ?? value;
}

function orchestrationTabFromPath(pathname: string): OrchestrationTab {
  if (pathname === orchestrationTabPaths.dynamic || pathname === "/dynamic") {
    return "dynamic";
  }
  return "manual";
}

function viewFromPath(pathname: string): OperatorView {
  if (pathname === operatorViewPaths.provision) {
    return "provision";
  }
  if (pathname === operatorViewPaths.notification) {
    return "notification";
  }
  return "orchestration";
}

function loginRedirectPath(): string {
  const nextPath = new URLSearchParams(window.location.search).get("next");
  if (
    nextPath &&
    (Object.values(operatorViewPaths).includes(nextPath as OperatorView) ||
      Object.values(orchestrationTabPaths).includes(nextPath as OrchestrationTab) ||
      nextPath === "/orchestration" ||
      nextPath === "/dynamic")
  ) {
    return nextPath;
  }
  return "/";
}

function App() {
  const [config, setConfig] = useState<UiConfig | null>(null);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let active = true;

    fetch("/ui/config", { credentials: "same-origin" })
      .then((response) => {
        if (!response.ok) {
          throw new Error("无法读取 UI 配置");
        }
        return response.json() as Promise<UiConfig>;
      })
      .then((payload) => {
        if (active) {
          setConfig(payload);
          document.title = payload.app_title;
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setLoadError(getErrorMessage(error, "无法读取 UI 配置"));
        }
      });

    return () => {
      active = false;
    };
  }, []);

  if (loadError) {
    return (
      <AppChrome title="Sub2API OpenAI OAuth 编排服务">
        <div className="empty-state" role="alert">
          {loadError}
        </div>
      </AppChrome>
    );
  }

  if (!config) {
    return (
      <AppChrome title="Sub2API OpenAI OAuth 编排服务">
        <div className="empty-state">
          <LoaderCircle className="spin" size={20} aria-hidden="true" />
          正在载入
        </div>
      </AppChrome>
    );
  }

  return (
    config.current_user ? (
      <AppChrome title={config.app_title}>
        <OperatorWorkspace config={config} />
      </AppChrome>
    ) : (
      <LoginView config={config} />
    )
  );
}

function AppChrome({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="page">
      <div className="shell">
        <header className="app-header">
          <div>
            <p className="eyebrow">Sub2API Sidecar</p>
            <h1>{title}</h1>
          </div>
        </header>
        {children}
      </div>
    </div>
  );
}

function LoginView({ config }: { config: UiConfig }) {
  const [username, setUsername] = useState(config.auth_username);
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<StatusState>(emptyStatus);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!username.trim()) {
      setStatus({ message: "请输入用户名。", tone: "error" });
      return;
    }

    if (!password) {
      setStatus({ message: "请输入密码。", tone: "error" });
      return;
    }

    setIsSubmitting(true);
    setStatus({ message: "正在验证管理员身份", tone: "info" });

    try {
      await requestJson("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: username.trim(),
          password
        })
      }, "登录失败");
      setStatus({ message: "登录成功", tone: "success" });
      window.location.href = loginRedirectPath();
    } catch (error: unknown) {
      setStatus({ message: getErrorMessage(error, "登录失败"), tone: "error" });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <div className="login-shell">
        <div className="login-brand" aria-label="Sub2API">
          <div className="login-mark" aria-hidden="true">S</div>
          <h1>Sub2API sidecar</h1>
        </div>

        <form className="login-panel form-stack" onSubmit={login}>
          <div className="login-heading">
            <h2>欢迎回来</h2>
          </div>
          <label className="login-field">
            <span>用户名</span>
            <div className="login-input-shell">
              <UserRound size={18} aria-hidden="true" />
              <input
                type="text"
                value={username}
                autoComplete="username"
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>
          </label>
          <label className="login-field">
            <span>密码</span>
            <div className="login-input-shell">
              <LockKeyhole size={18} aria-hidden="true" />
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                autoComplete="current-password"
                onChange={(event) => setPassword(event.target.value)}
              />
              <button
                className="password-toggle"
                type="button"
                aria-label={showPassword ? "隐藏密码" : "显示密码"}
                onClick={() => setShowPassword((value) => !value)}
              >
                {showPassword ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
              </button>
            </div>
          </label>
          <button className="login-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <LoaderCircle className="spin" size={18} aria-hidden="true" />
            ) : (
              <LogIn size={18} aria-hidden="true" />
            )}
            登录
          </button>
          <StatusLine status={status} />
        </form>
      </div>
    </main>
  );
}

function OperatorWorkspace({ config }: { config: UiConfig }) {
  const [activeView, setActiveView] = useState<OperatorView>(() => viewFromPath(window.location.pathname));
  const [activeOrchestrationTab, setActiveOrchestrationTab] = useState<OrchestrationTab>(() =>
    orchestrationTabFromPath(window.location.pathname)
  );
  const [logoutBusy, setLogoutBusy] = useState(false);

  useEffect(() => {
    function syncViewFromPath() {
      setActiveView(viewFromPath(window.location.pathname));
      setActiveOrchestrationTab(orchestrationTabFromPath(window.location.pathname));
    }

    window.addEventListener("popstate", syncViewFromPath);
    return () => window.removeEventListener("popstate", syncViewFromPath);
  }, []);

  function navigateView(view: OperatorView) {
    setActiveView(view);
    const nextPath = view === "orchestration" ? orchestrationTabPaths[activeOrchestrationTab] : operatorViewPaths[view];
    if (window.location.pathname !== nextPath) {
      window.history.pushState({}, "", nextPath);
    }
  }

  function navigateOrchestrationTab(tab: OrchestrationTab) {
    setActiveView("orchestration");
    setActiveOrchestrationTab(tab);
    const nextPath = orchestrationTabPaths[tab];
    if (window.location.pathname !== nextPath) {
      window.history.pushState({}, "", nextPath);
    }
  }

  async function logout() {
    setLogoutBusy(true);
    try {
      await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
    } finally {
      window.location.href = "/login";
    }
  }

  function handleAuthExpired(error: unknown, setStatus?: (status: StatusState) => void) {
    if (error instanceof ApiError && error.status === 401) {
      setStatus?.({ message: "登录已失效，正在返回登录页", tone: "error" });
      window.setTimeout(() => {
        window.location.href = "/login";
      }, 500);
      return true;
    }
    return false;
  }

  return (
    <main className="operator-stack">
      <section className="panel operator-toolbar">
        <div>
          <p className="eyebrow">当前用户</p>
          <h2>{config.current_user}</h2>
        </div>
        <div className="toolbar-actions">
          <div className="segmented" role="tablist" aria-label="编排视图">
            <button
              className={activeView === "orchestration" ? "active" : ""}
              type="button"
              onClick={() => navigateView("orchestration")}
            >
              <Play size={17} aria-hidden="true" />
              编排工作台
            </button>
            <button
              className={activeView === "provision" ? "active" : ""}
              type="button"
              onClick={() => navigateView("provision")}
            >
              <Plus size={17} aria-hidden="true" />
              OAuth 预配
            </button>
            <button
              className={activeView === "notification" ? "active" : ""}
              type="button"
              onClick={() => navigateView("notification")}
            >
              <ClipboardCheck size={17} aria-hidden="true" />
              告警中心
            </button>
          </div>
          <button className="button secondary compact" type="button" onClick={logout} disabled={logoutBusy}>
            {logoutBusy ? (
              <LoaderCircle className="spin" size={17} aria-hidden="true" />
            ) : (
              <LogOut size={17} aria-hidden="true" />
            )}
            退出登录
          </button>
        </div>
      </section>

      {activeView === "notification" ? (
        <NotificationSettingsPanel />
      ) : activeView === "provision" ? (
        <ProvisionForm
          config={config}
          onAuthExpired={handleAuthExpired}
          onFlowChanged={() => undefined}
        />
      ) : (
        <ExistingOrchestrationView
          activeTab={activeOrchestrationTab}
          onTabChange={navigateOrchestrationTab}
          onAuthExpired={handleAuthExpired}
        />
      )}
    </main>
  );
}

function ExistingOrchestrationView({
  activeTab,
  onTabChange,
  onAuthExpired
}: {
  activeTab: OrchestrationTab;
  onTabChange: (tab: OrchestrationTab) => void;
  onAuthExpired: (error: unknown, setStatus?: (status: StatusState) => void) => boolean;
}) {
  const [mode, setMode] = useState<OrchestrationMode>("replace_group");
  const [users, setUsers] = useState<OrchestrationUser[]>([]);
  const [groups, setGroups] = useState<OrchestrationGroup[]>([]);
  const [apiKeys, setApiKeys] = useState<OrchestrationApiKey[]>([]);
  const [apiKeysByUserId, setApiKeysByUserId] = useState<Record<string, OrchestrationApiKey[]>>({});
  const [userSearch, setUserSearch] = useState("");
  const [selectedUserId, setSelectedUserId] = useState("");
  const [sourceGroupId, setSourceGroupId] = useState("");
  const [targetGroupId, setTargetGroupId] = useState("");
  const [selectedKeyIds, setSelectedKeyIds] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<StatusState>(emptyStatus);
  const [recordsRefreshSignal, setRecordsRefreshSignal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingKeys, setLoadingKeys] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance | null>(null);
  const userSelectRef = useRef<RefSelectProps | null>(null);
  const userSearchTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (userSearchTimerRef.current) {
        window.clearTimeout(userSearchTimerRef.current);
      }
    };
  }, []);

  const selectedUser = users.find((user) => idValue(user.user_id) === selectedUserId) ?? null;
  const targetGroups = useMemo(
    () => (mode === "replace_group" ? groups.filter((group) => group.rotation_supported) : groups),
    [groups, mode]
  );
  const selectedUserDirectGroup = useMemo(() => {
    const currentGroupId = selectedUser?.current_group_id ?? null;
    const currentGroupName = selectedUser?.current_group_name ?? null;
    const currentGroupValue = idValue(currentGroupId);
    if (!currentGroupValue) {
      return null;
    }
    return groups.find((group) => idValue(group.group_id) === currentGroupValue) ?? {
      group_id: currentGroupId,
      name: currentGroupName || currentGroupValue,
      group_kind: null,
      platform: null,
      status: null,
      is_exclusive: true,
      is_subscription: false,
      rotation_supported: true,
      unsupported_reason: null
    };
  }, [groups, selectedUser]);
  const sourceGroups = useMemo(
    () => (selectedUserDirectGroup ? [selectedUserDirectGroup] : []),
    [selectedUserDirectGroup]
  );
  const sourceGroupOptions = useMemo(() => sourceGroups.map((group) => buildGroupOption(group)), [sourceGroups]);
  const targetGroupOptions = useMemo(
    () =>
      targetGroups.map((group) =>
        buildGroupOption(group, mode === "replace_group" && !group.rotation_supported)
      ),
    [mode, targetGroups]
  );
  const selectedKeySet = useMemo(() => new Set(selectedKeyIds), [selectedKeyIds]);
  const userOptions = useMemo(() => users.map(buildUserOption), [users]);
  const selectedKeys = useMemo(
    () => apiKeys.filter((key) => selectedKeySet.has(idValue(key.key_id))),
    [apiKeys, selectedKeySet]
  );
  const toggleKeySelection = (keyId: string) => {
    if (!keyId) return;
    setSelectedKeyIds((current) =>
      current.includes(keyId) ? current.filter((id) => id !== keyId) : [...current, keyId]
    );
  };
  const graph = useMemo(() => {
    const groupUserCounts = new Map<string, number>();
    const groupKeyCounts = new Map<string, number>();
    const graphGroups = new Map<string, OrchestrationGroup>();
    const userKeyRows = users.flatMap((user) => {
      const userId = idValue(user.user_id);
      const keys = apiKeysByUserId[userId] ?? (userId === selectedUserId ? apiKeys : []);
      return keys.map((key) => ({ user, userId, key }));
    });
    const upsertGraphGroup = (group: OrchestrationGroup) => {
      const groupValue = idValue(group.group_id);
      if (!groupValue) {
        return;
      }
      if (!graphGroups.has(groupValue)) {
        graphGroups.set(groupValue, group);
      }
    };
    groups.forEach(upsertGraphGroup);
    users.forEach((user) => {
      const currentGroupId = user.current_group_id ?? null;
      const currentGroupValue = idValue(currentGroupId);
      if (!currentGroupValue) {
        return;
      }
      groupUserCounts.set(currentGroupValue, (groupUserCounts.get(currentGroupValue) ?? 0) + 1);
      if (!graphGroups.has(currentGroupValue)) {
        graphGroups.set(currentGroupValue, {
          group_id: currentGroupId,
          name: user.current_group_name ?? user.local_group_name ?? currentGroupValue,
          group_kind: null,
          platform: null,
          status: null,
          is_exclusive: true,
          is_subscription: false,
          rotation_supported: true,
          unsupported_reason: null
        });
      }
    });
    userKeyRows.forEach(({ key }) => {
      const keyGroupValue = idValue(key.group_id);
      if (!keyGroupValue) {
        return;
      }
      groupKeyCounts.set(keyGroupValue, (groupKeyCounts.get(keyGroupValue) ?? 0) + 1);
      if (!graphGroups.has(keyGroupValue)) {
        graphGroups.set(keyGroupValue, {
          group_id: key.group_id,
          name: key.group_name || keyGroupValue,
          group_kind: null,
          platform: null,
          status: null,
          is_exclusive: true,
          is_subscription: false,
          rotation_supported: true,
          unsupported_reason: null
        });
      }
    });

    const groupOrder = Array.from(graphGroups.values())
      .map((group, fallbackIndex) => {
        const groupValue = idValue(group.group_id);
        const userIndexes = users
          .map((user, index) => ({ index, groupValue: idValue(user.current_group_id) }))
          .filter((item) => item.groupValue === groupValue)
          .map((item) => item.index);
        const keyIndexes = userKeyRows
          .map((row, index) => ({ index, groupValue: idValue(row.key.group_id) }))
          .filter((item) => item.groupValue === groupValue)
          .map((item) => item.index);
        return {
          group,
          groupValue,
          score: average([...userIndexes, ...keyIndexes], users.length + fallbackIndex)
        };
      })
      .sort((first, second) => first.score - second.score || first.groupValue.localeCompare(second.groupValue));
    const initialGroupYById = new Map<string, number>();
    groupOrder.forEach(({ groupValue }, index) => {
      initialGroupYById.set(groupValue, graphTopY + index * graphRowGap);
    });

    const userRows = users
      .map((user, fallbackIndex) => {
        const userId = idValue(user.user_id);
        const userGroupId = idValue(user.current_group_id);
        const keyRows = userKeyRows.filter((row) => row.userId === userId);
        const keyGroupScores = keyRows.map((row) => initialGroupYById.get(idValue(row.key.group_id))).filter((value): value is number => typeof value === "number");
        return {
          user,
          userId,
          fallbackIndex,
          groupY: initialGroupYById.get(userGroupId) ?? graphTopY + groupOrder.length * graphRowGap + fallbackIndex * graphRowGap,
          keyScore: average(keyGroupScores, fallbackIndex * graphRowGap)
        };
      })
      .sort((first, second) => first.groupY - second.groupY || first.keyScore - second.keyScore || first.userId.localeCompare(second.userId));
    const userYById = spreadVerticalPositions(
      userRows.map(({ userId }, index) => ({ id: userId, desiredY: graphTopY + index * graphRowGap }))
    );
    const groupYById = spreadVerticalPositions(
      groupOrder.map(({ groupValue }) => {
        const relatedUserY = userRows
          .filter(({ user }) => idValue(user.current_group_id) === groupValue)
          .map(({ userId }) => userYById.get(userId))
          .filter((value): value is number => typeof value === "number");
        const desiredY = average(relatedUserY, initialGroupYById.get(groupValue) ?? graphTopY);
        return { id: groupValue, desiredY };
      })
    );

    const keyRows = userKeyRows
      .map((row, fallbackIndex) => ({
        ...row,
        fallbackIndex,
        userY: userYById.get(row.userId) ?? graphTopY + fallbackIndex * graphRowGap,
        groupY: groupYById.get(idValue(row.key.group_id)) ?? graphTopY + fallbackIndex * graphRowGap
      }))
      .sort((first, second) => first.userY - second.userY || first.groupY - second.groupY || idValue(first.key.key_id).localeCompare(idValue(second.key.key_id)));
    const keyYById = spreadVerticalPositions(
      keyRows.map(({ userId, key }) => ({
        id: `${userId}-${idValue(key.key_id)}`,
        desiredY: userYById.get(userId) ?? graphTopY
      }))
    );

    const groupNodes: Node[] = groupOrder.map(({ group, groupValue }) => {
      const tags = [
        `${groupUserCounts.get(groupValue) ?? 0} 用户`,
        `${groupKeyCounts.get(groupValue) ?? 0} Key`
      ];
      if (groupValue === sourceGroupId) {
        tags.push("当前");
      }
      if (groupValue === targetGroupId) {
        tags.push("目标");
      }
      return {
        id: `group-${groupValue}`,
        type: "output",
        position: { x: 692, y: groupYById.get(groupValue) ?? graphTopY },
        targetPosition: Position.Left,
        data: {
          groupId: groupValue,
          label: (
            <GraphNode
              icon={<ClusterOutlined />}
              title={group.name || "分组"}
              subtitle={`Group ${unknownToText(group.group_id)}`}
              tone={groupValue === sourceGroupId ? "source" : groupValue === targetGroupId ? "target" : "neutral"}
              tag={tags.join(" / ")}
            />
          )
        }
      };
    });

    const userNodes: Node[] = userRows.map(({ user, userId }) => {
      const currentGroupId = user.current_group_id ?? null;
      const currentGroupName = user.current_group_name ?? unknownToText(currentGroupId);
      return {
        id: `user-${userId}`,
        position: { x: 360, y: userYById.get(userId) ?? graphTopY },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: {
          userId,
          label: (
            <GraphNode
              icon={<UserOutlined />}
              title={userDisplayName(user)}
              subtitle={userEmailText(user)}
              tone={userId === selectedUserId ? "active" : "user"}
              tag={currentGroupName || user.status || "active"}
            />
          )
        }
      };
    });

    const keyNodes: Node[] = keyRows.map(({ userId, key }) => ({
      id: `key-${userId}-${idValue(key.key_id)}`,
      type: "input",
      position: { x: 28, y: keyYById.get(`${userId}-${idValue(key.key_id)}`) ?? graphTopY },
      sourcePosition: Position.Right,
      data: {
        userId,
        keyId: idValue(key.key_id),
        label: (
          <GraphNode
            icon={<KeyOutlined />}
            title={key.name || "api-key"}
            subtitle={`Key ID ${unknownToText(key.key_id)}`}
            tone={selectedKeySet.has(idValue(key.key_id)) ? "active" : "neutral"}
            tag={`Group ID ${apiKeyGroupIdText(key)}`}
          />
        )
      }
    }));
    const nodes: Node[] = [...groupNodes, ...userNodes, ...keyNodes];
    const edges: Edge[] = [
      ...users
        .filter((user) => idValue(user.current_group_id))
        .map((user) => {
          const userId = idValue(user.user_id);
          const groupId = idValue(user.current_group_id);
          return {
            id: `group-user-${groupId}-${userId}`,
            source: `user-${userId}`,
            target: `group-${groupId}`,
            animated: userId === selectedUserId,
            markerEnd: { type: MarkerType.ArrowClosed },
            label: "用户所在分组"
          };
        }),
      ...userKeyRows.map(({ userId, key }) => ({
        id: `user-key-${userId}-${idValue(key.key_id)}`,
        source: `key-${userId}-${idValue(key.key_id)}`,
        target: `user-${userId}`,
        animated: userId === selectedUserId || selectedKeySet.has(idValue(key.key_id)),
        markerEnd: { type: MarkerType.ArrowClosed },
        label: "用户 Key"
      })),
      ...userKeyRows
        .filter(({ user, key }) => {
          const keyGroupId = idValue(key.group_id);
          const userGroupId = idValue(user.current_group_id);
          const keyId = idValue(key.key_id);
          const userId = idValue(user.user_id);
          return keyGroupId && keyGroupId !== userGroupId && (userId === selectedUserId || selectedKeySet.has(keyId));
        })
        .map(({ userId, key }) => ({
          id: `group-key-${idValue(key.group_id)}-${userId}-${idValue(key.key_id)}`,
          source: `key-${userId}-${idValue(key.key_id)}`,
          target: `group-${idValue(key.group_id)}`,
          animated: userId === selectedUserId || selectedKeySet.has(idValue(key.key_id)),
          markerEnd: { type: MarkerType.ArrowClosed },
          style: { stroke: "#2563eb", strokeWidth: 1.6, strokeDasharray: "5 5" },
          label: "Key 当前分组"
        }))
    ];
    return { nodes, edges };
  }, [
    apiKeys,
    apiKeysByUserId,
    groups,
    selectedKeySet,
    selectedUserId,
    sourceGroupId,
    targetGroupId,
    users
  ]);
  const [nodes, setNodes, onNodesChange] = useNodesState(graph.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(graph.edges);

  async function loadResources(nextSelectedUserId?: string, searchOverride?: string) {
    setLoading(true);
    const searchTerm = (searchOverride !== undefined ? searchOverride : userSearch).trim();
    const params = new URLSearchParams();
    if (searchTerm) {
      params.set("email", searchTerm);
    }

    try {
      const [usersPayload, groupsPayload] = await Promise.all([
        requestJson<OrchestrationUsersPayload>(
          `/orchestration/users${params.toString() ? `?${params.toString()}` : ""}`,
          { method: "GET" },
          "加载用户失败"
        ),
        requestJson<OrchestrationGroupsPayload>(
          "/orchestration/groups",
          { method: "GET" },
          "加载分组失败"
        )
      ]);

      setUsers(usersPayload.items);
      setGroups(groupsPayload.items);
      const candidateUserId =
        nextSelectedUserId === ""
          ? ""
          : nextSelectedUserId && usersPayload.items.some((user) => idValue(user.user_id) === nextSelectedUserId)
          ? nextSelectedUserId
          : usersPayload.items[0] ? idValue(usersPayload.items[0].user_id) : "";
      setSelectedUserId(candidateUserId);
      void loadAllApiKeys(usersPayload.items);
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setUsers([]);
        setGroups([]);
        setApiKeys([]);
        setApiKeysByUserId({});
        setStatus({ message: getErrorMessage(error, "加载用户和分组失败"), tone: "error" });
      }
    } finally {
      setLoading(false);
    }
  }

  async function loadApiKeys(userId: string) {
    if (!userId) {
      setApiKeys([]);
      setSelectedKeyIds([]);
      return;
    }

    setLoadingKeys(true);
    try {
      const payload = await requestJson<OrchestrationApiKeysPayload>(
        `/orchestration/users/${encodeURIComponent(userId)}/api-keys`,
        { method: "GET" },
        "加载 API Keys 失败"
      );
      setApiKeys(payload.items);
      setApiKeysByUserId((current) => ({ ...current, [userId]: payload.items }));
      const validIds = new Set(payload.items.map((key) => idValue(key.key_id)));
      setSelectedKeyIds((current) => current.filter((id) => validIds.has(id)));
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setApiKeys([]);
        setSelectedKeyIds([]);
        setStatus({ message: getErrorMessage(error, "加载 API Keys 失败"), tone: "error" });
      }
    } finally {
      setLoadingKeys(false);
    }
  }

  async function loadAllApiKeys(nextUsers: OrchestrationUser[]) {
    const entries = await Promise.all(
      nextUsers.map(async (user) => {
        const userId = idValue(user.user_id);
        if (!userId) {
          return [userId, []] as const;
        }
        try {
          const payload = await requestJson<OrchestrationApiKeysPayload>(
            `/orchestration/users/${encodeURIComponent(userId)}/api-keys`,
            { method: "GET" },
            "加载 API Keys 失败"
          );
          return [userId, payload.items] as const;
        } catch (error: unknown) {
          if (!onAuthExpired(error, setStatus)) {
            setStatus({ message: getErrorMessage(error, "加载部分用户 API Keys 失败"), tone: "error" });
          }
          return [userId, []] as const;
        }
      })
    );
    setApiKeysByUserId(Object.fromEntries(entries));
  }

  useEffect(() => {
    void loadResources("");
  }, []);

  useEffect(() => {
    if (!selectedUser) {
      setSourceGroupId("");
      setApiKeys([]);
      setSelectedKeyIds([]);
      return;
    }
    setSourceGroupId(idValue(selectedUserDirectGroup?.group_id));
    void loadApiKeys(idValue(selectedUser.user_id));
  }, [selectedUserId, selectedUserDirectGroup?.group_id]);

  useEffect(() => {
    setTargetGroupId((current) =>
      current && targetGroups.some((group) => idValue(group.group_id) === current) ? current : ""
    );
  }, [targetGroups]);

  useEffect(() => {
    setNodes(graph.nodes);
    setEdges(graph.edges);
    window.setTimeout(() => flowInstance?.fitView({ padding: 0.16, duration: 220 }), 0);
  }, [flowInstance, graph, setEdges, setNodes]);

  function refreshGraphLayout() {
    setNodes(graph.nodes);
    setEdges(graph.edges);
    window.setTimeout(() => flowInstance?.fitView({ padding: 0.16, duration: 260 }), 0);
  }

  function selectGraphEntity(userId?: unknown, keyId?: unknown) {
    const nextUserId = idValue(userId);
    if (nextUserId && nextUserId !== selectedUserId) {
      setSelectedUserId(nextUserId);
    }
    const nextKeyId = idValue(keyId);
    if (nextKeyId) {
      setSelectedKeyIds((current) => (current.includes(nextKeyId) ? current : [...current, nextKeyId]));
    }
  }

  async function runExistingOrchestration(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();

    if (!selectedUser) {
      setStatus({ message: "请选择用户。", tone: "error" });
      return;
    }
    if (!targetGroupId) {
      setStatus({ message: "请选择目标分组。", tone: "error" });
      return;
    }
    if (mode === "replace_group" && !sourceGroupId) {
      setStatus({ message: "请选择源分组。", tone: "error" });
      return;
    }
    if (mode === "api_key" && selectedKeys.length === 0) {
      setStatus({ message: "请至少选择一个 API Key。", tone: "error" });
      return;
    }

    const knownGroupIds = groups.map((group) => group.group_id);
    const sourceGroupValue = resolveKnownId(sourceGroupId, knownGroupIds);
    const targetGroupValue = resolveKnownId(targetGroupId, knownGroupIds);
    const reasonValue = reason.trim() || undefined;

    setSubmitting(true);
    setStatus({
      message:
        mode === "api_key" && selectedKeys.length > 1
          ? `正在执行编排（${selectedKeys.length} 个 Key）`
          : "正在执行编排",
      tone: "info"
    });

    try {
      if (mode === "replace_group") {
        const payload = await requestJson<RotationExecutionPayload>(
          "/orchestration/assignments/replace-group",
          {
            method: "POST",
            body: JSON.stringify({
              user_id: selectedUser.user_id,
              email: selectedUser.email,
              source_group_id: sourceGroupValue,
              target_group_id: targetGroupValue,
              reason: reasonValue
            })
          },
          "编排执行失败"
        );
        setRecordsRefreshSignal((value) => value + 1);
        setStatus({
          message: payload.status === "failed" ? payload.reason || "编排执行失败" : "编排执行完成",
          tone: payload.status === "failed" ? "error" : "success"
        });
      } else {
        const results: RotationExecutionPayload[] = [];
        let failedCount = 0;
        let authExpired = false;
        for (const key of selectedKeys) {
          try {
            const payload = await requestJson<RotationExecutionPayload>(
              "/orchestration/api-keys/update-group",
              {
                method: "POST",
                body: JSON.stringify({
                  user_id: selectedUser.user_id,
                  email: selectedUser.email,
                  key_id: key.key_id,
                  source_group_id: key.group_id ?? sourceGroupValue,
                  target_group_id: targetGroupValue,
                  reason: reasonValue
                })
              },
              "编排执行失败"
            );
            results.push(payload);
            if (payload.status === "failed") failedCount += 1;
          } catch (error: unknown) {
            if (onAuthExpired(error, setStatus)) {
              authExpired = true;
              break;
            }
            results.push({
              success: false,
              status: "failed",
              key_id: key.key_id,
              detail: getErrorMessage(error, "编排执行失败")
            });
            failedCount += 1;
          }
        }
        if (authExpired) {
          if (results.length > 0) setRecordsRefreshSignal((value) => value + 1);
          return;
        }
        setRecordsRefreshSignal((value) => value + 1);
        const total = results.length;
        if (failedCount === 0) {
          setStatus({ message: `编排执行完成（${total} 个 Key）`, tone: "success" });
        } else if (failedCount === total) {
          setStatus({ message: `编排执行失败（${failedCount}/${total}）`, tone: "error" });
        } else {
          setStatus({ message: `部分执行成功（成功 ${total - failedCount} / 失败 ${failedCount}）`, tone: "error" });
        }
      }
      await loadResources(idValue(selectedUser.user_id));
      await loadApiKeys(idValue(selectedUser.user_id));
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setStatus({ message: getErrorMessage(error, "编排执行失败"), tone: "error" });
      }
    } finally {
      setSubmitting(false);
    }
  }

  const allKeyIds = apiKeys.map((key) => idValue(key.key_id)).filter(Boolean);
  const allKeysSelected = allKeyIds.length > 0 && allKeyIds.every((id) => selectedKeySet.has(id));
  const toggleAllKeys = () => {
    if (allKeysSelected) {
      setSelectedKeyIds([]);
    } else {
      setSelectedKeyIds(allKeyIds);
    }
  };

  const apiKeyListContent = apiKeys.length === 0 ? (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={loadingKeys ? "正在加载 API Keys" : "暂无 API Keys"}
    />
  ) : (
    <List
      className="orchestration-key-list"
      dataSource={apiKeys}
      renderItem={(key) => {
        const keyId = idValue(key.key_id);
        const checked = selectedKeySet.has(keyId);
        const interactive = mode === "api_key";
        const groupIdText = apiKeyGroupIdText(key);
        return (
          <List.Item
            className={`${checked ? "selected-key-row" : ""} ${interactive ? "selectable-key-row" : "readonly-key-row"}`.trim()}
            onClick={interactive ? () => toggleKeySelection(keyId) : undefined}
          >
            {interactive ? (
              <Checkbox
                className="orchestration-key-checkbox"
                checked={checked}
                onClick={(event) => event.stopPropagation()}
                onChange={() => toggleKeySelection(keyId)}
              />
            ) : null}
            <List.Item.Meta
              avatar={<KeyOutlined />}
              title={key.name || "api-key"}
              description={`Key ID ${unknownToText(key.key_id)}`}
            />
            <div className="api-key-meta-tags" aria-label="API Key 分组信息">
              <Tag color={checked ? "green" : "default"}>{`Group ID ${groupIdText}`}</Tag>
            </div>
          </List.Item>
        );
      }}
    />
  );

  return (
    <div className="orchestration-workbench">
      <section className="orchestration-top-panel">
        <div className="orchestration-top-header">
          <Space>
            <NodeIndexOutlined />
            <Typography.Text strong>关系编排</Typography.Text>
          </Space>
          <Space wrap>
            <AntSegmented
              value={activeTab}
              onChange={(value) => onTabChange(value as OrchestrationTab)}
              options={[
                { label: "手动编排", value: "manual", icon: <BranchesOutlined /> },
                { label: "动态编排", value: "dynamic", icon: <SyncOutlined /> }
              ]}
            />
            <AntButton icon={<ReloadOutlined />} loading={loading} onClick={() => void loadResources(selectedUserId)}>
              刷新
            </AntButton>
          </Space>
        </div>

        {activeTab === "manual" ? (
          <form className="orchestration-top-form" onSubmit={runExistingOrchestration}>
            <section className="manual-zone manual-zone--target" aria-labelledby="manual-zone-target-title">
              <header className="manual-zone-header">
                <span className="manual-zone-step">1</span>
                <Typography.Text strong id="manual-zone-target-title">选择对象</Typography.Text>
              </header>

              <AntSegmented
                className="manual-mode-switch"
                block
                value={mode}
                onChange={(value) => setMode(value as OrchestrationMode)}
                options={[
                  { label: "整体替换", value: "replace_group", icon: <BranchesOutlined /> },
                  { label: "单 Key", value: "api_key", icon: <KeyOutlined /> }
                ]}
              />

              <div className="ant-field">
                <Typography.Text strong>User</Typography.Text>
                <Select
                  ref={userSelectRef}
                  className="manual-user-select"
                  value={selectedUserId || undefined}
                  placeholder="按用户名或 email 搜索"
                  showSearch
                  filterOption={false}
                  loading={loading}
                  allowClear
                  optionFilterProp="searchText"
                  optionRender={renderUserOption}
                  labelRender={(item) => {
                    const option = userOptions.find((candidate) => candidate.value === item.value);
                    return option ? <UserIdentity name={option.label} email={option.emailText} /> : item.label;
                  }}
                  onSearch={(value) => {
                    setUserSearch(value);
                    if (userSearchTimerRef.current) {
                      window.clearTimeout(userSearchTimerRef.current);
                    }
                    userSearchTimerRef.current = window.setTimeout(() => {
                      void loadResources(selectedUserId, value);
                    }, 300);
                  }}
                  onClear={() => {
                    if (userSearchTimerRef.current) {
                      window.clearTimeout(userSearchTimerRef.current);
                    }
                    setUserSearch("");
                    setSelectedUserId("");
                    void loadResources("", "");
                  }}
                  onChange={(value) => setSelectedUserId(value ?? "")}
                  onSelect={() => {
                    setUserSearch("");
                    window.setTimeout(() => {
                      userSelectRef.current?.blur();
                    }, 0);
                  }}
                  options={userOptions}
                  notFoundContent={loading ? <Spin size="small" /> : "暂无用户"}
                />
              </div>

              <Typography.Text type="secondary" className="manual-zone-hint">
                {users.length} 用户 · {groups.length} 分组
              </Typography.Text>
            </section>

            <section className="manual-zone manual-zone--route" aria-labelledby="manual-zone-route-title">
              <header className="manual-zone-header">
                <span className="manual-zone-step">2</span>
                <Typography.Text strong id="manual-zone-route-title">路由配置</Typography.Text>
              </header>

              <div className="manual-route-flow">
                <div className="ant-field">
                  <Typography.Text strong>当前分组</Typography.Text>
                  <Select
                    className="group-select"
                    value={sourceGroupId || undefined}
                    placeholder="当前用户未绑定分组"
                    disabled
                    optionFilterProp="searchText"
                    options={sourceGroupOptions}
                    optionRender={renderGroupOption}
                  />
                </div>
                <div className="manual-route-arrow" aria-hidden="true">
                  <ArrowDownOutlined />
                </div>
                <div className="ant-field">
                  <Typography.Text strong>目标分组</Typography.Text>
                  <Select
                    className="group-select"
                    value={targetGroupId || undefined}
                    placeholder="选择目标分组"
                    showSearch
                    allowClear
                    optionFilterProp="searchText"
                    onChange={(value) => setTargetGroupId(value ?? "")}
                    options={targetGroupOptions}
                    optionRender={renderGroupOption}
                    notFoundContent="暂无可用分组"
                  />
                </div>
              </div>

              <section className="orchestration-keys-panel manual-keys-panel" aria-label="用户 API Keys">
                <div className="orchestration-keys-panel-title">
                  <Space>
                    <ApiOutlined />
                    <Typography.Text strong>
                      {mode === "api_key" ? "选择 API Key（支持多选）" : "用户 API Keys"}
                    </Typography.Text>
                    <Tooltip title="Key ID 是 API Key 的唯一编号；Group ID 是当前绑定分组编号。右上角显示当前用户的 Key 数量。">
                      <QuestionCircleOutlined className="panel-help-icon" aria-label="API Key 字段说明" />
                    </Tooltip>
                  </Space>
                  <Space size={8}>
                    {mode === "api_key" && apiKeys.length > 0 ? (
                      <>
                        <Tag color={selectedKeyIds.length > 0 ? "green" : "default"}>
                          已选 {selectedKeyIds.length} / {apiKeys.length}
                        </Tag>
                        <AntButton
                          size="small"
                          type="link"
                          onClick={toggleAllKeys}
                          disabled={loadingKeys}
                        >
                          {allKeysSelected ? "全不选" : "全选"}
                        </AntButton>
                      </>
                    ) : loadingKeys ? (
                      <Spin size="small" />
                    ) : (
                      <Tag>{apiKeys.length} Key</Tag>
                    )}
                  </Space>
                </div>
                {apiKeyListContent}
              </section>
            </section>

            <section className="manual-zone manual-zone--submit" aria-labelledby="manual-zone-submit-title">
              <header className="manual-zone-header">
                <span className="manual-zone-step">3</span>
                <Typography.Text strong id="manual-zone-submit-title">执行变更</Typography.Text>
              </header>

              <div className="ant-field manual-reason-field">
                <Typography.Text strong>Reason</Typography.Text>
                <Input.TextArea
                  rows={5}
                  value={reason}
                  placeholder="变更原因"
                  onChange={(event) => setReason(event.target.value)}
                />
              </div>

              {status.message ? (
                <Alert
                  className="manual-status-alert"
                  showIcon
                  type={status.tone === "error" ? "error" : status.tone === "success" ? "success" : "info"}
                  message={status.message}
                />
              ) : null}

              <AntButton
                className="manual-run-button"
                type="primary"
                htmlType="button"
                icon={<SendOutlined />}
                loading={submitting}
                disabled={loading || targetGroups.length === 0}
                onClick={() => void runExistingOrchestration()}
                block
              >
                {mode === "api_key" && selectedKeyIds.length > 1
                  ? `执行编排（${selectedKeyIds.length} 个 Key）`
                  : "执行编排"}
              </AntButton>
            </section>
          </form>
        ) : (
          <DynamicOrchestrationView
            onAuthExpired={onAuthExpired}
            onRunRecorded={() => setRecordsRefreshSignal((value) => value + 1)}
          />
        )}
      </section>

      <section className="orchestration-canvas-shell">
        <div className="canvas-title-row">
          <div className="canvas-meta-row">
            <Typography.Text type="secondary">Graph Canvas</Typography.Text>
            <div className="canvas-subtitle-tags">
              <Tag color="processing">左到右：Key / 用户 / 分组</Tag>
              <Tag color="default">全局关系</Tag>
            </div>
          </div>
          <Space wrap>
            <AntButton icon={<SyncOutlined />} onClick={refreshGraphLayout}>
              刷新布局
            </AntButton>
          </Space>
        </div>
        <div className="flow-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={(_, node) => selectGraphEntity(node.data.userId, node.data.keyId)}
            onInit={setFlowInstance}
            fitView
            minZoom={0.55}
            maxZoom={1.35}
            nodesDraggable
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>
      </section>

      <RunRecordsPanel
        className="orchestration-records-island"
        onAuthExpired={onAuthExpired}
        refreshSignal={recordsRefreshSignal}
        onStatus={setStatus}
      />
    </div>
  );
}

function GraphNode({
  icon,
  title,
  subtitle,
  tone,
  tag
}: {
  icon: ReactNode;
  title: string;
  subtitle: string;
  tone: "user" | "active" | "source" | "target" | "neutral";
  tag?: string;
}) {
  const tagColor =
    tone === "target" ? "green" : tone === "source" ? "gold" : tone === "active" ? "blue" : "default";
  return (
    <div className={`graph-node graph-node-${tone}`}>
      <span className="graph-node-icon">{icon}</span>
      <div className="graph-node-copy">
        <strong>{title}</strong>
        <small>{subtitle}</small>
      </div>
      {tag ? <Tag color={tagColor}>{tag}</Tag> : null}
    </div>
  );
}

function RunRecordsPanel({
  className,
  onAuthExpired,
  refreshSignal = 0,
  onStatus
}: RunRecordsPanelProps) {
  const [records, setRecords] = useState<AutoRotationRunPayload[]>([]);
  const [selectedRecord, setSelectedRecord] = useState<AutoRotationRunPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [rollbackRunId, setRollbackRunId] = useState<string | null>(null);

  async function loadRecords() {
    setLoading(true);
    try {
      const payload = await requestJson<AutoRotationRunsPayload>(
        "/rotation/auto/runs?limit=20",
        { method: "GET" },
        "加载运行记录失败"
      );
      setRecords(payload.items);
    } catch (error: unknown) {
      if (!onAuthExpired(error, onStatus)) {
        onStatus?.({ message: getErrorMessage(error, "加载运行记录失败"), tone: "error" });
      }
    } finally {
      setLoading(false);
    }
  }

  async function rollbackRun(runId: string) {
    setRollbackRunId(runId);
    onStatus?.({ message: "正在回滚动态编排记录", tone: "info" });
    try {
      const payload = await requestJson<AutoRotationRunPayload>(
        `/rotation/auto/runs/${encodeURIComponent(runId)}/rollback`,
        { method: "POST" },
        "回滚失败"
      );
      setSelectedRecord(payload);
      await loadRecords();
      const failed = payload.rollback_results?.filter((item) => item.status === "failed").length ?? 0;
      onStatus?.({
        message: failed > 0 ? `回滚完成，但失败 ${failed} 项` : "回滚完成",
        tone: failed > 0 ? "error" : "success"
      });
    } catch (error: unknown) {
      if (!onAuthExpired(error, onStatus)) {
        onStatus?.({ message: getErrorMessage(error, "回滚失败"), tone: "error" });
      }
    } finally {
      setRollbackRunId(null);
    }
  }

  useEffect(() => {
    void loadRecords();
  }, [refreshSignal]);

  const stats = useMemo(() => {
    const rollbackable = records.filter((record) => {
      const counts = runCounts(record);
      return record.run_kind !== "manual" && !record.dry_run && counts.moved > 0 && !record.rollback_status;
    }).length;
    return {
      total: records.length,
      manual: records.filter((record) => record.run_kind === "manual").length,
      automatic: records.filter((record) => record.run_kind !== "manual").length,
      rollbackable
    };
  }, [records]);

  return (
    <section
      className={`run-records-island ${className ?? ""}`.trim()}
      aria-labelledby="run-records-title"
    >
      <header className="run-records-header">
        <div className="run-records-heading">
          <span className="run-records-heading-icon" aria-hidden="true">
            <ListChecks size={18} />
          </span>
          <div>
            <Typography.Text strong id="run-records-title">运行记录</Typography.Text>
            <Typography.Text type="secondary">手动 / 动态 / 回滚</Typography.Text>
          </div>
        </div>
        <Space wrap className="run-records-toolbar">
          <Tag color={loading ? "processing" : "default"}>{loading ? "同步中" : "最近 20 条"}</Tag>
          <AntButton size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void loadRecords()}>
            刷新
          </AntButton>
        </Space>
      </header>
      <div className="run-records-summary" aria-label="运行记录摘要">
        <div className="run-record-stat run-record-stat--total">
          <span>全部</span>
          <strong>{stats.total}</strong>
        </div>
        <div className="run-record-stat run-record-stat--manual">
          <span>手动</span>
          <strong>{stats.manual}</strong>
        </div>
        <div className="run-record-stat run-record-stat--automatic">
          <span>自动</span>
          <strong>{stats.automatic}</strong>
        </div>
        <div className="run-record-stat run-record-stat--rollback">
          <span>可回滚</span>
          <strong>{stats.rollbackable}</strong>
        </div>
      </div>
      <div className="run-record-list-shell">
        {records.length === 0 ? (
          <Empty description={loading ? "正在加载运行记录" : "暂无运行记录"} />
        ) : (
          <div className="run-record-list" role="list">
            {records.map((run) => {
              const counts = runCounts(run);
              const canRollback =
                run.run_kind !== "manual" && !run.dry_run && counts.moved > 0 && !run.rollback_status;
              const runKey = run.run_id ?? `${run.created_at ?? "record"}-${run.tag ?? run.status ?? "unknown"}`;
              return (
                <article
                  key={runKey}
                  className="run-record-row"
                  role="listitem"
                >
                  <span className={`run-record-marker run-record-marker--${run.run_kind === "manual" ? "manual" : "automatic"}`} />
                  <div className="run-record-main">
                    <div className="run-record-title-line">
                      <Typography.Text strong>{runKindLabel(run)}</Typography.Text>
                      <Tag color={runKindColor(run)}>{run.run_kind === "manual" ? "手动" : "自动"}</Tag>
                      <Tag color={run.status === "failed" ? "red" : "default"}>{run.status ?? "-"}</Tag>
                      {run.rollback_status ? <Tag color="gold">{run.rollback_status}</Tag> : null}
                    </div>
                    <div className="run-record-meta-line">
                      <span>{run.created_at ? formatDate(run.created_at) : "-"}</span>
                      <span>Run {run.run_id?.slice(0, 8) ?? "-"}</span>
                      {run.window ? <span>{run.window}</span> : null}
                    </div>
                  </div>
                  <div className="run-record-counts" aria-label="执行结果">
                    <span><strong>{counts.moved}</strong> 迁移</span>
                    <span><strong>{counts.planned}</strong> 计划</span>
                    <span><strong>{counts.skipped}</strong> 跳过</span>
                    <span className={counts.failed > 0 ? "run-record-count-danger" : ""}>
                      <strong>{counts.failed}</strong> 失败
                    </span>
                  </div>
                  <div className="run-record-actions">
                    <AntButton
                      size="small"
                      icon={<Eye size={15} aria-hidden="true" />}
                      onClick={() => setSelectedRecord(run)}
                    >
                      查看
                    </AntButton>
                    <AntButton
                      size="small"
                      danger
                      icon={<TimerReset size={15} aria-hidden="true" />}
                      loading={rollbackRunId === run.run_id}
                      disabled={!run.run_id || !canRollback || rollbackRunId !== null}
                      onClick={() => run.run_id && void rollbackRun(run.run_id)}
                    >
                      回滚
                    </AntButton>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
      <Modal
        title="运行记录详情"
        open={Boolean(selectedRecord)}
        width={760}
        footer={null}
        onCancel={() => setSelectedRecord(null)}
      >
        {selectedRecord ? (
          <div className="run-record-detail">
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="Run ID" span={2}>{selectedRecord.run_id}</Descriptions.Item>
              <Descriptions.Item label="类型">{runKindLabel(selectedRecord)}</Descriptions.Item>
              <Descriptions.Item label="状态">{selectedRecord.status ?? "-"}</Descriptions.Item>
              <Descriptions.Item label="用量窗口">{selectedRecord.window ?? "-"}</Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {selectedRecord.created_at ? formatDate(selectedRecord.created_at) : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="回滚状态">{selectedRecord.rollback_status ?? "-"}</Descriptions.Item>
              <Descriptions.Item label="回滚原因">{selectedRecord.rollback_reason ?? "-"}</Descriptions.Item>
            </Descriptions>
            <div className="run-record-summary">
              {(() => {
                const counts = runCounts(selectedRecord);
                return (
                  <>
                    <Tag color="blue">计划 {counts.planned}</Tag>
                    <Tag color="green">迁移 {counts.moved}</Tag>
                    <Tag>跳过 {counts.skipped}</Tag>
                    <Tag color={counts.failed > 0 ? "red" : "default"}>失败 {counts.failed}</Tag>
                  </>
                );
              })()}
            </div>
            <pre className="drawer-payload">{formatPayload(selectedRecord)}</pre>
          </div>
        ) : null}
      </Modal>
    </section>
  );
}

function DynamicOrchestrationView({
  onAuthExpired,
  onRunRecorded
}: {
  onAuthExpired: (error: unknown, setStatus?: (status: StatusState) => void) => boolean;
  onRunRecorded: () => void;
}) {
  const [candidates, setCandidates] = useState<RotationPoolCandidate[]>([]);
  const [config, setConfig] = useState<AutoRotationConfig>({
    enabled: false,
    auto_assign_new_users: false,
    cooldown_minutes: 0,
    usage_window: "1d",
    usage_thresholds: [],
    imbalance_epsilon: 0,
    improvement_delta: 0,
    schedule_source_group_ids: []
  });
  const [status, setStatus] = useState<StatusState>(emptyStatus);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState<"preview" | "run" | null>(null);
  const [selectedPoolCandidateIds, setSelectedPoolCandidateIds] = useState<string[]>([]);

  const selectedGroups = candidates.filter((group) => group.rotation_selected || group.selected);
  const selectedLandingGroups = candidates.filter((group) => group.landing_selected);
  const supportedGroups = candidates.filter((group) => group.rotation_supported);
  const selectedPoolCandidates = supportedGroups.filter((group) =>
    selectedPoolCandidateIds.includes(idValue(group.group_id))
  );
  const landingPoolCandidates = selectedPoolCandidates.filter((group) => !group.landing_selected);
  const rotationPoolCandidates = selectedPoolCandidates.filter(
    (group) => !(group.rotation_selected || group.selected)
  );
  const poolCandidateOptions = supportedGroups.map((group) =>
    buildGroupOption({
      group_id: group.group_id,
      name: group.name,
      group_kind: group.group_kind,
      platform: group.platform,
      status: group.status,
      is_exclusive: group.is_exclusive,
      is_subscription: group.is_subscription,
      rotation_supported: group.rotation_supported,
      unsupported_reason: group.unsupported_reason
    })
  );
  async function loadDynamicConfig() {
    setLoading(true);
    try {
      const [poolPayload, configPayload] = await Promise.all([
        requestJson<RotationPoolCandidatesPayload>("/rotation/pool/candidates", { method: "GET" }, "加载轮转池失败"),
        requestJson<AutoRotationConfigPayload>("/rotation/auto/config", { method: "GET" }, "加载动态配置失败")
      ]);
      setCandidates(poolPayload.items);
      const supportedIds = new Set(
        poolPayload.items
          .filter((group) => group.rotation_supported)
          .map((group) => idValue(group.group_id))
      );
      setSelectedPoolCandidateIds((current) => current.filter((id) => supportedIds.has(id)));
      setConfig(configPayload.config);
      setStatus({ message: `已加载 ${poolPayload.items.length} 个分组候选`, tone: "success" });
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setStatus({ message: getErrorMessage(error, "加载动态配置失败"), tone: "error" });
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDynamicConfig();
  }, []);

  async function saveConfig(nextConfig = config) {
    setSaving(true);
    try {
      const payload = await requestJson<AutoRotationConfigPayload>(
        "/rotation/auto/config",
        {
          method: "PUT",
          body: JSON.stringify({
            ...nextConfig,
            schedule_source_group_ids: [],
            usage_thresholds: []
          })
        },
        "保存动态配置失败"
      );
      setConfig(payload.config);
      setStatus({ message: "动态配置已保存", tone: "success" });
      return true;
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setStatus({ message: getErrorMessage(error, "保存动态配置失败"), tone: "error" });
      }
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function togglePoolGroup(group: RotationPoolCandidate, poolKind: "landing" | "rotation") {
    const selected = poolKind === "landing" ? group.landing_selected : group.rotation_selected || group.selected;
    const poolSize = poolKind === "landing" ? selectedLandingGroups.length : selectedGroups.length;
    setSaving(true);
    try {
      if (selected) {
        await requestJson<ApiPayload>(
          `/rotation/pool/groups/${encodeURIComponent(idValue(group.group_id))}?pool_kind=${poolKind}`,
          { method: "DELETE" },
          poolKind === "landing" ? "移出 Landing 池失败" : "移出轮转池失败"
        );
      } else {
        await requestJson<ApiPayload>(
          "/rotation/pool/groups",
          {
            method: "POST",
            body: JSON.stringify({
              group_id: group.group_id,
              pool_kind: poolKind,
              priority: poolSize
            })
          },
          poolKind === "landing" ? "加入 Landing 池失败" : "加入轮转池失败"
        );
      }
      await loadDynamicConfig();
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setStatus({ message: getErrorMessage(error, "更新池配置失败"), tone: "error" });
      }
    } finally {
      setSaving(false);
    }
  }

  async function addPoolGroups(groupsToAdd: RotationPoolCandidate[], poolKind: "landing" | "rotation") {
    if (groupsToAdd.length === 0) {
      return;
    }
    const poolSize = poolKind === "landing" ? selectedLandingGroups.length : selectedGroups.length;
    setSaving(true);
    try {
      await Promise.all(
        groupsToAdd.map((group, index) =>
          requestJson<ApiPayload>(
            "/rotation/pool/groups",
            {
              method: "POST",
              body: JSON.stringify({
                group_id: group.group_id,
                pool_kind: poolKind,
                priority: poolSize + index
              })
            },
            poolKind === "landing" ? "加入 Landing 池失败" : "加入轮转池失败"
          )
        )
      );
      const addedIds = new Set(groupsToAdd.map((group) => idValue(group.group_id)));
      setSelectedPoolCandidateIds((current) => current.filter((id) => !addedIds.has(id)));
      await loadDynamicConfig();
      setStatus({
        message: `${poolKind === "landing" ? "Landing 池" : "轮转池"}已加入 ${groupsToAdd.length} 个分组`,
        tone: "success"
      });
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setStatus({ message: getErrorMessage(error, "更新池配置失败"), tone: "error" });
      }
    } finally {
      setSaving(false);
    }
  }

  const renderSelectedPoolList = (
    groups: RotationPoolCandidate[],
    poolKind: "landing" | "rotation"
  ) => (
    <List
      className="dynamic-selected-pool-list"
      dataSource={groups}
      locale={{ emptyText: poolKind === "landing" ? "Landing 池为空" : "轮转池为空" }}
      renderItem={(group) => (
        <List.Item
          actions={[
            <AntButton
              key="remove"
              size="small"
              disabled={saving}
              onClick={() => void togglePoolGroup(group, poolKind)}
            >
              移出
            </AntButton>
          ]}
        >
          <List.Item.Meta
            avatar={poolKind === "landing" ? <UserOutlined /> : <ClusterOutlined />}
            title={group.name || unknownToText(group.group_id)}
            description={`Group ${unknownToText(group.group_id)} / Priority ${
              poolKind === "landing" ? group.landing_priority ?? "-" : group.priority ?? "-"
            }`}
          />
        </List.Item>
      )}
    />
  );

  async function runDynamic(dryRun: boolean) {
    const saved = await saveConfig();
    if (!saved) {
      return;
    }
    setRunning(dryRun ? "preview" : "run");
    setStatus({ message: dryRun ? "正在预览动态编排" : "正在执行动态编排", tone: "info" });
    try {
      const payload = await requestJson<AutoRotationRunPayload>(
        "/rotation/auto/run",
        {
          method: "POST",
          body: JSON.stringify({ dry_run: dryRun })
        },
        dryRun ? "动态编排预览失败" : "动态编排执行失败"
      );
      onRunRecorded();
      const failed = payload.failed?.length ?? 0;
      setStatus({
        message: dryRun
          ? `预览完成：计划 ${payload.planned?.length ?? 0}，跳过 ${payload.skipped?.length ?? 0}，失败 ${failed}`
          : `执行完成：迁移 ${payload.moved?.length ?? 0}，跳过 ${payload.skipped?.length ?? 0}，失败 ${failed}`,
        tone: failed > 0 ? "error" : "success"
      });
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setStatus({ message: getErrorMessage(error, dryRun ? "动态编排预览失败" : "动态编排执行失败"), tone: "error" });
      }
    } finally {
      setRunning(null);
    }
  }

  return (
    <div className="dynamic-grid">
      <Card
        title={
          <Space>
            <ClusterOutlined />
            <span>池配置</span>
          </Space>
        }
        extra={<AntButton icon={<ReloadOutlined />} loading={loading} onClick={() => void loadDynamicConfig()}>刷新</AntButton>}
      >
        <div className="dynamic-pool-builder">
          <div className="ant-field">
            <Typography.Text strong>候选分组</Typography.Text>
            <Select
              mode="multiple"
              value={selectedPoolCandidateIds}
              placeholder="搜索并选择一个或多个专属标准分组"
              showSearch
              allowClear
              loading={loading}
              optionFilterProp="searchText"
              onChange={(values) => setSelectedPoolCandidateIds(values)}
              options={poolCandidateOptions}
              optionRender={renderGroupOption}
              maxTagCount="responsive"
              notFoundContent={loading ? <Spin size="small" /> : "暂无可用候选"}
            />
          </div>
          <Space wrap>
            <AntButton
              type="primary"
              disabled={landingPoolCandidates.length === 0 || saving}
              onClick={() => void addPoolGroups(landingPoolCandidates, "landing")}
            >
              加入 Landing 池{landingPoolCandidates.length ? `（${landingPoolCandidates.length}）` : ""}
            </AntButton>
            <AntButton
              type="primary"
              disabled={rotationPoolCandidates.length === 0 || saving}
              onClick={() => void addPoolGroups(rotationPoolCandidates, "rotation")}
            >
              加入轮转池{rotationPoolCandidates.length ? `（${rotationPoolCandidates.length}）` : ""}
            </AntButton>
          </Space>
        </div>

        <div className="dynamic-pool-columns">
          <section className="dynamic-pool-section">
            <div className="dynamic-pool-section-header">
              <Typography.Text strong>Landing 池</Typography.Text>
              <Tag color="blue">{selectedLandingGroups.length}</Tag>
            </div>
            <Typography.Text type="secondary">
              新用户或 managed-pool provisioning 的默认落点。自动分配只会从这里接入。
            </Typography.Text>
            {renderSelectedPoolList(selectedLandingGroups, "landing")}
          </section>

          <section className="dynamic-pool-section">
            <div className="dynamic-pool-section-header">
              <Typography.Text strong>轮转池</Typography.Text>
              <Tag color="green">{selectedGroups.length}</Tag>
            </div>
            <Typography.Text type="secondary">
              已接入用户的动态轮转目标。执行时会把所选时间窗口内的用量尽量摊平到这些组里。
            </Typography.Text>
            {renderSelectedPoolList(selectedGroups, "rotation")}
          </section>
        </div>
      </Card>

      <Card
        title={
          <Space>
            <SyncOutlined />
            <span>动态配置</span>
          </Space>
        }
      >
        <div className="dynamic-config-form">
          <div className="dynamic-config-row">
            <Typography.Text strong>启用执行</Typography.Text>
            <AntSegmented
              value={config.enabled ? "on" : "off"}
              onChange={(value) => setConfig((current) => ({ ...current, enabled: value === "on" }))}
              options={[
                { label: "关闭", value: "off" },
                { label: "开启", value: "on" }
              ]}
            />
          </div>
          <div className="dynamic-config-row">
            <Typography.Text strong>自动分配新用户</Typography.Text>
            <AntSegmented
              value={config.auto_assign_new_users ? "on" : "off"}
              onChange={(value) => setConfig((current) => ({ ...current, auto_assign_new_users: value === "on" }))}
              options={[
                { label: "关闭", value: "off" },
                { label: "开启", value: "on" }
              ]}
            />
          </div>
          <div className="dynamic-config-row dynamic-config-row--stacked">
            <Space>
              <TimerReset size={16} aria-hidden="true" />
              <Typography.Text strong>用量窗口</Typography.Text>
            </Space>
            <AntSegmented
              value={config.usage_window}
              onChange={(value) =>
                setConfig((current) => ({
                  ...current,
                  usage_window: value as AutoRotationConfig["usage_window"]
                }))
              }
              options={[...usageWindowOptions]}
            />
            <Typography.Text type="secondary">
              动态编排会按这个时间范围汇总用户 API Key 用量，并把轮转池各组的总用量尽量拉平。
            </Typography.Text>
          </div>
          <div className="ant-field">
            <Typography.Text strong>Cooldown Minutes</Typography.Text>
            <Input
              type="number"
              min={0}
              value={config.cooldown_minutes}
              onChange={(event) => setConfig((current) => ({
                ...current,
                cooldown_minutes: Math.max(0, Number(event.target.value) || 0)
              }))}
            />
            <Typography.Text type="secondary">
              用户刚被搬过之后这段时间内不会再动他。0 表示不设。
            </Typography.Text>
          </div>
          <div className="ant-field">
            <Typography.Text strong>Imbalance Epsilon (ε)</Typography.Text>
            <Input
              type="number"
              min={0}
              step={0.1}
              value={config.imbalance_epsilon}
              onChange={(event) => setConfig((current) => ({
                ...current,
                imbalance_epsilon: Math.max(0, Number(event.target.value) || 0)
              }))}
            />
            <Typography.Text type="secondary">
              各组用量差小于这个值就当作已平衡，本轮不动任何人。0 表示不启用。
            </Typography.Text>
          </div>
          <div className="ant-field">
            <Typography.Text strong>Improvement Delta (δ)</Typography.Text>
            <Input
              type="number"
              min={0}
              step={0.1}
              value={config.improvement_delta}
              onChange={(event) => setConfig((current) => ({
                ...current,
                improvement_delta: Math.max(0, Number(event.target.value) || 0)
              }))}
            />
            <Typography.Text type="secondary">
              迁完后差距至少再缩小这么多才动手，防止反复横跳。0 表示不启用。
            </Typography.Text>
          </div>
          <div className="dynamic-allocation-summary">
            <Tag color={config.enabled ? "green" : "default"}>{config.enabled ? "允许执行" : "仅配置/预览"}</Tag>
            <Tag color={config.auto_assign_new_users ? "green" : "default"}>{config.auto_assign_new_users ? "自动分配开启" : "自动分配关闭"}</Tag>
            <Tag color="blue">Landing {selectedLandingGroups.length}</Tag>
            <Tag color="processing">Rotation {selectedGroups.length}</Tag>
            <Tag color="green">按用量均衡</Tag>
            <Tag color="gold">{usageWindowOptions.find((item) => item.value === config.usage_window)?.label}</Tag>
          </div>
          {config.auto_assign_new_users && selectedLandingGroups.length === 0 ? (
            <Alert
              showIcon
              type="warning"
              message="自动分配新用户需要先配置 Landing 池；空 Landing 池不会自动接入任何用户。"
            />
          ) : null}
          {status.message ? (
            <Alert
              className="operator-status-alert"
              showIcon
              type={status.tone === "error" ? "error" : status.tone === "success" ? "success" : "info"}
              message={status.message}
            />
          ) : null}
          <Space wrap>
            <AntButton
              type="primary"
              icon={<SendOutlined />}
              loading={saving}
              onClick={() => void saveConfig()}
            >
              保存配置
            </AntButton>
            <AntButton
              icon={<NodeIndexOutlined />}
              loading={running === "preview"}
              disabled={running !== null || loading || selectedGroups.length === 0}
              onClick={() => void runDynamic(true)}
            >
              预览动态编排
            </AntButton>
            <AntButton
              danger
              icon={<SyncOutlined />}
              loading={running === "run"}
              disabled={running !== null || loading || selectedGroups.length === 0 || !config.enabled}
              onClick={() => void runDynamic(false)}
            >
              执行动态编排
            </AntButton>
          </Space>
        </div>
      </Card>

    </div>
  );
}

function summarizeReasons(items?: RotationExecutionPayload[]): RunReasonSummary[] {
  const counts = new Map<string, number>();
  for (const item of items ?? []) {
    const reason = item.reason?.trim() || "未提供原因";
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([reason, count]) => ({ reason, count }))
    .sort((left, right) => right.count - left.count || left.reason.localeCompare(right.reason));
}
function severityLabel(severity: NotificationSeverity): string {
  if (severity === "critical") return "Critical";
  if (severity === "warning") return "Warning";
  return "Info";
}

function severityColor(severity: NotificationSeverity): string {
  if (severity === "critical") return "red";
  if (severity === "warning") return "gold";
  return "blue";
}

function operatorLabel(operator: NotificationRuleOperator): string {
  const labels: Record<NotificationRuleOperator, string> = {
    gt: ">",
    gte: ">=",
    lt: "<",
    lte: "<=",
    eq: "=",
    neq: "!="
  };
  return labels[operator];
}

function NotificationSettingsPanel() {
  const [settings, setSettings] = useState<NotificationSettings>(() => {
    const saved = window.localStorage.getItem("sub2api-notification-settings");
    if (saved) {
      try {
        const parsed = JSON.parse(saved) as Partial<NotificationSettings>;
        const webhooks = Array.isArray(parsed.webhooks) && parsed.webhooks.length > 0
          ? parsed.webhooks.map((webhook, index) => ({
            id: webhook.id || `webhook-${index + 1}`,
            name: webhook.name || `Webhook ${index + 1}`,
            enabled: Boolean(webhook.enabled),
            provider: (webhookProviderOptions.some((option) => option.value === webhook.provider)
              ? webhook.provider
              : "generic") as WebhookProvider,
            url: webhook.url || "",
            secret: webhook.secret || "",
            mentionOnFailure: webhook.mentionOnFailure ?? true
          }))
          : defaultNotificationSettings.webhooks;
        if (webhooks.length > 0) {
          const rules = Array.isArray(parsed.rules) && parsed.rules.length > 0
            ? parsed.rules.map((rule, index) => {
              const signal = notificationSignalByKey.get(rule.signalKey || defaultNotificationSignalKeys[0]);
              return {
                id: rule.id || `rule-${index + 1}`,
                name: rule.name || signal?.label || `规则 ${index + 1}`,
                enabled: rule.enabled ?? true,
                signalKey: rule.signalKey || signal?.key || defaultNotificationSignalKeys[0],
                severity: rule.severity || signal?.defaultSeverity || "warning",
                operator: rule.operator || signal?.defaultOperator || "gte",
                threshold: rule.threshold || signal?.defaultThreshold || "1",
                warningThreshold: rule.warningThreshold || signal?.defaultThreshold || "",
                recoveryThreshold: rule.recoveryThreshold || "",
                thresholdUnit: rule.thresholdUnit || signal?.unit || "",
                aggregation: rule.aggregation || signal?.defaultAggregation || "latest",
                readIntervalMinutes: Number(rule.readIntervalMinutes) || signal?.defaultReadIntervalMinutes || 10,
                evaluationWindowMinutes: Number(rule.evaluationWindowMinutes) || signal?.defaultEvaluationWindowMinutes || 30,
                forMinutes: Number(rule.forMinutes) || 5,
                cooldownMinutes: Number(rule.cooldownMinutes) || 60,
                targetWebhookIds: Array.isArray(rule.targetWebhookIds) ? rule.targetWebhookIds : [webhooks[0].id],
                includeResolved: rule.includeResolved ?? true,
                includeSnapshot: rule.includeSnapshot ?? true
              };
            })
            : defaultNotificationSettings.rules.map((rule) => ({
              ...rule,
              targetWebhookIds: [webhooks[0].id]
            }));
          return {
            webhooks,
            rules,
            policy: {
              ...defaultNotificationSettings.policy,
              ...(parsed.policy ?? {})
            }
          };
        }
      } catch {
        return defaultNotificationSettings;
      }
    }
    return defaultNotificationSettings;
  });
  const [selectedWebhookId, setSelectedWebhookId] = useState(() => defaultNotificationSettings.webhooks[0].id);
  const [selectedRuleId, setSelectedRuleId] = useState(() => defaultNotificationSettings.rules[0]?.id ?? "");
  const [status, setStatus] = useState<StatusState>(emptyStatus);
  const selectedWebhook =
    settings.webhooks.find((webhook) => webhook.id === selectedWebhookId) ?? settings.webhooks[0];
  const selectedRule = settings.rules.find((rule) => rule.id === selectedRuleId) ?? settings.rules[0];
  const selectedRuleSignal = selectedRule ? notificationSignalByKey.get(selectedRule.signalKey) : null;
  const enabledWebhookCount = settings.webhooks.filter((webhook) => webhook.enabled).length;
  const enabledRuleCount = settings.rules.filter((rule) => rule.enabled).length;
  const configuredSignalCount = new Set(settings.rules.map((rule) => rule.signalKey)).size;

  function updateWebhook(webhookId: string, partial: Partial<NotificationWebhook>) {
    setSettings((current) => ({
      ...current,
      webhooks: current.webhooks.map((webhook) =>
        webhook.id === webhookId ? { ...webhook, ...partial } : webhook
      )
    }));
  }

  function addWebhook() {
    const nextId = `webhook-${Date.now()}`;
    setSettings((current) => ({
      ...current,
      webhooks: [
        ...current.webhooks,
        {
          id: nextId,
          name: `Webhook ${current.webhooks.length + 1}`,
          enabled: false,
          provider: "generic",
          url: "",
          secret: "",
          mentionOnFailure: true
        }
      ]
    }));
    setSelectedWebhookId(nextId);
  }

  function removeSelectedWebhook() {
    if (settings.webhooks.length <= 1 || !selectedWebhook) {
      setStatus({ message: "至少保留一个 Webhook。", tone: "error" });
      return;
    }
    const remaining = settings.webhooks.filter((webhook) => webhook.id !== selectedWebhook.id);
    setSettings((current) => ({
      ...current,
      webhooks: remaining,
      rules: current.rules.map((rule) => ({
        ...rule,
        targetWebhookIds: rule.targetWebhookIds.filter((id) => id !== selectedWebhook.id)
      }))
    }));
    setSelectedWebhookId(remaining[0].id);
  }

  function updateRule(ruleId: string, partial: Partial<NotificationRule>) {
    setSettings((current) => ({
      ...current,
      rules: current.rules.map((rule) => (rule.id === ruleId ? { ...rule, ...partial } : rule))
    }));
  }

  function addRule(signalKey = "account_invalid") {
    const signal = notificationSignalByKey.get(signalKey) ?? notificationSignals[0];
    const nextId = `rule-${Date.now()}`;
    const targetWebhookId = selectedWebhook?.id ?? settings.webhooks[0]?.id ?? "";
    setSettings((current) => ({
      ...current,
      rules: [
        ...current.rules,
        {
          id: nextId,
          name: signal.label,
          enabled: true,
          signalKey: signal.key,
          severity: signal.defaultSeverity ?? "warning",
          operator: signal.defaultOperator ?? "gte",
          threshold: signal.defaultThreshold ?? "1",
          warningThreshold: signal.defaultThreshold ?? "",
          recoveryThreshold: "",
          thresholdUnit: signal.unit ?? "",
          aggregation: signal.defaultAggregation ?? "latest",
          readIntervalMinutes: signal.defaultReadIntervalMinutes ?? 10,
          evaluationWindowMinutes: signal.defaultEvaluationWindowMinutes ?? 30,
          forMinutes: 5,
          cooldownMinutes: 60,
          targetWebhookIds: targetWebhookId ? [targetWebhookId] : [],
          includeResolved: true,
          includeSnapshot: true
        }
      ]
    }));
    setSelectedRuleId(nextId);
  }

  function removeSelectedRule() {
    if (settings.rules.length <= 1 || !selectedRule) {
      setStatus({ message: "至少保留一条告警规则。", tone: "error" });
      return;
    }
    const remaining = settings.rules.filter((rule) => rule.id !== selectedRule.id);
    setSettings((current) => ({ ...current, rules: remaining }));
    setSelectedRuleId(remaining[0].id);
  }

  function updatePolicy(partial: Partial<NotificationRoutingPolicy>) {
    setSettings((current) => ({
      ...current,
      policy: { ...current.policy, ...partial }
    }));
  }

  function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    window.localStorage.setItem("sub2api-notification-settings", JSON.stringify(settings));
    setStatus({ message: "Webhook 信息权限已保存。", tone: "success" });
  }

  function sendTestNotification() {
    if (!selectedRule) {
      setStatus({ message: "请先选择一条告警规则。", tone: "error" });
      return;
    }
    const targetWebhooks = settings.webhooks.filter((webhook) => selectedRule.targetWebhookIds.includes(webhook.id));
    if (targetWebhooks.length === 0) {
      setStatus({ message: "请至少给当前规则选择一个 Webhook。", tone: "error" });
      return;
    }
    if (targetWebhooks.some((webhook) => !webhook.enabled || !webhook.url.trim())) {
      setStatus({ message: "当前规则包含未启用或未填写 URL 的 Webhook。", tone: "error" });
      return;
    }
    setStatus({ message: `${selectedRule.name} 测试通知已准备好，将发送到 ${targetWebhooks.length} 个 Webhook。`, tone: "info" });
  }

  return (
    <div className="notification-workspace">
      <section className="panel form-panel notification-panel">
        <div className="panel-title-row">
          <div>
            <p className="eyebrow">Alert Center</p>
            <h2>综合信息告警</h2>
          </div>
          <div className="action-row">
            <button className="button secondary compact" type="button" onClick={addWebhook}>
              <Plus size={17} aria-hidden="true" />
              新增 Webhook
            </button>
            <button className="button secondary compact" type="button" onClick={() => addRule()}>
              <Plus size={17} aria-hidden="true" />
              新增规则
            </button>
          </div>
        </div>

        <form className="form-stack" onSubmit={saveSettings}>
          <div className="notification-section-title">
            <div>
              <h3>Webhook 接收器</h3>
              <p>接收器只定义消息发到哪里；是否发送、阈值和频率由告警规则控制。</p>
            </div>
          </div>
          <div className="webhook-list">
            {settings.webhooks.map((webhook) => (
              <button
                key={webhook.id}
                className={`webhook-row ${webhook.id === selectedWebhook?.id ? "active" : ""}`}
                type="button"
                onClick={() => setSelectedWebhookId(webhook.id)}
              >
                <span>
                  <strong>{webhook.name || "未命名 Webhook"}</strong>
                  <small>{webhook.url || "未配置 URL"}</small>
                </span>
                <Tag color={webhook.enabled ? "green" : "default"}>
                  {settings.rules.filter((rule) => rule.targetWebhookIds.includes(webhook.id)).length} 条规则
                </Tag>
              </button>
            ))}
          </div>

          {selectedWebhook ? (
            <>
              <label className="switch-row">
                <input
                  type="checkbox"
                  checked={selectedWebhook.enabled}
                  onChange={(event) => updateWebhook(selectedWebhook.id, { enabled: event.target.checked })}
                />
                <span>
                  <strong>启用当前 Webhook</strong>
                  <small>只有启用后才会接收它被授权的信息集合。</small>
                </span>
              </label>

              <div className="form-grid-2">
                <label className="field">
                  <span>Webhook 名称</span>
                  <input
                    value={selectedWebhook.name}
                    placeholder="Ops / Finance / Account Alerts"
                    onChange={(event) => updateWebhook(selectedWebhook.id, { name: event.target.value })}
                  />
                </label>
                <label className="field">
                  <span>接收平台</span>
                  <select
                    value={selectedWebhook.provider}
                    onChange={(event) =>
                      updateWebhook(selectedWebhook.id, { provider: event.target.value as WebhookProvider })
                    }
                  >
                    {webhookProviderOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <label className="field">
                <span>Secret / 加签密钥</span>
                <input
                  value={selectedWebhook.secret}
                  placeholder={webhookSecretHints[selectedWebhook.provider]}
                  onChange={(event) => updateWebhook(selectedWebhook.id, { secret: event.target.value })}
                />
              </label>

              <label className="field">
                <span>Webhook URL</span>
                <input
                  type="url"
                  value={selectedWebhook.url}
                  placeholder="https://example.com/webhook"
                  onChange={(event) => updateWebhook(selectedWebhook.id, { url: event.target.value })}
                />
              </label>

              <label className="switch-row">
                <input
                  type="checkbox"
                  checked={selectedWebhook.mentionOnFailure}
                  onChange={(event) => updateWebhook(selectedWebhook.id, { mentionOnFailure: event.target.checked })}
                />
                <span>
                  <strong>失败类消息提醒负责人</strong>
                  <small>账号失效、限流、错误率突增、支付失败等消息可以追加负责人 mention。</small>
                </span>
              </label>
            </>
          ) : null}

          <div className="notification-section-title">
            <div>
              <h3>告警规则</h3>
              <p>为每类信息设置读取频率、计算窗口、阈值、持续时间和 Webhook 路由。</p>
            </div>
          </div>
          <div className="rule-layout">
            <div className="rule-list">
              {settings.rules.map((rule) => {
                const signal = notificationSignalByKey.get(rule.signalKey);
                return (
                  <button
                    className={`rule-row ${rule.id === selectedRule?.id ? "active" : ""}`}
                    key={rule.id}
                    type="button"
                    onClick={() => setSelectedRuleId(rule.id)}
                  >
                    <span>
                      <strong>{rule.name || signal?.label || "未命名规则"}</strong>
                      <small>
                        {signal?.label || rule.signalKey} · 每 {rule.readIntervalMinutes} 分钟读取 · {rule.targetWebhookIds.length} 个 Webhook
                      </small>
                    </span>
                    <Tag color={rule.enabled ? severityColor(rule.severity) : "default"}>
                      {rule.enabled ? severityLabel(rule.severity) : "停用"}
                    </Tag>
                  </button>
                );
              })}
            </div>

            {selectedRule ? (
              <section className="rule-editor">
                <div className="notification-group-header">
                  <div>
                    <h3>{selectedRule.name || selectedRuleSignal?.label || "告警规则"}</h3>
                    <p>{selectedRuleSignal?.description || "选择信息类型后配置阈值和投递策略。"}</p>
                  </div>
                  <label className="mini-toggle">
                    <input
                      type="checkbox"
                      checked={selectedRule.enabled}
                      onChange={(event) => updateRule(selectedRule.id, { enabled: event.target.checked })}
                    />
                    启用
                  </label>
                </div>

                <div className="form-grid-2">
                  <label className="field">
                    <span>规则名称</span>
                    <input
                      value={selectedRule.name}
                      onChange={(event) => updateRule(selectedRule.id, { name: event.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span>信息类型</span>
                    <select
                      value={selectedRule.signalKey}
                      onChange={(event) => {
                        const signal = notificationSignalByKey.get(event.target.value);
                        updateRule(selectedRule.id, {
                          signalKey: event.target.value,
                          name: signal?.label ?? selectedRule.name,
                          threshold: signal?.defaultThreshold ?? selectedRule.threshold,
                          warningThreshold: signal?.defaultThreshold ?? selectedRule.warningThreshold,
                          thresholdUnit: signal?.unit ?? "",
                          operator: signal?.defaultOperator ?? selectedRule.operator,
                          aggregation: signal?.defaultAggregation ?? selectedRule.aggregation,
                          severity: signal?.defaultSeverity ?? selectedRule.severity,
                          readIntervalMinutes: signal?.defaultReadIntervalMinutes ?? selectedRule.readIntervalMinutes,
                          evaluationWindowMinutes: signal?.defaultEvaluationWindowMinutes ?? selectedRule.evaluationWindowMinutes
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

                <div className="rule-control-grid">
                  <label className="field">
                    <span>读取频率</span>
                    <input
                      type="number"
                      min={1}
                      value={selectedRule.readIntervalMinutes}
                      onChange={(event) => updateRule(selectedRule.id, { readIntervalMinutes: Math.max(1, Number(event.target.value) || 1) })}
                    />
                    <small>分钟</small>
                  </label>
                  <label className="field">
                    <span>评估窗口</span>
                    <input
                      type="number"
                      min={1}
                      value={selectedRule.evaluationWindowMinutes}
                      onChange={(event) => updateRule(selectedRule.id, { evaluationWindowMinutes: Math.max(1, Number(event.target.value) || 1) })}
                    />
                    <small>分钟</small>
                  </label>
                  <label className="field">
                    <span>持续时间</span>
                    <input
                      type="number"
                      min={0}
                      value={selectedRule.forMinutes}
                      onChange={(event) => updateRule(selectedRule.id, { forMinutes: Math.max(0, Number(event.target.value) || 0) })}
                    />
                    <small>持续异常才发送</small>
                  </label>
                  <label className="field">
                    <span>重复间隔</span>
                    <input
                      type="number"
                      min={1}
                      value={selectedRule.cooldownMinutes}
                      onChange={(event) => updateRule(selectedRule.id, { cooldownMinutes: Math.max(1, Number(event.target.value) || 1) })}
                    />
                    <small>同一异常再次提醒</small>
                  </label>
                </div>

                <div className="rule-control-grid">
                  <label className="field">
                    <span>聚合方式</span>
                    <select
                      value={selectedRule.aggregation}
                      onChange={(event) => updateRule(selectedRule.id, { aggregation: event.target.value as NotificationRuleAggregation })}
                    >
                      <option value="latest">最新值</option>
                      <option value="avg">平均值</option>
                      <option value="max">最大值</option>
                      <option value="min">最小值</option>
                      <option value="sum">求和</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>条件</span>
                    <select
                      value={selectedRule.operator}
                      onChange={(event) => updateRule(selectedRule.id, { operator: event.target.value as NotificationRuleOperator })}
                    >
                      <option value="gte">大于等于</option>
                      <option value="gt">大于</option>
                      <option value="lte">小于等于</option>
                      <option value="lt">小于</option>
                      <option value="eq">等于</option>
                      <option value="neq">不等于</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>触发阈值</span>
                    <input
                      value={selectedRule.threshold}
                      onChange={(event) => updateRule(selectedRule.id, { threshold: event.target.value })}
                    />
                    <small>{selectedRule.thresholdUnit || "按信息类型定义"}</small>
                  </label>
                  <label className="field">
                    <span>恢复阈值</span>
                    <input
                      value={selectedRule.recoveryThreshold}
                      placeholder="可选"
                      onChange={(event) => updateRule(selectedRule.id, { recoveryThreshold: event.target.value })}
                    />
                    <small>达到该值后发送恢复</small>
                  </label>
                </div>

                <div className="form-grid-2">
                  <label className="field">
                    <span>严重等级</span>
                    <select
                      value={selectedRule.severity}
                      onChange={(event) => updateRule(selectedRule.id, { severity: event.target.value as NotificationSeverity })}
                    >
                      <option value="info">Info</option>
                      <option value="warning">Warning</option>
                      <option value="critical">Critical</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>目标 Webhook</span>
                    <select
                      multiple
                      value={selectedRule.targetWebhookIds}
                      onChange={(event) =>
                        updateRule(selectedRule.id, {
                          targetWebhookIds: Array.from(event.currentTarget.selectedOptions).map((option) => option.value)
                        })
                      }
                    >
                      {settings.webhooks.map((webhook) => (
                        <option value={webhook.id} key={webhook.id}>
                          {webhook.name || webhook.id}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <div className="notification-event-grid">
                  <label className="switch-row compact-switch-row">
                    <input
                      type="checkbox"
                      checked={selectedRule.includeResolved}
                      onChange={(event) => updateRule(selectedRule.id, { includeResolved: event.target.checked })}
                    />
                    <span>
                      <strong>发送恢复通知</strong>
                      <small>告警恢复时通知相同 webhook。</small>
                    </span>
                  </label>
                  <label className="switch-row compact-switch-row">
                    <input
                      type="checkbox"
                      checked={selectedRule.includeSnapshot}
                      onChange={(event) => updateRule(selectedRule.id, { includeSnapshot: event.target.checked })}
                    />
                    <span>
                      <strong>附带数据快照</strong>
                      <small>发送当前值、阈值、窗口、来源和对象。</small>
                    </span>
                  </label>
                </div>

                <div className="action-row">
                  <button className="button tertiary" type="button" onClick={removeSelectedRule}>
                    删除规则
                  </button>
                </div>
              </section>
            ) : null}
          </div>

          <section className="notification-signal-group">
            <div className="notification-group-header">
              <div>
                <h3>路由与降噪</h3>
                <p>控制多条告警如何合并、多久重复，以及是否在安静时段暂停发送。</p>
              </div>
            </div>
            <div className="rule-control-grid">
              <label className="field">
                <span>分组方式</span>
                <select
                  value={settings.policy.groupBy}
                  onChange={(event) => updatePolicy({ groupBy: event.target.value as NotificationRoutingPolicy["groupBy"] })}
                >
                  <option value="severity">按严重等级</option>
                  <option value="signal">按信息类型</option>
                  <option value="source">按数据来源</option>
                </select>
              </label>
              <label className="field">
                <span>合并等待</span>
                <input
                  type="number"
                  min={0}
                  value={settings.policy.groupWaitMinutes}
                  onChange={(event) => updatePolicy({ groupWaitMinutes: Math.max(0, Number(event.target.value) || 0) })}
                />
                <small>分钟内把相近告警合并发送</small>
              </label>
              <label className="field">
                <span>默认重复间隔</span>
                <input
                  type="number"
                  min={1}
                  value={settings.policy.repeatIntervalMinutes}
                  onChange={(event) => updatePolicy({ repeatIntervalMinutes: Math.max(1, Number(event.target.value) || 1) })}
                />
                <small>未被规则覆盖时使用</small>
              </label>
              <label className="switch-row compact-switch-row">
                <input
                  type="checkbox"
                  checked={settings.policy.quietHoursEnabled}
                  onChange={(event) => updatePolicy({ quietHoursEnabled: event.target.checked })}
                />
                <span>
                  <strong>启用安静时段</strong>
                  <small>非 critical 告警可以延迟到时段结束。</small>
                </span>
              </label>
            </div>
            <div className="form-grid-2">
              <label className="field">
                <span>安静时段开始</span>
                <input
                  type="time"
                  value={settings.policy.quietHoursStart}
                  onChange={(event) => updatePolicy({ quietHoursStart: event.target.value })}
                />
              </label>
              <label className="field">
                <span>安静时段结束</span>
                <input
                  type="time"
                  value={settings.policy.quietHoursEnd}
                  onChange={(event) => updatePolicy({ quietHoursEnd: event.target.value })}
                />
              </label>
            </div>
          </section>

          <div className="action-row">
            <button className="button primary" type="submit">
              <Save size={18} aria-hidden="true" />
              保存设置
            </button>
            <button className="button secondary" type="button" onClick={sendTestNotification}>
              <Send size={18} aria-hidden="true" />
              发送测试
            </button>
            <button className="button tertiary" type="button" onClick={removeSelectedWebhook}>
              删除当前
            </button>
          </div>
          <StatusLine status={status} />
        </form>
      </section>

      <section className="panel result-panel notification-summary-panel">
        <div className="panel-title-row">
          <div>
            <p className="eyebrow">Summary</p>
            <h2>当前配置</h2>
          </div>
        </div>
        <div className="summary-grid">
          <SummaryItem label="Webhook" value={`${settings.webhooks.length}`} />
          <SummaryItem label="已启用" value={`${enabledWebhookCount}`} />
          <SummaryItem label="启用规则" value={`${enabledRuleCount}`} />
          <SummaryItem label="信息类型" value={`${configuredSignalCount}`} />
        </div>
        <div className="notification-route-summary">
          {settings.webhooks.map((webhook) => {
            const providerLabel =
              webhookProviderOptions.find((option) => option.value === webhook.provider)?.label ?? "通用";
            return (
              <div className="notification-route-card" key={webhook.id}>
                <div>
                  <strong>{webhook.name || "未命名 Webhook"}</strong>
                  <small>
                    {providerLabel} · {webhook.enabled ? "已启用" : "未启用"} · {settings.rules.filter((rule) => rule.targetWebhookIds.includes(webhook.id)).length} 条规则
                  </small>
                </div>
                <code>{webhook.url || "未配置 URL"}</code>
              </div>
            );
          })}
        </div>
        <div className="notification-route-summary">
          {settings.rules.map((rule) => {
            const signal = notificationSignalByKey.get(rule.signalKey);
            return (
              <div className="notification-route-card" key={rule.id}>
                <div>
                  <strong>{rule.name || signal?.label || "未命名规则"}</strong>
                  <small>
                    {severityLabel(rule.severity)} · 每 {rule.readIntervalMinutes} 分钟 · {operatorLabel(rule.operator)} {rule.threshold}{rule.thresholdUnit ? ` ${rule.thresholdUnit}` : ""}
                  </small>
                </div>
                <code>{signal?.source || rule.signalKey}</code>
              </div>
            );
          })}
        </div>
        <div className="hint-box notification-preview">
          <span>Payload 范围</span>
          <code>platform_key / user / admin_ops / ai_account / payment / usage_anomaly</code>
        </div>
      </section>
    </div>
  );
}

function ProvisionForm({
  config,
  onAuthExpired,
  onFlowChanged
}: {
  config: UiConfig;
  onAuthExpired: (error: unknown, setStatus?: (status: StatusState) => void) => boolean;
  onFlowChanged: () => void;
}) {
  const [email, setEmail] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [startPayload, setStartPayload] = useState<ProvisionStartPayload | null>(null);
  const [completePayload, setCompletePayload] = useState<ApiPayload | null>(null);
  const [status, setStatus] = useState<StatusState>(emptyStatus);
  const [busyAction, setBusyAction] = useState<"start" | "complete" | null>(null);

  const oauthUrl = typeof startPayload?.oauth_url === "string" ? startPayload.oauth_url : "";
  const redirectUri =
    typeof startPayload?.oauth_redirect_uri === "string"
      ? startPayload.oauth_redirect_uri
      : config.oauth_redirect_uri;
  const visiblePayload = useMemo(() => completePayload ?? startPayload, [completePayload, startPayload]);

  async function startProvision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCompletePayload(null);

    if (!email.trim()) {
      setStatus({ message: "请先输入 email。", tone: "error" });
      return;
    }

    setBusyAction("start");
    setStatus({ message: "正在创建分组、用户并生成 OAuth 链接", tone: "info" });

    try {
      const payload = await requestJson<ProvisionStartPayload>("/provision/start", {
        method: "POST",
        body: JSON.stringify({ email: email.trim() })
      }, "请求失败");
      setStartPayload(payload);
      setStatus({ message: "创建成功。完成 OAuth 后粘贴 localhost 回调 URL。", tone: "success" });
      onFlowChanged();
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setStartPayload({ success: false, detail: getErrorMessage(error, "请求失败") });
        setStatus({ message: getErrorMessage(error, "请求失败"), tone: "error" });
      }
    } finally {
      setBusyAction(null);
    }
  }

  async function completeProvision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!callbackUrl.trim()) {
      setStatus({ message: "请先粘贴 localhost 回调地址。", tone: "error" });
      return;
    }

    setBusyAction("complete");
    setStatus({ message: "正在解析回调地址并完成 OAuth 绑定", tone: "info" });

    try {
      const payload = await requestJson<ApiPayload>("/provision/oauth/complete", {
        method: "POST",
        body: JSON.stringify({ callback_url: callbackUrl.trim() })
      }, "完成绑定失败");
      setCompletePayload(payload);
      setStatus({ message: "OAuth 绑定已完成。", tone: "success" });
      onFlowChanged();
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setCompletePayload({ success: false, detail: getErrorMessage(error, "完成绑定失败") });
        setStatus({ message: getErrorMessage(error, "完成绑定失败"), tone: "error" });
      }
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <div className="workspace">
      <section className="panel form-panel">
        <form className="form-stack" onSubmit={startProvision}>
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              placeholder="user@example.com"
              autoComplete="email"
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <div className="action-row">
            <button className="button primary" type="submit" disabled={busyAction === "start"}>
              {busyAction === "start" ? (
                <LoaderCircle className="spin" size={18} aria-hidden="true" />
              ) : (
                <Play size={18} aria-hidden="true" />
              )}
              开始创建
            </button>
            <a
              className={`button tertiary ${oauthUrl ? "" : "disabled"}`}
              href={oauthUrl || undefined}
              target="_blank"
              rel="noopener noreferrer"
              aria-disabled={!oauthUrl}
            >
              <ExternalLink size={18} aria-hidden="true" />
              完成 OAuth 授权
            </a>
          </div>
        </form>

        <div className="hint-box">
          <span>Redirect URI</span>
          <code>{redirectUri}</code>
        </div>

        <form className="form-stack" onSubmit={completeProvision}>
          <label className="field">
            <span>Paste Callback URL</span>
            <textarea
              value={callbackUrl}
              placeholder="http://localhost:3000/callback?code=...&state=..."
              onChange={(event) => setCallbackUrl(event.target.value)}
            />
          </label>
          <div className="action-row">
            <button className="button primary" type="submit" disabled={busyAction === "complete"}>
              {busyAction === "complete" ? (
                <LoaderCircle className="spin" size={18} aria-hidden="true" />
              ) : (
                <ClipboardCheck size={18} aria-hidden="true" />
              )}
              粘贴回调并完成绑定
            </button>
          </div>
          <StatusLine status={status} />
        </form>
      </section>

      <section className="panel result-panel" aria-live="polite">
        <div className="panel-title-row">
          <div>
            <p className="eyebrow">Result</p>
            <h2>执行结果</h2>
          </div>
        </div>
        {visiblePayload ? (
          <pre>{formatPayload(visiblePayload)}</pre>
        ) : (
          <div className="empty-state">等待发起流程</div>
        )}
      </section>
    </div>
  );
}

function DashboardView({
  config,
  refreshSignal,
  onAuthExpired
}: {
  config: UiConfig;
  refreshSignal: number;
  onAuthExpired: (error: unknown, setStatus?: (status: StatusState) => void) => boolean;
}) {
  const [statusFilter, setStatusFilter] = useState<FlowStatusFilter>("");
  const [assignmentFilter, setAssignmentFilter] = useState<AssignmentModeFilter>("");
  const [emailFilter, setEmailFilter] = useState("");
  const [flows, setFlows] = useState<ProvisionFlowSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProvisionFlowDetail | null>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [status, setStatus] = useState<StatusState>(emptyStatus);

  async function loadDetail(flowId: string) {
    setLoadingDetail(true);
    try {
      const payload = await requestJson<ProvisionFlowDetail>(
        `/provision/flows/${encodeURIComponent(flowId)}`,
        { method: "GET" },
        "加载编排详情失败"
      );
      setDetail(payload);
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setDetail(null);
        setStatus({ message: getErrorMessage(error, "加载编排详情失败"), tone: "error" });
      }
    } finally {
      setLoadingDetail(false);
    }
  }

  async function loadFlows(nextSelectedId?: string | null) {
    setLoadingList(true);
    const params = new URLSearchParams({ limit: "50", offset: "0" });
    if (statusFilter) {
      params.set("status", statusFilter);
    }
    if (assignmentFilter) {
      params.set("assignment_mode", assignmentFilter);
    }
    if (emailFilter.trim()) {
      params.set("email", emailFilter.trim());
    }

    try {
      const payload = await requestJson<ProvisionFlowsPayload>(
        `/provision/flows?${params.toString()}`,
        { method: "GET" },
        "加载编排列表失败"
      );
      setFlows(payload.items);
      setTotal(payload.total);
      setStatus({ message: `已加载 ${payload.items.length}/${payload.total} 条编排记录`, tone: "success" });

      const candidateId =
        nextSelectedId && payload.items.some((item) => item.flow_id === nextSelectedId)
          ? nextSelectedId
          : payload.items[0]?.flow_id ?? null;
      setSelectedFlowId(candidateId);
      if (candidateId) {
        await loadDetail(candidateId);
      } else {
        setDetail(null);
      }
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setFlows([]);
        setTotal(0);
        setDetail(null);
        setSelectedFlowId(null);
        setStatus({ message: getErrorMessage(error, "加载编排列表失败"), tone: "error" });
      }
    } finally {
      setLoadingList(false);
    }
  }

  useEffect(() => {
    void loadFlows(selectedFlowId);
  }, [refreshSignal]);

  async function runFilters() {
    await loadFlows(null);
  }

  async function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runFilters();
  }

  async function selectFlow(flowId: string) {
    setSelectedFlowId(flowId);
    await loadDetail(flowId);
  }

  return (
    <div className="dashboard-grid">
      <section className="panel dashboard-list-panel">
        <div className="panel-title-row">
          <div>
            <p className="eyebrow">Flows</p>
            <h2>编排记录</h2>
          </div>
          <button className="button secondary compact" type="button" onClick={() => void loadFlows(selectedFlowId)} disabled={loadingList}>
            {loadingList ? (
              <LoaderCircle className="spin" size={17} aria-hidden="true" />
            ) : (
              <RefreshCw size={17} aria-hidden="true" />
            )}
            刷新
          </button>
        </div>

        <form className="filter-grid" onSubmit={applyFilters}>
          <label className="field">
            <span>Status</span>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as FlowStatusFilter)}>
              <option value="">全部</option>
              <option value="pending_oauth">Pending OAuth</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
            </select>
          </label>
          <label className="field">
            <span>Mode</span>
            <select value={assignmentFilter} onChange={(event) => setAssignmentFilter(event.target.value as AssignmentModeFilter)}>
              <option value="">全部</option>
              <option value="dedicated">Dedicated</option>
              <option value="managed_pool">Managed Pool</option>
            </select>
          </label>
          <label className="field">
            <span>Email</span>
            <input value={emailFilter} placeholder="按 email 搜索" onChange={(event) => setEmailFilter(event.target.value)} />
          </label>
          <button className="button primary compact" type="button" onClick={() => void runFilters()} disabled={loadingList}>
            <Search size={17} aria-hidden="true" />
            查询
          </button>
        </form>

        <StatusLine status={status} />

        <div className="flow-list" aria-live="polite">
          {loadingList && flows.length === 0 ? (
            <div className="empty-state">
              <LoaderCircle className="spin" size={20} aria-hidden="true" />
              正在加载编排记录
            </div>
          ) : flows.length === 0 ? (
            <div className="empty-state">没有符合条件的编排记录</div>
          ) : (
            flows.map((flow) => (
              <button
                key={flow.flow_id}
                className={`flow-row ${selectedFlowId === flow.flow_id ? "active" : ""}`}
                type="button"
                onClick={() => void selectFlow(flow.flow_id)}
              >
                <span className={`status-pill ${flow.status}`}>{flow.status}</span>
                <span className="flow-main">
                  <strong>{flow.email}</strong>
                  <small>{flow.flow_id}</small>
                </span>
                <span className="flow-meta">
                  <span>{flow.assignment_mode}</span>
                  <span>{formatDate(flow.updated_at)}</span>
                </span>
              </button>
            ))
          )}
        </div>
        <div className="list-footnote">Total {total}</div>
      </section>

      <section className="panel detail-panel">
        <div className="panel-title-row">
          <div>
            <p className="eyebrow">Detail</p>
            <h2>编排详情</h2>
          </div>
          <Eye size={20} aria-hidden="true" />
        </div>
        {loadingDetail ? (
          <div className="empty-state">
            <LoaderCircle className="spin" size={20} aria-hidden="true" />
            正在加载详情
          </div>
        ) : detail ? (
          <FlowDetail detail={detail} defaultRedirectUri={config.oauth_redirect_uri} />
        ) : (
          <div className="empty-state">选择一条编排记录查看详情</div>
        )}
      </section>
    </div>
  );
}

function FlowDetail({ detail, defaultRedirectUri }: { detail: ProvisionFlowDetail; defaultRedirectUri: string }) {
  const callbackExample = `${detail.oauth_redirect_uri || defaultRedirectUri}?code=<code>&state=${detail.state}`;

  return (
    <div className="detail-stack">
      <div className="summary-grid">
        <SummaryItem label="Email" value={detail.email} />
        <SummaryItem label="Status" value={detail.status} />
        <SummaryItem label="User ID" value={unknownToText(detail.user_id)} />
        <SummaryItem label="Group ID" value={unknownToText(detail.group_id)} />
        <SummaryItem label="Account ID" value={unknownToText(detail.oauth_account_id)} />
        <SummaryItem label="Updated" value={formatDate(detail.updated_at)} />
      </div>

      {detail.error_message ? <div className="error-box">{detail.error_message}</div> : null}

      <div className="hint-box">
        <span>OAuth Handoff</span>
        <code>{detail.oauth_url || "-"}</code>
      </div>
      <div className="hint-box">
        <span>Callback Example</span>
        <code>{callbackExample}</code>
      </div>

      {detail.oauth_exchange_payload ? (
        <div className="payload-box">
          <span>OAuth Exchange Payload</span>
          <pre>{JSON.stringify(detail.oauth_exchange_payload, null, 2)}</pre>
        </div>
      ) : null}

      <div>
        <div className="section-heading">Timeline</div>
        {detail.events.length === 0 ? (
          <div className="empty-state">这条编排还没有时间线事件</div>
        ) : (
          <ol className="timeline">
            {detail.events.map((event) => (
              <li key={event.event_id} className={event.status}>
                <div>
                  <strong>{event.message}</strong>
                  <span>{event.event_type}</span>
                </div>
                <time>{formatDate(event.created_at)}</time>
                {event.details ? <pre>{JSON.stringify(event.details, null, 2)}</pre> : null}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="summary-item">
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

function StatusLine({ status }: { status: StatusState }) {
  return (
    <div className={`status-line ${status.tone}`} role={status.tone === "error" ? "alert" : "status"}>
      {status.message}
    </div>
  );
}

export default App;
