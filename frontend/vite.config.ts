/**
 * Vite 开发与构建配置
 * 包含 React 插件、后端 API 开发代理、产物代码分割
 */
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // 默认代理到后端 8000 端口。历史曾因 8000 端口被僵尸 socket 占用临时改 8001，
  // 当前 8000 已正常释放，回归 8000；如需切换可设置 VITE_DEV_PROXY_TARGET 环境变量。
  const backend = env.VITE_DEV_PROXY_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/api": {
          target: backend,
          changeOrigin: true,
        },
      },
    },
    build: {
      // 手动 vendor 分割：把 React/ReactDOM 单独打 chunk，业务代码独立
      // 浏览器可独立缓存 vendor chunk，业务代码更新不重新下载 React
      rollupOptions: {
        output: {
          manualChunks: {
            "vendor-react": ["react", "react-dom"],
            "vendor-icons": ["lucide-react"],
          },
        },
      },
      // 单 chunk 体积警告阈值（默认 500kb，提早发现体积膨胀）
      chunkSizeWarningLimit: 600,
    },
  };
});