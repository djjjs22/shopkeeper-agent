# SQL 高频面试题集 · 27 届秋招备战

> **目标岗位**：数据开发 / 数据分析 / 数仓工程师  
> **数据库兼容**：MySQL 8.0+ / Hive SQL / Spark SQL（语法基本通用，差异点会单独标注）  
> **刷题节奏建议**：基础 2 天 → 关联 2 天 → 窗口 3 天 → 业务 4 天 → 真题 3 天，约 2 周可刷完

---

## 📋 题目导览

| #  | 难度 | 章节 | 考点 | 形式 |
|----|------|------|------|------|
| 1  | ⭐   | 基础 | WHERE + 子查询 | ✅ 含答案 |
| 2  | ⭐   | 基础 | GROUP BY + HAVING | ✅ 含答案 |
| 3  | ⭐   | 基础 | CASE WHEN 行转列 | ✅ 含答案 |
| 4  | ⭐   | 基础 | 日期函数 DATEDIFF | ✅ 含答案 |
| 5  | ⭐⭐ | 基础 | 子查询 + IN | ✅ 含答案 |
| 6  | ⭐⭐ | 多表 | INNER JOIN 基础 | ✅ 含答案 |
| 7  | ⭐⭐ | 多表 | LEFT JOIN + 聚合 | ✅ 含答案 |
| 8  | ⭐⭐ | 多表 | 自连接（同部门） | ✅ 含答案 |
| 9  | ⭐⭐ | 多表 | M-N 关系（选课问题） | ✅ 含答案 |
| 10 | ⭐⭐ | CTE  | 相关子查询 | ✅ 含答案 |
| 11 | ⭐⭐ | CTE  | EXISTS / NOT EXISTS | ✅ 含答案 |
| 12 | ⭐⭐⭐| CTE  | CTE 重构复杂查询 | ✅ 含答案 |
| 13 | ⭐⭐⭐| 窗口 | ROW_NUMBER 取 Top1 | ✅ 含答案 |
| 14 | ⭐⭐⭐| 窗口 | RANK vs DENSE_RANK | ✅ 含答案 |
| 15 | ⭐⭐⭐| 窗口 | LAG / LEAD 环比 | ✏️ **挖空** |
| 16 | ⭐⭐⭐| 窗口 | SUM OVER 累计 | ✅ 含答案 |
| 17 | ⭐⭐⭐| 窗口 | FIRST_VALUE / LAST_VALUE | ✅ 含答案 |
| 18 | ⭐⭐⭐⭐| 业务 | 连续登录 N 天 | ✏️ **挖空** |
| 19 | ⭐⭐⭐⭐| 业务 | 用户次日 / 7 日留存 | ✏️ **挖空** |
| 20 | ⭐⭐⭐⭐| 业务 | 转化漏斗 | ✅ 含答案 |
| 21 | ⭐⭐⭐⭐| 业务 | 帕累托累计贡献率 | ✏️ **挖空** |
| 22 | ⭐⭐⭐⭐| 业务 | 首次下单 + 复购判定 | ✅ 含答案 |
| 23 | ⭐⭐⭐⭐⭐| 真题 | **字节**：同时在线峰值 | ✏️ **挖空** |
| 24 | ⭐⭐⭐⭐⭐| 真题 | **美团**：复购率 | ✅ 含答案 |
| 25 | ⭐⭐⭐⭐⭐| 真题 | **阿里**：销售业绩综合 | ✅ 含答案 |

> ✏️ **挖空说明**：题目会把核心 SQL 留空，用 `-- TODO` 标记，并给出**解题提示**和**参考答案**。建议先自己写再对照答案。

---

# 📘 第一部分：基础篇（1-5 题）

## 第 1 题：查询超过平均工资的员工

**业务场景**：HR 部门要找出公司里"高收入"员工名单。

**表结构**：
```sql
CREATE TABLE employees (
  emp_id    INT PRIMARY KEY,
  name      VARCHAR(50),
  salary    DECIMAL(10,2),
  dept_id   INT,
  hire_date DATE
);
```

**测试数据**：
```sql
INSERT INTO employees VALUES
(1, '张三', 8000,  1, '2023-01-15'),
(2, '李四', 12000, 2, '2022-03-20'),
(3, '王五', 9500,  1, '2024-05-10'),
(4, '赵六', 15000, 3, '2021-11-01'),
(5, '钱七', 11000, 2, '2023-08-25'),
(6, '孙八', 6000,  1, '2024-02-01');
```

**题目要求**：查询工资**高于公司平均工资**的员工姓名和工资，结果按工资降序。

**答案**：
```sql
SELECT name, salary
FROM employees
WHERE salary > (SELECT AVG(salary) FROM employees)
ORDER BY salary DESC;
```

**关键考点**：`WHERE` 子句里嵌套聚合子查询（标量子查询，必须返回一行一列）。

---

## 第 2 题：每个部门的人数与平均工资

**业务场景**：给老板一份部门维度的人力报告。

**表结构**：延续第 1 题的 `employees` 表。

**题目要求**：查询每个部门的**人数**和**平均工资**，仅显示平均工资 ≥ 10000 的部门，按平均工资降序。

