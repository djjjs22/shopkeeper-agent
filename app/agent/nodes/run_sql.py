# -*- coding: utf-8 -*-
"""
SQL 执行节点（v2.0 安全升级版）

负责执行最终 SQL，并记录查询结果。
它是整个 LangGraph 问数流程的最后一个节点，执行完成后流程进入 END。

v2.0 安全升级内容：
  在执行 SQL 之前加入了 SQLSafetyValidator 三层防护：
    第一层 - 危险关键字拦截（DROP/DELETE/UPDATE/ALTER/TRUNCATE 等）
    第二层 - 只允许 SELECT/WITH 开头的查询
    第三层 - SQL 注入特征检测（UNION SELECT、OR '1'='1' 等）

Python 知识点：
  - async def：定义"异步函数"（协程），可以用 await 等待其他异步操作
  - await：等待异步操作完成，不阻塞事件循环
  - try/except：异常处理，try 块出错时跳到 except 块
"""

import re  # import = 导入。re 是 Python 的"正则表达式"模块

from langgraph.runtime import Runtime

# from ... import ... = 从某个模块导入特定的东西
# Runtime = LangGraph 框架的"运行时上下文"，节点通过它获取依赖和流式输出
from app.agent.context import DataAgentContext

# DataAgentContext = 定义了 Agent 运行时需要的所有"依赖资源"
# 比如数据库连接、embedding 客户端等
from app.agent.state import DataAgentState

# DataAgentState = 定义了 Agent 的"状态数据"
# 类似一个字典，在节点之间传递（sql、query、table_infos 等字段）
from app.core.log import logger

# logger = 日志记录器，用来输出日志信息
# logger.info() = 记录普通信息
# logger.warning() = 记录警告信息
# logger.error() = 记录错误信息
from app.core.sql_safety import SQLSafetyValidator

# 正则表达式：一种文本模式匹配语言，用来查找、替换符合特定模式的字符串
from app.core.timing import timed_node

# SQLSafetyValidator = 我们刚写的 SQL 安全校验器
# 包含三层安全检查：关键字拦截 + SELECT白名单 + SQL注入检测


def _clean_sql(sql: str) -> str:
    """
    清理 LLM 生成的 SQL 中可能混入的非 SQL 内容

    为什么需要这个函数？
    LLM（大语言模型）生成 SQL 时，经常会把它包裹在 Markdown 代码块中：
      ```sql
      SELECT * FROM users;
      ```
    如果直接执行带 ```sql 标记的文本，MySQL 会报语法错误。

    这个函数的作用就是"剥掉"外层的 Markdown 标记，只保留纯 SQL。

    参数：
        sql (str): LLM 生成的原始 SQL 文本

    返回：
        str: 清理后的纯 SQL 文本

    Python 知识点 - re.sub(pattern, replacement, text, flags):
        re.sub() = 正则替换函数，把匹配 pattern 的部分替换成 replacement
        参数：
          第1个：正则表达式（要匹配的模式）
          第2个：替换成什么（这里是空字符串 ""，即删除匹配部分）
          第3个：要处理的文本
          第4个：flags（可选，如 re.IGNORECASE = 不区分大小写）

    正则表达式解释：
        r"^```(?:sql)?\s*\n?"
        ^         = 行首
        ```       = 三个反引号（字面匹配）
        (?:sql)?  = (?:...) 是非捕获组，? 表示可选，整体表示 "sql" 这个单词可有可无
                    (?:) 和 () 的区别：(?:) 不保存匹配结果，更高效
        \s*       = 零个或多个空白字符（空格、制表符等）
        \n?       = 零个或一个换行符

        r"\n?```\s*$"
        \n?       = 零个或一个换行符
        ```       = 三个反引号
        \s*       = 零个或多个空白字符
        $         = 行尾
    """
    # 第一步：去掉开头的 ```sql 或 ``` 标记
    # 例如 "```sql\nSELECT * FROM users;\n```" → "SELECT * FROM users;\n```"
    sql = re.sub(
        r"^```(?:sql)?\s*\n?",  # 匹配模式：行首的代码块开始标记
        "",                      # 替换为：空字符串（即删除）
        sql,                     # 要处理的文本
        flags=re.IGNORECASE      # 不区分大小写（```SQL 和 ```sql 都能匹配）
    )

    # 第二步：去掉末尾的 ``` 标记
    # 例如 "SELECT * FROM users;\n```" → "SELECT * FROM users;"
    sql = re.sub(
        r"\n?```\s*$",           # 匹配模式：行尾的代码块结束标记
        "",                      # 替换为：空字符串
        sql                      # 要处理的文本
    )

    # 第三步：去掉首尾空白（空格、换行、制表符）
    # .strip() = 字符串方法，删除开头和结尾的所有空白字符
    return sql.strip()


