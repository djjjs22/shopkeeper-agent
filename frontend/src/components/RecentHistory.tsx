/**
 * 最近查询侧栏组件（Block Studio Apple 风）
 * - 卡片：白/70 半透明 + backdrop-blur + 圆角 + hairline 边
 * - hover：边框变 Apple 蓝 + 背景变白 + 微缩放
 * - leading 图标：搜历史感更强
 */
import { memo } from "react";
import { Clock, MessageSquareText } from "lucide-react";

type RecentHistoryProps = {
  queries: string[];
  disabled: boolean;
  onUseQuery: (query: string) => void;
};

function RecentHistoryImpl({ queries, disabled, onUseQuery }: RecentHistoryProps) {
  if (queries.length === 0) return null;

  return (
    <section>
      <div className="mb-2 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
        <Clock className="h-3.5 w-3.5" aria-hidden="true" />
        最近
      </div>
      <div className="space-y-1.5">
        {queries.map((query) => (
          <button
            key={query}
            type="button"
            disabled={disabled}
            onClick={() => onUseQuery(query)}
            title={query}
            className="group flex w-full items-center gap-2.5 rounded-xl border border-black/5 bg-white/70 px-3 py-2.5 text-left text-[13px] leading-5 text-gray-700 backdrop-blur-md transition-all duration-200 ease-spring hover:scale-[1.01] hover:border-apple-blue/30 hover:bg-white/90 hover:shadow-sm active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-55 dark:border-white/10 dark:bg-white/5 dark:text-gray-300 dark:hover:border-apple-blue/30 dark:hover:bg-white/10"
          >
            <MessageSquareText className="h-3.5 w-3.5 shrink-0 text-gray-400 transition-colors group-hover:text-apple-blue dark:text-gray-500" aria-hidden="true" />
            <span className="truncate">{query}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

// React.memo：queries 数组引用不变 + onUseQuery 引用不变时不重渲染
export const RecentHistory = memo(RecentHistoryImpl);