# -*- coding: utf-8 -*-
"""
gen_dw_sql.py
=============

数据仓库种子脚本生成器（2026-07-17 扩容版）。

目标：把 dw.sql 从 115 单扩到 5 万+ 单，覆盖 2025-01 ~ 2026-07。
同时扩展维表 + 给 fact_order 加 3 个新字段。

设计取舍：
- 用 stdlib random，不用 faker（venv 没 pip，且业务字段有限）
- 数据分布模拟真实电商：周末高 / 工作日低、618/双 11 爆量、品类有偏置
- 大区维度统一：让"华东地区" = R002(浙江省) + R005(上海市) + R007(华东大区)
  （注意：R007 是大区总条目，dim_region 自身用 province 字段区分层级，
  通过 R002/R005 关联 fact_order，统计华东用 province IN ('浙江省','上海市')）

输出：直接 print 到 stdout，shell 重定向到 dw.sql 即可。
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from io import StringIO

random.seed(42)  # 固定随机种子，结果可复现

# ---------- 1. 时间维度（2025-01-01 ~ 2026-07-17，共 563 天） ----------
DATE_START = date(2025, 1, 1)
DATE_END = date(2026, 7, 17)
DAYS = (DATE_END - DATE_START).days + 1  # 563

def quarter_of(month: int) -> str:
    return f"Q{(month - 1) // 3 + 1}"

def make_date_id(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day

date_rows: list[tuple[int, int, str, int, int]] = []  # (date_id, year, quarter, month, day)
for i in range(DAYS):
    d = DATE_START + timedelta(days=i)
    date_rows.append((make_date_id(d), d.year, quarter_of(d.month), d.month, d.day))

# ---------- 2. 区域维度（6 省/直辖市 + 大区行） ----------
# 注：province 是省级名（大区所在省/直辖市），region_name 是所属大区
# 这样事实表 region_id 关联 dim_region，统计"华东" 用 region_name='华东'
# 比双层建模简单，且现有 Agent LLM 容易理解
region_rows: list[tuple[str, str, str, str]] = [
    ('R001', '广东省', '华南', '中国'),
    ('R002', '浙江省', '华东', '中国'),
    ('R003', '四川省', '西南', '中国'),
    ('R004', '北京市', '华北', '中国'),
    ('R005', '上海市', '华东', '中国'),
    ('R006', '湖北省', '华中', '中国'),
    ('R007', '江苏省', '华东', '中国'),  # 新增：让华东更有量
    ('R008', '山东省', '华东', '中国'),  # 新增
    ('R009', '福建省', '华东', '中国'),  # 新增
    ('R010', '湖南省', '华中', '中国'),  # 新增
    ('R011', '陕西省', '西北', '中国'),  # 新增
    ('R012', '辽宁省', '东北', '中国'),  # 新增
]

# ---------- 3. 客户维度（500 人：原 20 + 扩 480） ----------
# 姓氏表 + 名字表，避免 faker 直接出怪名
FAMILY_NAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗宋郑梁谢唐韩曹许邓萧冯曾程蔡彭潘袁于董余苏叶吕魏蒋田杜丁沈姜范江傅钟卢汪戴崔任陆廖姚方金邱夏谭韦贾邹石熊孟秦阎薛侯雷白龙段郝孔邵史毛常万顾赖武康贺严尹钱施牛洪龚")
GIVEN_NAMES_M = ["伟", "强", "磊", "洋", "勇", "军", "杰", "涛", "明", "超", "辉", "鹏", "斌", "波", "浩", "亮", "俊", "飞", "鑫", "宇", "晗", "昊", "辰", "逸", "轩"]
GIVEN_NAMES_F = ["芳", "敏", "静", "丽", "艳", "娟", "霞", "梅", "燕", "玲", "婷", "雪", "倩", "雯", "洁", "颖", "佳", "欣", "怡", "宁", "玥", "瑶", "萌", "蕊", "彤"]
GENDERS = ["男", "女"]
MEMBER_LEVELS = ["青铜", "白银", "黄金", "铂金", "钻石"]  # 钻石是新增，4→5 级

# 会员等级权重：金字塔分布（青铜最多，钻石最少）
LEVEL_WEIGHTS = [40, 30, 18, 9, 3]

customer_rows: list[tuple[str, str, str, str]] = []
used_names: set[str] = set()
for i in range(1, 501):  # C001 ~ C500
    gender = random.choice(GENDERS)
    given_pool = GIVEN_NAMES_M if gender == "男" else GIVEN_NAMES_F
    # 2 字或 3 字名
    name = random.choice(FAMILY_NAMES) + "".join(random.sample(given_pool, random.choice([1, 2])))
    if name in used_names:
        # 重名兜底加编号
        name = name + str(random.randint(1, 99))
    used_names.add(name)
    level = random.choices(MEMBER_LEVELS, weights=LEVEL_WEIGHTS, k=1)[0]
    customer_rows.append((f"C{i:03d}", name, gender, level))

# ---------- 4. 商品维度（100 个 SKU：每品类 15-20 个） ----------
# 5 大类，每类扩展到 20 个（基础系列 + 子型号）
PRODUCT_TEMPLATE = {
    "手机数码": [
        ("iPhone 15 Pro Max", "苹果"), ("iPhone 15", "苹果"), ("iPhone 14", "苹果"),
        ("Galaxy S24 Ultra", "三星"), ("Galaxy S24", "三星"), ("Galaxy Z Fold5", "三星"),
        ("Mate 60 Pro+", "华为"), ("Mate 60", "华为"), ("Pura 70 Pro", "华为"), ("nova 12", "华为"),
        ("小米 14 Ultra", "小米"), ("小米 14", "小米"), ("Redmi K70", "小米"),
        ("OPPO Find X7", "OPPO"), ("OPPO Reno11", "OPPO"),
        ("vivo X100 Pro", "vivo"), ("vivo S18", "vivo"),
        ("一加 12", "一加"), ("真我 GT5", "realme"),
        ("Kindle Paperwhite", "亚马逊"),
    ],
    "家用电器": [
        ("戴森 V15 吸尘器", "戴森"), ("戴森吹风机 HD15", "戴森"),
        ("美的空调 KFR-35GW", "美的"), ("美的电饭煲 MB-HS4093", "美的"), ("美的微波炉 M1-L213B", "美的"),
        ("格力空调 KFR-26GW", "格力"), ("格力电热水器", "格力"),
        ("海尔冰箱 BCD-501WLHFD9DGYU1", "海尔"), ("海尔洗衣机 EG100MATE71S", "海尔"),
        ("西门子洗碗机 SJ23HB66KC", "西门子"), ("西门子冰箱", "西门子"),
        ("松下空气净化器", "松下"), ("松下电饭煲", "松下"),
        ("飞利浦电动牙刷 HX9924", "飞利浦"), ("飞利浦剃须刀 S9000", "飞利浦"),
        ("九阳破壁机 Y88", "九阳"), ("九阳豆浆机", "九阳"),
        ("苏泊尔电压力锅", "苏泊尔"), ("Instant Pot 多功能电压力锅", "Instant Pot"),
        ("小米米家扫地机器人", "小米"),
    ],
    "鞋靴": [
        ("耐克 Air Max 270", "耐克"), ("耐克 Air Force 1", "耐克"), ("耐克 Dunk Low", "耐克"),
        ("阿迪达斯 Ultraboost", "阿迪达斯"), ("阿迪达斯 Stan Smith", "阿迪达斯"), ("阿迪达斯 Superstar", "阿迪达斯"),
        ("新百伦 990v6", "新百伦"), ("新百伦 574", "新百伦"),
        ("匡威 Chuck 70", "匡威"), ("匡威 All Star", "匡威"),
        ("万斯 Old Skool", "Vans"), ("万斯 Authentic", "Vans"),
        ("彪马 Suede Classic", "彪马"), ("彪马 RS-X", "彪马"),
        ("亚瑟士 Gel-Kayano 30", "亚瑟士"), ("亚瑟士 Nimbus 25", "亚瑟士"),
        ("斯凯奇 D'Lites", "斯凯奇"), ("斯凯奇 Go Walk 6", "斯凯奇"),
        ("安踏 KT9", "安踏"), ("李宁 飞电 3", "李宁"),
    ],
    "服饰": [
        ("优衣库 Heattech 保暖夹克", "优衣库"), ("优衣库摇粒绒外套", "优衣库"), ("优衣库 AIRism T 恤", "优衣库"),
        ("李维斯 501 牛仔裤", "李维斯"), ("李维斯 511 牛仔裤", "李维斯"),
        ("杰克琼斯 羽绒夹克", "杰克琼斯"), ("杰克琼斯 针织衫", "杰克琼斯"),
        ("太平鸟 连衣裙", "太平鸟"), ("太平鸟 风衣", "太平鸟"),
        ("GXG 卫衣", "GXG"), ("GXG 夹克", "GXG"),
        ("海澜之家 西装外套", "海澜之家"), ("海澜之家 衬衫", "海澜之家"),
        ("波司登 羽绒服", "波司登"), ("波司登 商务夹克", "波司登"),
        ("森马 T 恤", "森马"), ("森马 牛仔裤", "森马"),
        ("ONLY 针织衫", "ONLY"), ("ONLY 牛仔裤", "ONLY"),
        ("VERO MODA 连衣裙", "VERO MODA"),
    ],
    "食品饮料": [
        ("雀巢金牌速溶咖啡", "雀巢"), ("雀巢丝滑拿铁", "雀巢"),
        ("蒙牛纯牛奶 250ml*12", "蒙牛"), ("蒙牛特仑苏", "蒙牛"), ("蒙牛酸奶", "蒙牛"),
        ("伊利安慕希酸奶", "伊利"), ("伊利金典牛奶", "伊利"), ("伊利舒化奶", "伊利"),
        ("农夫山泉矿泉水 550ml*24", "农夫山泉"), ("农夫山泉东方树叶", "农夫山泉"),
        ("康师傅红烧牛肉面 5 包", "康师傅"), ("康师傅冰红茶", "康师傅"),
        ("统一老坛酸菜面", "统一"), ("统一阿萨姆奶茶", "统一"),
        ("可口可乐 330ml*24", "可口可乐"), ("雪碧 330ml*24", "雪碧"),
        ("百事可乐 330ml*24", "百事"), ("芬达橙味 330ml*24", "芬达"),
        ("青岛啤酒 500ml*12", "青岛"), ("雪花啤酒 500ml*12", "雪花"),
    ],
    "休闲零食": [
        ("乐事原味薯片 150g", "乐事"), ("乐事黄瓜味薯片", "乐事"), ("乐事烧烤味", "乐事"),
        ("奥利奥巧克力夹心饼干", "奥利奥"), ("奥利奥薄脆", "奥利奥"),
        ("趣多多巧克力豆", "趣多多"), ("趣多多曲奇", "趣多多"),
        ("上好佳鲜虾片", "上好佳"), ("上好佳玉米卷", "上好佳"),
        ("旺旺仙贝 84g", "旺旺"), ("旺旺雪饼", "旺旺"), ("旺旺小小酥", "旺旺"),
        ("三只松鼠每日坚果 750g", "三只松鼠"), ("三只松鼠夏威夷果", "三只松鼠"),
        ("良品铺子肉松饼", "良品铺子"), ("良品铺子芒果干", "良品铺子"),
        ("百草味芒果干", "百草味"), ("百草味猪肉脯", "百草味"),
        ("洽洽香瓜子 160g", "洽洽"), ("洽洽焦糖瓜子", "洽洽"),
    ],
}

product_rows: list[tuple[str, str, str, str]] = []
pid = 0
for category, items in PRODUCT_TEMPLATE.items():
    for pname, brand in items:
        pid += 1
        product_rows.append((f"P{pid:03d}", pname, category, brand))

# ---------- 5. 订单事实表（5 万+ 行） ----------
# 分布设计：
# - 日均订单量：100-300（普通日 80-150、618/双11 爆 800-1500、春节 50-100）
# - 客单价：品类有偏置（手机 5000-10000、家电 500-5500、服饰 200-1500、食品 30-200）
# - 客户分布：头部 5% 客户贡献 25% 订单（长尾）
# - 状态：85% 已支付 / 10% 已退款 / 5% 已取消
# - 渠道：APP 50% / 小程序 30% / PC 20%
# - 支付：支付宝 45% / 微信 40% / 银行卡 15%

ORDER_STATUS = ["已支付", "已支付", "已支付", "已支付", "已支付", "已支付", "已支付", "已支付", "已退款", "已取消"]
CHANNELS = ["APP", "APP", "APP", "APP", "APP", "小程序", "小程序", "小程序", "PC", "PC"]
PAYMENTS = ["支付宝", "支付宝", "支付宝", "支付宝", "支付宝", "微信", "微信", "微信", "微信", "银行卡"]

# 销售量爆点
SALES_PEAKS = {
    (6, 1): 3.0, (6, 18): 5.0,    # 618
    (11, 11): 6.0, (11, 1): 2.5,  # 双 11
    (5, 1): 1.5,                   # 51 劳动节
    (1, 1): 0.4, (2, 10): 0.5,     # 春节
    (12, 25): 1.3,                 # 圣诞
    (8, 15): 1.4, (9, 10): 1.4,   # 中秋教师节
}

def day_multiplier(d: date) -> float:
    """返回该日期的订单量倍率"""
    base = 1.0
    # 周末加成
    if d.weekday() in (5, 6):  # 周六周日
        base *= 1.3
    # 节日加成
    base *= SALES_PEAKS.get((d.month, d.day), 1.0)
    return base

# 品类价格区间
PRICE_RANGE = {
    "手机数码": (499, 10999, 5500),
    "家用电器": (299, 5999, 1500),
    "鞋靴": (299, 1999, 800),
    "服饰": (99, 1999, 400),
    "食品饮料": (15, 200, 60),
    "休闲零食": (8, 150, 35),
}

# 客户头部偏好：让前 25 个客户权重 3x
CUSTOMER_WEIGHT_BASE = [3.0 if i < 25 else 1.0 for i in range(500)]

def generate_fact_orders() -> list[tuple]:
    """生成 5 万+ 订单，按日期分布"""
    orders: list[tuple] = []
    oid = 0

    # 先按天算出"目标单量"分布
    daily_target: list[tuple[date, int]] = []
    for i in range(DAYS):
        d = DATE_START + timedelta(days=i)
        base = random.randint(80, 150)
        target = int(base * day_multiplier(d))
        daily_target.append((d, target))

    total_target = sum(t for _, t in daily_target)
    import sys
    print(f"# 目标总单量：{total_target}", file=sys.stderr, flush=True)

    for d, target in daily_target:
        date_id = make_date_id(d)
        for _ in range(target):
            oid += 1
            # 客户：有偏置采样
            customer_id = random.choices(
                [c[0] for c in customer_rows],
                weights=CUSTOMER_WEIGHT_BASE,
                k=1
            )[0]
            # 商品：直接均匀采样
            product = random.choice(product_rows)
            product_id, _, category, _ = product
            # 区域：华东/华南/华北 偏多
            region_id = random.choices(
                [r[0] for r in region_rows],
                weights=[12, 18, 8, 15, 25, 12, 18, 14, 10, 10, 5, 5],  # 华东高（R002/R005/R007/R008/R009 都有重）
                k=1
            )[0]
            # 数量 1-3
            qty = random.choices([1, 2, 3], weights=[70, 25, 5], k=1)[0]
            # 价格：按品类
            lo, hi, mid = PRICE_RANGE[category]
            unit_price = random.randint(lo, hi) if random.random() > 0.1 else mid
            amount = round(qty * unit_price, 2)
            # 新字段
            status = random.choice(ORDER_STATUS)
            channel = random.choice(CHANNELS)
            payment = random.choice(PAYMENTS)
            orders.append((
                f"ORD{date_id}{oid:04d}", customer_id, product_id, date_id,
                region_id, qty, amount, status, channel, payment
            ))

    return orders

fact_orders = generate_fact_orders()
import sys
print(f"# 实际生成订单数：{len(fact_orders)}", file=sys.stderr, flush=True)

# ---------- 6. 输出 SQL ----------
def sql_str(s: str) -> str:
    """SQL 字符串转义（用双引号包裹，转义双引号）"""
    return '"' + s.replace('"', '\\"') + '"'

def to_sql_value(v) -> str:
    if isinstance(v, str):
        return sql_str(v)
    return str(v)

out = StringIO()
out.write("SET NAMES utf8mb4;\n\n")
out.write("CREATE DATABASE IF NOT EXISTS dw DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;\n")
out.write("GRANT ALL PRIVILEGES ON dw.* TO 'root'@'%';\n")
out.write("USE dw;\n\n")

# ----- dim_region -----
out.write("DROP TABLE IF EXISTS dim_region;\n")
out.write("""CREATE TABLE dim_region (
    region_id   VARCHAR(20) PRIMARY KEY,
    province    VARCHAR(50),
    region_name VARCHAR(50),
    country     VARCHAR(50)
);

