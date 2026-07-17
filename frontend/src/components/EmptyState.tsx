/**
 * 首页空状态组件（Block Studio Bento 风格）
 * - 卡片：白/80 半透明 + backdrop-blur + 大圆角
 * - 图标容器：渐变背景 + 9px 圆角小方块
 * - 入场：fade-in-up 序列动画（每个延迟 60ms）
 */
import { memo } from "react";
import { LineChart, Search, ShoppingBag, Sparkles } from "lucide-react";

type EmptyStateProps = {
  examples: string[];
  onUseExample: (example: string) => void;
};

const highlights = [
  { label: "混合检索", icon: Search },
  { label: "SQL 闭环", icon: LineChart },
  { label: "电商数仓", icon: ShoppingBag },
];

function EmptyStateImpl({ examples, onUseExample }: EmptyStateProps) {
  return (
    <div className="mx-auto flex min-h-full max-w-5xl flex-col justify-center px-4 py-12">
      <div className="mb-10 max-w-3xl animate-fade-in-up">
        <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-apple-blue/30 bg-apple-blue/10 px-3 py-1.5 text-sm font-semibold text-apple-blue">
          <Sparkles className="h-4 w-4" aria-hidden="true" />
          Shopkeeper Agent
        </div>
        <h1 className="text-balance text-5xl font-bold leading-[1.05] tracking-tight text-gray-900 sm:text-7xl">
          电商问数
        </h1>
        <p className="mt-4 max-w-xl text-base leading-6 text-gray-500 sm:text-lg">
          用中文问数电商数据，秒级返回带口径的答案。
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {highlights.map((item, idx) => {
          const Icon = item.icon;
          return (
            <div
              key={item.label}
              style={{ animationDelay: `${idx * 60 + 100}ms` }}
              className="group animate-fade-in-up rounded-2xl border border-black/5 bg-white/80 p-4 shadow-sm backdrop-blur-xl transition-all duration-300 ease-spring hover:-translate-y-1 hover:shadow-md dark:border-white/10 dark:bg-white/5"
            >
              <div className="mb-4 grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-apple-blue/15 to-apple-purple/15 text-apple-blue">
                <Icon className="h-4 w-4" aria-hidden="true" />
              </div>
              <div className="text-sm font-semibold tracking-tight text-gray-900 dark:text-white">
                {item.label}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-6 grid gap-3 md:grid-cols-2">
        {examples.map((example, idx) => (
          <button
            key={example}
            type="button"
            onClick={() => onUseExample(example)}
            style={{ animationDelay: `${idx * 60 + 280}ms` }}
            className="group min-h-20 animate-fade-in-up rounded-2xl border border-black/5 bg-white/80 p-4 text-left text-[15px] leading-6 text-gray-900 shadow-sm backdrop-blur-xl transition-all duration-200 ease-spring hover:-translate-y-1 hover:scale-[1.01] hover:border-apple-blue/30 hover:shadow-md active:scale-[0.99] focus:outline-none focus-visible:ring-2 focus-visible:ring-apple-blue/40 dark:border-white/10 dark:bg-white/5 dark:text-white"
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}

export const EmptyState = memo(EmptyStateImpl);