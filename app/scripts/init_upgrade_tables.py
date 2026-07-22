"""
升级表初始化脚本

创建 Memory + Eval + 数据飞轮所需的 4 张表：
  - user_profile   （Semantic Memory）
  - sql_pattern    （Procedural Memory）
  - query_log      （飞轮信号源）
  - bad_case       （失败 case 归集）

运行方式：
  cd D:/shopkeeper-agent
  uv run python -m app.scripts.init_upgrade_tables

设计：
  用 CREATE TABLE IF NOT EXISTS inline DDL（参照 archive_sessions.py 风格），
  不引入 Alembic migration 框架（项目无 migration 目录，保持一致）。

  注意：DDL 字段定义必须与 app/models/*.py ORM 模型保持一致——
  ORM 依赖这些表存在才能查询，否则会抛 Table doesn't exist。
"""

import asyncio
import sys

from sqlalchemy import text

from app.clients.mysql_client_manager import meta_mysql_client_manager
from app.core.log import logger


# 4 张表的 DDL（IF NOT EXISTS 保证幂等，可重复跑）
_DDL_STATEMENTS = [
    # ── user_profile：用户偏好（Semantic Memory）──
    """
    CREATE TABLE IF NOT EXISTS user_profile (
        id INT NOT NULL AUTO_INCREMENT,
        user_id VARCHAR(64) NOT NULL,
        preference_type VARCHAR(32) NOT NULL,
        content TEXT NOT NULL,
        confidence FLOAT NOT NULL DEFAULT 0.5,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        INDEX idx_user_profile_user_id (user_id),
        UNIQUE KEY uk_user_profile_user_type (user_id, preference_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户长期偏好'
    """,
    # ── sql_pattern：SQL 模板（Procedural Memory）──
    """
    CREATE TABLE IF NOT EXISTS sql_pattern (
        id VARCHAR(64) NOT NULL,
        query_intent_text TEXT NOT NULL,
        sql_template TEXT NOT NULL,
        source VARCHAR(16) NOT NULL DEFAULT 'online',
        confidence FLOAT NOT NULL DEFAULT 0.5,
        hit_count INT NOT NULL DEFAULT 0,
        vector_id VARCHAR(64) DEFAULT NULL,
        tags JSON DEFAULT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        INDEX idx_sql_pattern_source (source),
        INDEX idx_sql_pattern_confidence (confidence)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='历史成功 SQL 沉淀的模板'
    """,
    # ── query_log：查询日志（飞轮信号源）──
    """
    CREATE TABLE IF NOT EXISTS query_log (
        id INT NOT NULL AUTO_INCREMENT,
        session_id VARCHAR(64) NOT NULL,
        query TEXT NOT NULL,
        `sql` TEXT,
        success TINYINT(1) NOT NULL DEFAULT 0,
        latency_ms FLOAT DEFAULT NULL,
        reviewer_score FLOAT DEFAULT NULL,
        intent VARCHAR(32) DEFAULT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        INDEX idx_query_log_session_id (session_id),
        INDEX idx_query_log_created_at (created_at),
        INDEX idx_query_log_success (success)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每次问数查询的执行记录'
    """,
    # ── bad_case：失败 case（数据飞轮核心沉淀）──
    """
    CREATE TABLE IF NOT EXISTS bad_case (
        id INT NOT NULL AUTO_INCREMENT,
        query TEXT NOT NULL,
        `sql` TEXT,
        error_type VARCHAR(32) NOT NULL,
        detail TEXT,
        session_id VARCHAR(64) DEFAULT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'new',
        failure_mode VARCHAR(64) DEFAULT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        reviewed_at DATETIME DEFAULT NULL,
        PRIMARY KEY (id),
        INDEX idx_bad_case_status (status),
        INDEX idx_bad_case_session_id (session_id),
        INDEX idx_bad_case_created_at (created_at),
        INDEX idx_bad_case_error_type (error_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='失败 case 自动归集'
    """,
]


async def init_upgrade_tables() -> int:
    """创建所有升级表（幂等，已存在的表会被跳过）

    Returns:
        创建/确认存在的表数量
    """
    # init() 是同步的（建 engine + session_factory，不碰网络），参照 archive_sessions.py:98
    meta_mysql_client_manager.init()

    created = 0
    async with meta_mysql_client_manager.session_factory() as session:
        for ddl in _DDL_STATEMENTS:
            try:
                # 提取表名用于日志（CREATE TABLE IF NOT EXISTS <name> ( ...）
                ddl_stripped = " ".join(ddl.split())
                table_name = ddl_stripped.split("EXISTS")[1].split("(")[0].strip()
                await session.execute(text(ddl))
                await session.commit()
                created += 1
                logger.info(f"[init_upgrade_tables] 表 {table_name} 就绪")
            except Exception as e:
                logger.error(f"[init_upgrade_tables] 建表失败: {e}")
                # 不中断，继续下一张表（其他表可能独立可用）

    return created


async def main_async() -> int:
    """异步主函数"""
    try:
        n = await init_upgrade_tables()
        print(f"\n✓ 完成：{n}/4 张升级表就绪")
        print("  - user_profile   (Semantic Memory)")
        print("  - sql_pattern    (Procedural Memory)")
        print("  - query_log      (飞轮信号源)")
        print("  - bad_case       (失败 case 归集)")
        return 0
    finally:
        await meta_mysql_client_manager.close()


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
