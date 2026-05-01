import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = "http://127.0.0.1:8000";

export default defineConfig({
  base: "/ui-static/",
  plugins: [react()],
  build: {
    outDir: "../app/static/ui",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-antd": ["antd", "@ant-design/icons"],
          "vendor-flow": ["@xyflow/react"]
        }
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/auth": apiTarget,
      "/orchestration": apiTarget,
      "/provision": apiTarget,
      "/rotation": apiTarget,
      "/ui/config": apiTarget
    }
  }
});