**答案**：
```sql
SELECT dept_id,
       COUNT(*)        AS emp_cnt,
       AVG(salary)     AS avg_salary
FROM employees
GROUP BY dept_id
HAVING AVG(salary) >= 10000
ORDER BY avg_salary DESC;
```

**关键考点**：`GROUP BY` + `HAVING`（**聚合后过滤**用 HAVING，行级过滤用 WHERE）。

---

## 第 3 题：行转列（学生成绩）

**业务场景**：教务系统需要把学生每科成绩从"长表"转"宽表"，方便导出报表。

**表结构**：
```sql
CREATE TABLE scores (
  student VARCHAR(50),
  subject VARCHAR(50),
  score   INT
);
```

**测试数据**：
```sql
INSERT INTO scores VALUES
('张三','语文',80),('张三','数学',90),('张三','英语',85),
('李四','语文',75),('李四','数学',85),('李四','英语',70),
('王五','语文',95),('王五','数学',60),('王五','英语',88);
```

**题目要求**：转化为如下格式：

| student | 语文 | 数学 | 英语 |
|---------|------|------|------|
| 张三    | 80   | 90   | 85   |
| 李四    | 75   | 85   | 70   |
| 王五    | 95   | 60   | 88   |

**答案**：
```sql
SELECT student,
       SUM(CASE WHEN subject='语文' THEN score END) AS 语文,
       SUM(CASE WHEN subject='数学' THEN score END) AS 数学,
       SUM(CASE WHEN subject='英语' THEN score END) AS 英语
FROM scores
GROUP BY student;
```

**关键考点**：`CASE WHEN` + 聚合函数实现行转列。MySQL 也可用 `IF(subject='语文', score, NULL)`。

---

## 第 4 题：日期函数 - 工龄与生日提醒

**业务场景**：HR 系统需要在员工入职周年发送祝福。

**表结构**：延续 `employees` 表（字段：`hire_date`）。

**题目要求**：查询 2024 年内入职满 1 年的员工（即 `hire_date` 在 2023-01-01 ~ 2023-12-31 范围内），并显示**距今入职多少天**。

**答案**：
```sql
SELECT name, hire_date,
       DATEDIFF(CURDATE(), hire_date) AS days_since_join
FROM employees
WHERE hire_date BETWEEN '2023-01-01' AND '2023-12-31';
```

**关键考点**：`DATEDIFF(date1, date2)` 返回相差天数。Hive/Spark 用 `DATEDIFF(CURRENT_DATE, hire_date)`。

---

## 第 5 题：子查询 + IN

**业务场景**：找出"明星部门"（人数 ≥ 2 人）的所有员工。

**表结构**：延续 `employees` 表。

**题目要求**：查询所有在人数 ≥ 2 人的部门工作的员工，按 emp_id 升序。

**答案**：
```sql
SELECT *
FROM employees
WHERE dept_id IN (
    SELECT dept_id
    FROM employees
    GROUP BY dept_id
    HAVING COUNT(*) >= 2
)
ORDER BY emp_id;
```

**关键考点**：`IN` + 子查询（也可改写为 `JOIN`，性能上 Hive 推荐 JOIN）。

---

# 📗 第二部分：多表关联（6-9 题）

## 第 6 题：INNER JOIN - 订单与用户

**业务场景**：电商运营要看"已下单用户"的画像。

**表结构**：
```sql
CREATE TABLE users (
  user_id  INT PRIMARY KEY,
  name     VARCHAR(50),
  city     VARCHAR(50)
);

CREATE TABLE orders (
  order_id   INT PRIMARY KEY,
  user_id    INT,
  amount     DECIMAL(10,2),
  order_date DATE
);
```

**测试数据**：
```sql
INSERT INTO users VALUES
(1,'张三','北京'),(2,'李四','上海'),(3,'王五','广州'),(4,'赵六','深圳');

INSERT INTO orders VALUES
(101,1, 200,'2024-01-15'),
(102,2, 500,'2024-01-16'),
(103,1, 300,'2024-02-01'),
(104,3, 800,'2024-02-10');
```

**题目要求**：查询所有**有订单**的用户的城市、订单金额、订单日期。

**答案**：
```sql
SELECT u.city, o.amount, o.order_date
FROM users u
INNER JOIN orders o ON u.user_id = o.user_id
ORDER BY o.order_date;
```

**关键考点**：`INNER JOIN` 只保留两表都有的记录；赵六没订单，不会出现。

---

## 第 7 题：LEFT JOIN - 找出未下单用户

**业务场景**：运营做激活召回，需要找出"注册了但从没下过单"的用户。

**题目要求**：查询**所有用户**的下单情况，包括未下单用户（金额、日期显示为 NULL）。

**答案**：
```sql
SELECT u.user_id, u.name, o.amount, o.order_date
FROM users u
LEFT JOIN orders o ON u.user_id = o.user_id;
```

**进阶**：筛选"没下过单"的用户：
```sql
SELECT u.user_id, u.name
FROM users u
LEFT JOIN orders o ON u.user_id = o.user_id
WHERE o.order_id IS NULL;
```

**关键考点**：`LEFT JOIN` 保留左表全部记录；`IS NULL` 找"孤儿"。

