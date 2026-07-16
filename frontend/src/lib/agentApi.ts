/**
 * 智能体接口客户端
 * 封装后端 /api/query SSE 流式接口请求与事件解析逻辑
 */
import type { AgentEvent } from "../types/agent";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";

/**
 * API 错误：携带 HTTP 状态码，让前端能映射到友好提示
 * 字段 `status` 用于 errorMessages.mapHttpError 分类
 */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type QueryOptions = {
  signal?: AbortSignal;
  onEvent: (event: AgentEvent) => void;
};

export async function streamQuery(query: string, options: QueryOptions) {
  const response = await fetch(`${API_BASE_URL}/api/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ query }),
    signal: options.signal,
  });

  if (!response.ok) {
    // 尝试读后端返回的错误体作为补充信息
    let bodyText = "";
    try {
      bodyText = await response.text();
    } catch {
      // 读不到 body 也无所谓
    }
    throw new ApiError(
      bodyText || `接口请求失败：HTTP ${response.status}`,
      response.status,
    );
  }

  if (!response.body) {
    throw new Error("浏览器未返回可读取的流式响应。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split(/\n\n/);
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const event = parseSseChunk(chunk);
      if (event) {
        options.onEvent(event);
      }
    }
  }

  buffer += decoder.decode();
  const tail = parseSseChunk(buffer);
  if (tail) {
    options.onEvent(tail);
  }
}

/**
 * 清空当前会话的后端历史（配合「新会话」按钮）
 * 仅清前端本地消息会导致多轮对话上下文错位——session_id（cookie）不变，
 * 后端仍会读取旧历史。调用此接口显式清掉后端历史。
 */
export async function clearSession() {
  const response = await fetch(`${API_BASE_URL}/api/clear-session`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(`清空会话失败：HTTP ${response.status}`, response.status);
  }
  return response.json();
}

function parseSseChunk(chunk: string): AgentEvent | null {
  const payload = chunk
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.replace(/^data:\s?/, ""))
    .join("\n")
    .trim();

  if (!payload) return null;

  try {
    return JSON.parse(payload) as AgentEvent;
  } catch {
    return {
      type: "error",
      message: `无法解析后端事件：${payload}`,
    };
  }
}
