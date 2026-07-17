/**
 * Toast 通知系统
 * - ToastContext + Provider + useToast hook
 * - 自动消失（默认 3s，可自定义）
 * - 4 种类型：success / error / info / warning
 * - 右下角堆叠 + 进度条
 * - 集成 motion-safe：用户开启"减少动画"时禁用进度条动画
 */
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  X,
  XCircle,
} from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { cn } from "../lib/format";

export type ToastType = "success" | "error" | "info" | "warning";

type ToastItem = {
  id: string;
  type: ToastType;
  message: string;
  duration: number; // 毫秒，0 = 不自动消失
};

type ToastContextValue = {
  toasts: ToastItem[];
  push: (type: ToastType, message: string, duration?: number) => void;
  dismiss: (id: string) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast 必须在 ToastProvider 内使用");
  }
  return ctx;
}

const STYLE_BY_TYPE: Record<
  ToastType,
  { icon: typeof CheckCircle2; ring: string; bg: string; text: string; bar: string }
> = {
  success: {
    icon: CheckCircle2,
    ring: "border-moss/35",
    bg: "bg-apple-blue/10",
    text: "text-apple-blue",
    bar: "bg-apple-blue",
  },
  error: {
    icon: XCircle,
    ring: "border-apple-red/40",
    bg: "bg-apple-red/10",
    text: "text-apple-red",
    bar: "bg-apple-red",
  },
  info: {
    icon: Info,
    ring: "border-gray-200 dark:border-gray-800",
    bg: "bg-black/5 dark:bg-white dark:bg-gray-900/8",
    text: "text-gray-900 dark:text-white",
    bar: "bg-gray-900 dark:bg-white dark:bg-gray-900",
  },
  warning: {
    icon: AlertTriangle,
    ring: "border-amber-500/45",
    bg: "bg-amber-500/10",
    text: "text-amber-600 dark:text-amber-400",
    bar: "bg-amber-500",
  },
};

function ToastCard({
  toast,
  onDismiss,
}: {
  toast: ToastItem;
  onDismiss: (id: string) => void;
}) {
  const style = STYLE_BY_TYPE[toast.type];
  const Icon = style.icon;

  // 用 ref 跟踪是否已 dismiss（避免 setTimeout 在用户手动关后还触发）
  const dismissedRef = useRef(false);
  useEffect(() => {
    if (toast.duration <= 0) return;
    const timer = setTimeout(() => {
      if (!dismissedRef.current) onDismiss(toast.id);
    }, toast.duration);
    return () => clearTimeout(timer);
  }, [toast.id, toast.duration, onDismiss]);

  const handleDismiss = () => {
    dismissedRef.current = true;
    onDismiss(toast.id);
  };

  return (
    <div
      role={toast.type === "error" || toast.type === "warning" ? "alert" : "status"}
      aria-live={toast.type === "error" ? "assertive" : "polite"}
      className={cn(
        "pointer-events-auto flex w-80 items-start gap-3 border bg-white dark:bg-gray-900/95 dark:bg-gray-900/95 px-4 py-3 shadow-lg backdrop-blur",
        style.ring,
      )}
    >
      <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", style.text)} aria-hidden="true" />
      <p className="flex-1 text-sm leading-5 text-gray-900 dark:text-white">{toast.message}</p>
      <button
        type="button"
        onClick={handleDismiss}
        className="shrink-0 rounded-full p-1 text-gray-900 dark:text-white/40 outline-none transition hover:bg-black/5 dark:bg-white dark:bg-gray-900/8 hover:text-gray-900 dark:text-white focus:ring-2 focus:ring-apple-blue/40"
        title="关闭"
        aria-label="关闭"
      >
        <X className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      {/* 进度条：仅在 duration > 0 时显示，且尊重 prefers-reduced-motion */}
      {toast.duration > 0 && (
        <div className="absolute bottom-0 left-0 h-0.5 w-full overflow-hidden">
          <div
            className={cn(
              "h-full motion-safe:animate-toast-progress",
              style.bar,
            )}
            style={{ animationDuration: `${toast.duration}ms` }}
          />
        </div>
      )}
      {/* motion-reduce 静态进度条（无动画） */}
      {toast.duration > 0 && (
        <div className="absolute bottom-0 left-0 h-0.5 w-full overflow-hidden motion-reduce:visible motion-safe:hidden">
          <div className={cn("h-full w-full opacity-30", style.bar)} />
        </div>
      )}
    </div>
  );
}

function ToastContainer({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}) {
  return (
    <div
      className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex flex-col items-center gap-2 px-4"
      aria-label="通知"
    >
      {toasts.map((toast) => (
        <ToastCard key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (type: ToastType, message: string, duration = 3000) => {
      const id =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      setToasts((current) => [...current, { id, type, message, duration }]);
    },
    [],
  );

  return (
    <ToastContext.Provider value={{ toasts, push, dismiss }}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}