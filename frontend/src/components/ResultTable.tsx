/**
 * 查询结果表格组件
 * - 加载中：显示 6 行骨架占位（motion-safe:animate-pulse）
 * - 成功有数据：渲染可滚动表格 + CSV/JSON 导出按钮 + query 关键词高亮
 * - 成功零结果：友好空状态（提示常见原因）
 * - 加载/异常：由父组件控制，不在此处渲染
 */
import { memo } from "react";
import { Database, Download, FileJson, FileSpreadsheet, SearchX } from "lucide-react";
import { downloadFile, timestampedFilename, toCsv, toClipboardText } from "../lib/format";
import { useToast } from "./Toast";

// 加载骨架屏：流式响应期间占位，避免用户以为"卡住了"
function ResultTableSkeleton() {
  return (
    <section
      className="mt-4 overflow-hidden border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900/80 dark:bg-gray-900/80 shadow-sm"
      aria-busy="true"
      aria-live="polite"
    >
      <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-800 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
          <Database className="motion-safe:animate-pulse h-4 w-4 text-apple-blue" aria-hidden="true" />
          查询结果
          <span className="text-xs font-normal text-gray-500 dark:text-gray-400">（正在生成...）</span>
        </div>
      </div>
      <div className="space-y-2.5 p-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex gap-3">
            <div className="motion-safe:animate-pulse h-4 w-20 rounded-xl bg-black/5 dark:bg-white dark:bg-gray-900/8" />
            <div className="motion-safe:animate-pulse h-4 w-32 rounded-xl bg-black/5 dark:bg-white dark:bg-gray-900/8" />
            <div className="motion-safe:animate-pulse h-4 w-24 rounded-xl bg-black/5 dark:bg-white dark:bg-gray-900/8" />
            <div className="motion-safe:animate-pulse h-4 flex-1 rounded-xl bg-black/5 dark:bg-white dark:bg-gray-900/8" />
          </div>
        ))}
      </div>
    </section>
  );
}

// 归一化后端返回的数据为表格行
function normalizeRows(data: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(data)) {
    return data.map((item, index) =>
      item && typeof item === "object" && !Array.isArray(item)
        ? (item as Record<string, unknown>)
        : { 序号: index + 1, 值: item },
    );
  }
  if (data && typeof data === "object") {
    return [data as Record<string, unknown>];
  }
  return [{ 值: data ?? "" }];
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// 零结果空状态：SQL 成功执行但数据库无匹配行
function EmptyResult() {
  return (
    <section className="mt-4 overflow-hidden border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900/80 dark:bg-gray-900/80 shadow-sm">
      <div className="flex items-center gap-2 border-b border-gray-200 dark:border-gray-800 px-4 py-3 text-sm font-semibold text-gray-900 dark:text-white">
        <Database className="h-4 w-4 text-apple-blue" aria-hidden="true" />
        查询结果
      </div>
      <div className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
        <div className="grid h-12 w-12 place-items-center rounded-full bg-black/5 dark:bg-white dark:bg-gray-900/8 text-gray-500 dark:text-gray-400">
          <SearchX className="h-6 w-6" aria-hidden="true" />
        </div>
        <div className="space-y-1">
          <p className="text-sm font-semibold text-gray-900 dark:text-white">未找到匹配的数据</p>
          <p className="text-xs text-gray-500 dark:text-gray-400">SQL 已成功执行，但数据库中没有匹配的行</p>
        </div>
        <div className="mt-2 max-w-md text-left text-xs text-gray-500 dark:text-gray-400">
          <p className="font-medium text-gray-600 dark:text-gray-400">可能原因：</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            <li>指定的时间段内没有数据</li>
            <li>字段尚未在元数据中声明</li>
            <li>筛选条件过严，可放宽</li>
          </ul>
        </div>
      </div>
    </section>
  );
}

// 中文常用停用词（单字 + 双字），过滤掉"我想问下..."这类噪声
const STOP_WORDS = new Set([
  "的", "了", "是", "在", "和", "与", "或", "我", "你", "他", "她", "它",
  "请", "帮", "把", "给", "这", "那", "有", "没", "什么", "怎么", "如何",
  "查询", "看下", "一下", "统计", "一下", "看看", "想要", "需要", "请问",
  "里面", "今天", "明天", "去年", "上月", "上上", "今年", "前年",
]);

/**
 * 从 query 中提取关键词：
 * - 按空格 / 标点切分
 * - 过滤停用词 + 长度 < 2 的词
 * - 去重保留顺序
 */