""")
out.write("INSERT INTO dim_region (region_id, province, region_name, country) VALUES\n")
out.write(",\n".join(f"  ({sql_str(r[0])}, {sql_str(r[1])}, {sql_str(r[2])}, {sql_str(r[3])})" for r in region_rows))
out.write(";\n\n")

# ----- dim_customer -----
out.write("DROP TABLE IF EXISTS dim_customer;\n")
out.write("""CREATE TABLE dim_customer (
    customer_id   VARCHAR(20) PRIMARY KEY,
    customer_name VARCHAR(50),
    gender        VARCHAR(10),
    member_level  VARCHAR(20)
);

""")
out.write("INSERT INTO dim_customer (customer_id, customer_name, gender, member_level) VALUES\n")
out.write(",\n".join(f"  ({sql_str(c[0])}, {sql_str(c[1])}, {sql_str(c[2])}, {sql_str(c[3])})" for c in customer_rows))
out.write(";\n\n")

# ----- dim_product -----
out.write("DROP TABLE IF EXISTS dim_product;\n")
out.write("""CREATE TABLE dim_product (
    product_id   VARCHAR(20) PRIMARY KEY,
    product_name VARCHAR(200),
    category     VARCHAR(50),
    brand        VARCHAR(50)
);

""")
out.write("INSERT INTO dim_product (product_id, product_name, category, brand) VALUES\n")
out.write(",\n".join(f"  ({sql_str(p[0])}, {sql_str(p[1])}, {sql_str(p[2])}, {sql_str(p[3])})" for p in product_rows))
out.write(";\n\n")

# ----- dim_date -----
out.write("DROP TABLE IF EXISTS dim_date;\n")
out.write("""CREATE TABLE dim_date (
    date_id INT PRIMARY KEY,
    year    INT,
    quarter VARCHAR(2),
    month   INT,
    day     INT
);

