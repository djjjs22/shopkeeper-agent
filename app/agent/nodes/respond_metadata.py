"""
元数据查询响应节点

当意图分类判定为 metadata_query 时走这条短路。
用户想了解数据库本身的结构（有哪些表、字段、指标怎么算），
而不是查业务数据。直接从 Meta MySQL 读取元数据返回，不生成 SQL。

支持的查询模式（基于关键词匹配，简单但够用）：
  - "有哪些表" / "表列表" → 返回所有表名和描述
  - "xxx表有哪些字段" / "xxx表结构" → 返回指定表的字段列表
  - "xxx怎么算的" / "xxx指标" → 返回指标定义和依赖字段
"""

import re

import jieba
from app.core.timing import timed_node
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


def _find_mentioned_names(query: str, names: list[str]) -> list[str]:
    """从 query 中找出所有明确提及的名称（表名或指标名）

    策略（刀 12）：
    1. jieba 分词，命中完整 token 的名称优先（最准）
    2. 未被分词命中的名称（如 dim_xxx），回退到「整词边界」子串匹配：
       用正则确保名称前后不是字母/数字/下划线，避免 "data" 误命中 "database"
    3. 收集所有匹配后，剔除被其他匹配名称完整包含的短名称
       （如 dim_date 被 dim_date_new 包含时，只保留 dim_date_new）
    4. 支持多名称同时出现（如「dim_product 和 dim_customer 的字段」）
    """
    tokens = set(jieba.lcut(query))
    matched = [name for name in names if name in tokens]
    # 分词未命中时（dim_xxx 这类 jieba 切不出的名称），用边界子串回退
    if not matched:
        for name in names:
            if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", query):
                matched.append(name)
    # 去子串：剔除被其他匹配名称完整包含的短名称
    final = [
        name
        for name in matched
        if not any(other != name and name in other for other in matched)
    ]
    return final


def _is_table_list_query(query: str) -> bool:
    """判断是否在问有哪些表"""
    table_list_keywords = ["有哪些表", "表列表", "所有表", "都有哪些表", "什么表"]
    return any(kw in query for kw in table_list_keywords)


def _is_table_schema_query(query: str) -> bool:
    """判断是否在问某张表的字段结构"""
    schema_keywords = ["有哪些字段", "表结构", "字段列表", "什么字段", "有哪些列"]
    return any(kw in query for kw in schema_keywords)


def _is_metric_definition_query(query: str) -> bool:
    """判断是否在问指标定义"""
    metric_keywords = ["怎么算", "是什么意思", "指标定义", "怎么定义", "怎么计算"]
    return any(kw in query for kw in metric_keywords)


@timed_node
async def respond_metadata(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """元数据查询短路响应：直接查 Meta MySQL 返回，不走 RAG 链路"""

    writer = runtime.stream_writer
    step = "元数据查询"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        meta_mysql_repository = runtime.context["meta_mysql_repository"]

        result = []

        if _is_table_list_query(query):
            # 查所有表
            tables = await meta_mysql_repository.get_all_table_infos()
            result = [{"表名": t.name, "描述": t.description, "角色": t.role} for t in tables]
            logger.info(f"元数据查询-表列表: {len(result)} 张表")

        elif _is_table_schema_query(query):
            # 查某张（或多张）表的字段——从 query 里提取表名
            tables = await meta_mysql_repository.get_all_table_infos()
            table_names = [t.name for t in tables]

            # 刀 12：全匹配 + 去子串 + 多表支持，不再 break 在第一个
            matched_tables = _find_mentioned_names(query, table_names)

            if not matched_tables:
                writer({
                    "type": "warning",
                    "message": f"未找到匹配的表名，请明确指定表名（可用表：{', '.join(table_names)}）",
                })
            else:
                for table_name in matched_tables:
                    columns = await meta_mysql_repository.get_columns_by_table_id(table_name)
                    result.extend([
                        {
                            "表名": table_name,
                            "字段名": c.name,
                            "类型": c.type,
                            "描述": c.description,
                            "角色": c.role,
                            "样例": c.examples[:3] if c.examples else [],
                        }
                        for c in columns
                    ])
                logger.info(f"元数据查询-表字段: {matched_tables}")

        elif _is_metric_definition_query(query):
            # 查指标定义——需要从 query 里提取指标名
            all_metrics = await meta_mysql_repository.get_all_metric_infos()
            metric_names = [m.name for m in all_metrics]

            # 刀 12：全匹配 + 去子串 + 多指标支持
            matched_metrics = _find_mentioned_names(query, metric_names)

            if not matched_metrics:
                writer({
                    "type": "warning",
                    "message": f"未找到匹配的指标名，请明确指定指标名（可用指标：{', '.join(metric_names)}）",
                })
            else:
                for metric_name in matched_metrics:
                    metric = next(m for m in all_metrics if m.name == metric_name)
                    result.append({
                        "指标名": metric.name,
                        "描述": metric.description,
                        "依赖字段": metric.relevant_columns,
                        "别名": metric.alias,
                    })
                logger.info(f"元数据查询-指标定义: {matched_metrics}")

        else:
            # 兜底：无法识别具体元数据查询意图
            writer({
                "type": "warning",
                "message": "无法识别您想查询的元数据，请明确指定：表列表、某张表的字段、或某指标的定义",
            })

        writer({"type": "progress", "step": step, "status": "success"})
        writer({"type": "result", "data": result})

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        writer({"type": "error", "message": str(e)})
        raise