function extractKeywords(query: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const token of query.split(/[\s,，。？?!！、；:：]+/)) {
    const t = token.trim();
    if (t.length < 2) continue;
    if (STOP_WORDS.has(t)) continue;
    const lower = t.toLowerCase();
    if (seen.has(lower)) continue;
    seen.add(lower);
    result.push(t);
  }
  return result;
}

function escapeRegex(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 把单元格文本中的关键词包成 <mark> 高亮
 * - 大小写不敏感
 * - 关键词按长度倒序匹配，避免短词抢匹配
 */
function highlightCell(text: string, keywords: string[]): React.ReactNode {
  if (keywords.length === 0 || text.length === 0) return text;
  // 按长度倒序：长关键词优先匹配，避免被短关键词截断
  const sorted = [...keywords].sort((a, b) => b.length - a.length);
  const pattern = new RegExp(`(${sorted.map(escapeRegex).join("|")})`, "gi");
  const parts = text.split(pattern);
  return parts.map((part, i) => {
    const isMatch = sorted.some((k) => k.toLowerCase() === part.toLowerCase());
    return isMatch ? (
      <mark
        key={i}
        className="rounded-xl bg-amber-500/30 px-0.5 text-gray-900 dark:text-white"
        title="匹配 query 关键词"
      >
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    );
  });
}

function ResultTableImpl({
  data,
  isLoading = false,
  query,
}: {
  data?: unknown;
  isLoading?: boolean;
  /** 用户的原始 query，用于在表格里高亮关键词 */
  query?: string;
}) {
  const toast = useToast();

  if (isLoading) {
    return <ResultTableSkeleton />;
  }

  if (data === undefined) {
    return null;
  }

  const rows = normalizeRows(data);
  const columns = Array.from(
    rows.reduce((keys, row) => {
      Object.keys(row).forEach((key) => keys.add(key));
      return keys;
    }, new Set<string>()),
  );

  if (Array.isArray(data) && data.length === 0) {
    return <EmptyResult />;
  }

  if (columns.length === 0) {
    return null;
  }

  // 一次性提取关键词（避免每次 cell 渲染都算）
  const keywords = query ? extractKeywords(query) : [];

  const handleExportCsv = () => {
    const csv = toCsv(data);
    if (!csv) return;
    const filename = timestampedFilename("shopkeeper-result", "csv");
    try {
      downloadFile(filename, csv, "text/csv");
      toast.push("success", `CSV 已下载：${filename}`);
    } catch (error) {
      toast.push("error", `CSV 导出失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const handleExportJson = () => {
    const json = toClipboardText(data);
    const filename = timestampedFilename("shopkeeper-result", "json");
    try {
      downloadFile(filename, json, "application/json");
      toast.push("success", `JSON 已下载：${filename}`);
    } catch (error) {
      toast.push("error", `JSON 导出失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  return (
    <section className="mt-4 overflow-hidden border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900/80 dark:bg-gray-900/80 shadow-sm">
      <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-800 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
          <Database className="h-4 w-4 text-apple-blue" aria-hidden="true" />
          查询结果
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
            <FileJson className="h-3.5 w-3.5" aria-hidden="true" />
            {rows.length} 行
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={handleExportCsv}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-gray-600 dark:text-gray-400 transition hover:bg-apple-blue/10 hover:text-apple-blue focus:outline-none focus:ring-2 focus:ring-apple-blue/40"
              title="导出 CSV（Excel 友好）"
              aria-label="导出 CSV"
            >
              <FileSpreadsheet className="h-3.5 w-3.5" aria-hidden="true" />
              CSV
            </button>
            <button
              type="button"
              onClick={handleExportJson}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-gray-600 dark:text-gray-400 transition hover:bg-apple-blue/10 hover:text-apple-blue focus:outline-none focus:ring-2 focus:ring-apple-blue/40"
              title="导出 JSON（保留原始结构）"
              aria-label="导出 JSON"
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              JSON
            </button>
          </div>
        </div>
      </div>
      <div className="max-h-[360px] overflow-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="sticky top-0 z-10 bg-[#efe6d8]">
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className="border-b border-gray-200 dark:border-gray-800 px-4 py-3 font-semibold text-gray-700 dark:text-gray-300"
                >
                  {highlightCell(column, keywords)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="odd:bg-white dark:bg-gray-900/80 dark:bg-gray-900/80 even:bg-white dark:bg-gray-900/40 dark:bg-gray-900/40">
                {columns.map((column) => (
                  <td key={column} className="border-b border-gray-900 dark:border-gray-200/5 px-4 py-3 text-gray-800 dark:text-gray-200">
                    {highlightCell(formatCell(row[column]), keywords)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// React.memo：表格数据不变时跳过重渲染
export const ResultTable = memo(ResultTableImpl);