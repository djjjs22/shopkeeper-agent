/**
 * Vite 开发与构建配置
 * 包含 React 插件、后端 API 开发代理、产物代码分割
 */
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // 2026-07-20 注：8000 端口被 uvicorn 旧进程占用成僵尸 socket，
  // 临时改用 8001。下次启动 uvicorn 应该先用 SO_REUSEADDR 或彻底释放 socket。
  const backend = env.VITE_DEV_PROXY_TARGET || "http://127.0.0.1:8001";

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