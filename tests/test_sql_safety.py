# -*- coding: utf-8 -*-
"""
SQL 安全校验器 — 单元测试

测试覆盖：
  1. 正常 SELECT/WITH 查询 → 应通过
  2. 危险关键字（DROP/DELETE/UPDATE/ALTER/...） → 应拦截
  3. SQL 注入特征（UNION SELECT / OR '1'='1' / -- 注释） → 应拦截
  4. 边界条件（空 SQL、非 SELECT 开头） → 应拦截
  5. 防误杀（关键字出现在字符串中） → 不应拦截

运行方式：
  cd D:\shopkeeper-agent-main
  uv run pytest tests/test_sql_safety.py -v
"""

import pytest  # pip install pytest  # Python 最流行的测试框架

from app.core.sql_safety import SQLSafetyValidator


class TestSQLSafetyValidator:
    """SQL 安全校验器单元测试

    pytest 类命名规则：类名以 Test 开头
    pytest 方法命名规则：方法名以 test_ 开头
    """

    # ════════════════════════════════════════════════
    # 正向测试：合法的查询应该通过
    # ════════════════════════════════════════════════

    def test_正常SELECT查询_应通过(self):
        """最简单的 SELECT 不应该被拦截"""
        result = SQLSafetyValidator.validate(
            "SELECT region_name, SUM(order_amount) FROM fact_order"
            " JOIN dim_region ON fact_order.region_id = dim_region.region_id"
            " GROUP BY region_name"
        )
        assert "SELECT" in result  # assert = 断言：条件为 True 则通过，否则失败

    def test_WITH_CTE查询_应通过(self):
        """WITH 开头的 CTE（公用表表达式）也应该通过"""
        result = SQLSafetyValidator.validate(
            "WITH temp AS (SELECT region_id, region_name FROM dim_region)"
            " SELECT * FROM temp"
        )
        assert "WITH" in result

    # ════════════════════════════════════════════════
    # 负向测试：危险操作必须被拦截
    # ════════════════════════════════════════════════

    def test_DROP_TABLE_应被拦截(self):
        """DROP TABLE —— 最危险的操作之一"""
        # pytest.raises(异常类型, match=错误信息关键词)
        # 作用：断言代码会抛出指定异常，且错误信息包含指定文字
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("DROP TABLE fact_order")

    def test_DELETE_应被拦截(self):
        """DELETE FROM —— 删除数据"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("DELETE FROM fact_order WHERE region_id = 1")

    def test_UPDATE_应被拦截(self):
        """UPDATE SET —— 修改数据"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("UPDATE dim_product SET brand = 'test'")

    def test_INSERT_应被拦截(self):
        """INSERT INTO —— 插入数据"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate(
                "INSERT INTO dim_region VALUES (999, 'test', 'test', 'CN')"
            )

    def test_ALTER_TABLE_应被拦截(self):
        """ALTER TABLE —— 修改表结构"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("ALTER TABLE fact_order ADD COLUMN test INT")

    def test_TRUNCATE_应被拦截(self):
        """TRUNCATE —— 清空表（比 DELETE 更快更暴力）"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("TRUNCATE TABLE fact_order")

    def test_CREATE_TABLE_应被拦截(self):
        """CREATE TABLE —— 创建新表"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("CREATE TABLE backup AS SELECT * FROM fact_order")

    # ════════════════════════════════════════════════
    # SQL 注入特征测试
    # ════════════════════════════════════════════════

    def test_UNION_SELECT注入_应被拦截(self):
        """UNION SELECT —— 经典注入手法，合并其他表数据"""
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT * FROM dim_product WHERE name = '' UNION SELECT * FROM users"
            )

    def test_OR_1等于1注入_应被拦截(self):
        """' OR '1'='1' —— 永远为真的条件，绕过登录验证"""
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT * FROM dim_customer WHERE name = '' OR '1'='1'"
            )

    def test_注释截断注入_应被拦截(self):
        """' OR 1=1 -- —— 用注释截断后面的 SQL"""
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT * FROM dim_customer WHERE id = '' OR 1=1 --"
            )

    # ════════════════════════════════════════════════
    # 边界条件测试
    # ════════════════════════════════════════════════

    def test_空SQL_应被拦截(self):
        """空字符串不应该被执行"""
        with pytest.raises(ValueError, match="为空"):
            SQLSafetyValidator.validate("")

    def test_空白SQL_应被拦截(self):
        """全是空格的字符串也不应该被执行"""
        with pytest.raises(ValueError, match="为空"):
            SQLSafetyValidator.validate("   \n\t  ")

    def test_非SELECT开头_应被拦截(self):
        """SHOW TABLES、DESCRIBE 等非查询语句也应拦截"""
        with pytest.raises(ValueError, match="只允许 SELECT"):
            SQLSafetyValidator.validate("SHOW TABLES")

    def test_非WITH开头_应被拦截(self):
        """不是 SELECT 也不是 WITH 开头的语句"""
        with pytest.raises(ValueError, match="只允许 SELECT"):
            SQLSafetyValidator.validate("EXPLAIN SELECT * FROM dim_region")

    # ════════════════════════════════════════════════
    # 防误杀测试（关键字在字符串中不应被误判）
    # ════════════════════════════════════════════════

    def test_DROP在字段别名中_不应被误杀(self):
        """SELECT 'DROP' AS label —— DROP 在引号里是数据，不是关键字"""
        result = SQLSafetyValidator.validate(
            "SELECT 'DROP' AS action_label FROM dim_region"
        )
        assert "SELECT" in result

    def test_UPDATE在字段名中_不应被误杀(self):
        """
        SELECT updated_at FROM ... —— updated_at 包含 UPDATE 但不应该被拦截
        因为 \b 词边界确保了只匹配完整单词 UPDATE，不会匹配 UPDATED
        """
        result = SQLSafetyValidator.validate(
            "SELECT updated_at FROM fact_order LIMIT 1"
        )
        assert "SELECT" in result

    def test_DELETE在注释风格SQL中_不应被误杀(self):
        """只匹配独立的关键字，注释中的不算"""
        result = SQLSafetyValidator.validate(
            "SELECT * FROM dim_product WHERE is_deleted = 0"
        )
        assert "SELECT" in result  # is_deleted 包含 DELETE，但 \b 只匹配完整单词

    def test_大小写混合DELETE_应被拦截(self):
        """DeLeTe 这种大小写混合写法也应该被拦截（因为校验前转了 upper）"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("DeLeTe FROM fact_order")

    # ════════════════════════════════════════════════
    # 表名白名单测试（2026-07-20 修复：之前 ALLOWED_TABLES 形同虚设）
    # ════════════════════════════════════════════════

    def test_非白名单表_应被拦截(self):
        """SELECT * FROM users —— users 不在白名单"""
        with pytest.raises(ValueError, match="非白名单表"):
            SQLSafetyValidator.validate("SELECT * FROM users")

    def test_JOIN中含非白名单表_应被拦截(self):
        """合法表 JOIN 非法表，应拦截（任何一个越界都不行）"""
        with pytest.raises(ValueError, match="非白名单表"):
            SQLSafetyValidator.validate(
                "SELECT * FROM fact_order"
                " JOIN users ON fact_order.user_id = users.id"
            )

    def test_多表全部非法_应列出全部(self):
        """错误信息里应包含所有越界表名"""
        with pytest.raises(ValueError, match="USERS"):
            SQLSafetyValidator.validate(
                "SELECT * FROM users JOIN passwords ON users.id = passwords.uid"
            )

    def test_schema前缀合法表_应通过(self):
        """dw.dim_region 形态：取真正的表名 dim_region 校验，schema 前缀不参与比对"""
        result = SQLSafetyValidator.validate("SELECT * FROM dw.dim_region")
        assert "SELECT" in result

    def test_反引号包裹合法表_应通过(self):
        """`dim_region` 形态：反引号被忽略，提取出的表名进白名单比对"""
        result = SQLSafetyValidator.validate("SELECT * FROM `fact_order`")
        assert "SELECT" in result

    def test_大小写混合表名_应通过(self):
        """大小写不敏感：DIM_REGION 应等同 dim_region"""
        result = SQLSafetyValidator.validate("SELECT * FROM DIM_REGION")
        assert "SELECT" in result

    def test_显式自定义白名单_应通过(self):
        """调用方传 allowed_tables=["users"] 时，users 应放行"""
        result = SQLSafetyValidator.validate(
            "SELECT * FROM users", allowed_tables=["users"]
        )
        assert "SELECT" in result

    def test_显式传空白名单_应跳过校验(self):
        """allowed_tables=[] 表示调用方主动关闭白名单（测试/特殊场景用）"""
        result = SQLSafetyValidator.validate(
            "SELECT * FROM any_table", allowed_tables=[]
        )
        assert "SELECT" in result

    def test_字面量FROM_不应误提取(self):
        """SELECT 'FROM xxx' AS s —— 'FROM xxx' 在字符串里，被移除后不会误提取表名"""
        result = SQLSafetyValidator.validate(
            "SELECT 'FROM users' AS s FROM dim_region"
        )
        assert "SELECT" in result

    def test_单CTE引用临时表_应通过(self):
        """WITH temp AS (...) SELECT * FROM temp —— temp 是 CTE 自定义，应放行"""
        result = SQLSafetyValidator.validate(
            "WITH temp AS (SELECT region_id, region_name FROM dim_region)"
            " SELECT * FROM temp"
        )
        assert "SELECT" in result

    def test_多CTE引用临时表_应通过(self):
        """WITH a AS (...), b AS (...) 多 CTE 一起定义，引用都应放行"""
        result = SQLSafetyValidator.validate(
            "WITH r AS (SELECT region_id FROM dim_region),"
            " f AS (SELECT region_id, SUM(order_amount) AS gmv FROM fact_order GROUP BY region_id)"
            " SELECT r.region_id FROM f JOIN r ON f.region_id = r.region_id"
        )
        assert "SELECT" in result

    def test_CTE体内非白名单表_应被拦截(self):
        """CTE 名本身放行，但 CTE 体内 FROM 的真实表不在白名单仍要拦"""
        with pytest.raises(ValueError, match="非白名单表"):
            SQLSafetyValidator.validate(
                "WITH temp AS (SELECT * FROM users) SELECT * FROM temp"
            )

    # ════════════════════════════════════════════════
    # 2026-07-20 新增（#2 安全加固）：盲区补全测试
    # ════════════════════════════════════════════════

    def test_SLEEP时间盲注_应被拦截(self):
        """SLEEP() 时间盲注"""
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT * FROM dim_region WHERE 1=1 AND SLEEP(30)"
            )

    def test_BENCHMARK时间盲注_应被拦截(self):
        """BENCHMARK() 时间盲注"""
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT BENCHMARK(10000000, MD5('x')) FROM dim_region"
            )

    def test_information_schema访问_应被拦截(self):
        """information_schema 系统表访问"""
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'dw'"
            )

    def test_mysql系统库访问_应被拦截(self):
        """mysql.user 等系统库访问"""
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT user, host FROM mysql.user"
            )

    def test_INTO_OUTFILE写文件_应被拦截(self):
        """SELECT ... INTO OUTFILE 写文件"""
        with pytest.raises(ValueError):  # 关键字 OUTFILE 或注入特征都会拦
            SQLSafetyValidator.validate(
                "SELECT * FROM dim_region INTO OUTFILE '/tmp/x'"
            )

    def test_LOAD_FILE读文件_应被拦截(self):
        """LOAD_FILE() 读服务器文件"""
        with pytest.raises(ValueError):
            SQLSafetyValidator.validate(
                "SELECT LOAD_FILE('/etc/passwd') FROM dim_region"
            )

    def test_CALL存储过程_应被拦截(self):
        """CALL 关键字（执行存储过程）"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("CALL p_archive()")

    def test_SET修改session变量_应被拦截(self):
        """SET 关键字（修改 session 变量）"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("SET sql_mode = 'STRICT_TRANS_TABLES'")

    def test_块注释分割UNION_应被拦截(self):
        """UNION/**/SELECT 用块注释分割绕过关键字检测"""
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT * FROM dim_region WHERE id = 1"
                " UNION/**/SELECT user, password FROM dim_customer"
            )
