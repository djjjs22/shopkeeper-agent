/**
 * 最近查询侧栏组件
 * 显示用户最近发起过的 query 列表（持久化在 localStorage）
 * 点击历史项可重新发起问数
 */
import { memo } from "react";
import { Clock } from "lucide-react";

type RecentHistoryProps = {
  queries: string[];
  disabled: boolean;
  onUseQuery: (query: string) => void;
};

function RecentHistoryImpl({ queries, disabled, onUseQuery }: RecentHistoryProps) {
  if (queries.length === 0) return null;

  return (
    <section>
      <div className="mb-2 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-[0.16em] text-ink/45">
        <Clock className="h-3.5 w-3.5" aria-hidden="true" />
        最近
      </div>
      <div className="space-y-2">
        {queries.map((query) => (
          <button
            key={query}
            type="button"
            disabled={disabled}
            onClick={() => onUseQuery(query)}
            title={query}
            className="w-full truncate border border-ink/10 bg-white/42 px-3 py-2.5 text-left text-sm leading-5 text-ink/75 transition hover:border-moss/35 hover:bg-white/75 disabled:cursor-not-allowed disabled:opacity-55"
          >
            {query}
          </button>
        ))}
      </div>
    </section>
  );
}

// React.memo：queries 数组引用不变 + onUseQuery 引用不变时不重渲染
export const RecentHistory = memo(RecentHistoryImpl);