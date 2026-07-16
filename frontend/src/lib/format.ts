/**
 * 通用格式化工具
 * 提供 className 合并、时间格式化和查询结果文本化等通用工具函数
 */
export function cn(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

export function formatTime(timestamp: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

export function summarizeResult(data: unknown) {
  if (Array.isArray(data)) {
    return data.length > 0 ? `查询完成，共 ${data.length} 行结果。` : "查询完成，结果为空。";
  }

  if (data && typeof data === "object") {
    return "查询完成，已返回结构化结果。";
  }

  if (data === null || data === undefined || data === "") {
    return "查询完成，结果为空。";
  }

  return `查询完成：${String(data)}`;
}

export function toClipboardText(value: unknown) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

/**
 * 把后端返回的结果数组转成 CSV 字符串
 *
 * 处理 3 个边界：
 * 1. value 不是数组 → 转成单行 [[value]]（避免空表）
 * 2. value 是基本类型数组 → 加 "值" 列
 * 3. value 是对象数组 → 用所有出现过的 key 作为列
 *
 * CSV 转义：包含逗号/引号/换行的字段用双引号包裹，引号本身用两个引号转义
 */
export function toCsv(value: unknown): string {
  const rows: Array<Record<string, unknown>> = [];
  if (Array.isArray(value)) {
    rows.push(...normalizeToRows(value));
  } else if (value && typeof value === "object") {
    rows.push(value as Record<string, unknown>);
  } else if (value !== null && value !== undefined) {
    rows.push({ 值: value });
  }

  if (rows.length === 0) return "";

  // 列顺序：第一次出现顺序（保留语义）
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!columns.includes(key)) columns.push(key);
    }
  }

  const escapeCell = (cell: unknown): string => {
    if (cell === null || cell === undefined) return "";
    const str = typeof cell === "object" ? JSON.stringify(cell) : String(cell);
    // 包含 , " \n 的字段必须用双引号包裹，引号本身用 "" 转义
    if (/[",\n\r]/.test(str)) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  };

  const header = columns.join(",");
  const body = rows.map((row) => columns.map((col) => escapeCell(row[col])).join(","));
  // Excel 友好：在文件开头加 BOM，让中文不乱码
  return "\uFEFF" + [header, ...body].join("\r\n");
}

function normalizeToRows(data: unknown[]): Array<Record<string, unknown>> {
  return data.map((item, index) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? (item as Record<string, unknown>)
      : { 序号: index + 1, 值: item },
  );
}

/**
 * 触发浏览器下载（用 Blob + a[download] 模拟点击）
 *
 * 不依赖第三方库，纯前端实现；CSV 文件名带时间戳，避免覆盖
 */
export function downloadFile(filename: string, content: string, mimeType: string) {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  // 释放 URL 避免内存泄漏（浏览器会在下次 GC 回收，但显式释放更安全）
  URL.revokeObjectURL(url);
}

/**
 * 生成文件名：shopkeeper-agent-YYYYMMDD-HHmmss.csv
 * 用本地时区，跟用户预期一致
 */
export function timestampedFilename(prefix: string, ext: string): string {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const mi = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  return `${prefix}-${yyyy}${mm}${dd}-${hh}${mi}${ss}.${ext}`;
}