---

## 第 8 题：自连接 - 查找同部门员工配对

**业务场景**：做团队建设，找出所有"同部门"的员工配对。

**表结构**：延续 `employees`。

**题目要求**：输出所有同部门的两两配对（要求 `emp_id_a < emp_id_b` 避免重复）。

**答案**：
```sql
SELECT a.emp_id AS emp_id_a, a.name AS name_a,
       b.emp_id AS emp_id_b, b.name AS name_b,
       a.dept_id
FROM employees a
JOIN employees b
  ON a.dept_id = b.dept_id
 AND a.emp_id < b.emp_id
ORDER BY a.dept_id, a.emp_id;
```

**关键考点**：自连接 + `a.id < b.id` 防止 A-B / B-A 重复输出（面试常考坑）。

---

## 第 9 题：M-N 关系 - 选课问题（面试经典）

**业务场景**：教务系统查询"选了所有课程的学生"。

**表结构**：
```sql
CREATE TABLE student(sid INT PRIMARY KEY, sname VARCHAR(50));
CREATE TABLE course(cid  INT PRIMARY KEY, cname VARCHAR(50));
CREATE TABLE sc(sid INT, cid INT, score INT);  -- 选课成绩表
```

**测试数据**：
```sql
INSERT INTO student VALUES (1,'张三'),(2,'李四'),(3,'王五');
INSERT INTO course  VALUES (10,'数学'),(20,'语文'),(30,'英语');
INSERT INTO sc VALUES
(1,10,80),(1,20,75),(1,30,90),
(2,10,85),(2,20,70),
(3,10,90),(3,20,80),(3,30,88);
```

**题目要求**：找出选了**所有课程**的学生姓名。

**答案（双否写法）**：
```sql
SELECT s.sname
FROM student s
WHERE NOT EXISTS (
    SELECT c.cid FROM course c
    WHERE NOT EXISTS (
        SELECT 1 FROM sc
        WHERE sc.sid = s.sid AND sc.cid = c.cid
    )
);
```

**答案（聚合写法）**：
```sql
SELECT s.sname
FROM student s
JOIN sc ON s.sid = sc.sid
GROUP BY s.sid, s.sname
HAVING COUNT(DISTINCT sc.cid) = (SELECT COUNT(*) FROM course);
```

**关键考点**：M-N 关系 + "所有"语义。`NOT EXISTS` 是经典面试题，记住双否句式"没有一门课是他没选的"。

---

# 📙 第三部分：子查询与 CTE（10-12 题）

## 第 10 题：相关子查询 - 部门内高于平均工资的员工

**业务场景**：每个部门独立比较，列出"部门内高薪员工"。

**表结构**：延续 `employees`。

**题目要求**：查询每个部门工资**高于本部门平均工资**的员工姓名、工资、部门。

**答案（相关子查询）**：
```sql
SELECT name, salary, dept_id
FROM employees e1
WHERE salary > (
    SELECT AVG(salary)
    FROM employees e2
    WHERE e2.dept_id = e1.dept_id
);
```

**答案（窗口函数改写）**：
```sql
SELECT name, salary, dept_id
FROM (
    SELECT name, salary, dept_id,
           AVG(salary) OVER (PARTITION BY dept_id) AS dept_avg
    FROM employees
) t
WHERE salary > dept_avg;
```

**关键考点**：`相关子查询`（内层引用外层字段）；窗口函数改写更通用。

---

## 第 11 题：EXISTS - 有订单的用户 / 没选课的学生

**业务场景**：找出**至少有一笔订单**的用户（与 `IN` 等价但语义更明确）。

**答案**：
```sql
SELECT u.user_id, u.name
FROM users u
WHERE EXISTS (
    SELECT 1 FROM orders o WHERE o.user_id = u.user_id
);
```

**反向**：找出**没有选过任何课**的学生。
```sql
SELECT s.sid, s.sname
FROM student s
WHERE NOT EXISTS (
    SELECT 1 FROM sc WHERE sc.sid = s.sid
);
```

**关键考点**：`EXISTS` 只关心"有没有"，不看具体字段，写 `SELECT 1` 是惯例；`NOT EXISTS` 是反连接常用手段。

---

## 第 12 题：CTE 重构复杂查询

**业务场景**：用 CTE 把"查每个部门工资最高员工"写得更清晰。

**表结构**：延续 `employees`。

**题目要求**：查询每个部门工资最高的员工姓名、工资、部门。

**答案**：
```sql
WITH dept_max AS (
    SELECT dept_id, MAX(salary) AS max_salary
    FROM employees
    GROUP BY dept_id
)
SELECT e.name, e.salary, e.dept_id
FROM employees e
JOIN dept_max d
  ON e.dept_id = d.dept_id
 AND e.salary = d.max_salary;
```

**关键考点**：`WITH ... AS` 公共表表达式，可读性比嵌套子查询好很多，MySQL 8.0+ / Hive 0.13+ 支持。

---

# 📒 第四部分：窗口函数（13-17 题）

## 第 13 题：ROW_NUMBER - 取每组 Top1

**业务场景**：每个部门工资最高的**唯一**一名员工（同工资用 ROW_NUMBER 保证唯一）。

