import {
  ClipboardCheck,
  ExternalLink,
  Eye,
  ListChecks,
  LoaderCircle,
  LogOut,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck
} from "lucide-react";
import type { ReactNode } from "react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button as AntButton,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Segmented as AntSegmented,
  Select,
  Space,
  Spin,
  Tag,
  Typography
} from "antd";
import {
  ApiOutlined,
  BranchesOutlined,
  ClusterOutlined,
  KeyOutlined,
  NodeIndexOutlined,
  ReloadOutlined,
  SendOutlined,
  UserOutlined
} from "@ant-design/icons";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node
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
  user_id?: unknown;
  email?: string;
  source_group_id?: unknown | null;
  target_group_id?: unknown | null;
  trigger_type?: string;
  status?: string;
  reason?: string;
  migrated_keys?: number;
};

const emptyStatus: StatusState = { message: "", tone: "idle" };

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

function idValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function resolveKnownId(value: string, knownValues: unknown[]): unknown {
  const known = knownValues.find((item) => idValue(item) === value);
  return known ?? value;
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
    <AppChrome title={config.app_title}>
      {config.current_user ? <OperatorWorkspace config={config} /> : <LoginView config={config} />}
    </AppChrome>
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
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<StatusState>(emptyStatus);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!password) {
      setStatus({ message: "请先从启动日志复制当前密码。", tone: "error" });
      return;
    }

    setIsSubmitting(true);
    setStatus({ message: "正在验证管理员身份", tone: "info" });

    try {
      await requestJson("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: config.auth_username,
          password
        })
      }, "登录失败");
      setStatus({ message: "登录成功，正在进入编排页", tone: "success" });
      window.location.href = "/";
    } catch (error: unknown) {
      setStatus({ message: getErrorMessage(error, "登录失败"), tone: "error" });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="panel login-panel">
      <section className="note-grid" aria-label="登录信息">
        <InfoNote title="登录方式">
          用户名固定为 <code>{config.auth_username}</code>。
        </InfoNote>
        <InfoNote title="密码来源">密码会在每次服务启动时重新生成，请从服务日志复制。</InfoNote>
        <InfoNote title="OAuth 回调">授权完成后，把浏览器最后落到的 localhost URL 粘贴回编排页。</InfoNote>
      </section>

      <form className="form-stack" onSubmit={login}>
        <label className="field">
          <span>Username</span>
          <input type="text" value={config.auth_username} readOnly />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            placeholder="从启动日志复制当前密码"
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <div className="action-row">
          <button className="button primary" type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <LoaderCircle className="spin" size={18} aria-hidden="true" />
            ) : (
              <ShieldCheck size={18} aria-hidden="true" />
            )}
            登录并进入编排页
          </button>
        </div>
        <StatusLine status={status} />
      </form>
    </main>
  );
}

function OperatorWorkspace({ config }: { config: UiConfig }) {
  const [activeView, setActiveView] = useState<"orchestrate" | "dashboard" | "provision">("orchestrate");
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [dashboardRefresh, setDashboardRefresh] = useState(0);

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

  function noteFlowChanged() {
    setDashboardRefresh((value) => value + 1);
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
              className={activeView === "orchestrate" ? "active" : ""}
              type="button"
              onClick={() => setActiveView("orchestrate")}
            >
              <Play size={17} aria-hidden="true" />
              用户分组编排
            </button>
            <button
              className={activeView === "dashboard" ? "active" : ""}
              type="button"
              onClick={() => setActiveView("dashboard")}
            >
              <ListChecks size={17} aria-hidden="true" />
              历史看板
            </button>
            <button
              className={activeView === "provision" ? "active" : ""}
              type="button"
              onClick={() => setActiveView("provision")}
            >
              <Plus size={17} aria-hidden="true" />
              OAuth 预配
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

      {activeView === "orchestrate" ? (
        <ExistingOrchestrationView onAuthExpired={handleAuthExpired} />
      ) : activeView === "provision" ? (
        <ProvisionForm
          config={config}
          onAuthExpired={handleAuthExpired}
          onFlowChanged={noteFlowChanged}
        />
      ) : (
        <DashboardView
          config={config}
          refreshSignal={dashboardRefresh}
          onAuthExpired={handleAuthExpired}
        />
      )}
    </main>
  );
}

