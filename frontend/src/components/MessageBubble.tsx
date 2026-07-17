/**
 * 聊天消息气泡组件
 * 组合展示用户问题、智能体回复、执行流程和结果表格
 */
import { memo } from "react";
import { Bot, Copy, RefreshCcw, UserRound } from "lucide-react";
import { ResultTable } from "./ResultTable";
import { StepRail } from "./StepRail";
import { useToast } from "./Toast";
import { cn, formatTime, toClipboardText } from "../lib/format";
import type { ChatMessage } from "../types/agent";

function MessageBubbleImpl({
  message,
  onRetry,
  isStreaming,
  query,
}: {
  message: ChatMessage;
  /**
   * 重试回调：用户消息点重试按钮时触发，传入原 query 文本
   * App 层会调用 startQuery(query) 重新发起问数
   *
   * 注意：
   * - 流式响应期间禁用（避免重复请求）
   * - 必须明确传入，因为 MessageBubble 是纯展示组件，不知道如何发起请求
   */
  onRetry?: (query: string) => void;
  isStreaming?: boolean;
  /**
   * 用户原 query（用于在 ResultTable 中高亮关键词）
   * - 仅 assistant message 且有 result 时使用
   * - 来自 App 层遍历消息列表时找到对应 user message
   */
  query?: string;
}) {
  const isUser = message.role === "user";
  const toast = useToast();

  const copy = async () => {
    const text = message.result ? toClipboardText(message.result) : message.content;
    try {
      await navigator.clipboard.writeText(text);
      toast.push("success", "已复制到剪贴板");
    } catch (error) {
      toast.push("error", `复制失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const retry = () => {
    if (onRetry && !isStreaming) {
      toast.push("info", `重新查询：${message.content.slice(0, 30)}${message.content.length > 30 ? "..." : ""}`);
      onRetry(message.content);
    }
  };

  return (
    <article className={cn("group flex gap-3", isUser && "justify-end")}>
      {!isUser && (
        <div className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gray-900 dark:bg-white dark:bg-gray-900 text-white">
          <Bot className="h-4 w-4" aria-hidden="true" />
        </div>
      )}

      <div className={cn("max-w-[920px] flex-1", isUser && "flex max-w-[760px] justify-end")}>
        <div
          className={cn(
            "relative border px-5 py-4 shadow-sm",
            isUser
              ? "border-gray-900/10 dark:border-white/10 bg-gray-900 dark:bg-gray-900 text-white"
              : "border-black/5 dark:border-white/10 bg-white/80 dark:bg-gray-900/70 text-gray-900 dark:text-white backdrop-blur-xl shadow-[0_8px_24px_rgba(0,0,0,0.04)]",
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <p className="whitespace-pre-wrap text-[15px] leading-7">{message.content}</p>
            {!isUser && message.status !== "streaming" && (
              <button
                type="button"
                onClick={copy}
                className="shrink-0 rounded-full p-1.5 text-gray-500 dark:text-gray-400 opacity-0 outline-none transition hover:bg-black/5 dark:bg-white dark:bg-gray-900/8 hover:text-gray-900 dark:text-white focus:opacity-100 focus:ring-2 focus:ring-apple-blue/40 group-hover:opacity-100"
                title="复制"
                aria-label="复制"
              >
                <Copy className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
            {isUser && onRetry && !isStreaming && (
              <button
                type="button"
                onClick={retry}
                className="shrink-0 rounded-full p-1.5 text-white/70 opacity-0 outline-none transition hover:bg-white dark:bg-gray-900 dark:bg-gray-900/10 hover:text-white focus:opacity-100 focus:ring-2 focus:ring-parchment/40 group-hover:opacity-100"
                title="重新查询"
                aria-label="重新查询"
              >
                <RefreshCcw className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          </div>

          {message.error && (
            <div className="mt-3 border border-apple-red/30 bg-apple-red/10 px-3 py-2 text-sm text-apple-red">
              {message.error}
            </div>
          )}

          {!isUser && <StepRail steps={message.steps} />}
          {!isUser && (message.result !== undefined
            ? <ResultTable data={message.result} query={query} />
            : message.status === "streaming" && <ResultTable isLoading />)}

          <div
            className={cn(
              "mt-3 text-xs",
              isUser ? "text-white/70" : "text-gray-500 dark:text-gray-400",
            )}
          >
            {formatTime(message.createdAt)}
          </div>
        </div>
      </div>

      {isUser && (
        <div className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-apple-blue text-white">
          <UserRound className="h-4 w-4" aria-hidden="true" />
        </div>
      )}
    </article>
  );
}

// React.memo：避免流式响应期间其他消息气泡不必要地重渲染
// props 对比（浅相等）失败时才重渲染，message/status/steps 不变的消息会跳过
export const MessageBubble = memo(MessageBubbleImpl);