**表结构**：延续 `employees` 测试数据，新增员工 `(7, '周九', 11000, 2, ...)` 与"钱七"同部门同工资。

**答案**：
```sql
SELECT name, salary, dept_id
FROM (
    SELECT name, salary, dept_id,
           ROW_NUMBER() OVER (PARTITION BY dept_id ORDER BY salary DESC) AS rn
    FROM employees
) t
WHERE rn = 1;
```

**关键考点**：`ROW_NUMBER()` 即使分数相同也会强制编号 1, 2, 3...（取 Top1 唯一）；`RANK()` 会并列跳号；`DENSE_RANK()` 会并列不跳号。

---

## 第 14 题：RANK vs DENSE_RANK 差异

**业务场景**：成绩排名，老板要看"并列第一不算第二"的版本。

**测试数据**：
```sql
-- students: 张三 90, 李四 90, 王五 85, 赵六 70
```

**题目要求**：分别用 `RANK()` 和 `DENSE_RANK()` 排名，对比输出差异。

**答案**：
```sql
SELECT name, score,
       RANK()       OVER (ORDER BY score DESC) AS rk,
       DENSE_RANK() OVER (ORDER BY score DESC) AS drk,
       ROW_NUMBER() OVER (ORDER BY score DESC) AS rn
FROM students;
```

| name | score | rk | drk | rn |
|------|-------|----|-----|----|
| 张三 | 90    | 1  | 1   | 1  |
| 李四 | 90    | 1  | 1   | 2  |
| 王五 | 85    | 3  | 2   | 3  |
| 赵六 | 70    | 4  | 3   | 4  |

**关键考点**：三种排名函数的差异，面试直接问"取第 N 名唯一记录"用哪个。

---

## 第 15 题：LAG / LEAD 计算环比 ✏️【挖空】

**业务场景**：运营周报需要看"每日销售额的环比增长"。

**表结构**：
```sql
CREATE TABLE daily_sales (
  dt      DATE,
  revenue DECIMAL(10,2)
);
```

**测试数据**：
```sql
INSERT INTO daily_sales VALUES
('2024-03-01', 1000),
('2024-03-02', 1500),
('2024-03-03', 1200),
('2024-03-04', 2000),
('2024-03-05', 1800);
```

**题目要求**：查询每天的销售额、以及**相比上一天的环比增长率**（百分比，保留 2 位小数）。

**挖空**：请你补全下面的 SQL（提示：需要 `LAG()` 窗口函数）：
```sql
SELECT dt,
       revenue,
       _______________________ AS prev_revenue,            -- TODO
       ROUND(______________________________, 2) AS growth  -- TODO
FROM daily_sales
ORDER BY dt;
```

**提示**：
1. `LAG(col, n)` 取往上第 n 行的值（这里是 1）。
2. 增长率公式 = `(当前 - 上一天) / 上一天 * 100`。

**参考答案**：
```sql
SELECT dt,
       revenue,
       LAG(revenue, 1) OVER (ORDER BY dt) AS prev_revenue,
       ROUND((revenue - LAG(revenue, 1) OVER (ORDER BY dt))
              / LAG(revenue, 1) OVER (ORDER BY dt) * 100, 2) AS growth
FROM daily_sales
ORDER BY dt;
```

**关键考点**：`LAG(col, offset, default)` / `LEAD(col, offset, default)` 看前后行；是写"环比 / 同比"的万能模板。

---

## 第 16 题：SUM OVER - 累计销售额

**业务场景**：画出每天的累计销售额曲线（帕累托前置练习）。

**表结构**：延续 `daily_sales`。

**题目要求**：查询每天销售额、累计销售额，按日期升序。

**答案**：
```sql
SELECT dt,
       revenue,
       SUM(revenue) OVER (ORDER BY dt)            AS cum_revenue,
       SUM(revenue) OVER (ORDER BY dt ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_revenue_explicit
FROM daily_sales
ORDER BY dt;
```

**关键考点**：
- `SUM() OVER (ORDER BY col)` 默认就是从首行累计到当前行。
- `ROWS BETWEEN ...` 是窗口范围子句，常用：`UNBOUNDED PRECEDING`（起点到当前）、`n PRECEDING`（前 n 行）、`UNBOUNDED FOLLOWING`（当前到末尾）。

---

## 第 17 题：FIRST_VALUE / LAST_VALUE - 分组极值

**业务场景**：每件商品的当前价、历史最高价、历史最低价。

**表结构**：
```sql
CREATE TABLE price_history (
  product  VARCHAR(50),
  price    DECIMAL(10,2),
  rec_date DATE
);
```

**测试数据**：
```sql
INSERT INTO price_history VALUES
('A', 100,'2024-01-01'),('A', 120,'2024-02-01'),('A', 90,'2024-03-01'),
('B', 200,'2024-01-01'),('B', 180,'2024-02-01'),('B', 220,'2024-03-01');
```

**题目要求**：查询每件商品的当前价、历史最高价、历史最低价（按分组内的极值）。

