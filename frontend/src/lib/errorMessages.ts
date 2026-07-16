/**
 * 错误友好映射
 * 把 HTTP 状态码 / SSE 错误事件 / 异常对象 → 用户能看懂的提示 + 重试建议
 *
 * 设计原则：
 * - 不暴露技术黑话（internal_error、Unknown column 等）
 * - 给出具体可操作的下一步
 * - 区分"用户能解决"vs"系统性问题"
 */

export type ErrorCategory = "network" | "auth" | "rate" | "server" | "data" | "abort" | "unknown";

export type FriendlyError = {
  /** 顶部标题（用户看到的） */
  title: string;
  /** 详细说明 + 建议 */
  detail: string;
  /** Toast 类型 */
  toastType: "error" | "warning";
  /** 是否建议用户重试 */
  retryable: boolean;
};

// HTTP 状态码 → 友好错误
const HTTP_STATUS_MAP: Record<number, FriendlyError> = {
  400: {
    title: "请求格式错误",
    detail: "问数请求格式不正确，请刷新页面后重试。",
    toastType: "error",
    retryable: true,
  },
  401: {
    title: "需要登录",
    detail: "会话已过期，请重新登录后重试。",
    toastType: "error",
    retryable: true,
  },
  403: {
    title: "无访问权限",
    detail: "当前账号无权使用问数功能，请联系管理员开通权限。",
    toastType: "error",
    retryable: false,
  },
  404: {
    title: "服务接口不存在",
    detail: "问数服务接口地址异常，请联系管理员检查后端配置。",
    toastType: "error",
    retryable: false,
  },
  408: {
    title: "请求超时",
    detail: "问数服务响应过慢，请稍后重试。",
    toastType: "warning",
    retryable: true,
  },
  429: {
    title: "请求过于频繁",
    detail: "系统限流保护已触发，请等待几秒后重试。",
    toastType: "warning",
    retryable: true,
  },
  500: {
    title: "服务异常",
    detail: "后端服务出现内部错误，已记录日志，请稍后重试或联系管理员。",
    toastType: "error",
    retryable: true,
  },
  502: {
    title: "网关异常",
    detail: "后端网关不可达，请稍后重试。",
    toastType: "error",
    retryable: true,
  },
  503: {
    title: "服务暂不可用",
    detail: "服务正在维护或重启中，请稍后重试。",
    toastType: "warning",
    retryable: true,
  },
  504: {
    title: "网关超时",
    detail: "后端响应超时，请稍后重试。",
    toastType: "warning",
    retryable: true,
  },
};

// 后端 message 关键字 → 友好错误（用于 SSE error 事件）
// 按最常见 → 最罕见排序
const BACKEND_KEYWORD_MAP: Array<{ pattern: RegExp; error: FriendlyError }> = [
  {
    pattern: /Unknown column|unknown column|字段不存在|column not found/i,
    error: {
      title: "字段未声明",
      detail: "你问的字段在元数据中尚未声明，请联系管理员补充字段定义后再试。",
      toastType: "warning",
      retryable: false,
    },
  },
  {
    pattern: /Table.*doesn't exist|table not found|表不存在/i,
    error: {
      title: "数据表不存在",
      detail: "你问的数据表未在元数据中注册，请检查问法或联系管理员。",
      toastType: "warning",
      retryable: false,
    },
  },
  {
    pattern: /Access denied|权限不足|permission denied/i,
    error: {
      title: "权限不足",
      detail: "当前账号无权访问这部分数据，请联系管理员开通权限。",
      toastType: "error",
      retryable: false,
    },
  },
  {
    pattern: /SQL syntax|语法错误|syntax error/i,
    error: {
      title: "SQL 生成失败",
      detail: "智能体生成的 SQL 有语法问题，已自动重试，若仍失败请换个问法。",
      toastType: "warning",
      retryable: true,
    },
  },
  {
    pattern: /METADATA_MISSING|metadata missing/i,
    error: {
      title: "元数据缺失",
      detail: "数仓元数据未完整初始化，请联系管理员运行 build_meta_knowledge.py。",
      toastType: "error",
      retryable: false,
    },
  },
  {
    pattern: /LLM.*timeout|模型超时|model timeout/i,
    error: {
      title: "模型响应超时",
      detail: "大模型响应过慢，请稍后重试或简化问题描述。",
      toastType: "warning",
      retryable: true,
    },
  },
  {
    pattern: /timeout|timed out/i,
    error: {
      title: "请求超时",
      detail: "问数处理耗时过长，请稍后重试或简化问题。",
      toastType: "warning",
      retryable: true,
    },
  },
];

/**
 * 把 HTTP status code 转友好错误
 */
export function mapHttpError(status: number): FriendlyError {
  return (
    HTTP_STATUS_MAP[status] ?? {
      title: `请求失败（HTTP ${status}）`,
      detail: "问数服务返回错误状态码，请稍后重试或联系管理员。",
      toastType: "error",
      retryable: true,
    }
  );
}

/**
 * 把后端 SSE error 事件的 message 转友好错误
 * 优先关键字匹配，否则保留原 message 让用户看到具体信息
 */
export function mapBackendMessage(message: string): FriendlyError {
  for (const { pattern, error } of BACKEND_KEYWORD_MAP) {
    if (pattern.test(message)) {
      return error;
    }
  }
  // 没匹配到关键字 → 透传后端消息（可能是具体业务错误）
  return {
    title: "查询失败",
    detail: message || "后端返回了未识别的错误，请稍后重试或联系管理员。",
    toastType: "error",
    retryable: true,
  };
}

/**
 * 把 JS 异常对象转友好错误
 */
export function mapJsError(error: unknown): FriendlyError {
  // 用户主动停止 → 不算错误
  if (error instanceof DOMException && error.name === "AbortError") {
    return {
      title: "已停止",
      detail: "查询已被手动取消。",
      toastType: "warning",
      retryable: false,
    };
  }
  // 网络异常
  if (error instanceof TypeError && /fetch|network/i.test(error.message)) {
    return {
      title: "网络异常",
      detail: "无法连接问数服务，请检查网络后重试。",
      toastType: "error",
      retryable: true,
    };
  }
  // 其他 Error
  if (error instanceof Error) {
    return {
      title: "查询出错",
      detail: error.message || "未知错误，请稍后重试。",
      toastType: "error",
      retryable: true,
    };
  }
  return {
    title: "查询出错",
    detail: String(error),
    toastType: "error",
    retryable: true,
  };
}