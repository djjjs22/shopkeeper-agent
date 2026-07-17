/**
 * 前端应用主组件
 * 负责聊天会话状态、SSE 事件消费和整体页面布局
 */
import {
  Activity,
  BarChart3,
  Eraser,
  History,
  Leaf,
  Menu,
  MessageSquarePlus,
  Server,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { MessageBubble } from "./components/MessageBubble";
import { RecentHistory } from "./components/RecentHistory";
import { streamQuery, clearSession, ApiError } from "./lib/agentApi";
import { mapBackendMessage, mapHttpError, mapJsError } from "./lib/errorMessages";
import { buildPendingSteps, mergeStepEvent } from "./lib/stepTemplate";
import { useToast } from "./components/Toast";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { KeyboardShortcutsModal } from "./components/KeyboardShortcutsModal";
import { cn, summarizeResult } from "./lib/format";
import type { AgentEvent, ChatMessage, StepState } from "./types/agent";

const examples = [
  "统计 2025 年第一季度各大区的 GMV，并按 GMV 从高到低排序",
  "统计 2025 年 3 月各商品品类的销量和销售额",
  "查询华东地区 2025 年第一季度销售额最高的前 5 个商品",
  "按会员等级统计 2025 年第一季度的订单数和销售额",
];

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "Vite /api proxy";
const RECENT_QUERIES_KEY = "shopkeeper-recent-queries";
const MAX_RECENT_QUERIES = 8;

function makeId() {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [activeController, setActiveController] = useState<AbortController | null>(null);
  // 最近查询历史：localStorage 持久化，刷新页面不丢失
  const [recentQueries, setRecentQueries] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem(RECENT_QUERIES_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.filter((q) => typeof q === "string") : [];
    } catch {
      return [];
    }
  });
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // 移动端 drawer 开关：true 时 sidebar 作为 fixed 浮层显示
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const isStreaming = Boolean(activeController);
  const canSubmit = draft.trim().length > 0 && !isStreaming;

  const completedCount = useMemo(
    () => messages.filter((message) => message.role === "assistant" && message.status === "done").length,
    [messages],
  );

  // 把 query 加到最近查询列表（去重 + 移到最前 + 截断到 N 条 + 持久化）
  const pushRecentQuery = useCallback((query: string) => {
    setRecentQueries((current) => {
      const next = [query, ...current.filter((q) => q !== query)].slice(0, MAX_RECENT_QUERIES);
      try {
        localStorage.setItem(RECENT_QUERIES_KEY, JSON.stringify(next));
      } catch {
        // localStorage 可能满/被禁用，吞掉异常不阻塞主流程
      }
      return next;
    });
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  // 全局快捷键：?（Shift+/）打开/关闭快捷键面板
  // - 输入框内输入问号时不触发
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      // event.key 在大多数键盘上 Shift+/ 输出 "?"
      if (event.key !== "?") return;
      const target = event.target as HTMLElement | null;
      // 在输入框/文本域里输入问号时不打开面板
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      event.preventDefault();
      setShortcutsOpen((current) => !current);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const startQuery = async (rawQuery = draft) => {
    const query = rawQuery.trim();
    if (!query || isStreaming) return;

    const userMessage: ChatMessage = {
      id: makeId(),
      role: "user",
      content: query,
      createdAt: Date.now(),
    };

    const assistantId = makeId();
    // 预填完整执行计划（默认 data_query，13 步 + 1 步分类共 14 步），
    // 全部 pending。后端事件流到来后逐步覆盖状态。
    // 效果：用户从一开始就能看到系统将要做什么，而不是"等半天看不到东西"。
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "正在连接问数智能体...",
      createdAt: Date.now(),
      status: "streaming",
      steps: buildPendingSteps("data_query"),
    };

    const controller = new AbortController();
    setActiveController(controller);
    setDraft("");
    setMessages((current) => [...current, userMessage, assistantMessage]);
    pushRecentQuery(query);

    const onEvent = (event: AgentEvent) => {
      setMessages((current) =>
        current.map((message) => {
          if (message.id !== assistantId) return message;

          if (event.type === "progress") {
            return {
              ...message,
              content: event.status === "running" ? `正在执行：${event.step}` : message.content,
              steps: mergeStepEvent(message.steps ?? [], event),
            };
          }

          if (event.type === "result") {
            return {
              ...message,
              status: "done",
              content: summarizeResult(event.data),
              result: event.data,
            };
          }

          return {
            ...message,
            status: "error",
            content: mapBackendMessage(event.message).title,
            error: mapBackendMessage(event.message).detail,
          };
        }),
      );
    };

    try {
      await streamQuery(query, { signal: controller.signal, onEvent });
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId && message.status === "streaming"
            ? { ...message, status: "done", content: "流程已结束，后端未返回查询结果。" }
            : message,
        ),
      );
    } catch (error) {
      const isAbort = error instanceof DOMException && error.name === "AbortError";
      // 区分 ApiError（HTTP 状态码错误）vs JS Error（网络/解析）
      const friendly = isAbort
        ? { title: "已停止", detail: "查询已被手动取消。", toastType: "warning" as const, retryable: false }
        : error instanceof ApiError
          ? mapHttpError(error.status)
          : mapJsError(error);
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                status: isAbort ? "done" : "error",
                content: isAbort ? "已停止本次查询。" : friendly.title,
                error: isAbort ? undefined : friendly.detail,
              }
            : message,
        ),
      );
    } finally {
      setActiveController(null);
    }
  };

  const stopQuery = () => {
    activeController?.abort();
  };

  const clearConversation = async () => {
    if (isStreaming) return;
    // 先清后端历史，再清前端本地消息（刀 17）
    try {
      await clearSession();
      toast.push("success", "已开启新会话");
    } catch (error) {
      // 后端清空失败不阻塞前端清空，本地先清掉
      const friendly = error instanceof ApiError
        ? mapHttpError(error.status)
        : mapJsError(error);
      toast.push(
        friendly.toastType,
        `${friendly.title}：${friendly.detail}`,
      );
    }
    setMessages([]);
    setDraft("");
  };

  // useRef 模式：保留 startQuery 最新引用，但回调本身引用稳定
  // 这样 React.memo 子组件不会因为回调引用变化而失效
  const startQueryRef = useRef(startQuery);
  startQueryRef.current = startQuery;

  // useCallback 包裹的回调，让子组件的 React.memo 真正生效
  const handleUseExample = useCallback((example: string) => setDraft(example), []);
  const handleUseRecentQuery = useCallback(
    (query: string) => startQueryRef.current(query),
    [],
  );
  const handleRetry = useCallback(
    (query: string) => startQueryRef.current(query),
    [],
  );

  // Drawer 操作：发起 query 后自动关闭移动端 drawer，避免遮住对话内容
  const startQueryAndCloseDrawer = useCallback(
    (query?: string) => {
      startQueryRef.current(query);
      setSidebarOpen(false);
    },
    [],
  );
  // 移动端：在 draft 输入框填入示例并关闭 drawer
  const handleUseExampleAndCloseDrawer = useCallback((example: string) => {
    setDraft(example);
    setSidebarOpen(false);
  }, []);

  // Esc 关闭移动端 drawer
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && sidebarOpen) {
        setSidebarOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen]);

  return (
    <div className="h-dvh overflow-hidden text-gray-900 dark:text-white">
      {/* Aurora 极光背景层：3 个浮动光球 + 极大模糊（Block Studio 规范） */}
      <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
        <div
          className="absolute -left-[15%] -top-[10%] h-[60vw] w-[60vw] rounded-full opacity-70 blur-[100px] animate-aurora-1"
          style={{
            background:
              "radial-gradient(circle, rgba(162, 210, 255, 0.55) 0%, rgba(162, 210, 255, 0) 70%)",
          }}
        />
        <div
          className="absolute right-[-10%] top-[25%] h-[55vw] w-[55vw] rounded-full opacity-60 blur-[100px] animate-aurora-2"
          style={{
            background:
              "radial-gradient(circle, rgba(200, 180, 255, 0.5) 0%, rgba(200, 180, 255, 0) 70%)",
          }}
        />
        <div
          className="absolute bottom-[-15%] left-[25%] h-[50vw] w-[50vw] rounded-full opacity-55 blur-[100px] animate-aurora-3"
          style={{
            background:
              "radial-gradient(circle, rgba(255, 200, 230, 0.45) 0%, rgba(255, 200, 230, 0) 70%)",
          }}
        />
      </div>

      <div className="relative z-10 grid h-full min-h-0 overflow-hidden lg:grid-cols-[320px_minmax(0,1fr)]">
        {/* 移动端遮罩：drawer 打开时背景暗化 */}
        <div
          className={cn(
            "fixed inset-0 z-30 bg-black/45 dark:bg-black/65 backdrop-blur-sm transition-opacity lg:hidden",
            sidebarOpen ? "opacity-100" : "pointer-events-none opacity-0",
          )}
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
        {/* Sidebar：lg 以上常驻，lg 以下作为 drawer */}
        <aside
          id="app-sidebar"
          className={cn(
            "fixed inset-y-0 left-0 z-40 flex w-72 min-h-0 flex-col border-r border-black/5 bg-white/70 shadow-lg backdrop-blur-2xl transition-transform duration-300 motion-safe:ease-spring-in lg:static lg:translate-x-0 lg:bg-white/60 lg:shadow-none dark:border-white/10 dark:bg-gray-950/70 lg:dark:bg-gray-950/60",
            sidebarOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
          )}
          aria-label="侧栏导航"
        >
          <div className="flex items-center justify-between border-b border-black/5 px-6 py-5 dark:border-white/10">
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center rounded-2xl bg-gradient-to-br from-apple-blue to-apple-purple text-white shadow-sm">
                <BarChart3 className="h-5 w-5" aria-hidden="true" />
              </div>
              <div>
                <div className="text-base font-semibold tracking-tight text-gray-900 dark:text-white">电商问数</div>
                <div className="text-xs text-gray-500 dark:text-gray-400">shopkeeper-agent</div>
              </div>
            </div>
            {/* 移动端关闭按钮（lg 以上隐藏） */}
            <button
              type="button"
              onClick={() => setSidebarOpen(false)}
              className="grid h-8 w-8 place-items-center rounded-full text-gray-500 dark:text-gray-400 transition hover:bg-black/5 dark:bg-white dark:bg-gray-900/8 hover:text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-apple-blue/40 lg:hidden"
              title="关闭侧栏 (Esc)"
              aria-label="关闭侧栏"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
            <button
              type="button"
              onClick={() => {
                clearConversation();
                setSidebarOpen(false);
              }}
              disabled={isStreaming}
              className="group flex h-11 w-full items-center justify-center gap-2 rounded-full bg-apple-blue text-sm font-semibold text-white shadow-sm transition-all duration-200 ease-spring hover:bg-apple-blue-hover hover:scale-[1.02] hover:shadow-md active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 dark:disabled:bg-gray-800"
            >
              <MessageSquarePlus className="h-4 w-4 transition-transform group-hover:rotate-90" aria-hidden="true" />
              新会话
            </button>

            <section>
              <div className="mb-2 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                <History className="h-3.5 w-3.5" aria-hidden="true" />
                样例
              </div>
              <div className="space-y-2">
                {examples.map((example, idx) => (
                  <button
                    key={example}
                    type="button"
                    disabled={isStreaming}
                    onClick={() => startQueryAndCloseDrawer(example)}
                    className="group w-full rounded-2xl border border-black/5 bg-gray-100 px-4 py-3 text-left text-[13px] leading-5 text-gray-900 transition-all duration-200 ease-spring hover:bg-gray-200 hover:scale-[1.01] active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/5 dark:bg-gray-800 dark:text-white dark:hover:bg-gray-700"
                    style={{ animationDelay: `${idx * 40}ms` }}
                  >
                    {example}
                  </button>
                ))}
              </div>
            </section>

            <RecentHistory
              queries={recentQueries}
              disabled={isStreaming}
              onUseQuery={startQueryAndCloseDrawer}
            />
          </div>

          <div className="border-t border-gray-200 dark:border-gray-800 p-4">
            <div className="grid gap-2 text-xs text-gray-500 dark:text-gray-400">
              <div className="flex items-center justify-between gap-3">
                <span className="inline-flex items-center gap-2">
                  <Server className="h-3.5 w-3.5" aria-hidden="true" />
                  API
                </span>
                <span className="truncate font-mono">{API_BASE_URL}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="inline-flex items-center gap-2">
                  <Activity className="h-3.5 w-3.5" aria-hidden="true" />
                  完成
                </span>
                <span>{completedCount}</span>
              </div>
            </div>
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col overflow-hidden bg-transparent">
          <header className="flex h-16 shrink-0 items-center justify-between border-b border-black/5 bg-white/60 px-4 backdrop-blur-xl lg:px-6 dark:border-white/10 dark:bg-gray-950/60">
            <div className="flex min-w-0 items-center gap-3">
              {/* 移动端汉堡按钮：打开 sidebar drawer（lg 以上隐藏） */}
              <button
                type="button"
                onClick={() => setSidebarOpen(true)}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-gray-600 transition-all duration-200 ease-spring hover:bg-black/5 hover:scale-105 active:scale-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-apple-blue/40 lg:hidden dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white"
                title="打开侧栏"
                aria-label="打开侧栏"
                aria-expanded={sidebarOpen}
                aria-controls="app-sidebar"
              >
                <Menu className="h-5 w-5" aria-hidden="true" />
              </button>
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-apple-blue to-apple-purple text-white shadow-sm">
                <BarChart3 className="h-4 w-4" aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold tracking-tight text-gray-900 dark:text-white">智能数据分析 Agent</div>
                <div className="truncate text-xs text-gray-500 dark:text-gray-400">FastAPI SSE / LangGraph</div>
              </div>
            </div>
            <button
              type="button"
              onClick={clearConversation}
              disabled={messages.length === 0 || isStreaming}
              className={cn(
                "grid h-9 w-9 place-items-center rounded-full text-gray-500 transition-all duration-200 ease-spring hover:bg-black/5 hover:scale-105 hover:text-gray-900 active:scale-95 disabled:cursor-not-allowed disabled:opacity-35 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white",
              )}
              title="清空"
              aria-label="清空"
            >
              <Eraser className="h-4 w-4" aria-hidden="true" />
            </button>
          </header>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            <ErrorBoundary>
              {messages.length === 0 ? (
                <EmptyState examples={examples} onUseExample={handleUseExample} />
              ) : (
                <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 lg:px-8">
                  {messages.map((message, index) => {
                    // 找到对应的 user query：assistant 消息往前找最近的 user 消息
                    const userQuery =
                      message.role === "assistant"
                        ? messages
                            .slice(0, index)
                            .reverse()
                            .find((m) => m.role === "user")?.content
                        : undefined;
                    return (
                      <MessageBubble
                        key={message.id}
                        message={message}
                        isStreaming={isStreaming}
                        onRetry={handleRetry}
                        query={userQuery}
                      />
                    );
                  })}
                </div>
              )}
            </ErrorBoundary>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-800 bg-gray-100/70 dark:bg-gray-900/45 px-4 py-2 text-center text-xs text-gray-500 dark:text-gray-400">
            <span className="inline-flex items-center gap-2">
              <Leaf className="h-3.5 w-3.5 text-apple-blue" aria-hidden="true" />
              {isStreaming ? "运行中" : "就绪"}
            </span>
          </div>
          <Composer
            value={draft}
            disabled={!canSubmit}
            isStreaming={isStreaming}
            onChange={setDraft}
            onSubmit={() => startQuery()}
            onStop={stopQuery}
          />
        </main>
      </div>
      <KeyboardShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </div>
  );
}
