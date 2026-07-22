"""
元数据库 MySQL 仓储

这一层对应文档里的 Meta Repository，负责接收业务实体并落到 Meta MySQL
Repository 自身只关心“如何写入”，而“哪些写操作要放在同一笔事务里”，由 Service 层统一决定

表 字段 指标和字段指标关系都会先以业务实体流转，再在这里统一转成 ORM 模型
问数链路运行时也会从这里读取元数据，用来把召回到的 id 补齐成完整实体
"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.bad_case import BadCase
from app.entities.column_info import ColumnInfo
from app.entities.column_metric import ColumnMetric
from app.entities.metric_info import MetricInfo
from app.entities.query_log import QueryLog
from app.entities.sql_pattern import SqlPattern
from app.entities.table_info import TableInfo
from app.entities.user_profile import UserProfile
from app.models.column_info import ColumnInfoMySQL
from app.models.table_info import TableInfoMySQL
from app.repositories.mysql.meta.mappers.bad_case_mapper import BadCaseMapper
from app.repositories.mysql.meta.mappers.column_info_mapper import ColumnInfoMapper
from app.repositories.mysql.meta.mappers.column_metric_mapper import ColumnMetricMapper
from app.repositories.mysql.meta.mappers.metric_info_mapper import MetricInfoMapper
from app.repositories.mysql.meta.mappers.query_log_mapper import QueryLogMapper
from app.repositories.mysql.meta.mappers.sql_pattern_mapper import SqlPatternMapper
from app.repositories.mysql.meta.mappers.table_info_mapper import TableInfoMapper
from app.repositories.mysql.meta.mappers.user_profile_mapper import UserProfileMapper


def _normalize_column_info_row(row: dict) -> ColumnInfo:
    """把 column_info 查询结果标准化为 ColumnInfo 实体。

    背景：
    - column_info.examples 和 column_info.alias 在 MySQL 里是 JSON 类型字段。
    - SQLAlchemy 从不同驱动/不同查询路径取出来时，可能已经是 list，
      也可能还是 JSON 字符串，例如 '["微信支付", "支付宝"]'。
    - 下游 merge_retrieved_info 节点会执行 examples.append(value)。
      如果 examples 还是 str，就会触发：'str' object has no attribute 'append'。

    处理策略：
    - 每次从 column_info 表批量查询字段元数据时，统一把 examples/alias 转回 list。
    - 如果 JSON 内容异常，降级为空 list，避免整条问数链路因为单个脏字段中断。
    - 只在 Repository 出口做一次标准化，保证后续 Agent 节点拿到的数据结构稳定。
    """

    data = dict(row)

    for key in ("examples", "alias"):
        value = data.get(key)

        if isinstance(value, str):
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                data[key] = []

    return ColumnInfo(**data)


class MetaMySQLRepository:
    """负责把元数据业务实体持久化到 Meta MySQL"""

    def __init__(self, session: AsyncSession):
        self.session = session

    def save_table_infos(self, table_infos: list[TableInfo]):
        """批量保存表元数据。输入仍然是业务实体，而不是 ORM 模型"""
        self.session.add_all(
            [TableInfoMapper.to_model(table_info) for table_info in table_infos]
        )

    def save_column_infos(self, column_infos: list[ColumnInfo]):
        """批量保存字段元数据。实体到模型的转换统一通过 Mapper 完成"""
        self.session.add_all(
            [ColumnInfoMapper.to_model(column_info) for column_info in column_infos]
        )

    def save_metric_infos(self, metric_infos: list[MetricInfo]):
        """批量保存指标元数据。指标本身和字段关联关系分开写入"""
        self.session.add_all(
            [MetricInfoMapper.to_model(metric_info) for metric_info in metric_infos]
        )

    def save_column_metrics(self, column_metrics: list[ColumnMetric]):
        """批量保存字段与指标的关联关系"""
        self.session.add_all(
            [
                ColumnMetricMapper.to_model(column_metric)
                for column_metric in column_metrics
            ]
        )

    async def get_column_info_by_id(self, id: str) -> ColumnInfo | None:
        """按字段 id 查询字段元数据，供召回信息合并阶段补齐字段上下文"""

        column_info: ColumnInfoMySQL | None = await self.session.get(
            ColumnInfoMySQL, id
        )
        if column_info:
            return ColumnInfoMapper.to_entity(column_info)
        else:
            return None

    async def get_table_info_by_id(self, id: str) -> TableInfo | None:
        """按表 id 查询表元数据，最终组装成提示词里的表结构信息"""

        table_info: TableInfoMySQL | None = await self.session.get(TableInfoMySQL, id)
        if table_info:
            return TableInfoMapper.to_entity(table_info)
        else:
            return None

    async def get_key_columns_by_table_id(self, table_id: str) -> list[ColumnInfo]:
        """查询指定表的主外键字段，避免 Join 关键字段被向量召回漏掉"""

        # 主外键字段用于后续生成 join 条件，不能完全依赖向量召回命中
        sql = "select * from column_info where table_id = :table_id and role in ('primary_key','foreign_key')"
        # :table_id 是 SQLAlchemy text SQL 的占位符，实际值通过第二个参数传入
        result = await self.session.execute(text(sql), {"table_id": table_id})
        # mappings() 会把结果行转成类似字典的结构，便于解包成 ColumnInfo
        return [_normalize_column_info_row(row) for row in result.mappings().fetchall()]

    async def get_column_infos_by_ids(self, ids: list[str]) -> list[ColumnInfo]:
        """按多个字段 id 批量查询，避免合并阶段 N+1 串行查询（刀 15）"""

        if not ids:
            return []
        sql = "select * from column_info where id in :ids"
        # 传入 tuple，SQLAlchemy 会自动展开为 IN 占位符列表
        result = await self.session.execute(text(sql), {"ids": tuple(ids)})
        return [_normalize_column_info_row(row) for row in result.mappings().fetchall()]

    async def get_table_infos_by_ids(self, ids: list[str]) -> list[TableInfo]:
        """按多个表 id 批量查询，避免合并阶段 N+1 串行查询（刀 15）"""

        if not ids:
            return []
        sql = "select * from table_info where id in :ids"
        result = await self.session.execute(text(sql), {"ids": tuple(ids)})
        return [TableInfo(**dict(row)) for row in result.mappings().fetchall()]

    async def get_key_columns_by_table_ids(
        self, table_ids: list[str]
    ) -> list[ColumnInfo]:
        """按多个表 id 批量查询主外键字段，避免 N+1 串行查询（刀 15）

        返回扁平列表，调用方按 column_info.table_id 自行分组。
        """

        if not table_ids:
            return []
        sql = (
            "select * from column_info where table_id in :table_ids "
            "and role in ('primary_key','foreign_key')"
        )
        result = await self.session.execute(text(sql), {"table_ids": tuple(table_ids)})
        return [_normalize_column_info_row(row) for row in result.mappings().fetchall()]

    async def get_all_table_infos(self) -> list[TableInfo]:
        """查询所有表元数据，供元数据查询短路节点（respond_metadata）使用"""
        result = await self.session.execute(text("select * from table_info"))
        return [TableInfo(**dict(row)) for row in result.mappings().fetchall()]

    async def get_columns_by_table_id(self, table_id: str) -> list[ColumnInfo]:
        """查询指定表的所有字段，供元数据查询短路节点使用"""
        sql = "select * from column_info where table_id = :table_id"
        result = await self.session.execute(text(sql), {"table_id": table_id})
        return [_normalize_column_info_row(row) for row in result.mappings().fetchall()]

    async def get_all_metric_infos(self) -> list[MetricInfo]:
        """查询所有指标元数据，供元数据查询短路节点使用"""
        result = await self.session.execute(text("select * from metric_info"))
        rows = result.mappings().fetchall()
        entities = []
        for row in rows:
            d = dict(row)
            # relevant_columns 和 alias 在 MySQL 里以 JSON 字符串存储，需解析为 list
            import json
            if isinstance(d.get("relevant_columns"), str):
                d["relevant_columns"] = json.loads(d["relevant_columns"])
            if isinstance(d.get("alias"), str):
                d["alias"] = json.loads(d["alias"])
            entities.append(MetricInfo(**d))
        return entities

    # ════════════════════════════════════════════════════════════════════
    # 升级表（user_profile / sql_pattern / query_log / bad_case）
    # 对应 docs/AI应用架构升级路线.md 第 4 章 Memory + 第 5 章 Eval/飞轮
    # ════════════════════════════════════════════════════════════════════

    # ── user_profile（Semantic Memory）──
    def save_user_profile(self, profile: UserProfile) -> None:
        """保存用户偏好（UPSERT 语义在 service 层处理冲突，这里只 add）"""
        self.session.add(UserProfileMapper.to_model(profile))

    async def upsert_user_profile(
        self, user_id: str, preference_type: str, content: str, confidence: float
    ) -> None:
        """UPSERT 用户偏好：同 (user_id, preference_type) 存在则覆盖 content+confidence"""
        await self.session.execute(
            text(
                "INSERT INTO user_profile (user_id, preference_type, content, confidence) "
                "VALUES (:uid, :ptype, :content, :conf) "
                "ON DUPLICATE KEY UPDATE content = :content, confidence = :conf"
            ),
            {"uid": user_id, "ptype": preference_type, "content": content, "conf": confidence},
        )

    async def get_user_profiles(self, user_id: str) -> list[UserProfile]:
        """查询某用户所有偏好，供 generate_intent 注入 prompt"""
        result = await self.session.execute(
            text("select * from user_profile where user_id = :uid"), {"uid": user_id}
        )
        return [UserProfileMapper.to_entity_dict(dict(row)) for row in result.mappings().fetchall()]

    async def delete_low_confidence_profiles(self, threshold: float = 0.3) -> int:
        """遗忘机制：删除置信度低于阈值的偏好，返回删除条数"""
        result = await self.session.execute(
            text("delete from user_profile where confidence < :t"), {"t": threshold}
        )
        return result.rowcount

    # ── sql_pattern（Procedural Memory）──
    def save_sql_pattern(self, pattern: SqlPattern) -> None:
        """保存 SQL 模板（id 冲突时由 service 层先查再决定 update）"""
        self.session.add(SqlPatternMapper.to_model(pattern))

    async def upsert_sql_pattern(self, pattern: SqlPattern) -> None:
        """UPSERT SQL 模板：id 存在则更新 sql_template/confidence/hit_count/vector_id/tags"""
        await self.session.execute(
            text(
                "INSERT INTO sql_pattern (id, query_intent_text, sql_template, source, "
                "confidence, hit_count, vector_id, tags) "
                "VALUES (:id, :qit, :st, :src, :conf, :hc, :vid, :tags) "
                "ON DUPLICATE KEY UPDATE sql_template = :st, confidence = :conf, "
                "hit_count = :hc, vector_id = :vid, tags = :tags"
            ),
            {
                "id": pattern.id,
                "qit": pattern.query_intent_text,
                "st": pattern.sql_template,
                "src": pattern.source,
                "conf": pattern.confidence,
                "hc": pattern.hit_count,
                "vid": pattern.vector_id,
                "tags": json.dumps(pattern.tags or [], ensure_ascii=False),
            },
        )

    async def get_sql_pattern_by_id(self, pattern_id: str) -> SqlPattern | None:
        result = await self.session.execute(
            text("select * from sql_pattern where id = :id"), {"id": pattern_id}
        )
        row = result.mappings().first()
        if row is None:
            return None
        return SqlPatternMapper.to_entity_from_row(dict(row))

    async def increment_pattern_hit_count(self, pattern_id: str) -> None:
        """召回命中并跑通后调用（hit_count 影响 online 来源置信度）"""
        await self.session.execute(
            text("update sql_pattern set hit_count = hit_count + 1 where id = :id"),
            {"id": pattern_id},
        )

    async def get_all_sql_patterns(self) -> list[SqlPattern]:
        """全量读取（重建 Qdrant 索引用）"""
        result = await self.session.execute(text("select * from sql_pattern"))
        return [SqlPatternMapper.to_entity_from_row(dict(row)) for row in result.mappings().fetchall()]

    # ── query_log（飞轮信号源）──
    def save_query_log(self, log: QueryLog) -> None:
        """保存一次查询日志"""
        self.session.add(QueryLogMapper.to_model(log))

    async def get_recent_successful_query_logs(self, limit: int = 100) -> list[QueryLog]:
        """读最近 N 条成功查询，供 pattern_bank_service 消费"""
        result = await self.session.execute(
            text("select * from query_log where success = 1 order by id desc limit :lim"),
            {"lim": limit},
        )
        return [QueryLogMapper.to_entity_from_row(dict(row)) for row in result.mappings().fetchall()]

    # ── bad_case（失败 case 归集）──
    def save_bad_case(self, case: BadCase) -> None:
        """保存一条失败 case（去重在 bad_case_collector 层做）"""
        self.session.add(BadCaseMapper.to_model(case))

    async def get_bad_cases(
        self, status: str = "new", limit: int = 50, error_type: str | None = None
    ) -> list[BadCase]:
        """按状态/类型查询失败 case（周报用）"""
        if error_type:
            sql = (
                "select * from bad_case where status = :status and error_type = :etype "
                "order by id desc limit :lim"
            )
            params = {"status": status, "etype": error_type, "lim": limit}
        else:
            sql = "select * from bad_case where status = :status order by id desc limit :lim"
            params = {"status": status, "lim": limit}
        result = await self.session.execute(text(sql), params)
        return [BadCaseMapper.to_entity_from_row(dict(row)) for row in result.mappings().fetchall()]

    async def count_bad_cases_by_error_type(self) -> dict[str, int]:
        """按 error_type 聚合（周报用）"""
        result = await self.session.execute(
            text("select error_type, count(*) as cnt from bad_case group by error_type")
        )
        return {row.error_type: row.cnt for row in result}