**答案**：
```sql
SELECT product, rec_date, price,
       FIRST_VALUE(price) OVER (PARTITION BY product ORDER BY rec_date ASC)  AS hist_min,
       LAST_VALUE(price)  OVER (PARTITION BY product ORDER BY rec_date ASC
                                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS hist_max
FROM price_history;
```

**关键考点**：`LAST_VALUE` 默认窗口只到当前行，要拿真正的"组内最后一个"必须显式写 `ROWS BETWEEN ... UNBOUNDED FOLLOWING`。这是高频坑。

---

# 📕 第五部分：经典业务（18-22 题）

## 第 18 题：连续登录 N 天的用户 ✏️【挖空】

**业务场景**：运营做"用户黏性"分析，找出真正活跃（连续登录 ≥ 3 天）的用户。

**表结构**：
```sql
CREATE TABLE login_log (
  user_id    INT,
  login_date DATE
);
```

**测试数据**：
```sql
INSERT INTO login_log VALUES
(1,'2024-01-01'),(1,'2024-01-02'),(1,'2024-01-03'),(1,'2024-01-05'),
(2,'2024-01-01'),(2,'2024-01-03'),(2,'2024-01-04'),(2,'2024-01-05'),
(3,'2024-01-01'),(3,'2024-01-08'),(3,'2024-01-09'),(3,'2024-01-10');
```

**题目要求**：找出**连续登录 ≥ 3 天**的用户 ID。

**挖空（核心方法）**：经典做法是"日期减去排名，相同值代表连续段"，请你补全下面的 SQL：

```sql
WITH t AS (
    SELECT user_id, login_date,
           _______________________________ AS grp_key   -- TODO: 关键一行
    FROM (
        SELECT user_id, login_date,
               ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY login_date) AS rn
        FROM (SELECT DISTINCT user_id, login_date FROM login_log) a
    ) b
)
SELECT user_id
FROM t
GROUP BY user_id, grp_key
HAVING COUNT(*) >= 3;
```

**提示**：
1. 用户 1 的 rn=1,2,3,4，日期 01,02,03,05。连续段是 01-03（rn=1,2,3）。
2. 如果 `login_date - rn`（或 `DATE_SUB(login_date, INTERVAL rn DAY)`），连续段的这个值会相同。
3. 因此 `grp_key` 就应该是 `DATE_SUB(login_date, INTERVAL rn DAY)`（Hive/MySQL 都支持）。

**参考答案**：
```sql
WITH t AS (
    SELECT user_id, login_date,
           DATE_SUB(login_date, INTERVAL rn DAY) AS grp_key
    FROM (
        SELECT user_id, login_date,
               ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY login_date) AS rn
        FROM (SELECT DISTINCT user_id, login_date FROM login_log) a
    ) b
)
SELECT user_id
FROM t
GROUP BY user_id, grp_key
HAVING COUNT(*) >= 3;
```

**关键考点**：连续问题的万能公式 —— "日期 - 排名 = 连续段标识"。务必背下来。

---

## 第 19 题：用户留存率（次日 / 7 日 / 30 日）✏️【挖空】

**业务场景**：分析 2024-01-01 新增用户的次日、7 日、30 日留存率。

**表结构**：
```sql
CREATE TABLE user_login (
  user_id    INT,
  login_date DATE
);
```

**测试数据**：
```sql
INSERT INTO user_login VALUES
(1,'2024-01-01'),(1,'2024-01-02'),(1,'2024-01-08'),
(2,'2024-01-01'),(2,'2024-01-03'),
(3,'2024-01-01'),(3,'2024-01-15'),(3,'2024-01-31'),
(4,'2024-01-01');
```

**题目要求**：计算 2024-01-01 当天新增用户的**次日留存率**（=次日还登录的人数 / 当天新增人数）。

**挖空（核心留存判定）**：
```sql
WITH first_login AS (
    SELECT user_id, MIN(login_date) AS first_date
    FROM user_login
    GROUP BY user_id
),
new_users AS (
    SELECT user_id FROM first_login WHERE first_date = '2024-01-01'
),
retain_1d AS (
    SELECT COUNT(DISTINCT a.user_id) AS cnt
    FROM new_users a
    _______________________________ b     -- TODO: 关联 user_login 表，判断次日是否登录
)
SELECT _______________________ AS retention_1d  -- TODO: 计算留存率
FROM (SELECT COUNT(*) AS total FROM new_users) t, retain_1d;
```

**提示**：
1. 次日留存 = `DATEDIFF(b.login_date, a.user_id 的首日) = 1`，或写成 `b.login_date = DATE_ADD('2024-01-01', INTERVAL 1 DAY)`。
2. 留存率 = `次日登录人数 / 当天新增人数`。

**参考答案**：
```sql
WITH first_login AS (
    SELECT user_id, MIN(login_date) AS first_date
    FROM user_login
    GROUP BY user_id
),
new_users AS (
    SELECT user_id FROM first_login WHERE first_date = '2024-01-01'
),
retain_1d AS (
    SELECT COUNT(DISTINCT a.user_id) AS cnt
    FROM new_users a
    JOIN user_login b
      ON a.user_id = b.user_id
     AND b.login_date = DATE_ADD('2024-01-01', INTERVAL 1 DAY)
)
SELECT ROUND(retain_1d.cnt * 1.0 / (SELECT COUNT(*) FROM new_users), 4) AS retention_1d
FROM (SELECT 1) one, retain_1d;
```

