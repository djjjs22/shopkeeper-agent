/**
 * 聊天输入区组件（Block Studio 风格）
 * - 输入框：浅灰背景 (#F2F2F7) + Focus 变白 + 蓝色光晕
 * - 发送按钮：胶囊圆角 + hover scale 弹性
 * - 自动高度 + Ctrl/Cmd+K + Esc
 *
 * 2026-07-17 改造：加 Multi-Agent 开关
 * - 开关在输入框下方左侧，灰色未激活 / 蓝色激活
 * - 激活时调用 /api/query 时 use_multi_agent=true
 * - 默认 false（走老 13 节点 graph）
 */
import { ArrowUp, Square, WandSparkles, Sparkles } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useRef } from "react";
import { cn } from "../lib/format";

type ComposerProps = {
    value: string;
    disabled: boolean;
    isStreaming: boolean;
    useMultiAgent: boolean;
    onChange: (value: string) => void;
    onSubmit: () => void;
    onStop: () => void;
    onToggleMultiAgent: (next: boolean) => void;
};

export function Composer({
    value,
    disabled,
    isStreaming,
    useMultiAgent,
    onChange,
    onSubmit,
    onStop,
    onToggleMultiAgent,
}: ComposerProps) {
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);

    useEffect(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.style.height = "auto";
        el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
    }, [value]);

    useEffect(() => {
        const onKey = (event: globalThis.KeyboardEvent) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "k") {
                event.preventDefault();
                textareaRef.current?.focus();
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, []);

    const submit = (event: FormEvent) => {
        event.preventDefault();
        if (!disabled) onSubmit();
    };

    const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (!disabled) onSubmit();
            return;
        }
        if (event.key === "Escape" && isStreaming) {
            event.preventDefault();
            onStop();
        }
    };

    return (
        <form onSubmit={submit} className="px-4 pb-6 pt-2">
            <div className="group mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-black/5 bg-white/80 p-2 shadow-lg backdrop-blur-xl transition-all duration-200 ease-spring focus-within:border-apple-blue/40 focus-within:bg-white focus-within:shadow-xl dark:border-white/10 dark:bg-white/5 dark:focus-within:border-apple-blue/40 dark:focus-within:bg-gray-900">
                <div className="hidden h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-apple-blue/15 to-apple-purple/15 text-apple-blue sm:grid">
                    <WandSparkles className="h-4 w-4" aria-hidden="true" />
                </div>
                <textarea
                    ref={textareaRef}
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    onKeyDown={onKeyDown}
                    rows={1}
                    placeholder="问一个电商数据问题..."
                    className="max-h-36 min-h-10 flex-1 resize-none overflow-y-auto bg-transparent px-2 py-2.5 text-[15px] leading-6 text-gray-800 outline-none placeholder:text-gray-400 dark:text-white"
                />
                <button
                    type={isStreaming ? "button" : "submit"}
                    onClick={isStreaming ? onStop : undefined}
                    disabled={!isStreaming && disabled}
                    className={cn(
                        "grid h-10 w-10 shrink-0 place-items-center rounded-full text-white transition-all duration-200 ease-spring hover:scale-105 active:scale-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-apple-blue/40",
                        isStreaming
                            ? "bg-apple-red hover:bg-apple-red/90"
                            : "bg-apple-blue hover:bg-apple-blue-hover disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 dark:disabled:bg-gray-800",
                    )}
                    title={isStreaming ? "停止 (Esc)" : "发送 (Enter)"}
                    aria-label={isStreaming ? "停止" : "发送"}
                >
                    {isStreaming ? (
                        <Square className="h-3.5 w-3.5 fill-current" aria-hidden="true" />
                    ) : (
                        <ArrowUp className="h-4 w-4" aria-hidden="true" />
                    )}
                </button>
            </div>
            <div className="mx-auto mt-2 flex max-w-3xl items-center justify-between gap-4 text-[11px] text-gray-400">
                {/* Multi-Agent 开关（左） */}
                <label
                    className={cn(
                        "inline-flex cursor-pointer select-none items-center gap-1.5 rounded-full px-2.5 py-1 transition-colors duration-150",
                        useMultiAgent
                            ? "bg-apple-blue/10 text-apple-blue"
                            : "bg-gray-100/80 text-gray-500 hover:bg-gray-200/80 dark:bg-white/5 dark:hover:bg-white/10",
                    )}
                    title={
                        useMultiAgent
                            ? "Multi-Agent 模式已启用：planner 拆 sub_query 并行执行（适合复杂查询）"
                            : "点击启用 Multi-Agent 模式"
                    }
                >
                    <input
                        type="checkbox"
                        checked={useMultiAgent}
                        onChange={(event) => onToggleMultiAgent(event.target.checked)}
                        className="sr-only"
                        aria-label="启用 Multi-Agent 模式"
                    />
                    <Sparkles className="h-3 w-3" aria-hidden="true" />
                    <span className="font-medium">
                        Multi-Agent
                    </span>
                    <span
                        className={cn(
                            "inline-block h-3 w-6 rounded-full transition-colors duration-150",
                            useMultiAgent ? "bg-apple-blue" : "bg-gray-300 dark:bg-gray-600",
                        )}
                        aria-hidden="true"
                    >
                        <span
                            className={cn(
                                "inline-block h-3 w-3 translate-y-0 transform rounded-full bg-white shadow transition-transform duration-150",
                                useMultiAgent ? "translate-x-3" : "translate-x-0",
                            )}
                        />
                    </span>
                </label>
                {/* 快捷键提示（右） */}
                <p className="text-center">
                    Enter 发送 · Shift+Enter 换行 · Esc 停止 ·{" "}
                    <kbd className="font-mono">⌘K</kbd> 聚焦 ·{" "}
                    <kbd className="font-mono">?</kbd> 快捷键面板
                </p>
            </div>
        </form>
    );
}