# ─────────────────────────────────────────────────────────────────────
# 错误信息脱敏（2026-07-20 #1 安全加固）
# ─────────────────────────────────────────────────────────────────────
# 原 run_sql 直接 `writer({"type": "error", "message": str(e)})` 会把 MySQL /
# SQLAlchemy 异常原文（含表名、列名、SQL 片段、连接信息）泄露给前端。
# 改成分类映射，给前端友好文案，完整异常服务端 logger 已记。

# 后端错误特征 → 友好文案 的映射表
# 顺序敏感：更具体的特征放在前面
_BACKEND_ERROR_MAP: list[tuple[str, str]] = [
    # 表不存在
    ("table", "查询的表不存在，请确认表名或换一种问法"),
    ("unknown column", "查询的字段名有误，请换一种问法描述"),
    ("doesn't exist", "查询的对象不存在，请换一种问法"),
    # 语法
    ("syntax", "SQL 语法错误，已记录，请重试或换一种问法"),
    ("you have an error in your sql syntax", "SQL 语法错误，已记录，请重试"),
    # 字段引用歧义
    ("ambiguous", "查询字段有歧义，请更精确地描述"),
    # 连接 / 超时
    ("connection", "数据库连接异常，请稍后重试"),
    ("timeout", "查询超时，请缩小查询范围或换更精确的条件"),
    # 权限
    ("access denied", "无权限执行此查询"),
    # 内存
    ("out of memory", "查询结果过大，请加更精确的筛选条件"),
]


def _safe_error_message(exc: Exception) -> str:
    """把数据库异常分类成对前端安全的简短文案

    Args:
        exc: 数据库执行抛出的异常

    Returns:
        前端可展示的中文文案（不包含 SQL / 表名 / 列名等敏感信息）
    """
    msg = str(exc).lower()
    for pattern, friendly in _BACKEND_ERROR_MAP:
        if pattern in msg:
            return friendly
    # 兜底：完全不匹配任何已知特征时，不透传原文，给通用文案
    return "查询执行失败，已记录日志，请换一种问法或缩小查询范围"


