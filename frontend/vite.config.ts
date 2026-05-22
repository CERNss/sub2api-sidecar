import { defineConfig } from "vite";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";

declare const process: { env: Record<string, string | undefined> };

const apiTarget = "http://127.0.0.1:8000";
const uiBase = "/ui-static";
const mockDataSync = process.env.VITE_MOCK_DATA_SYNC === "1";
const devSpaRoutes = new Set([
  "/login",
  "/orchestration",
  "/orchestration/manual",
  "/orchestration/dynamic",
  "/key-transfer",
  "/data-sync",
  "/credit-control",
  "/credit-control/users",
  "/credit-control/policies",
  "/credit-control/runs",
  "/credit-control/audit",
  "/dynamic",
  "/dashboard",
  "/provision",
  "/notifications"
]);

function devSpaRouteFallback(): Plugin {
  return {
    name: "dev-spa-route-fallback",
    configureServer(server) {
      server.middlewares.use((request, _response, next) => {
        const mutableRequest = request as { url?: string };
        const url = mutableRequest.url ?? "";
        const path = url.split(/[?#]/, 1)[0] || "/";
        if (devSpaRoutes.has(path)) {
          mutableRequest.url = `${uiBase}${url}`;
        }
        next();
      });
    }
  };
}

const mockUsers = [
  {
    user_id: "admin-001",
    email: "admin@example.com",
    name: "Key Transfer Admin",
    username: "admin",
    display_name: "Key Transfer Admin",
    status: "active",
    current_group_id: "grp-admin",
    current_group_name: "Admin Holding",
    local_group_id: null,
    local_group_name: null,
    has_local_assignment: false
  },
  {
    user_id: "user-feng",
    email: "fengxinyang@jihuanshe.com",
    name: "fengxinyang",
    username: "fengxinyang",
    display_name: "fengxinyang",
    status: "active",
    current_group_id: "grp-codex-a",
    current_group_name: "Codex 可用组 A",
    local_group_id: null,
    local_group_name: null,
    has_local_assignment: false
  },
  {
    user_id: "user-qiao",
    email: "qiao@example.com",
    name: "qiao",
    username: "qiao",
    display_name: "qiao",
    status: "active",
    current_group_id: "grp-codex-b",
    current_group_name: "Codex 可用组 B",
    local_group_id: null,
    local_group_name: null,
    has_local_assignment: false
  }
];

const mockSourceKeys = [
  {
    key_id: "key-transfer-feng",
    name: "rotom:codex:v1:fengxinyang@jihuanshe.com",
    group_id: "grp-admin",
    group_name: "Admin Holding",
    status: "active",
    usage_5h: 1.2,
    usage_1d: 6.4,
    usage_7d: 18.9
  },
  {
    key_id: "key-transfer-qiao",
    name: "service:object:v2:qiao@example.com",
    group_id: "grp-admin",
    group_name: "Admin Holding",
    status: "active",
    usage_5h: 0.4,
    usage_1d: 2.1,
    usage_7d: 7.6
  },
  {
    key_id: "key-transfer-missing",
    name: "rotom:codex:v1:missing@example.com",
    group_id: "grp-admin",
    group_name: "Admin Holding",
    status: "active",
    usage_5h: 0,
    usage_1d: 0.1,
    usage_7d: 0.3
  },
  {
    key_id: "key-normal",
    name: "manual-key-not-transfer",
    group_id: "grp-admin",
    group_name: "Admin Holding",
    status: "active",
    usage_5h: 0,
    usage_1d: 0,
    usage_7d: 0
  }
];

type DevResponse = {
  statusCode: number;
  setHeader(name: string, value: string): unknown;
  end(chunk?: string): unknown;
};

type DevRequest = {
  method?: string;
  url?: string;
  on(event: "data", handler: (chunk: unknown) => void): unknown;
  on(event: "end", handler: () => void): unknown;
};

function sendJson(response: unknown, payload: unknown) {
  const devResponse = response as DevResponse;
  devResponse.statusCode = 200;
  devResponse.setHeader("Content-Type", "application/json");
  devResponse.end(JSON.stringify(payload));
}

function mockKeyTransfer(dryRun: boolean) {
  return {
    success: true,
    run_id: dryRun ? "mock-preview-run" : "mock-sync-run",
    run_kind: "manual",
    tag: dryRun ? "key_transfer_preview" : "key_transfer",
    dry_run: dryRun,
    source_user_id: "admin-001",
    key_name_pattern: "service:object:version:email",
    planned_count: dryRun ? 2 : 0,
    moved_count: dryRun ? 0 : 2,
    skipped_count: 2,
    failed_count: 0,
    items: [
      {
        key_id: "key-transfer-feng",
        key_name: "rotom:codex:v1:fengxinyang@jihuanshe.com",
        source_user_id: "admin-001",
        source_group_id: "grp-admin",
        target_user_id: "user-feng",
        target_email: "fengxinyang@jihuanshe.com",
        target_group_id: "grp-codex-a",
        status: dryRun ? "planned" : "moved",
        reason: dryRun ? "Ready to transfer API key to target email user" : "API key transferred to target email user",
        quota: 0
      },
      {
        key_id: "key-transfer-qiao",
        key_name: "service:object:v2:qiao@example.com",
        source_user_id: "admin-001",
        source_group_id: "grp-admin",
        target_user_id: "user-qiao",
        target_email: "qiao@example.com",
        target_group_id: "grp-codex-b",
        status: dryRun ? "planned" : "moved",
        reason: dryRun ? "Ready to transfer API key to target email user" : "API key transferred to target email user",
        quota: 0
      },
      {
        key_id: "key-transfer-missing",
        key_name: "rotom:codex:v1:missing@example.com",
        source_user_id: "admin-001",
        source_group_id: "grp-admin",
        target_user_id: null,
        target_email: "missing@example.com",
        target_group_id: null,
        status: "skipped",
        reason: "USER_NOT_FOUND",
        quota: null
      },
      {
        key_id: "key-normal",
        key_name: "manual-key-not-transfer",
        source_user_id: "admin-001",
        source_group_id: "grp-admin",
        target_user_id: null,
        target_email: null,
        target_group_id: null,
        status: "skipped",
        reason: "API key name does not match the service:object:version:email pattern",
        quota: null
      }
    ]
  };
}

function keyTransferMockApi(): Plugin {
  return {
    name: "key-transfer-mock-api",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const devRequest = request as DevRequest;
        if (!mockDataSync) {
          next();
          return;
        }
        const method = devRequest.method ?? "GET";
        const url = new URL(devRequest.url ?? "/", "http://127.0.0.1");
        if (method === "GET" && url.pathname === "/auth/session") {
          sendJson(response, {
            username: "admin",
            expires_at: new Date(Date.now() + 12 * 60 * 60 * 1000).toISOString()
          });
          return;
        }
        if (method === "GET" && url.pathname === "/api/operational-data/status") {
          sendJson(response, {
            success: true,
            enabled: true,
            running: true,
            cadence_seconds: 60,
            collect_interval_seconds: 60,
            storage_bytes: 20480,
            tick_count: 12,
            sampled_signal_count: 8,
            source_statuses: []
          });
          return;
        }
        if (method === "GET" && url.pathname === "/api/operational-data/settings") {
          sendJson(response, {
            success: true,
            settings: {
              enabled: true,
              collect_interval_seconds: 60,
              expiration: null,
              retention_seconds: null,
              max_storage_mb: null,
              updated_at: new Date().toISOString()
            }
          });
          return;
        }
        if (method === "GET" && url.pathname === "/orchestration/users") {
          const search = (url.searchParams.get("email") ?? "").toLowerCase();
          const items = mockUsers.filter((user) =>
            [user.email, user.username, user.name, user.display_name].some((value) =>
              value.toLowerCase().includes(search)
            )
          );
          sendJson(response, { success: true, items, total: items.length });
          return;
        }
        if (method === "GET" && url.pathname === "/orchestration/users/admin-001/api-keys") {
          sendJson(response, { success: true, items: mockSourceKeys, total: mockSourceKeys.length });
          return;
        }
        if (
          method === "POST" &&
          (url.pathname === "/orchestration/api-keys/transfer" ||
            url.pathname === "/orchestration/api-keys/migrate-rotom")
        ) {
          let body = "";
          devRequest.on("data", (chunk) => {
            body += String(chunk);
          });
          devRequest.on("end", () => {
            const payload = body ? JSON.parse(body) as { dry_run?: boolean } : {};
            sendJson(response, mockKeyTransfer(Boolean(payload.dry_run)));
          });
          return;
        }
        next();
      });
    }
  };
}

export default defineConfig({
  base: `${uiBase}/`,
  plugins: [keyTransferMockApi(), devSpaRouteFallback(), react()],
  build: {
    outDir: "../app/static/ui",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-antd": ["antd", "@ant-design/icons"],
          "vendor-graph": ["@xyflow/react", "dagre"]
        }
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/auth": apiTarget,
      "/api": apiTarget,
      "/notifications": apiTarget,
      "/orchestration": apiTarget,
      "/provision": apiTarget,
      "/rotation": apiTarget
    }
  }
});
