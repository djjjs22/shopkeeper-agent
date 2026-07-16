/**
 * React 应用入口
 * 挂载根组件并加载全局样式
 * ToastProvider 放在最外层，所有子组件都能调用 useToast()
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ToastProvider } from "./components/Toast";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
);