@timed_node
async def run_sql(
    state: DataAgentState,     # state = Agent 的当前状态，包含 sql、query 等字段
    runtime: Runtime[DataAgentContext]  # runtime = 运行时上下文，提供依赖和流式输出
):
    """
    安全执行 SQL 并产出最终问数结果

    LangGraph 框架会自动传入 state 和 runtime 参数，
    不需要我们手动构造这些参数。

    Python 知识点：
    - async def：定义异步函数，函数内部可以用 await 等待其他异步操作
    - 异步函数的返回值"看起来"是直接返回，但实际上是一个协程对象
    - 调用方需要用 await 来获取异步函数的真正返回值

    安全流程（5 步）：
    1. 从状态中取出 LLM 生成的 SQL
    2. 清理 Markdown 代码块标记（如果有的话）
    3. ⭐ 三层安全检查（SQLSafetyValidator）—— 数据库执行前的最后防线
    4. 如果安全检查通过 → 送交 MySQL 执行
    5. 流式返回结果（成功）或错误信息（失败）给前端

    参数：
        state (DataAgentState): Agent 状态，通过 state["sql"] 获取 SQL
        runtime (Runtime): LangGraph 运行时，用于获取依赖和流式输出

    返回值：
        无显式返回值（通过 runtime.stream_writer 流式输出进度和结果）
        如果安全拦截，通过 return 提前结束（不执行 SQL）
        如果执行出错，通过 raise 抛出异常让 LangGraph 处理
    """
    # ── 获取流式输出器 ──
    # runtime.stream_writer 是 LangGraph 提供的"流式输出通道"
    # 通过 writer(...) 可以把数据实时推送给前端（SSE 协议）
    # 类似于 print()，但是实时传输到浏览器
    writer = runtime.stream_writer

    # ── 定义当前步骤名称 ──
    # 这个名称会显示在前端的进度条上
    step = "执行SQL"

    # ── 向前端发送"开始执行"信号 ──
    # writer(...) 的调用格式：
    #   writer({"type": "progress", "step": 步骤名, "status": "running"})
    # type="progress" → 表示这是一条进度信息
    # step="执行SQL" → 步骤名称
    # status="running" → 状态为"运行中"
    writer({"type": "progress", "step": step, "status": "running"})  # 前端显示：执行SQL ⏳

    try:
        # try 块：尝试执行以下代码，如果出错跳到 except 块
        # try/except 是 Python 的异常处理机制，防止程序因错误而崩溃

        # ═══════════════════════════════════════════════════
        # Step 1：从 Agent 状态中取出 SQL
        # ═══════════════════════════════════════════════════
        # state["sql"] 可能是：
        #   - generate_sql 节点直接生成的 SQL（如果校验通过）
        #   - correct_sql 节点修正后的 SQL（如果第一次校验失败）
        raw_sql = state["sql"]
        # 字典取值：state 是一个 TypedDict（类型化的字典），用方括号取字段值
        # 类似于 dict_obj["key"]，但 TypedDict 会有类型检查

        # ═══════════════════════════════════════════════════
        # Step 2：清理 Markdown 代码块标记
        # ═══════════════════════════════════════════════════
        # 调用我们上面定义的 _clean_sql 函数，去掉 ```sql ... ``` 标记
        sql = _clean_sql(raw_sql)

        # ═══════════════════════════════════════════════════
        # 2026-07-11 加固：空 SQL 防御（v2 修正：移到 _clean_sql 之后）
        # ═══════════════════════════════════════════════════
        # 之前 v4 在 raw_sql 阶段检查，但 raw_sql 有可能包裹在 ```sql ``` 围栏里
        # _clean_sql 之后才是真正的 SQL——这时再判空更准
        # 例：raw_sql="```sql\nSELECT 1\n```" → sql="SELECT 1"（之前会被误判空）
        if not sql or not sql.strip():
            error_msg = "Agent 生成的 SQL 为空（可能是上游节点 think 块解析失败或状态异常）"
            logger.error(f"[SQL安全] {error_msg}")
            return {
                "sql": raw_sql or "",
                "execution_result": None,
                "error": error_msg,
            }

        # 日志记录：方便排查问题时查看当时执行的 SQL
        # sql[:200] = 切片，只取前 200 个字符（避免日志过长）
        # 切片语法 [start:end]：你见过但不知道名字的操作
        logger.info(f"[SQL安全] 待执行的 SQL: {sql[:200]}...")

        # ═══════════════════════════════════════════════════
        # Step 3：⭐ 三层安全校验（新增核心逻辑）
        # ═══════════════════════════════════════════════════
        # 这是数据库执行前的"最后一道安检门"
        #
        # 嵌套 try/except：内层 try 专门捕获安全校验的 ValueError
        # 外层的 except 捕获数据库执行错误
        #
        # 为什么嵌套？
        # - 安全校验失败 → 记录日志 + 通知前端，但不抛异常（让流程正常结束）
        # - 数据库执行失败 → 抛异常，让 LangGraph 框架处理
        try:
            # SQLSafetyValidator.validate(sql) → @classmethod，直接通过类名调用
            # 返回清洗后的 SQL（去除空白），如果校验失败抛出 ValueError
            sql = SQLSafetyValidator.validate(sql)
            logger.info("[SQL安全] 安全校验通过 ✅")

        except ValueError as safety_err:
            # except 类型 as 变量名：捕获特定类型的异常，并把异常对象存到变量中
            # as safety_err = 把捕获到的 ValueError 对象命名为 safety_err
            # safety_err 包含了错误的详细信息

            # 安全拦截 → 记录警告日志
            logger.warning(f"[SQL安全] 校验拦截: {safety_err}")

            # 通知前端：状态变为 error
            writer({"type": "progress", "step": step, "status": "error"})
            # 向前端发送错误详情
            # str(safety_err) = 把异常对象转换为字符串，得到错误消息
            writer({"type": "error", "message": str(safety_err)})

            # return 直接退出函数，不执行后续的 SQL
            # 注意：这里没有 raise，所以不会触发外层的 except
            return

        # ═══════════════════════════════════════════════════
        # Step 4：执行 SQL
        # ═══════════════════════════════════════════════════
        # runtime.context = 运行时上下文中注册的所有依赖资源
        # ["dw_mysql_repository"] = 取出"数仓 MySQL 仓储"
        # DW = Data Warehouse（数据仓库）
        # Repository = 仓储层，封装了数据库 CRUD 操作的类
        dw_mysql_repository = runtime.context["dw_mysql_repository"]

        # await = 等待异步操作完成
        # dw_mysql_repository.run(sql) = 执行 SQL 并返回 (rows, truncated) 元组
        # 2026-07-20 改造（#3 LIMIT 兜底）：run() 现在返回 (list[dict], bool)
        #   rows = 最多 _MAX_RESULT_ROWS 行的查询结果
        #   truncated = True 表示结果集超过上限被截断（前端需提示用户加条件）
        result, truncated = await dw_mysql_repository.run(sql)

        # ═══════════════════════════════════════════════════
        # Step 5：向前端返回结果
        # ═══════════════════════════════════════════════════
        # 2026-07-20 (#8)：原代码有重复的 Step 5 块（粘贴残留），合并成一个。
        logger.info(f"[SQL安全] SQL 执行成功，返回 {len(result)} 行数据")

        # ⭐ 空结果校验（防 LLM 语义幻觉：SQL 语法对但 WHERE 条件偏差 → 空结果）
        if len(result) == 0:
            query = state["query"]
            warning_msg = (
                f"查询'{query}'返回0行数据，"
                f"可能是查询条件过于严格或者筛选条件有误"
            )
            writer({"type": "warning", "message": warning_msg})
            logger.warning(f"[结果校验] 空结果警告: {warning_msg}")

        # 通知前端：执行成功
        writer({"type": "progress", "step": step, "status": "success"})

        # 把查询结果推送给前端
        # type="result" → 表示这是最终结果
        # data=result → 实际的数据内容
        # truncated=True → 结果超过 _MAX_RESULT_ROWS 被截断（2026-07-20 #3）
        result_event: dict = {"type": "result", "data": result}
        if truncated:
            result_event["truncated"] = True
            result_event["max_rows"] = 5000
            # 同时发一条 warning 让前端 UI 明显提示
            writer({
                "type": "warning",
                "message": "结果集过大，仅展示前 5000 行。请加更精确的筛选条件或聚合维度。",
            })
            logger.warning(
                f"[SQL安全] 结果截断：返回 {len(result)} 行（实际更多）"
            )
        writer(result_event)

    except Exception as e:
        # 外层 except：捕获数据库执行层面的错误
        # 这些错误不是安全问题，而是 LLM 生成的 SQL 有语义问题
        # 例如：
        #   - 表不存在：Table 'xxx' doesn't exist
        #   - 字段错误：Unknown column 'xxx' in 'field list'
        #   - JOIN 问题：Column 'xxx' in on clause is ambiguous

        # 记录错误日志（完整错误留服务端，含 SQL 上下文便于排查）
        logger.error(f"[SQL安全] 执行失败: {e}")

        # 通知前端：执行失败
        writer({"type": "progress", "step": step, "status": "error"})

        # 2026-07-20 改造（#1 错误脱敏）：不再把数据库原始异常 str(e) 直接推给前端
        # 原做法会泄露表名、列名、SQL 片段、连接信息。改成分类后的友好文案。
        safe_msg = _safe_error_message(e)
        writer({"type": "error", "message": safe_msg})

        # raise 重新抛出异常，让 LangGraph 框架和上层调用者也能感知到这个错误
        # 重新抛出的作用：保持错误传播链不断
        raise
