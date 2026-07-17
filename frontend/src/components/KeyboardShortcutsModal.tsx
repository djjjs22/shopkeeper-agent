/**
 * 键盘快捷键面板
 * - 按 ? (shift+/) 打开
 * - 列出所有可用快捷键（Enter / Shift+Enter / Ctrl+K / Esc / ?）
 * - 点空白 / 按 Esc / 点关闭按钮关闭
 * - 焦点陷阱：打开时焦点移到面板内第一个按钮
 */
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { Keyboard, X } from "lucide-react";
import { cn } from "../lib/format";

export type Shortcut = {
  /** 跨平台按键组合，Mac 显示 ⌘，其他显示 Ctrl */
  keys: { mac: string; other: string };
  description: string;
};

export const SHORTCUTS: Shortcut[] = [
  { keys: { mac: "Enter", other: "Enter" }, description: "发送问数查询" },
  { keys: { mac: "⇧ Enter", other: "Shift + Enter" }, description: "在输入框换行" },
  { keys: { mac: "⌘ K", other: "Ctrl + K" }, description: "聚焦到输入框" },
  { keys: { mac: "Esc", other: "Esc" }, description: "停止当前流式响应 / 关闭弹窗" },
  { keys: { mac: "?", other: "?" }, description: "打开 / 关闭快捷键面板" },
];

function isMac() {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad/.test(navigator.platform);
}

export function KeyboardShortcutsModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const mac = isMac();

  // 打开时焦点移到关闭按钮（让屏幕阅读器读到面板标题）
  useEffect(() => {
    if (open) {
      closeBtnRef.current?.focus();
    }
  }, [open]);

  // Esc 关闭（面板打开时拦截）
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-40 grid place-items-center bg-black/45 dark:bg-black/65 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-title"
        className="w-full max-w-md border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-800 px-5 py-4">
          <div className="flex items-center gap-2 text-base font-semibold text-gray-900 dark:text-white">
            <Keyboard className="h-4 w-4 text-apple-blue" aria-hidden="true" />
            <span id="shortcuts-title">键盘快捷键</span>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-full text-gray-500 dark:text-gray-400 transition hover:bg-black/5 dark:bg-white dark:bg-gray-900/8 hover:text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-apple-blue/40"
            title="关闭 (Esc)"
            aria-label="关闭"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <ul className="divide-y divide-ink/5 px-2 py-2">
          {SHORTCUTS.map((shortcut) => (
            <li
              key={shortcut.description}
              className="flex items-center justify-between gap-3 px-3 py-2.5"
            >
              <span className="text-sm text-gray-700 dark:text-gray-300">{shortcut.description}</span>
              <kbd
                className={cn(
                  "inline-flex min-w-[3.5rem] items-center justify-center border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 dark:bg-gray-900/60 px-2 py-1 text-center text-xs font-mono font-medium text-gray-700 dark:text-gray-300",
                )}
              >
                {mac ? shortcut.keys.mac : shortcut.keys.other}
              </kbd>
            </li>
          ))}
        </ul>
        <div className="border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900/80 dark:bg-gray-900/80 dark:bg-gray-900/45 px-5 py-2.5 text-center text-xs text-gray-500 dark:text-gray-400">
          按 <kbd className="border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-1 py-0.5 font-mono text-[10px]">?</kbd> 随时打开
        </div>
      </div>
    </div>,
    document.body,
  );
}