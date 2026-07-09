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

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


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
            # 查某张表的字段——需要从 query 里提取表名
            # 简单做法：找 query 里包含 "表" 前面的词组，或直接匹配已知表名
            tables = await meta_mysql_repository.get_all_table_infos()
            table_names = [t.name for t in tables]

            matched_table = None
            for name in table_names:
                if name in query:
                    matched_table = name
                    break

            if matched_table:
                columns = await meta_mysql_repository.get_columns_by_table_id(matched_table)
                result = [
                    {
                        "字段名": c.name,
                        "类型": c.type,
                        "描述": c.description,
                        "角色": c.role,
                        "样例": c.examples[:3] if c.examples else [],
                    }
                    for c in columns
                ]
                logger.info(f"元数据查询-表字段: {matched_table} 有 {len(result)} 个字段")
            else:
                writer({
                    "type": "warning",
                    "message": f"未找到匹配的表名，请明确指定表名（可用表：{', '.join(table_names)}）",
                })

        elif _is_metric_definition_query(query):
            # 查指标定义——需要从 query 里提取指标名
            all_metrics = await meta_mysql_repository.get_all_metric_infos()
            metric_names = [m.name for m in all_metrics]

            matched_metric = None
            for name in metric_names:
                if name in query:
                    matched_metric = name
                    break

            if matched_metric:
                metric = next(m for m in all_metrics if m.name == matched_metric)
                result = [{
                    "指标名": metric.name,
                    "描述": metric.description,
                    "依赖字段": metric.relevant_columns,
                    "别名": metric.alias,
                }]
                logger.info(f"元数据查询-指标定义: {matched_metric}")
            else:
                writer({
                    "type": "warning",
                    "message": f"未找到匹配的指标名，请明确指定指标名（可用指标：{', '.join(metric_names)}）",
                })

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
