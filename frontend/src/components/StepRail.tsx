/**
 * 智能体执行流程组件
 * 按后端 SSE 推送的步骤顺序，以带序号的流式文本展示
 */
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

export function StepRail({ steps = [] }: { steps?: StepState[] }) {
  if (steps.length === 0) return null;

  // 按 updatedAt 排序，保持步骤出现顺序
  const sorted = [...steps].sort((a, b) => a.updatedAt - b.updatedAt);

  return (
    <section className="mt-3 border border-ink/10 bg-white/40 px-4 py-3 text-sm">
      <div className="mb-2 text-xs font-semibold text-ink/50">执行流程</div>
      <ol className="space-y-1.5">
        {sorted.map((step, idx) => (
          <li key={step.step} className="flex items-center gap-2.5">
            <span
              className={cn(
                "grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-semibold tabular-nums",
                step.status === "running" && "bg-brass/20 text-brass",
                step.status === "success" && "bg-moss/15 text-moss",
                step.status === "error" && "bg-tomato/15 text-tomato",
              )}
            >
              {idx + 1}
            </span>
            <span
              className={cn(
                "inline-flex h-5 w-5 shrink-0 place-items-center justify-center rounded-full",
                step.status === "running" && "bg-brass/15 text-brass",
                step.status === "success" && "bg-moss/10 text-moss",
                step.status === "error" && "bg-tomato/10 text-tomato",
              )}
            >
              <NodeIcon status={step.status} />
            </span>
            <span
              className={cn(
                "leading-5",
                step.status === "running" && "text-ink font-medium",
                step.status === "success" && "text-ink/70",
                step.status === "error" && "text-tomato",
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