""")
out.write("INSERT INTO dim_date (date_id, year, quarter, month, day) VALUES\n")
out.write(",\n".join(f"  ({d[0]}, {d[1]}, {sql_str(d[2])}, {d[3]}, {d[4]})" for d in date_rows))
out.write(";\n\n")

# ----- fact_order（加 3 个新字段） -----
out.write("DROP TABLE IF EXISTS fact_order;\n")
out.write("""CREATE TABLE fact_order (
    order_id       VARCHAR(30) PRIMARY KEY,
    customer_id    VARCHAR(20),
    product_id     VARCHAR(20),
    date_id        INT,
    region_id      VARCHAR(20),
    order_quantity INT,
    order_amount   FLOAT,
    order_status   VARCHAR(20),  -- 2026-07-17 新增：已支付/已退款/已取消
    channel        VARCHAR(20),  -- 2026-07-17 新增：APP/小程序/PC
    payment_method VARCHAR(20)   -- 2026-07-17 新增：支付宝/微信/银行卡
);

""")
# 分批 INSERT（每 1000 行一批，减小单语句体积）
BATCH = 1000
out.write("INSERT INTO fact_order (order_id, customer_id, product_id, date_id, region_id, order_quantity, order_amount, order_status, channel, payment_method) VALUES\n")
for i in range(0, len(fact_orders), BATCH):
    batch = fact_orders[i:i + BATCH]
    out.write(",\n".join(
        f"  ({sql_str(o[0])}, {sql_str(o[1])}, {sql_str(o[2])}, {o[3]}, {sql_str(o[4])}, {o[5]}, {o[6]:.2f}, {sql_str(o[7])}, {sql_str(o[8])}, {sql_str(o[9])})"
        for o in batch
    ))
    if i + BATCH < len(fact_orders):
        out.write(";\nINSERT INTO fact_order (order_id, customer_id, product_id, date_id, region_id, order_quantity, order_amount, order_status, channel, payment_method) VALUES\n")
    else:
        out.write(";\n")

# ----- 索引建议 -----
out.write("""
-- 索引建议（应用层可选，dw.sql 兜底不带）
-- CREATE INDEX idx_fact_date ON fact_order(date_id);
-- CREATE INDEX idx_fact_region ON fact_order(region_id);
-- CREATE INDEX idx_fact_customer ON fact_order(customer_id);
-- CREATE INDEX idx_fact_product ON fact_order(product_id);
""")

print(out.getvalue())
