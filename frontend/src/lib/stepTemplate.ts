/**
 * 执行流程步骤模板
 *
 * 后端 LangGraph 图的节点顺序是固定的（详见 app/agent/graph.py），
 * 但 SSE 只推送"已开始/已结束"事件，pending 阶段不发任何消息，
 * 导致前端只看到已完成的步骤、看不到完整计划。
 *
 * 这里把每条意图路径的完整步骤预定义好，前端在发起 query 时
 * 先把模板填进 message.steps（全部 pending），后端事件流来了再
 * 覆盖对应步骤的状态。这样用户从一开始就能看到完整执行计划。
 *
 * 注意：步骤名必须和后端 writer({"step": ..., ...}) 完全一致，
 *   否则 upsertStep 找不到匹配项会变成追加重复。
 */
export type IntentKey = "data_query" | "chitchat" | "metadata_query";

export const STEP_TEMPLATES: Record<IntentKey, readonly string[]> = {
  // 闲聊：分类后直接响应
  chitchat: ["意图分类", "闲聊响应"],
  // 元数据查询：分类后直接响应
  metadata_query: ["意图分类", "元数据响应"],
  // 数据查询：完整 14 步 RAG + SQL 链路
  // 顺序按后端实际事件先后排列；并行节点按字母序排（recall_/filter_/generate_）
  data_query: [
    "意图分类",
    "查询改写",
    "抽取关键词",
    "召回字段信息",
    "召回指标信息",
    "召回字段取值",
    "合并召回信息",
    "过滤表信息",
    "过滤指标信息",
    "添加额外上下文",
    "生成查询意图",
    "生成SQL",
    "校验SQL",
    "执行SQL",
  ],
} as const;

/**
 * 创建一个全 pending 的 steps 数组
 * 用 data_query 作为默认模板——绝大多数查询走这条路径
 */
export function buildPendingSteps(intent: IntentKey = "data_query"): {
  step: string;
  status: "pending";
  updatedAt: number;
}[] {
  const now = Date.now();
  return STEP_TEMPLATES[intent].map((step) => ({
    step,
    status: "pending" as const,
    updatedAt: now,
  }));
}

/**
 * 用后端事件流更新步骤状态
 * - 后端事件里出现的步骤：覆盖状态
 * - 后端事件里没有、但模板里有：保持 pending（用户可看到还没跑的步骤）
 * - 模板里没有、后端事件出现：append（防御性，应对后端加新节点）
 */
export function mergeStepEvent<
  T extends { step: string; status: "pending" | "running" | "success" | "error"; updatedAt: number },
>(
  steps: T[],
  event: { step: string; status: "running" | "success" | "error"; updatedAt?: number },
): T[] {
  const now = Date.now();
  const idx = steps.findIndex((s) => s.step === event.step);
  if (idx === -1) {
    // 模板里没有的步骤（比如后端新增节点），append 到末尾
    return [
      ...steps,
      {
        step: event.step,
        status: event.status,
        updatedAt: event.updatedAt ?? now,
      } as T,
    ];
  }
  // 模板里有的步骤：原位覆盖状态，保留位置顺序
  const next = steps.slice();
  next[idx] = { ...next[idx], status: event.status, updatedAt: event.updatedAt ?? now };
  return next;
}