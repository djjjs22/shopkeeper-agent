/**
 * 聊天输入区组件（Block Studio 风格）
 * - 输入框：浅灰背景 (#F2F2F7) + Focus 变白 + 蓝色光晕
 * - 发送按钮：胶囊圆角 + hover scale 弹性
 * - 自动高度 + Ctrl/Cmd+K + Esc
 */
import { ArrowUp, Square, WandSparkles } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useRef } from "react";
import { cn } from "../lib/format";

type ComposerProps = {
    value: string;
    disabled: boolean;
    isStreaming: boolean;
    onChange: (value: string) => void;
    onSubmit: () => void;
    onStop: () => void;
};

export function Composer({
    value,
    disabled,
    isStreaming,
    onChange,
    onSubmit,
    onStop,
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
                    className="max-h-36 min-h-10 flex-1 resize-none overflow-y-auto bg-transparent px-2 py-2.5 text-[15px] leading-6 text-gray-900 outline-none placeholder:text-gray-400 dark:text-white"
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
            <p className="mx-auto mt-2 max-w-3xl text-center text-[11px] text-gray-400">
                Enter 发送 · Shift+Enter 换行 · Esc 停止 ·{" "}
                <kbd className="font-mono">⌘K</kbd> 聚焦 ·{" "}
                <kbd className="font-mono">?</kbd> 快捷键面板
            </p>
        </form>
    );
}