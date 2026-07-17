/**
 * 聊天消息气泡组件
 * 组合展示用户问题、智能体回复、执行流程和结果表格
 *
 * 设计：
 * - 用户气泡：iMessage 蓝色渐变 + Squircle 大圆角 + 苹果蓝阴影（Apple HIG 标准）
 * - 助手气泡：白/70 玻璃 + hairline 边 + Squircle 大圆角（Block Studio 美学）
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
        <div
          className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full text-white shadow-sm"
          style={{
            backgroundImage:
              "linear-gradient(135deg, #48484A 0%, #1C1C1E 100%)",
          }}
        >
          <Bot className="h-4 w-4" aria-hidden="true" />
        </div>
      )}

      <div className={cn("max-w-[920px] flex-1", isUser && "flex max-w-[760px] justify-end")}>
        <div
          className={cn(
            // 用户气泡：iMessage 蓝渐变 + Squircle 大圆角 + 苹果蓝色阴影
            // 助手气泡：白/70 玻璃 + hairline 边 + Squircle 大圆角 + 苹果黑色阴影
            "relative px-5 py-4 transition-shadow",
            isUser
              ? "rounded-2xl rounded-br-md border border-apple-blue/40 text-white shadow-[0_8px_24px_rgba(0,113,227,0.25)] hover:shadow-[0_10px_28px_rgba(0,113,227,0.30)]"
              : "rounded-2xl rounded-bl-md border border-black/5 bg-white/80 text-gray-900 shadow-[0_8px_24px_rgba(0,0,0,0.04)] backdrop-blur-xl dark:border-white/10 dark:bg-white/5 dark:text-white",
          )}
          style={
            isUser
              ? {
                  // iMessage 蓝色渐变（用 inline style 兜底，保证 100% 渲染）
                  backgroundImage:
                    "linear-gradient(135deg, #0071e3 0%, #0077ed 100%)",
                }
              : undefined
          }
        >
          <div className="flex items-start justify-between gap-3">
            <p className="whitespace-pre-wrap text-[15px] leading-7">{message.content}</p>
            {!isUser && message.status !== "streaming" && (
              <button
                type="button"
                onClick={copy}
                className="shrink-0 rounded-full p-1.5 text-gray-500 opacity-0 outline-none transition hover:bg-black/5 hover:text-gray-900 focus:opacity-100 focus:ring-2 focus:ring-apple-blue/40 group-hover:opacity-100 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white"
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
                className="shrink-0 rounded-full p-1.5 text-white/70 opacity-0 outline-none transition hover:bg-white/15 hover:text-white focus:opacity-100 focus:ring-2 focus:ring-white/40 group-hover:opacity-100"
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
              "mt-2.5 flex items-center gap-1.5 text-[11px] tabular-nums",
              isUser ? "text-white/75" : "text-gray-400 dark:text-gray-500",
            )}
          >
            <span className="inline-block h-1 w-1 rounded-full bg-current opacity-50" aria-hidden="true" />
            {formatTime(message.createdAt)}
          </div>
        </div>
      </div>

      {isUser && (
        <div
          className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full text-white shadow-sm"
          style={{
            backgroundImage:
              "linear-gradient(135deg, #0071e3 0%, #AF52DE 100%)",
          }}
        >
          <UserRound className="h-4 w-4" aria-hidden="true" />
        </div>
      )}
    </article>
  );
}

// React.memo：避免流式响应期间其他消息气泡不必要地重渲染
// props 对比（浅相等）失败时才重渲染，message/status/steps 不变的消息会跳过
export const MessageBubble = memo(MessageBubbleImpl);
