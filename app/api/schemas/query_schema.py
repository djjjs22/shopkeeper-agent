"""
问数接口请求体定义

集中声明 API 层输入输出的数据结构，让路由函数只处理业务流程，
字段校验和 OpenAPI 文档生成交给 Pydantic 与 FastAPI 完成。
"""

from pydantic import BaseModel, Field


class QuerySchema(BaseModel):
    """`/api/query` 请求体，承载用户输入的自然语言问题"""

    # 前端请求体中的 query 字段，例如 {"query": "统计华北地区销售额"}
    # max_length=500 防止超大 payload 灌进 jieba + LLM（刀 10）
    query: str = Field(max_length=500, description="用户查询文本")

    # 2026-07-17 改造：Multi-Agent 开关
    # 默认 False（保持向后兼容，走老 13 节点 graph）
    # True 时走 supervisor_graph（planner → multi-sub → aggregator → reviewer）
    use_multi_agent: bool = Field(
        default=False,
        description="是否启用 Multi-Agent 模式（planner 拆 sub_query 并行执行）",
    )