**输出**：4 个新增用户中只有 user_id=1 在 2024-01-02 登录，所以次日留存率 = `1/4 = 0.25`。

**关键考点**：留存率 = `DATEDIFF(回访日期, 首日)` 落在 [1, 7, 30] 区间的人数 / 新增人数。

---

## 第 20 题：转化漏斗

**业务场景**：电商统计"浏览 → 加购 → 支付"三步漏斗。

**表结构**：
```sql
CREATE TABLE user_event (
  user_id    INT,
  event_type VARCHAR(20),  -- 'browse' / 'cart' / 'pay'
  event_time DATETIME
);
```

**测试数据**：
```sql
INSERT INTO user_event VALUES
(1,'browse','2024-01-01 10:00'),(1,'cart','2024-01-01 10:05'),(1,'pay','2024-01-01 10:10'),
(2,'browse','2024-01-01 11:00'),(2,'cart','2024-01-01 11:05'),
(3,'browse','2024-01-01 12:00'),(3,'pay','2024-01-01 12:30'),
(4,'browse','2024-01-01 13:00');
```

**题目要求**：统计各环节的用户数，以及**单步转化率**与**总体转化率**。

**答案**：
```sql
WITH funnel AS (
    SELECT event_type, COUNT(DISTINCT user_id) AS uv
    FROM user_event
    WHERE event_type IN ('browse','cart','pay')
    GROUP BY event_type
),
ordered AS (
    SELECT 'browse' AS step, uv FROM funnel WHERE event_type='browse'
    UNION ALL
    SELECT 'cart', uv FROM funnel WHERE event_type='cart'
    UNION ALL
    SELECT 'pay', uv FROM funnel WHERE event_type='pay'
)
SELECT step, uv,
       ROUND(uv * 1.0 / FIRST_VALUE(uv) OVER (ORDER BY FIELD(step,'browse','cart','pay')), 4) AS overall_rate,
       ROUND(uv * 1.0 / LAG(uv) OVER (ORDER BY FIELD(step,'browse','cart','pay')), 4) AS step_rate
FROM ordered;
```

**输出参考**：

| step   | uv | overall_rate | step_rate |
|--------|----|--------------|-----------|
| browse | 4  | 1.0000       | NULL      |
| cart   | 2  | 0.5000       | 0.5000    |
| pay    | 2  | 0.5000       | 1.0000    |

**关键考点**：漏斗的两类转化率（绝对 vs 单步）；按业务顺序排列 + `LAG` 取上一档。

---

## 第 21 题：帕累托累计贡献率（TopN 累计销售额 ≥ 80%）✏️【挖空】

**业务场景**：分析哪些商品贡献了 80% 的销售额（少数商品贡献大部分的"二八定律"）。

**表结构**：
```sql
CREATE TABLE product_sales (
  product VARCHAR(50),
  sales   DECIMAL(10,2)
);
```

**测试数据**：
```sql
INSERT INTO product_sales VALUES
('A', 1000),('B', 800),('C', 600),('D', 400),('E', 200),('F', 100);
```

**题目要求**：按销售额降序排名，并计算**累计销售额占比**；找出**累计占比 ≥ 80% 所需的最少商品数**。

**挖空（核心累计逻辑）**：
```sql
SELECT product, sales,
       _______________________________ AS cum_sales,                    -- TODO
       ROUND(_____________________________ * 100, 2) AS cum_pct          -- TODO
FROM (
    SELECT product, sales,
           ROW_NUMBER() OVER (ORDER BY sales DESC) AS rn
    FROM product_sales
) t
ORDER BY rn;
```

**再补一题**：找出累计占比 ≥ 80% 的最少商品数。
```sql
SELECT MIN(rn) AS min_cnt_to_80pct
FROM (
    SELECT ROW_NUMBER() OVER (ORDER BY sales DESC) AS rn,
           SUM(sales) OVER (ORDER BY sales DESC) / SUM(sales) OVER () AS cum_pct
    FROM product_sales
) t
WHERE cum_pct >= 0.8;
```

**提示**：
1. 累计销售额用 `SUM(col) OVER (ORDER BY ...)`。
2. 累计占比 = `累计销售额 / 总销售额`；分母可以用 `SUM(col) OVER ()`。

**参考答案**：
```sql
SELECT product, sales,
       SUM(sales) OVER (ORDER BY sales DESC) AS cum_sales,
       ROUND(SUM(sales) OVER (ORDER BY sales DESC) / SUM(sales) OVER () * 100, 2) AS cum_pct
FROM (
    SELECT product, sales,
           ROW_NUMBER() OVER (ORDER BY sales DESC) AS rn
    FROM product_sales
) t
ORDER BY rn;
```

**关键考点**：累计求和 + 总体占比的组合。`SUM() OVER ()` 这种"无 PARTITION / 无 ORDER"用法是面试高频 trick。

---

## 第 22 题：用户首次下单与复购判定

**业务场景**：识别"新客"与"复购老客"，并按月统计复购率。

**表结构**：延续前面的 `orders` 表。

