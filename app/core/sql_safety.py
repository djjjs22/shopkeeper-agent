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
        r"/\*.*\*/",
    ]

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

        # 第一层：危险关键字拦截（在去掉字符串后的 SQL 中匹配）
        for keyword in cls.FORBIDDEN_KEYWORDS:
            pattern = r"\b" + keyword + r"\b"
            # 用 sql_no_strings 而不是 sql_upper，避免引号内的关键字被误杀
            if re.search(pattern, sql_no_strings):
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
        # 第三层：SQL 注入特征检测
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
        # 全部校验通过 → 返回干净的 SQL
        # ═══════════════════════════════════════════════════════════
        # return：函数返回语句，把结果传给调用者
        # sql.strip()：再次去除首尾空白，确保返回的是干净的 SQL
        return sql.strip()
