/**
 * 智能体执行流程组件
 * 按后端 SSE 推送的步骤顺序，以带序号的流式文本展示
 */
import { memo } from "react";
import { Check, Circle, LoaderCircle, X } from "lucide-react";
import { cn } from "../lib/format";
import type { ProgressStatus, StepState } from "../types/agent";

function NodeIcon({ status }: { status: ProgressStatus | "pending" }) {
  if (status === "running") {
    return <LoaderCircle className="h-3 w-3 animate-spin" aria-hidden="true" />;
  }
  if (status === "success") {
    return <Check className="h-3 w-3" aria-hidden="true" />;
  }
  if (status === "error") {
    return <X className="h-3 w-3" aria-hidden="true" />;
  }
  return <Circle className="h-3 w-3" aria-hidden="true" />;
}

function StepRailImpl({ steps = [] }: { steps?: StepState[] }) {
  if (steps.length === 0) return null;

  // 按 updatedAt 排序，保持步骤出现顺序
  const sorted = [...steps].sort((a, b) => a.updatedAt - b.updatedAt);

  return (
    <section className="mt-3 border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900/70 dark:bg-gray-900/70 px-4 py-3 text-sm">
      <div className="mb-2 text-xs font-semibold text-gray-500 dark:text-gray-400">执行流程</div>
      <ol className="space-y-1.5">
        {sorted.map((step, idx) => (
          <li key={step.step} className="flex items-center gap-2.5">
            <span
              className={cn(
                "grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-semibold tabular-nums",
                step.status === "running" && "bg-amber-500/20 text-amber-600 dark:text-amber-400",
                step.status === "success" && "bg-apple-blue/15 text-apple-blue",
                step.status === "error" && "bg-apple-red/15 text-apple-red",
              )}
            >
              {idx + 1}
            </span>
            <span
              className={cn(
                "inline-flex h-5 w-5 shrink-0 place-items-center justify-center rounded-full",
                step.status === "running" && "bg-amber-500/15 text-amber-600 dark:text-amber-400",
                step.status === "success" && "bg-apple-blue/10 text-apple-blue",
                step.status === "error" && "bg-apple-red/10 text-apple-red",
              )}
            >
              <NodeIcon status={step.status} />
            </span>
            <span
              className={cn(
                "leading-5",
                step.status === "running" && "text-gray-900 dark:text-white font-medium",
                step.status === "success" && "text-gray-700 dark:text-gray-300",
                step.status === "error" && "text-apple-red",
              )}
            >
              {step.step}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

// React.memo：steps 数组引用不变时不重渲染
// 流式响应中本组件被频繁更新，memo 可避免重渲染时不必要的子树重建
export const StepRail = memo(StepRailImpl);
