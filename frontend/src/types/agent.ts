/**
 * 智能体类型定义
 * 定义问数智能体前端使用的 SSE 事件、流程步骤和聊天消息类型
 */
export type ProgressStatus = "running" | "success" | "error";

export type ProgressEvent = {
  type: "progress";
  step: string;
  status: ProgressStatus;
};

export type ResultEvent = {
  type: "result";
  data: unknown;
  // 2026-07-20 (#3)：结果集过大时后端会带 truncated=true 和 max_rows
  truncated?: boolean;
  max_rows?: number;
};

export type ErrorEvent = {
  type: "error";
  message: string;
};

// 2026-07-20 (#6)：warning 事件（如意图解析失败、结果截断）
// 与 error 区别：warning 不终止流程，只是提示用户结果可能不完整
export type WarningEvent = {
  type: "warning";
  message: string;
  step?: string;
};

export type AgentEvent =
  | ProgressEvent
  | ResultEvent
  | ErrorEvent
  | WarningEvent;

export type StepState = {
  step: string;
  status: ProgressStatus;
  updatedAt: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "streaming" | "done" | "error";
  steps?: StepState[];
  result?: unknown;
  truncated?: boolean;  // 2026-07-20 (#3)：结果集是否被截断
  warnings?: string[];  // 2026-07-20 (#6)：累积的 warning 提示
  error?: string;
};