**题目要求**：
1. 给每笔订单标注"是否首单"。
2. 统计每月**下单用户数**和**复购用户数**（订单数 ≥ 2 的用户）。

**答案**：
```sql
WITH order_tag AS (
    SELECT user_id, order_id, amount, order_date,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY order_date) AS order_seq
    FROM orders
)
SELECT DATE_FORMAT(order_date, '%Y-%m') AS month,
       COUNT(DISTINCT user_id) AS user_cnt,
       COUNT(DISTINCT CASE WHEN order_seq >= 2 THEN user_id END) AS repurchase_cnt,
       ROUND(COUNT(DISTINCT CASE WHEN order_seq >= 2 THEN user_id END) * 1.0
             / COUNT(DISTINCT user_id), 4) AS repurchase_rate
FROM order_tag
GROUP BY DATE_FORMAT(order_date, '%Y-%m')
ORDER BY month;
```

**关键考点**：`ROW_NUMBER` 给订单编号 → `>=2` 即复购；`COUNT(DISTINCT CASE WHEN ...)` 是统计"满足条件的去重用户数"惯用写法。

---

# 📓 第六部分：大厂真题（23-25 题）

## 第 23 题：字节 - 同时在线人数峰值 ✏️【挖空】

**业务场景**：直播平台统计**同时在线**用户数的峰值（即最多有多少人"同时"在线）。

**表结构**：
```sql
CREATE TABLE user_online (
  user_id     INT,
  login_time  DATETIME,
  logout_time DATETIME
);
```

**测试数据**：
```sql
INSERT INTO user_online VALUES
(1,'2024-01-01 10:00:00','2024-01-01 12:00:00'),
(2,'2024-01-01 11:00:00','2024-01-01 13:00:00'),
(3,'2024-01-01 12:30:00','2024-01-01 14:00:00'),
(4,'2024-01-01 13:15:00','2024-01-01 15:00:00'),
(5,'2024-01-01 09:00:00','2024-01-01 11:30:00');
```

**题目要求**：求**同时在线人数的最大值**（peak concurrent users）。

**挖空（核心思路）**：把所有事件拆成"时间点 + 变化量"（登录 +1、登出 -1），排序后做累计求和，max 即为峰值。请补全：

```sql
WITH events AS (
    SELECT login_time AS tm,  1 AS delta FROM user_online
    UNION ALL
    SELECT logout_time, -1 AS delta FROM user_online
),
running AS (
    SELECT tm, delta,
           _______________________________ AS online_cnt   -- TODO: 累计求和
    FROM events
)
SELECT MAX(online_cnt) AS peak_online
FROM running;
```

**提示**：
1. 关键思路："差分 + 累计" — 每个时间点的 delta 累计起来就是"那一刻的在线人数"。
2. **坑**：登出那一刻在线人数要 -1，所以登出事件要先于登录事件排序（或登录 +1 在登出 -1 之前，让"刚登录"那一刻被算入）。
3. 可以这样处理：登录记 +1，登出记 -1，按时间排序后做 `SUM(delta) OVER (ORDER BY tm)`。

**参考答案**：
```sql
WITH events AS (
    SELECT login_time AS tm,  1 AS delta FROM user_online
    UNION ALL
    SELECT logout_time, -1 AS delta FROM user_online
),
running AS (
    SELECT tm, delta,
           SUM(delta) OVER (ORDER BY tm) AS online_cnt
    FROM events
)
SELECT MAX(online_cnt) AS peak_online
FROM running;
```

**手动验证**：10:00 +1(1) → 11:00 +1(2) → 11:30 -1(1) → 12:00 -1(0) → 12:30 +1(1) → 13:00 -1(0) → 13:15 +1(1) → 14:00 -1(0) → 15:00 -1(-1)

峰值 = **2**（11:00 ~ 11:30 之间，用户 1、2 同时在线）。

**关键考点**：经典的"区间重叠 → 事件流"转换。同时在线峰值、会议室占用峰值、停车场峰值都是同一类题。

---

## 第 24 题：美团 - 1 月份复购率

**业务场景**：统计 2024-01 当月有订单的用户中，"再次下单"的用户比例。

**表结构**：延续 `orders`。

**测试数据**：
```sql
-- orders: 
-- user 1 -> 2024-01-01 (100), 2024-01-15 (200), 2024-02-01 (150)
-- user 2 -> 2024-01-01 (50),  2024-01-10 (80)
-- user 3 -> 2024-01-05 (300)
```

**题目要求**：2024-01 内复购率（=1 月下单 ≥ 2 次的用户数 / 1 月总下单用户数）。

**答案**：
```sql
WITH jan_users AS (
    SELECT user_id, COUNT(*) AS order_cnt
    FROM orders
    WHERE order_date BETWEEN '2024-01-01' AND '2024-01-31'
    GROUP BY user_id
)
SELECT
    COUNT(*) AS total_users,
    SUM(CASE WHEN order_cnt >= 2 THEN 1 ELSE 0 END) AS repurchase_users,
    ROUND(SUM(CASE WHEN order_cnt >= 2 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 4) AS repurchase_rate
FROM jan_users;
```

