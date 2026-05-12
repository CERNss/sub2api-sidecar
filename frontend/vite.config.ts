import { defineConfig } from "vite";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = "http://127.0.0.1:8000";
const uiBase = "/ui-static";
const devSpaRoutes = new Set([
  "/login",
  "/orchestration",
  "/orchestration/manual",
  "/orchestration/dynamic",
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

export default defineConfig({
  base: `${uiBase}/`,
  plugins: [devSpaRouteFallback(), react()],
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
      "/notifications": apiTarget,
      "/orchestration": apiTarget,
      "/provision": apiTarget,
      "/rotation": apiTarget
    }
  }
});
