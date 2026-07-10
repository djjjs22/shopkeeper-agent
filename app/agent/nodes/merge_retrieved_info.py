"""
召回信息合并节点

负责把字段 字段取值和指标三路召回结果聚合成统一上下文
这一层会补齐指标依赖字段 字段真实取值 主外键字段和表信息
后续过滤节点不再关心信息来自哪个检索分支，只处理合并后的表上下文和指标上下文
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import (
    ColumnInfoState,
    DataAgentState,
    MetricInfoState,
    TableInfoState,
)
from app.core.log import logger
from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.table_info import TableInfo
from app.entities.value_info import ValueInfo


async def merge_retrieved_info(
    state: DataAgentState, runtime: Runtime[DataAgentContext]
):
    """合并召回结果，并输出 SQL 生成前的候选表信息和指标信息"""

    writer = runtime.stream_writer
    step = "合并召回信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        retrieved_column_infos: list[ColumnInfo] = state["retrieved_column_infos"]
        retrieved_metric_infos: list[MetricInfo] = state["retrieved_metric_infos"]
        retrieved_value_infos: list[ValueInfo] = state["retrieved_value_infos"]

        meta_mysql_repository = runtime.context["meta_mysql_repository"]

        # 本节点的主线是：
        # 字段召回 + 指标依赖字段 + 字段真实取值 + 主外键补齐
        # -> table_infos / metric_infos，交给后续过滤和 SQL 生成节点继续使用。

        # 1. 以 column_id 为 key 合并字段信息
        # 三路召回可能命中同一个字段，用 dict 可以自然去重；
        # 后续指标依赖字段和字段取值也都通过 column_id 合并进来。
        retrieved_column_infos_map: dict[str, ColumnInfo] = {
            retrieved_column_info.id: retrieved_column_info
            for retrieved_column_info in retrieved_column_infos
        }

        # 2 & 3. 收集需要补齐的字段 id（指标依赖字段 + 字段取值对应的字段）
        # 一次性批量查询，避免逐个 get_by_id 的 N+1 串行开销（刀 15）
        missing_column_ids: set[str] = set()
        for retrieved_metric_info in retrieved_metric_infos:
            for relevant_column in retrieved_metric_info.relevant_columns:
                if relevant_column not in retrieved_column_infos_map:
                    missing_column_ids.add(relevant_column)
        for retrieved_value_info in retrieved_value_infos:
            if retrieved_value_info.column_id not in retrieved_column_infos_map:
                missing_column_ids.add(retrieved_value_info.column_id)

        if missing_column_ids:
            extra_column_infos = (
                await meta_mysql_repository.get_column_infos_by_ids(
                    list(missing_column_ids)
                )
            )
            for column_info in extra_column_infos:
                if column_info is not None:
                    retrieved_column_infos_map[column_info.id] = column_info

        # 把字段取值合并回字段 examples
        # 字段取值召回命中的是 column_id + value，例如 dim_region.region_name.华北。
        # 把真实 value 放进字段 examples，可以帮助模型写出更接近真实数据的 where 条件。
        for retrieved_value_info in retrieved_value_infos:
            value = retrieved_value_info.value
            column_id = retrieved_value_info.column_id
            if column_id in retrieved_column_infos_map:
                if value not in retrieved_column_infos_map[column_id].examples:
                    retrieved_column_infos_map[column_id].examples.append(value)

        # 4. 按表组织字段上下文
        # SQL 生成提示词通常按“表 -> 字段列表”的方式描述结构，
        # 所以这里先把分散的字段按 table_id 归到各自所属表下面。
        table_to_columns_map: dict[str, list[ColumnInfo]] = {}
        for column_info in retrieved_column_infos_map.values():
            table_id = column_info.table_id
            if table_id not in table_to_columns_map:
                table_to_columns_map[table_id] = []
            table_to_columns_map[table_id].append(column_info)

        # 5. 补齐主外键字段（批量查询后按 table_id 分组）
        # 主外键通常不会出现在用户问题里，单靠向量召回容易漏掉；
        # 但多表查询的 Join 路径必须依赖它们，所以每张候选表都要兜底补齐。
        all_table_ids = list(table_to_columns_map.keys())
        key_column_infos = (
            await meta_mysql_repository.get_key_columns_by_table_ids(all_table_ids)
            if all_table_ids
            else []
        )
        table_to_key_columns_map: dict[str, list[ColumnInfo]] = {}
        for key_column in key_column_infos:
            table_to_key_columns_map.setdefault(key_column.table_id, []).append(
                key_column
            )

        for table_id, column_infos in table_to_columns_map.items():
            column_ids = [column_info.id for column_info in column_infos]
            for key_column in table_to_key_columns_map.get(table_id, []):
                if key_column.id not in column_ids:
                    table_to_columns_map[table_id].append(key_column)

        # 6. 生成表结构上下文（批量查询表信息，避免逐表 N+1 查询）
        # 数据库实体里可能包含入库和索引用字段，传给模型前只保留必要信息，
        # 让后续过滤和 SQL 生成节点面对的是更稳定的 TableInfoState 结构。
        table_infos_result = (
            await meta_mysql_repository.get_table_infos_by_ids(all_table_ids)
            if all_table_ids
            else []
        )
        table_info_map: dict[str, TableInfo] = {
            table_info.id: table_info for table_info in table_infos_result
        }

        table_infos: list[TableInfoState] = []
        for table_id, column_infos in table_to_columns_map.items():
            table_info = table_info_map.get(table_id)
            # 兜底：极端情况下表信息查不到时跳过该表，避免整条链路崩
            if table_info is None:
                logger.warning(f"合并召回信息：未找到表 {table_id} 的元数据，已跳过")
                continue
            columns = [
                ColumnInfoState(
                    name=column_info.name,
                    type=column_info.type,
                    role=column_info.role,
                    examples=column_info.examples,
                    description=column_info.description,
                    alias=column_info.alias,
                )
                for column_info in column_infos
            ]
            table_info_state = TableInfoState(
                name=table_info.name,
                role=table_info.role,
                description=table_info.description,
                columns=columns,
            )
            table_infos.append(table_info_state)

        # 7. 生成指标上下文
        # 指标上下文保留名称 描述 别名和依赖字段，足够让模型理解业务口径。
        metric_infos: list[MetricInfoState] = [
            MetricInfoState(
                name=retrieved_metric_info.name,
                description=retrieved_metric_info.description,
                relevant_columns=retrieved_metric_info.relevant_columns,
                alias=retrieved_metric_info.alias,
            )
            for retrieved_metric_info in retrieved_metric_infos
        ]

        logger.info(
            f"合并后的表信息：{[table_info['name'] for table_info in table_infos]}"
        )
        logger.info(
            f"合并后的指标信息：{[metric_info['name'] for metric_info in metric_infos]}"
        )

        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "table_infos": table_infos,
            "metric_infos": metric_infos,
        }
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
