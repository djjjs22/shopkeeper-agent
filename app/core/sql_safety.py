# -*- coding: utf-8 -*-
"""
SQL 安全校验器

作用：在 LLM 生成的 SQL 执行之前，进行多层安全检查。
就像一个"安检门"——所有 SQL 必须先通过这里，才允许进入 MySQL 执行。

Python 知识点：
- \"\"\" 三引号：多行注释/文档字符串
- # 号：单行注释
- 模块级文档字符串：放在文件最开头的 \"\"\" 块，描述整个文件是做什么的
"""

import re  # import = 导入。re 是 Python 的"正则表达式"模块，用来做文本模式匹配
from typing import List, Optional

# from ... import ... = 从某个模块导入特定的东西
# List = 类型注解，表示"列表类型"，如 List[str] 表示"字符串列表"
# Optional = 类型注解，表示"可以为 None 的值"，如 Optional[str] 表示"字符串或 None"


class SQLSafetyValidator:
    """
    SQL 安全校验器

    用 class 定义一个类，类是创建对象的"蓝图"。
    这里的类只包含类方法（@classmethod），不需要创建对象实例就能用。

    Python 类知识点：
    - class ClassName: 定义一个类
    - 类变量：定义在类体内、所有方法之外的变量，属于类本身而非实例
    - @classmethod：装饰器，让方法可以直接通过"类名.方法名()"调用，不需要先创建对象
    - cls：类方法的第一个参数，代表类本身（类似 self 但指向类而非实例）
    """

    # ═══════════════════════════════════════════════════════════════
    # 第一层防护：危险关键字列表
    # ═══════════════════════════════════════════════════════════════
    # 以下关键字如果出现在 SQL 中，说明 LLM 想修改数据库结构或数据
    # 问数系统只允许"查询"（只读），禁止任何"修改"（写入）
    #
    # Python 知识点：
    # - List[str]：类型注解，冒号后面是类型，等号后面是值
    # - [] 方括号：创建一个列表（list），列表是有序的可变序列
    # - 列表中的每个元素用逗号分隔
    FORBIDDEN_KEYWORDS: List[str] = [
        "DROP",       # 删除表/数据库（例如：DROP TABLE users）
        "DELETE",     # 删除数据行（例如：DELETE FROM orders）
        "UPDATE",     # 修改数据（例如：UPDATE products SET price=0）
        "ALTER",      # 修改表结构（例如：ALTER TABLE users ADD COLUMN）
        "TRUNCATE",   # 清空表数据（比 DELETE 更快更彻底）
        "INSERT",     # 插入新数据（例如：INSERT INTO users VALUES(...)）
        "CREATE",     # 创建表/索引/视图等（例如：CREATE TABLE backup）
        "REPLACE",    # 替换数据（MySQL 特有语句）
        "GRANT",      # 授权（例如：GRANT SELECT ON ...）
        "REVOKE",     # 撤销授权（例如：REVOKE SELECT ON ...）
        "RENAME",     # 重命名（例如：RENAME TABLE old TO new）
        "LOAD",       # 加载数据文件（例如：LOAD DATA INFILE）
        "IMPORT",     # 导入数据
        # 2026-07-20 新增（#2 安全加固）：
        "CALL",       # 调用存储过程（可能执行任意 SQL）
        "HANDLER",    # MySQL 直读表数据接口（绕过权限检查）
        "DO",         # 执行表达式（DO SLEEP(30) 等）
        "SET",        # 修改 session 变量（SET sql_mode=...）
        "OUTFILE",    # SELECT ... INTO OUTFILE 写文件
        "DUMPFILE",   # SELECT ... INTO DUMPFILE 写文件
        "LOAD_FILE",  # 读服务器文件
    ]

    # ═══════════════════════════════════════════════════════════════
    # 第二层防护：允许访问的表名白名单
    # ═══════════════════════════════════════════════════════════════
    # 只允许查询这些表，防止 LLM 去查不该查的表
    ALLOWED_TABLES: List[str] = [
        "dim_region",    # 地区维度表（省份、大区、国家）
        "dim_customer",  # 客户维度表（会员等级、性别等）
        "dim_product",   # 商品维度表（品类、品牌）
        "dim_date",      # 时间维度表（年、季度、月、日）
        "fact_order",    # 订单事实表（核心数据表）
    ]

    # 2026-07-20 (#9)：启动时从 meta_db 动态加载的表名白名单
    # 为空时退回类变量 ALLOWED_TABLES（兜底）。
    # 通过 set_dynamic_allowed_tables() 在 lifespan 启动钩子里设置。
    _DYNAMIC_ALLOWED_TABLES: set[str] = set()

    @classmethod
    def set_dynamic_allowed_tables(cls, table_names: list[str]) -> None:
        """启动时从 meta_db 加载表名 → 注入到 SQL 校验器

        让"加表"流程零代码改动：在 meta_config.yaml 加表 + 跑 build_meta_knowledge，
        下次启动后 SQL 校验器自动识别新表，不用改 ALLOWED_TABLES 常量。
        """
        cls._DYNAMIC_ALLOWED_TABLES = {
            t.lower() for t in table_names if t and isinstance(t, str)
        }

    @classmethod
    def get_effective_allowed_tables(cls) -> list[str]:
        """返回当前生效的表名白名单（动态优先，否则兜底）"""
        if cls._DYNAMIC_ALLOWED_TABLES:
            return sorted(cls._DYNAMIC_ALLOWED_TABLES)
        return list(cls.ALLOWED_TABLES)

    # ═══════════════════════════════════════════════════════════════
    # 第三层防护：SQL 注入特征检测
    # ═══════════════════════════════════════════════════════════════
    # 这些正则表达式用来检测"SQL 注入攻击"的特征
    # SQL 注入：攻击者通过在输入中嵌入 SQL 代码来操控数据库
    #
    # Python 知识点：
    # - r"..."：原始字符串（raw string），反斜杠 \ 不会被转义
    #   例如 r"\n" 是两个字符 \ 和 n，而不是换行符
    # - 正则表达式语法：
    #   \s* = 零个或多个空白字符（空格、制表符、换行等）
    #   \s+ = 一个或多个空白字符
    #   \b  = 单词边界（确保匹配的是完整单词，不是单词的一部分）
    #   '   = 单引号字符
    #   .*  = 零个或多个任意字符
    #   */  = 星号 + 斜杠，即 */
    #   $   = 行尾
    INJECTION_PATTERNS: List[str] = [
        # 经典 SQL 注入模式：' OR '1'='1 （永远为真，绕过密码验证）
        # 匹配示例: WHERE password = '' OR '1'='1'
        r"'\s*OR\s+'1'\s*=\s*'1",

        # SQL 注入变体：' OR 1=1 -- （-- 后面的内容被注释掉）
        # 匹配示例: WHERE id = '' OR 1=1 --'
        r"'\s*OR\s+1\s*=\s*1\s*--",

        # UNION SELECT 注入：通过 UNION 合并其他查询结果
        # 匹配示例: '' UNION SELECT username, password FROM users --
        # 注意：原 `r"UNION\s+SELECT"` 可被 `UNION/**/SELECT` 绕过，
        # 现在先在 _strip_block_comments 里剥掉块注释，再用这个正则即可覆盖
        r"UNION\s+SELECT",

        # 多语句注入（使用分号堆叠多条 SQL）
        # 匹配示例: SELECT * FROM products; DROP TABLE users
        r";\s*DROP\s+",
        r";\s*DELETE\s+",
        r";\s*UPDATE\s+",

        # 行尾注释截断：用 -- 把原始 SQL 的后续部分注释掉
        # 匹配示例: ' OR 1=1 --'
        r"--\s*$",

        # 块注释绕过：用 /* ... */ 包裹关键词来绕过检测
        # 匹配示例: DROP/**/TABLE users
        # 注意：这条要在剥块注释之前匹配（验证 SQL 里是否原本就带块注释，
        # 因为正常 SELECT 不会用块注释，出现就高度可疑）
        r"/\*.*\*/",

        # 2026-07-20 新增（#2 安全加固）：

        # 时间盲注：SLEEP() / BENCHMARK() 让数据库卡住测试是否注入成功
        # 匹配示例: SELECT IF(1=1, SLEEP(30), 0)
        r"\bSLEEP\s*\(",
        r"\bBENCHMARK\s*\(",

        # 系统表/系统 schema 访问：泄露数据库元信息（列名、用户、权限）
        # 匹配示例: SELECT * FROM information_schema.tables
        #         SELECT * FROM mysql.user
        #         SELECT * FROM performance_schema.threads
        #         SELECT * FROM sys.schema_table_statistics
        r"\binformation_schema\b",
        r"\bmysql\s*\.",
        r"\bperformance_schema\b",
        r"\bsys\s*\.",

        # 文件读写（虽然 OUTFILE/DUMPFILE/LOAD_FILE 已在 FORBIDDEN_KEYWORDS，
        # 但 LLM 可能写成 INTO OUTFILE 形式，独立正则兜底）
        r"\bINTO\s+(OUTFILE|DUMPFILE)\b",
        r"\bLOAD_FILE\s*\(",

        # 不在白名单的系统函数（信息泄露/资源消耗）
        r"\bLOAD_FILE\s*\(",
    ]

    # ═══════════════════════════════════════════════════════════════
    # 第二层半防护：表名引用提取正则
    # ═══════════════════════════════════════════════════════════════
    # 用来从 FROM / JOIN 后面提取表名，配合 ALLOWED_TABLES 做白名单校验
    #
    # 支持的形态：
    #   FROM dim_region               → name="dim_region"
    #   JOIN fact_order               → name="fact_order"
    #   FROM dw.dim_region            → name="dw", name2="dim_region"（取 name2）
    #   FROM `dim_region`             → name="dim_region"（反引号被忽略）
    #   FROM dim_region dr            → name="dim_region"（别名 dr 不被提取）
    #
    # 不支持：FROM a, b, c（逗号分隔多表）——当前 SQL 模板不生成这种形态
    TABLE_REFERENCE_PATTERN: str = (
        r"(?:\bFROM|\bJOIN)\s+`?(?P<name>[a-zA-Z_]\w*)`?"
        r"(?:\s*\.\s*`?(?P<name2>[a-zA-Z_]\w*)`?)?"
    )

    # CTE 定义抽取：WITH temp AS (...) 或 WITH a AS (...), b AS (...)
    # 只捕获 CTE 名（temp / a / b），不捕获 AS 后面的内容。
    # 用在表名白名单校验时把 CTE 名动态并入白名单（CTE 是 SQL 自己定义的临时表）
    #
    # 形态覆盖：
    #   WITH temp AS (...)                → 命中 temp（开头 WITH 触发）
    #   WITH a AS (...), b AS (...)       → 命中 a + b（开头 WITH + 逗号后续）
    #
    # 关键约束：要求 name 后紧跟 `AS (`（左括号必须紧跟），避免误匹配
    # SELECT 子句里的 `col AS alias`（alias 后面不会是左括号）
    CTE_NAME_PATTERN: str = (
        r"(?:\bWITH\s+|,\s*)`?(?P<cte>[a-zA-Z_]\w*)`?\s+AS\s*\("
    )

    # ═══════════════════════════════════════════════════════════════
    # validate() 方法：核心校验入口
    # ═══════════════════════════════════════════════════════════════
    @classmethod
    # ↑ @classmethod 是"装饰器"（decorator）
    #   装饰器：一个以 @ 开头的特殊语法，用来修改它下面函数的"行为"
    #   @classmethod 的作用：让方法可以直接通过"类名.方法名()"调用，
    #   不需要先创建对象（不需要写 validator = SQLSafetyValidator()）
    #   调用方式：SQLSafetyValidator.validate(sql)  ← 直接用类名调用
    #
    # cls：类方法的第一个参数，代表"类本身"（SQLSafetyValidator 这个类）
    #       通过 cls 可以访问类变量（如 cls.FORBIDDEN_KEYWORDS）
    def validate(
        cls,  # Python 类方法的第一个参数永远是 cls
        sql: str,  # : str 是类型注解，表示"这个参数应该是字符串类型"
        allowed_tables: Optional[List[str]] = None,
        # ↑ Optional[List[str]] = 这个参数可以是一个"字符串列表"，也可以是 None
        #   = None 表示默认值为 None（如果调用时不传这个参数，默认就是 None）
    ) -> str:
        # ↑ -> str 是返回值类型注解，表示"这个函数返回一个字符串"
        """
        对 SQL 执行多层安全检查

        参数：
            sql (str): LLM 生成的 SQL 语句文本
            allowed_tables (Optional[List[str]]): 自定义的允许表名列表，
                如果不传则使用类默认的 ALLOWED_TABLES

        返回：
            str: 清洗后的 SQL（去除无关空白）

        异常：
            ValueError: 校验不通过时抛出，附带具体拦截原因
        """
        # ── 空值检查 ──
        # if not sql: Python 的"假值"判断
        #   以下值在 Python 中都算 False：None、空字符串 ""、0、空列表 []、空字典 {}
        #   if not sql：如果 sql 是 None 或空字符串，就执行缩进的代码
        #   .strip()：字符串方法，去除首尾的空白字符（空格、换行、制表符等）
        if not sql or not sql.strip():
            # raise = 抛出异常，中断当前函数执行
            # ValueError = Python 内置异常类，表示"值错误"
            raise ValueError("SQL 语句为空，拒绝执行")

        # ── 转换为大写便于匹配 ──
        # .upper()：字符串方法，将所有字母转为大写
        # 为什么要转大写？因为正则匹配不区分大小写需要写 flag，先转大写更简单
        # 例如 "select" 变成 "SELECT"，"drop" 变成 "DROP"
        sql_upper = sql.upper()

        # ── 移除字符串字面量后再做关键字匹配，防止误杀 ──
        # 例如 SELECT 'DROP' AS label → DROP 在引号里是数据不是命令
        # re.sub(pattern, replacement, text) = 正则替换
        # r"'[^']*'" = 匹配一对单引号及其内容
        sql_no_strings = re.sub(r"'[^']*'", "", sql_upper)

        # 2026-07-20 新增（#2 安全加固）：剥块注释后再做关键字/注入匹配
        # 防止 `UNION/**/SELECT`、`DROP/**/TABLE` 这种用注释分割绕过检测。
        # 用空格替换（不是空串），保留关键字边界。
        # 注意：剥注释后 sql_no_strings 里就不再有块注释，所以 INJECTION_PATTERNS 里
        # 的 r"/\*.*\*/" 不会再匹配（这正好对——剥掉之后还匹配说明 SQL 本来就没块注释，
        # 是正常情况；INJECTION_PATTERNS 那条规则用于"在剥之前"已经拦住可疑 SQL，
        # 现在策略改成"剥之后用更严的关键字/注入规则"，更彻底）。
        sql_no_comments = re.sub(r"/\*.*?\*/", " ", sql_no_strings, flags=re.DOTALL)

        # 第一层：危险关键字拦截（在去掉字符串后的 SQL 中匹配）
        for keyword in cls.FORBIDDEN_KEYWORDS:
            pattern = r"\b" + keyword + r"\b"
            # 用 sql_no_comments 而不是 sql_upper，避免引号内/注释里的关键字被误杀
            if re.search(pattern, sql_no_comments):
                # 找到了危险关键字 → 拦截！
                # .index(keyword)：字符串方法，找到 keyword 在 sql_upper 中的位置（索引）
                #   Python 的索引起始是 0
                #   max(0, ...)：取较大值，防止索引为负数
                #   sql_upper.index(keyword)-20：从关键字前 20 字符开始
                #   sql_upper.index(keyword)+len(keyword)+20：到关键字后 20 字符结束
                #
                # 切片操作 [start:end]：截取字符串的一部分
                #   [5:10] 表示第 5 个字符到第 9 个字符（不包括第 10 个）
                start_idx = max(0, sql_upper.index(keyword) - 20)
                end_idx = sql_upper.index(keyword) + len(keyword) + 20
                context = sql_upper[start_idx:end_idx]

                # raise = 抛出异常，中断函数执行
                # ValueError(f"...") = 创建一个带错误信息的 ValueError
                raise ValueError(
                    f"SQL 安全拦截：检测到危险关键字 '{keyword}'。"
                    f"问数系统只允许 SELECT 查询，禁止修改数据库。"
                    f"被拦截的 SQL 片段: ...{context}..."
                )

        # ═══════════════════════════════════════════════════════════
        # 第二层：只允许 SELECT/WITH 查询
        # ═══════════════════════════════════════════════════════════
        # .strip()：去除首尾空白后取前 50 个字符
        # .startswith("SELECT")：字符串方法，检查是否以 "SELECT" 开头
        #   and 是逻辑"与"，需要两边都为 True
        #   not ...startswith("WITH")：WITH 开头的 CTE（公用表表达式）也允许
        #   例如：WITH temp AS (SELECT ...) SELECT * FROM temp
        stripped = sql_upper.strip()
        if not stripped.startswith("SELECT") and not stripped.startswith("WITH"):
            # stripped[:50]：切片，取前 50 个字符
            raise ValueError(
                f"SQL 安全拦截：只允许 SELECT/WITH 开头的查询语句。"
                f"当前 SQL 以 '{stripped[:50]}...' 开头"
            )

        # ═══════════════════════════════════════════════════════════
        # 第三层：SQL 注入特征检测（优先于表名白名单：注入是攻击特征，必须先拦）
        # ═══════════════════════════════════════════════════════════
        for pattern in cls.INJECTION_PATTERNS:
            # re.search(pattern, sql, re.IGNORECASE)
            #   re.IGNORECASE = 不区分大小写标志
            #   因为注入特征可能大小写混合（如 "Union Select"）
            if re.search(pattern, sql, re.IGNORECASE):
                raise ValueError(
                    f"SQL 安全拦截：检测到可疑的注入特征 (匹配模式: {pattern})。"
                    f"这可能是一次 SQL 注入攻击，已拒绝执行。"
                )

        # ═══════════════════════════════════════════════════════════
        # 第三层半：表名白名单校验（2026-07-20 修复：之前形同虚设）
        # ═══════════════════════════════════════════════════════════
        # 从 FROM / JOIN 后提取表名（已移除字符串字面量，防止 'FROM' 之类字面量误命中），
        # 大小写不敏感比对白名单；任何一个不在白名单即拦截。
        #
        # CTE 名（WITH temp AS (...)）作为临时表被 FROM 引用是合法的，
        # 抽取后动态并入白名单，避免误杀。
        #
        # 优先级（2026-07-20 #9 动态加载）：
        #   1. 调用方显式传 allowed_tables
        #   2. 启动时从 meta_db 加载的 _DYNAMIC_ALLOWED_TABLES（如有）
        #   3. 类变量 ALLOWED_TABLES 硬编码兜底
        # 传空列表 [] 则跳过校验（用于单元测试或非生产环境临时关闭白名单）。
        if allowed_tables is not None:
            base_whitelist = [t.upper() for t in allowed_tables]
        elif cls._DYNAMIC_ALLOWED_TABLES:
            base_whitelist = list(cls._DYNAMIC_ALLOWED_TABLES)
        else:
            base_whitelist = [t.upper() for t in cls.ALLOWED_TABLES]
        effective_whitelist = base_whitelist
        if effective_whitelist:
            cte_names = cls._extract_cte_names(sql_no_comments)
            referenced_tables = cls._extract_tables(sql_no_comments)
            # CTE 名视作"当前 SQL 内合法引用"，并入白名单
            allowed_set = set(effective_whitelist) | cte_names
            # set.difference 保留"引用了但不在白名单"的表名
            disallowed = sorted(referenced_tables - allowed_set)
            if disallowed:
                raise ValueError(
                    f"SQL 安全拦截：检测到非白名单表 {disallowed}。"
                    f"只允许查询 {cls.ALLOWED_TABLES}。"
                )

        # ═══════════════════════════════════════════════════════════
        # 全部校验通过 → 返回干净的 SQL
        # ═══════════════════════════════════════════════════════════
        # return：函数返回语句，把结果传给调用者
        # sql.strip()：再次去除首尾空白，确保返回的是干净的 SQL
        return sql.strip()

    @classmethod
    def _extract_tables(cls, sql_no_strings_upper: str) -> set[str]:
        """从 SQL 的 FROM / JOIN 子句中提取被引用的表名

        供 validate() 的白名单校验使用。输入必须是已大写化、已移除字符串字面量的
        SQL（防 'SELECT FROM xxx' 这类字面量误命中）。

        支持的形态（详见 TABLE_REFERENCE_PATTERN 注释）：
          - FROM dim_region
          - JOIN fact_order
          - FROM dw.dim_region （schema.table，取 table 部分）
          - FROM `dim_region` （反引号包裹，被忽略）
          - FROM dim_region dr （别名不被提取）

        不支持逗号分隔的多表 FROM（当前 SQL 模板不生成这种形态，遇不到）。

        Args:
            sql_no_strings_upper: 已处理过的 SQL（大写 + 字面量移除）

        Returns:
            表名集合（全部大写）。匹配不到返回空集。
        """
        tables: set[str] = set()
        for match in re.finditer(cls.TABLE_REFERENCE_PATTERN, sql_no_strings_upper):
            # name2 存在说明是 schema.table 形态，取真正的表名 name2；否则取 name
            table_name = match.group("name2") or match.group("name")
            if table_name:
                tables.add(table_name.upper())
        return tables

    @classmethod
    def _extract_cte_names(cls, sql_no_strings_upper: str) -> set[str]:
        """从 SQL 的 WITH 子句中提取 CTE 名

        供 validate() 的白名单校验使用。CTE 是当前 SQL 自己定义的临时表，
        被 FROM 引用时不应触发"非白名单"拦截。

        匹配形态（详见 CTE_NAME_PATTERN 注释）：
          - WITH temp AS (...)                → {TEMP}
          - WITH a AS (...), b AS (...)       → {A, B}

        Args:
            sql_no_strings_upper: 已处理过的 SQL（大写 + 字面量移除）

        Returns:
            CTE 名集合（全部大写）。无 WITH 子句返回空集。
        """
        names: set[str] = set()
        for match in re.finditer(cls.CTE_NAME_PATTERN, sql_no_strings_upper):
            cte_name = match.group("cte")
            if cte_name:
                names.add(cte_name.upper())
        return names
