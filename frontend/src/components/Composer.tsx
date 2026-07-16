/**
 * 聊天输入区组件
 * 处理问题输入、发送、停止流式、自动高度、键盘快捷键
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

    // 输入框自动高度增长：根据 scrollHeight 动态设置
    // 先重置为 auto 才能拿到正确的 scrollHeight（防止只增不减）
    useEffect(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.style.height = "auto";
        // max-h-36 = 144px，超过则滚动
        el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
    }, [value]);

    // 全局快捷键：Ctrl/Cmd+K 聚焦输入框（任何位置都能触发）
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
        // Enter 发送，Shift+Enter 换行
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (!disabled) onSubmit();
            return;
        }
        // Esc 停止流式响应（仅在流式中生效）
        if (event.key === "Escape" && isStreaming) {
            event.preventDefault();
            onStop();
        }
    };

    return (
        <form
            onSubmit={submit}
            className="border-t border-ink/10 bg-parchment/80 px-4 py-4 backdrop-blur"
        >
            <div className="mx-auto flex max-w-5xl items-end gap-3 border border-ink/15 bg-white/75 p-2 shadow-panel">
                <div className="hidden h-11 w-11 shrink-0 place-items-center bg-moss/10 text-moss sm:grid">
                    <WandSparkles className="h-5 w-5" aria-hidden="true" />
                </div>
                <textarea
                    ref={textareaRef}
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    onKeyDown={onKeyDown}
                    rows={1}
                    placeholder="问一个电商数据问题... (Enter 发送 · Shift+Enter 换行 · Esc 停止)"
                    className="max-h-36 min-h-11 flex-1 resize-none overflow-y-auto bg-transparent px-2 py-3 text-[15px] leading-6 text-ink outline-none placeholder:text-ink/35"
                />
                <button
                    type={isStreaming ? "button" : "submit"}
                    onClick={isStreaming ? onStop : undefined}
                    disabled={!isStreaming && disabled}
                    className={cn(
                        "grid h-11 w-11 shrink-0 place-items-center rounded-full text-white transition focus:outline-none focus:ring-2 focus:ring-moss/40 focus:ring-offset-2",
                        isStreaming
                            ? "bg-tomato hover:bg-tomato/90"
                            : "bg-ink hover:bg-soot disabled:cursor-not-allowed disabled:bg-ink/25",
                    )}
                    title={isStreaming ? "停止 (Esc)" : "发送 (Enter)"}
                    aria-label={isStreaming ? "停止" : "发送"}
                >
                    {isStreaming ? (
                        <Square
                            className="h-4 w-4 fill-current"
                            aria-hidden="true"
                        />
                    ) : (
                        <ArrowUp className="h-5 w-5" aria-hidden="true" />
                    )}
                </button>
            </div>
        </form>
    );
}