function ExistingOrchestrationView({
  onAuthExpired
}: {
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
  const [selectedKeyId, setSelectedKeyId] = useState("");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<StatusState>(emptyStatus);
  const [result, setResult] = useState<RotationExecutionPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingKeys, setLoadingKeys] = useState(false);
  const [submitting, setSubmitting] = useState(false);

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
  const selectedKey = apiKeys.find((key) => idValue(key.key_id) === selectedKeyId) ?? null;
  const selectedSourceGroup = sourceGroups.find((group) => idValue(group.group_id) === sourceGroupId) ?? null;
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

    const groupNodes: Node[] = Array.from(graphGroups.values()).map((group, index) => {
      const groupValue = idValue(group.group_id);
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
      id: `group-${idValue(group.group_id)}`,
      type: "output",
      position: { x: 692, y: 42 + index * 126 },
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

    const userNodes: Node[] = users.map((user, index) => {
      const userId = idValue(user.user_id);
      const currentGroupId = user.current_group_id ?? null;
      const currentGroupName = user.current_group_name ?? unknownToText(currentGroupId);
      return {
        id: `user-${userId}`,
        position: { x: 360, y: 42 + index * 126 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: {
          userId,
          label: (
            <GraphNode
              icon={<UserOutlined />}
              title={user.email || user.name || "用户"}
              subtitle={`User ${unknownToText(user.user_id)}`}
              tone={userId === selectedUserId ? "active" : "user"}
              tag={currentGroupName || user.status || "active"}
            />
          )
        }
      };
    });

    const keyNodes: Node[] = userKeyRows.map(({ userId, key }, index) => ({
      id: `key-${userId}-${idValue(key.key_id)}`,
      type: "input",
      position: { x: 28, y: 42 + index * 126 },
      sourcePosition: Position.Right,
      data: {
        userId,
        keyId: idValue(key.key_id),
        label: (
          <GraphNode
            icon={<KeyOutlined />}
            title={key.name || "api-key"}
            subtitle={unknownToText(key.key_id)}
            tone={idValue(key.key_id) === selectedKeyId ? "active" : "neutral"}
            tag={key.group_name || unknownToText(key.group_id)}
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
        animated: userId === selectedUserId || idValue(key.key_id) === selectedKeyId,
        markerEnd: { type: MarkerType.ArrowClosed },
        label: "用户 Key"
      })),
      ...userKeyRows
        .filter(({ key }) => idValue(key.group_id))
        .map(({ userId, key }) => ({
          id: `group-key-${idValue(key.group_id)}-${userId}-${idValue(key.key_id)}`,
          source: `key-${userId}-${idValue(key.key_id)}`,
          target: `group-${idValue(key.group_id)}`,
          animated: userId === selectedUserId || idValue(key.key_id) === selectedKeyId,
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
    selectedKeyId,
    selectedUserId,
    sourceGroupId,
    targetGroupId,
    users
  ]);
  const [nodes, setNodes, onNodesChange] = useNodesState(graph.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(graph.edges);

  async function loadResources(nextSelectedUserId?: string) {
    setLoading(true);
    const params = new URLSearchParams();
    if (userSearch.trim()) {
      params.set("email", userSearch.trim());
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
        nextSelectedUserId && usersPayload.items.some((user) => idValue(user.user_id) === nextSelectedUserId)
          ? nextSelectedUserId
          : usersPayload.items[0] ? idValue(usersPayload.items[0].user_id) : "";
      setSelectedUserId(candidateUserId);
      setStatus({ message: `已加载 ${usersPayload.total} 个用户、${groupsPayload.total} 个分组`, tone: "success" });
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
      setSelectedKeyId("");
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
      setSelectedKeyId((current) =>
        current && payload.items.some((key) => idValue(key.key_id) === current)
          ? current
          : payload.items[0] ? idValue(payload.items[0].key_id) : ""
      );
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setApiKeys([]);
        setSelectedKeyId("");
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
    void loadResources();
  }, []);

  useEffect(() => {
    if (!selectedUser) {
      setSourceGroupId("");
      return;
    }
    setSourceGroupId(idValue(selectedUserDirectGroup?.group_id));
    void loadApiKeys(idValue(selectedUser.user_id));
  }, [selectedUserId, selectedUserDirectGroup?.group_id]);

  useEffect(() => {
    if (targetGroups.length === 0) {
      setTargetGroupId("");
      return;
    }
    setTargetGroupId((current) =>
      current && targetGroups.some((group) => idValue(group.group_id) === current)
        ? current
        : idValue(targetGroups[0].group_id)
    );
  }, [targetGroups]);

  useEffect(() => {
    setNodes(graph.nodes);
    setEdges(graph.edges);
  }, [graph, setEdges, setNodes]);

  function selectGraphEntity(userId?: unknown, keyId?: unknown) {
    const nextUserId = idValue(userId);
    if (nextUserId && nextUserId !== selectedUserId) {
      setSelectedUserId(nextUserId);
    }
    const nextKeyId = idValue(keyId);
    if (nextKeyId) {
      setSelectedKeyId(nextKeyId);
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
    if (mode === "api_key" && !selectedKeyId) {
      setStatus({ message: "请选择 API Key。", tone: "error" });
      return;
    }

    const knownGroupIds = groups.map((group) => group.group_id);
    const sourceGroupValue = resolveKnownId(sourceGroupId, knownGroupIds);
    const targetGroupValue = resolveKnownId(targetGroupId, knownGroupIds);
    const selectedKeySourceGroup = selectedKey?.group_id ?? sourceGroupValue;
    const body =
      mode === "replace_group"
        ? {
            user_id: selectedUser.user_id,
            email: selectedUser.email,
            source_group_id: sourceGroupValue,
            target_group_id: targetGroupValue,
            reason: reason.trim() || undefined
          }
        : {
            user_id: selectedUser.user_id,
            email: selectedUser.email,
            key_id: selectedKey?.key_id ?? selectedKeyId,
            source_group_id: selectedKeySourceGroup,
            target_group_id: targetGroupValue,
            reason: reason.trim() || undefined
          };
    const url =
      mode === "replace_group"
        ? "/orchestration/assignments/replace-group"
        : "/orchestration/api-keys/update-group";

    setSubmitting(true);
    setStatus({ message: "正在执行编排", tone: "info" });
    try {
      const payload = await requestJson<RotationExecutionPayload>(
        url,
        {
          method: "POST",
          body: JSON.stringify(body)
        },
        "编排执行失败"
      );
      setResult(payload);
      setStatus({
        message: payload.status === "failed" ? payload.reason || "编排执行失败" : "编排执行完成",
        tone: payload.status === "failed" ? "error" : "success"
      });
      await loadResources(idValue(selectedUser.user_id));
      await loadApiKeys(idValue(selectedUser.user_id));
    } catch (error: unknown) {
      if (!onAuthExpired(error, setStatus)) {
        setResult({ success: false, detail: getErrorMessage(error, "编排执行失败") });
        setStatus({ message: getErrorMessage(error, "编排执行失败"), tone: "error" });
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="orchestration-ant-grid">
      <Card
        className="orchestration-control-card"
        title={
          <Space>
            <NodeIndexOutlined />
            <span>关系编排</span>
          </Space>
        }
        extra={
          <AntButton icon={<ReloadOutlined />} loading={loading} onClick={() => void loadResources(selectedUserId)}>
            刷新
          </AntButton>
        }
      >
        <form className="ant-orchestration-form" onSubmit={runExistingOrchestration}>
          <AntSegmented
            block
            value={mode}
            onChange={(value) => setMode(value as OrchestrationMode)}
            options={[
              { label: "整体替换", value: "replace_group", icon: <BranchesOutlined /> },
              { label: "单 Key", value: "api_key", icon: <KeyOutlined /> }
            ]}
          />

          <Input.Search
            allowClear
            value={userSearch}
            placeholder="按 email 搜索已有用户"
            enterButton="查询"
            onChange={(event) => setUserSearch(event.target.value)}
            onSearch={() => void loadResources(selectedUserId)}
          />

          <div className="ant-field">
            <Typography.Text strong>User</Typography.Text>
            <Select
              value={selectedUserId || undefined}
              placeholder="选择已有用户"
              showSearch
              optionFilterProp="label"
              onChange={(value) => setSelectedUserId(value)}
              options={users.map((user) => ({
                value: idValue(user.user_id),
                label: `${user.email} / ${unknownToText(user.user_id)}`
              }))}
              notFoundContent={loading ? <Spin size="small" /> : "暂无用户"}
            />
          </div>

          <div className="ant-field-grid">
            <div className="ant-field">
              <Typography.Text strong>Source Group</Typography.Text>
              <Select
                value={sourceGroupId || undefined}
                placeholder="当前用户未绑定分组"
                disabled
                optionFilterProp="label"
                options={sourceGroups.map((group) => ({
                  value: idValue(group.group_id),
                  label: `${group.name} / ${unknownToText(group.group_id)}（当前绑定）`
                }))}
              />
            </div>
            <div className="ant-field">
              <Typography.Text strong>Target Group</Typography.Text>
              <Select
                value={targetGroupId || undefined}
                placeholder="选择目标分组"
                showSearch
                optionFilterProp="label"
                onChange={(value) => setTargetGroupId(value)}
                options={targetGroups.map((group) => ({
                  value: idValue(group.group_id),
                  label: `${group.name} / ${unknownToText(group.group_id)}`,
                  disabled: mode === "replace_group" && !group.rotation_supported
                }))}
                notFoundContent="暂无可用分组"
              />
            </div>
          </div>

          {mode === "api_key" ? (
            <div className="ant-field">
              <Typography.Text strong>API Key</Typography.Text>
              <Select
                value={selectedKeyId || undefined}
                placeholder="选择 API Key"
                loading={loadingKeys}
                onChange={(value) => setSelectedKeyId(value)}
                options={apiKeys.map((key) => ({
                  value: idValue(key.key_id),
                  label: `${key.name || "api-key"} / ${unknownToText(key.key_id)}`
                }))}
                notFoundContent={loadingKeys ? <Spin size="small" /> : "暂无 API Key"}
              />
            </div>
          ) : null}

          <div className="ant-field">
            <Typography.Text strong>Reason</Typography.Text>
            <Input.TextArea
              rows={4}
              value={reason}
              placeholder="变更原因"
              onChange={(event) => setReason(event.target.value)}
            />
          </div>

          {status.message ? (
            <Alert
              showIcon
              type={status.tone === "error" ? "error" : status.tone === "success" ? "success" : "info"}
              message={status.message}
            />
          ) : null}

          <AntButton
            block
            type="primary"
            htmlType="button"
            icon={<SendOutlined />}
            loading={submitting}
            disabled={loading || targetGroups.length === 0}
            onClick={() => void runExistingOrchestration()}
          >
            执行编排
          </AntButton>
        </form>
      </Card>

      <section className="orchestration-canvas-shell">
        <div className="canvas-title-row">
          <div>
            <Typography.Text type="secondary">Graph Canvas</Typography.Text>
            <Typography.Title level={3}>所有 Key、用户、分组</Typography.Title>
          </div>
          <Space wrap>
            <Tag color="processing">左到右：Key / 用户 / 分组</Tag>
            <Tag color="default">全局关系</Tag>
          </Space>
        </div>
        <div className="flow-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={(_, node) => selectGraphEntity(node.data.userId, node.data.keyId)}
            fitView
            minZoom={0.55}
            maxZoom={1.35}
            nodesDraggable
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <MiniMap pannable zoomable />
            <Controls />
          </ReactFlow>
        </div>
      </section>

      <Card
        className="orchestration-keys-card"
        title={
          <Space>
            <ApiOutlined />
            <span>用户 API Keys</span>
          </Space>
        }
        extra={loadingKeys ? <Spin size="small" /> : <Tag>{apiKeys.length}</Tag>}
      >
        {apiKeys.length === 0 ? (
          <Empty description={loadingKeys ? "正在加载 API Keys" : "暂无 API Keys"} />
        ) : (
          <List
            dataSource={apiKeys}
            renderItem={(key) => (
              <List.Item
                className={idValue(key.key_id) === selectedKeyId ? "selected-key-row" : ""}
                onClick={() => setSelectedKeyId(idValue(key.key_id))}
              >
                <List.Item.Meta
                  avatar={<KeyOutlined />}
                  title={key.name || "api-key"}
                  description={unknownToText(key.key_id)}
                />
                <Tag color={idValue(key.key_id) === selectedKeyId ? "green" : "default"}>
                  {key.group_name || unknownToText(key.group_id)}
                </Tag>
              </List.Item>
            )}
          />
        )}
      </Card>

      <Drawer
        title="执行结果"
        open={Boolean(result)}
        width={520}
        onClose={() => setResult(null)}
      >
        {result ? (
          <Space direction="vertical" size={16} className="drawer-stack">
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="Status">{unknownToText(result.status)}</Descriptions.Item>
              <Descriptions.Item label="User">{unknownToText(result.email || result.user_id)}</Descriptions.Item>
              <Descriptions.Item label="Source Group">{unknownToText(result.source_group_id)}</Descriptions.Item>
              <Descriptions.Item label="Target Group">{unknownToText(result.target_group_id)}</Descriptions.Item>
              <Descriptions.Item label="Migrated Keys">{unknownToText(result.migrated_keys)}</Descriptions.Item>
              <Descriptions.Item label="Reason">{unknownToText(result.reason)}</Descriptions.Item>
            </Descriptions>
            <pre className="drawer-payload">{formatPayload(result)}</pre>
          </Space>
        ) : null}
      </Drawer>
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

function InfoNote({ title, children }: { title: string; children: ReactNode }) {
  return (
    <article className="note">
      <strong>{title}</strong>
      <p>{children}</p>
    </article>
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