**手动验证**：1 月下单用户 3 个（user 1: 2 单, user 2: 2 单, user 3: 1 单），复购 2 人，复购率 = `2/3 ≈ 0.6667`。

**关键考点**：
- 复购率定义要清晰：**月内复购** vs **跨月复购**。面试时一定要先确认。
- 简化套路：`COUNT(*) FILTER (WHERE 条件)`（PG 语法），或者 `SUM(CASE WHEN ... THEN 1 ELSE 0 END)`。

---

## 第 25 题：阿里 - 销售业绩综合报表

**业务场景**：销售总监要一张综合报表：每名销售的月度业绩、月度排名、与冠军的差距。

**表结构**：
```sql
CREATE TABLE sales (
  sales_id  INT,
  name      VARCHAR(50),
  amount    DECIMAL(10,2),
  sale_date DATE
);
```

**测试数据**：
```sql
INSERT INTO sales VALUES
(1,'张三',1000,'2024-01-01'),(2,'张三',2000,'2024-01-15'),(3,'张三',1500,'2024-01-25'),
(4,'李四',3000,'2024-01-05'),(5,'李四',2500,'2024-01-20'),
(6,'王五',1800,'2024-01-10'),(7,'王五',2200,'2024-01-28'),
(8,'赵六', 500,'2024-01-12');
```

**题目要求**：查询 2024-01 每名销售的：月度总业绩、月度排名、与第一名业绩的差值。

**答案**：
```sql
WITH monthly AS (
    SELECT name,
           DATE_FORMAT(sale_date, '%Y-%m') AS month,
           SUM(amount) AS month_amount
    FROM sales
    WHERE sale_date BETWEEN '2024-01-01' AND '2024-01-31'
    GROUP BY name, DATE_FORMAT(sale_date, '%Y-%m')
)
SELECT name, month, month_amount,
       RANK() OVER (PARTITION BY month ORDER BY month_amount DESC) AS rk,
       FIRST_VALUE(month_amount) OVER (PARTITION BY month ORDER BY month_amount DESC) AS top_amount,
       FIRST_VALUE(month_amount) OVER (PARTITION BY month ORDER BY month_amount DESC)
         - month_amount AS gap_to_top
FROM monthly
ORDER BY month, rk;
```

**输出参考**：

| name | month | month_amount | rk | top_amount | gap_to_top |
|------|-------|--------------|----|-----------:|-----------:|
| 李四 | 2024-01 | 5500 | 1 | 5500 | 0 |
| 王五 | 2024-01 | 4000 | 2 | 5500 | 1500 |
| 张三 | 2024-01 | 4500 | 3 | 5500 | 1000 |
| 赵六 | 2024-01 | 500  | 4 | 5500 | 5000 |

> ⚠️ 注意：王五和张三的金额会让 RANK 顺序按金额排，4500 > 4000，所以张三 rank 3、王五 rank 2。

**关键考点**：
- `RANK()` 分组排名；
- `FIRST_VALUE` + 与本行做减法 = 与冠军差距；
- 用 `CTE` 把聚合先抽出来，逻辑层次分明。

---

# 🎯 附录：高频考点速记卡

| 考点 | 关键函数 | 一句话记忆 |
|------|---------|-----------|
| 行转列 | `CASE WHEN` + `SUM` | 列值变成列名，聚合填值 |
| 连续 N 天 | `ROW_NUMBER` + `DATE_SUB` | 日期 - 排名 = 连续段 |
| Top1 唯一 | `ROW_NUMBER` | 排名 1,2,3... 强制唯一 |
| 同分并列 | `RANK` vs `DENSE_RANK` | 跳号 vs 不跳号 |
| 环比 | `LAG(col, 1)` | 取上一行 |
| 累计 | `SUM OVER (ORDER BY ...)` | 从首行到当前 |
| 留存 | `DATEDIFF` 落在 [1,7,30] | 回访人数 / 新增人数 |
| 漏斗 | `LAG` + 排序 | 当前 / 上一档 = 单步 |
| 同时在线峰值 | 事件流 + 累计 | login +1, logout -1 |
| 复购 | `COUNT>=2` 或 `ROW_NUMBER>=2` | 区分月内 / 跨月 |

---

# 🛠️ 刷题建议

1. **每题三遍法**：
   - 第一遍：看题目自己写（不看答案），15 分钟想不出来看提示。
   - 第二遍：对照答案，找出"卡在哪一步"。
   - 第三遍：隔一天再写一遍，能一次写出就算掌握。

2. **举一反三**：每道题都想想"如果表里有 null 怎么办？""如果数据量是 1 亿怎么办？"（考察对 NULL 语义和性能的理解）。

3. **面试表达模板**：
   - "我先说一下思路……（把解题方法讲清楚）"
   - "完整 SQL 是……（边写边讲关键函数）"
   - "如果考虑边界（NULL / 同分 / 性能），我会这样改……"

4. **背 + 理解并重**：挖空题（15、18、19、21、23）务必自己写一遍，这五道都是面试官最爱追问的"变形题"。

---

> 文档生成于：2026-07-20  
> 适用：27 届秋招（数据开发 / 数仓 / 数据分析）  
> 数据库：MySQL 8.0+ / Hive / Spark SQL 通用