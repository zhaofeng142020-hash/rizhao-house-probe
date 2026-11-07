"""演示数据生成器。

⚠️ 重要说明：本文件生成的是**示意数据**，小区名参考日照真实板块命名习惯，
但具体房源、价格、法拍标的不代表真实挂牌信息，仅用于本地演示与模型自检。
真实数据请通过 crawler/ 抓取公开页面（需自行确认目标站点 robots.txt 与合规要求）。

数据设计目标：让每一类异动/评分分支都能在演示数据里被触发，方便验收。
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta

from ..db import execute, get_conn, query_one

RNG_SEED = 20261105
TODAY = date.today()

# 演示数据版本号。启动时与 app_setting.seed_version 比对：
# 不一致就重灌一次（仅在 CRAWL_ENABLED=0 的演示模式下这么做），
# 避免「代码升级了但旧库还是老样本」，导致专题页样本不足或新表是空的。
SEED_VERSION = "2026-11-05.2"

# (小区名, 板块, lat, lng, 基准单价, 建成年, 电梯, 对口学校, 个盘流动性, 近90天成交, 是否扩展区)
# 近90天成交套数与流动性分数要保持自洽：成交太高而流动性分数偏低会很假，
# 也会让「去化月数」算出 1~2 个月这种不可能的值。
COMMUNITIES: list[tuple] = [
    ("安泰·未来城",   "新市区", 35.4185, 119.5320, 11800, 2015, 1, "日照市金海岸小学", 88, 8, 0),
    ("华润中心凯旋门", "新市区", 35.4250, 119.5270, 12400, 2017, 1, "日照市实验学校", 90, 9, 0),
    ("兴业·金海学府", "新市区", 35.4230, 119.5390, 11200, 2013, 1, "金海岸小学", 82, 6, 0),
    ("城建·时代名苑", "东港区", 35.4110, 119.5210, 10200, 2010, 1, "东港区第三小学", 76, 5, 0),
    ("金域华府",      "东港区", 35.4155, 119.5155,  9800, 2012, 1, "日照市第二实验小学", 74, 5, 0),
    ("石臼海滨小区",   "石臼",   35.3900, 119.5510,  8600, 2003, 0, "石臼小学", 62, 3, 0),
    ("石臼老街花园",   "老城",   35.4260, 119.5080,  7600, 1999, 0, "日照市实验小学", 55, 2, 0),
    ("教授花园",      "山海天", 35.4420, 119.5820,  9200, 2008, 1, "山海天实验小学", 58, 2, 0),
    ("香河佳苑",      "高新区", 35.4520, 119.4980,  7000, 2006, 0, "高新区实验学校", 50, 2, 0),
    ("奎山花园",      "开发区", 35.3800, 119.5620,  6600, 2005, 0, "开发区实验小学", 46, 1, 0),
    ("日照港家属院",   "石臼",   35.3950, 119.5480,  9100, 2010, 1, "石臼小学", 64, 3, 0),
    ("北京路小区",     "开发区", 35.3980, 119.5450,  7400, 2012, 1, "开发区实验学校", 54, 2, 0),
    # 扩展区：流动性明显更弱，需明确标注
    ("沭河人家",      "莒县",   35.5800, 118.8400,  5200, 2011, 1, "莒县第一实验小学", 32, 1, 1),
    ("五莲山城小区",   "五莲",   35.7500, 119.2100,  4800, 2009, 1, "五莲县实验小学", 28, 0, 1),
    ("岚山港口家园",   "岚山",   35.1200, 119.3300,  4500, 2007, 0, "岚山区实验小学", 26, 0, 1),
]

# (户型, 室数, 厅数, 卫数, 名义面积)
LAYOUTS = [
    ("2室1厅1卫", 2, 1, 1, 78.0),
    ("2室2厅1卫", 2, 2, 1, 89.0),
    ("3室2厅1卫", 3, 2, 1, 105.0),
    ("3室2厅2卫", 3, 2, 2, 118.0),
    ("4室2厅2卫", 4, 2, 2, 138.0),
]

# 重点面积段（120-150㎡）专用户型：改善型供给天然比刚需少，
# 但要保证足够样本，否则该面积段的中位数与折价率算不出来。
TARGET_LAYOUTS = [
    ("3室2厅2卫", 3, 2, 2, 131.0),   # 实际落在 127~137㎡
    ("4室2厅2卫", 4, 2, 2, 143.0),   # 实际落在 139~149㎡
]

# --------------------------------------------------------------------------
# 新房（在售楼盘）。字段顺序见 _NEW_HOME_FIELDS
# (楼盘, 板块, 均价, 最小面积, 最大面积, 主力户型, 交付, 装修, 物业费, 容积率, 绿化率, 总户数, 状态, 风险标签, 备注)
# --------------------------------------------------------------------------
NEW_HOMES: list[tuple] = [
    ("兴业·海曲府", "新市区", 9800, 115.0, 165.0, "3室2厅2卫 / 4室2厅2卫", "2026-12", "精装",
     2.6, 2.0, 35.0, 860, "尾盘", [], "尾盘特价清库存，价格低于同板块二手，需确认是否剩差楼层"),
    ("安泰·悦府", "新市区", 12600, 105.0, 143.0, "3室2厅2卫 / 4室2厅2卫", "2027-06", "精装",
     3.2, 2.4, 40.0, 620, "在售", [], "品牌开发商，溢价明显，比价要看套内单价"),
    ("城建·海韵华府", "东港区", 9200, 98.0, 128.0, "3室2厅1卫 / 3室2厅2卫", "2026-09", "精装",
     2.4, 2.2, 38.0, 740, "在售", [], "准现房，交付确定性较高"),
    ("华润·置地中心", "新市区", 13800, 128.0, 186.0, "4室2厅2卫 / 5室2厅2卫", "2027-10", "精装",
     3.8, 2.8, 42.0, 480, "在售", [], "改善大平层，总价门槛高，客群窄"),
    ("山海天·蓝湾", "山海天", 8600, 120.0, 158.0, "3室2厅2卫 / 4室2厅2卫", "2026-11", "毛坯",
     2.0, 1.8, 45.0, 560, "在售", [], "毛坯交付，装修要另算 1200 元/㎡ 以上"),
    ("高新·智谷佳苑", "高新区", 5800, 95.0, 125.0, "2室2厅1卫 / 3室2厅2卫", "2026-06", "精装",
     1.6, 2.3, 36.0, 920, "在售", [], "高新区限价盘，精装交付，价格明显低于周边二手"),
    ("石臼·海曲新苑", "石臼", 7800, 88.0, 118.0, "2室2厅1卫 / 3室2厅1卫", "2025-12", "精装",
     1.8, 2.5, 33.0, 680, "在售", [], "已交付现房尾盘，可直接验房"),
    ("开发区·奎山壹号", "开发区", 4800, 110.0, 140.0, "3室2厅2卫 / 4室2厅2卫", "2027-03", "毛坯",
     1.5, 2.9, 30.0, 520, "在售", ["交付风险", "教育配套未落地"],
     "工程抵账房为主，单价明显低于周边；名义便宜三成，调整装修与得房率后优势收窄，先查预售资金监管"),
]


DECORATIONS = ["毛坯", "简装", "精装", "豪装"]
ORIENTATIONS = ["南北", "南北", "南", "东南", "北"]

# 硬伤对单价的自然折价（市场真实成交里的体现）
DEFECT_UNIT_FACTOR = {
    "顶楼": 0.95, "临街": 0.96, "暗卫": 0.98, "户型异形": 0.95,
    "无电梯": 0.92, "房龄>25": 0.94, "腰线": 0.97, "装修旧": 0.97, "采光差": 0.96,
}


def _md5(text: str) -> str:
    import hashlib
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _round_price(total: float) -> float:
    """总价取整到千元。"""
    return round(total / 1000) * 1000 if total > 0 else 0


def seed_communities() -> dict[str, int]:
    mapping: dict[str, int] = {}
    with get_conn() as conn:
        for (name, district, lat, lng, *_rest) in COMMUNITIES:
            cur = conn.execute("SELECT id FROM community WHERE name=? AND district=?", (name, district)).fetchone()
            if cur:
                mapping[name] = int(cur["id"])
                continue
            _, district, lat, lng, base, year, elev, school, liq, deal, ext = (
                name, district, lat, lng, *_rest
            )
            cur = conn.execute(
                "INSERT INTO community (name,district,is_extended,lat,lng,build_year,has_elevator,"
                "school_district,liquidity_score,deal_active_90d) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name, district, ext, lat, lng, year, elev, school, liq, deal),
            )
            mapping[name] = int(cur.lastrowid)
    return mapping


def _make_history(
    listing_id: int, first_price: float, current_price: float, list_days: int, changes: int
) -> tuple[int, int, float | None, float]:
    """生成调价向量（首次价→当前价→每次调价幅度/间隔）。

    返回 (调价次数, 连续降价次数, 最近一次降价距今天数, 首次价)
    """
    if changes <= 0 or first_price <= current_price:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO price_history (listing_id,price,observed_at,drop_pct,days_gap,kind) "
                "VALUES (?,?,?,?,?,?)",
                (listing_id, current_price, (TODAY - timedelta(days=list_days)).isoformat(), 0, None, "listing"),
            )
        return 0, 0, None, current_price

    # 把总降幅拆到 n 次里，越靠后越急（符合真实急售节奏）
    weights = [1.0 + 0.6 * i for i in range(changes)]
    total_w = sum(weights)
    total_drop = first_price - current_price
    steps = [total_drop * w / total_w for w in weights]

    # 调价时间点：越靠后的调价越贴近今天（真实急售通常是「先小降、后急降」）
    span = max(list_days, changes * 8)
    dates: list[date] = []
    for i in range(changes):
        frac = min(0.97, ((i + 1) / changes) ** 1.4)   # 0~1，最后一条落在 97% 处
        dates.append(TODAY - timedelta(days=max(3, int(span * (1 - frac)))))

    prev_price = first_price
    prev_date = TODAY - timedelta(days=list_days)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO price_history (listing_id,price,observed_at,drop_pct,days_gap,kind) VALUES (?,?,?,?,?,?)",
            (listing_id, first_price, prev_date.isoformat(), 0, None, "listing"),
        )
        for step, d in zip(steps, dates):
            new_price = _round_price(prev_price - step)
            drop_pct = (new_price - prev_price) / prev_price if prev_price else 0
            conn.execute(
                "INSERT INTO price_history (listing_id,price,observed_at,drop_pct,days_gap,kind) "
                "VALUES (?,?,?,?,?,?)",
                (listing_id, new_price, d.isoformat(), round(drop_pct, 4), (d - prev_date).days, "listing"),
            )
            prev_price, prev_date = new_price, d

    consec = changes if all(s > 0 for s in steps) else 1
    last_drop_days_ago = (TODAY - dates[-1]).days
    return changes, consec, last_drop_days_ago, first_price


def insert_listing(
    community: tuple,
    layout: tuple,
    *,
    unit_override: float | None = None,
    area_override: float | None = None,
    defects: list[str] | None = None,
    list_days: int | None = None,
    changes: int | None = None,
    total_drop_pct: float = 0.0,
    source: str = "fangtianxia",
    rent_month: float | None = None,
    floor_text: str | None = None,
    size_factor: float = 1.0,
) -> int:
    """插入一条挂牌并生成其调价历史。返回 listing.id。"""
    name, district, lat, lng, base_unit, build_year, elev, school, liq, deal, ext = community
    layout_name, rooms, halls, baths, nominal_area = layout

    area = area_override or round(nominal_area + random.uniform(-4, 6), 1)
    defects = defects or []
    decor = random.choice(DECORATIONS)
    if "装修旧" in defects and decor in ("精装", "豪装"):
        decor = "简装"

    unit = unit_override or base_unit * random.uniform(0.90, 1.12)
    unit *= size_factor          # 大面积折价（真实市场里 120㎡+ 单价普遍低于小面积）
    for tag in defects:
        unit *= DEFECT_UNIT_FACTOR.get(tag, 1.0)
    unit = round(unit)
    total = _round_price(unit * area)

    has_elev = 0 if "无电梯" in defects else elev
    age = TODAY.year - build_year
    if age > 25 and "房龄>25" not in defects:
        defects = defects + ["房龄>25"]

    floor_total = random.choice([6, 6, 11, 18, 26, 33])
    if floor_text is None:
        if "顶楼" in defects:
            floor_no = floor_total
        else:
            floor_no = random.randint(1, max(1, floor_total - 1))
        floor_text = f"{floor_no}/{floor_total}层"
    else:
        try:
            floor_no = int(floor_text.split("/")[0])
        except (ValueError, IndexError):
            floor_no = 1

    days = list_days if list_days is not None else random.randint(15, 160)
    n_changes = changes if changes is not None else (random.choice([0, 0, 0, 1, 1, 2]) if days > 60 else 0)
    # 有调价但没给降幅时，补一个合理的累计降幅（3%~9%），否则历史里没有真实降价
    if n_changes > 0 and total_drop_pct <= 0:
        total_drop_pct = random.uniform(0.03, 0.09)
    first_price = _round_price(total / (1 - total_drop_pct)) if total_drop_pct > 0 else total

    # 绑定小区 id（关键：可比统计按 community_id 分组）
    cid_row = query_one("SELECT id FROM community WHERE name=? AND district=?", (name, district))
    community_id = int(cid_row["id"]) if cid_row else 0

    fingerprint = _md5(f"{source}|{name}|{layout_name}|{int(area)}|{int(total)}")
    execute(
        """
        INSERT OR IGNORE INTO listing
          (fingerprint,source,source_url,community_id,community_name,district,title,layout,rooms,halls,
           area,total_price,unit_price,floor_text,floor_no,floor_total,orientation,decoration,
           build_year,age,has_elevator,first_price,first_seen,last_seen,list_days,price_changes,
           consecutive_drops,defect_tags,rent_month,status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            fingerprint, source, "", community_id, name, district,
            f"{name} {layout_name} {area:g}㎡ {decor}", layout_name, rooms, halls,
            area, total, unit, floor_text, floor_no, floor_total,
            random.choice(ORIENTATIONS), decor, build_year, age, has_elev,
            first_price, (TODAY - timedelta(days=days)).isoformat(), TODAY.isoformat(),
            days, n_changes, 0, json.dumps(defects, ensure_ascii=False),
            rent_month if rent_month is not None else round(area * random.uniform(18, 26)),
            "active",
        ),
    )
    row = query_one("SELECT id FROM listing WHERE fingerprint=?", (fingerprint,))
    if not row:
        return 0
    listing_id = int(row["id"])

    final_changes, consec, last_drop_ago, fp = _make_history(listing_id, first_price, total, days, n_changes)
    execute(
        "UPDATE listing SET price_changes=?, consecutive_drops=?, last_drop_days_ago=?, first_price=? WHERE id=?",
        (final_changes, consec, last_drop_ago, fp, listing_id),
    )
    return listing_id


def insert_auction(
    *, title: str, community_name: str, district: str, area: float, layout: str,
    appraisal: float, start: float, stage: str, risk_tags: list[str],
    comparable_unit: float, tax: float, arrears: float, repair: float,
    auction_date: str, source: str = "ali", rooms: int = 3, elevator: int = 1,
) -> int:
    landing = start + tax + arrears + repair
    fingerprint = _md5(f"{source}|{title}|{int(start)}")
    start_discount = (appraisal - start) / appraisal if appraisal > 0 else 0
    landing_unit = landing / area if area > 0 else 0
    net_discount = (comparable_unit - landing_unit) / comparable_unit if comparable_unit > 0 else 0
    fatal = {"产权瑕疵", "占用"}
    risk_level = "high" if fatal & set(risk_tags) else ("mid" if risk_tags else "low")

    execute(
        """
        INSERT OR IGNORE INTO auction
          (fingerprint,source,source_url,title,community_name,district,area,layout,appraisal_price,
           start_price,stage,auction_date,tax_est,arrears_est,repair_reserve,landing_price,
           comparable_unit_price,start_discount,net_discount,risk_tags,risk_level,status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            fingerprint, source, "", title, community_name, district, area, layout, appraisal,
            start, stage, auction_date, tax, arrears, repair, landing,
            comparable_unit, round(start_discount, 4), round(net_discount, 4),
            json.dumps(risk_tags, ensure_ascii=False), risk_level, "active",
        ),
    )
    row = query_one("SELECT id FROM auction WHERE fingerprint=?", (fingerprint,))
    return int(row["id"]) if row else 0


def seed_new_homes() -> int:
    """灌入在售新盘。价格经过设计，让「真倒挂 / 假倒挂 / 有风险倒挂」三种情况都能看到。"""
    created = 0
    for (name, district, unit, amin, amax, layout_text, delivery, decor,
         fee, plot, green, units, sale_status, risks, note) in NEW_HOMES:
        fingerprint = _md5(f"newhome|{name}|{district}")
        # 坐标沿用板块内已有小区的中心位置（演示用，真实项目应写入售楼处坐标）
        anchor = next((c for c in COMMUNITIES if c[1] == district), None)
        lat, lng = (anchor[2], anchor[3]) if anchor else (None, None)
        if lat:
            lat += random.uniform(-0.004, 0.004)
            lng += random.uniform(-0.004, 0.004)
        execute(
            "INSERT OR IGNORE INTO new_home (fingerprint,name,developer,district,address,lat,lng,"
            "avg_unit_price,area_min,area_max,layout_text,delivery_date,decoration,property_fee,"
            "plot_ratio,green_ratio,total_units,sale_status,risk_tags,source,note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fingerprint, name, name.split("·")[0] + "置业", district,
                f"日照市{district}", lat, lng, unit, amin, amax, layout_text, delivery, decor,
                fee, plot, green, units, sale_status,
                json.dumps(risks, ensure_ascii=False), "demo", note,
            ),
        )
        created += 1
    return created


# =============================================================================
# 主入口
# =============================================================================
def seed_all(clear: bool = False) -> dict:
    random.seed(RNG_SEED)
    if clear:
        with get_conn() as conn:
            for table in ("price_history", "anomaly_event", "leak_score", "watch_item",
                          "alert_log", "listing", "auction", "new_home", "community",
                          "crawl_log", "weekly_report"):
                conn.execute(f"DELETE FROM {table}")

    ids = seed_communities()
    community_map = {c[0]: c for c in COMMUNITIES}

    def comm(name: str) -> tuple:
        return community_map[name]

    def find_id(name: str) -> int:
        return ids[name]

    created = 0
    new_home_count = seed_new_homes()

    # ---------------- 普通挂牌（构成价格中枢） ----------------
    for c in COMMUNITIES:
        name, district = c[0], c[1]
        ext = c[10]
        layout_pool = LAYOUTS[:3] if ext else LAYOUTS[:4]
        per_layout = 2 if ext else 3
        for layout in layout_pool:
            for i in range(per_layout):
                # 3室2厅1卫 / 3室2厅2卫 是主力户型，样本更厚
                if layout[1] == 3 and i == 0:
                    insert_listing(c, layout, list_days=random.randint(40, 150))
                else:
                    insert_listing(c, layout, list_days=random.randint(10, 190))
                created += 1

    # ---------------- 重点面积段（120-150㎡）补充样本 ----------------
    # 改善型房源在小城市占比低，若不单独补，专题页会陷入「样本不足算不出中位数」。
    # 0.95 的折价系数还原真实市场里的「大面积折价」：同一小区 140㎡ 的单价
    # 通常比 100㎡ 低 4%~6%，所以「大户型单价便宜」本身不是漏。
    for c in COMMUNITIES:
        if c[10]:          # 扩展区不补，避免拉低流动性判断
            continue
        for layout in TARGET_LAYOUTS:
            for _ in range(2):
                insert_listing(c, layout, list_days=random.randint(30, 220), size_factor=0.95)
                created += 1

    heroes: list[dict] = []

    # ========== 案例 1：深跌 + 连降（应命中 🔴 重点留意）==========
    # 安泰未来城 3室2厅1卫 106㎡：中枢约 11800，挂到 9200（-22%），只带「装修旧」硬伤
    hid = insert_listing(
        comm("安泰·未来城"), LAYOUTS[2],
        unit_override=9200, area_override=106.0, defects=["装修旧"],
        list_days=214, changes=3, total_drop_pct=0.19, rent_month=2300,
        floor_text="11/18层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "深跌+连降+久挂，硬伤仅装修旧"})

    # ========== 案例 2：错杀候选（偏低但硬伤可控）==========
    hid = insert_listing(
        comm("城建·时代名苑"), LAYOUTS[3],
        unit_override=8600, area_override=112.0, defects=["装修旧", "北向"],
        list_days=126, changes=1, total_drop_pct=0.07, floor_text="4/11层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "轻硬伤、单价低于小区中枢约 8%"})

    # ========== 案例 3：久挂让利（>180天且近30天降价）==========
    hid = insert_listing(
        comm("石臼老街花园"), LAYOUTS[0],
        unit_override=6900, area_override=79.0, defects=["无电梯"],
        list_days=402, changes=4, total_drop_pct=0.14, floor_text="5/6层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "挂 402 天、降 4 次，老城无电梯"})

    # ========== 案例 4：坑示例（极端低价 + 产权/临街硬伤）→ 应压分、只记录 ==========
    hid = insert_listing(
        comm("金域华府"), LAYOUTS[2],
        unit_override=5600, area_override=104.0,
        defects=["临街", "暗卫", "房龄>25"], list_days=88, changes=2, total_drop_pct=0.28,
        floor_text="2/18层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "低于中枢 40%+，大概率有坑（压分演示）"})

    # ========== 案例 5：板块背离（开发区个盘明显低于板块中枢）==========
    hid = insert_listing(
        comm("奎山花园"), LAYOUTS[1],
        unit_override=5800, area_override=89.0, defects=["装修旧", "无电梯"],
        list_days=150, changes=1, total_drop_pct=0.09, floor_text="3/6层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "开发区板块横盘，个盘低于板块中枢"})

    # ========== 案例 6：新房倒挂（新盘特价低于周边二手 5%+）==========
    hid = insert_listing(
        comm("香河佳苑"), LAYOUTS[2],
        unit_override=5550, area_override=108.0, defects=[], list_days=26, changes=0,
        source="newhome", floor_text="15/26层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "新盘特价/抵账房，单价低于周边二手"})

    # ========== 案例 7：扩展区低价（流动性惩罚演示）==========
    hid = insert_listing(
        comm("沭河人家"), LAYOUTS[2],
        unit_override=4300, area_override=110.0, defects=["装修旧"],
        list_days=240, changes=2, total_drop_pct=0.16, floor_text="6/11层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "莒县扩展区低价，流动性差需明确标注"})

    # ========== 案例 8：正常房源（作为对照，不应进榜）==========
    hid = insert_listing(
        comm("华润中心凯旋门"), LAYOUTS[3],
        unit_override=12600, area_override=117.0, defects=[], list_days=35, changes=0,
        floor_text="9/26层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "正常价格，不应进入重点留意"})

    # ========== 案例 9：重点面积段（145㎡）真让利 —— 改善型的「真漏」长什么样 ==========
    hid = insert_listing(
        comm("华润中心凯旋门"), LAYOUTS[4],
        unit_override=9900, area_override=145.0, defects=["装修旧"],
        list_days=196, changes=3, total_drop_pct=0.15, rent_month=2900,
        floor_text="12/26层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "145㎡ 改善四居，单价低于小区中枢约 16%，连降 3 次"})

    # ========== 案例 10：大面积陷阱 —— 便宜有便宜的原因，必须压到白级 ==========
    hid = insert_listing(
        comm("奎山花园"), LAYOUTS[4],
        unit_override=4200, area_override=143.0,
        defects=["顶楼", "临街", "房龄>25", "无电梯"],
        list_days=310, changes=3, total_drop_pct=0.22, floor_text="6/6层",
    )
    heroes.append({"id": hid, "type": "listing", "why": "143㎡ 极低价，但顶楼+临街+无电梯，典型「大面积坑」"})

    # ---------------- 法拍 ----------------
    insert_auction(
        title="日照市山海天旅游度假区教授花园 3室2厅2卫 118㎡ 司法拍卖",
        community_name="教授花园", district="山海天", area=118.0, layout="3室2厅2卫",
        appraisal=1_050_000, start=680_000, stage="二拍",
        risk_tags=["税费自担"], comparable_unit=9200,
        tax=30_000, arrears=12_000, repair=45_000,
        auction_date=(TODAY + timedelta(days=12)).isoformat(),
    )
    insert_auction(
        title="日照市东港区金域华府 3室2厅1卫 105.6㎡ 司法拍卖",
        community_name="金域华府", district="东港区", area=105.6, layout="3室2厅1卫",
        appraisal=1_010_000, start=760_000, stage="一拍",
        risk_tags=["占用", "税费自担"], comparable_unit=9800,
        tax=30_000, arrears=22_000, repair=60_000,
        auction_date=(TODAY + timedelta(days=20)).isoformat(),
    )
    insert_auction(
        title="日照市老城区石臼老街花园 2室1厅1卫 79㎡ 变卖",
        community_name="石臼老街花园", district="老城", area=79.0, layout="2室1厅1卫",
        appraisal=560_000, start=350_000, stage="变卖",
        risk_tags=["占用", "腾退难", "欠费"], comparable_unit=7600,
        tax=18_000, arrears=26_000, repair=40_000,
        auction_date=(TODAY + timedelta(days=6)).isoformat(),
    )
    insert_auction(
        title="日照市新市区安泰·未来城 3室2厅1卫 106㎡ 司法拍卖",
        community_name="安泰·未来城", district="新市区", area=106.0, layout="3室2厅1卫",
        appraisal=1_210_000, start=830_000, stage="二拍",
        risk_tags=[], comparable_unit=11800,
        tax=36_000, arrears=6_000, repair=30_000,
        auction_date=(TODAY + timedelta(days=25)).isoformat(),
    )

    # 重点面积段（120-150㎡）的法拍：改善型法拍标的往往被低估，折扣更容易出现
    insert_auction(
        title="日照市新市区华润中心凯旋门 4室2厅2卫 145㎡ 司法拍卖",
        community_name="华润中心凯旋门", district="新市区", area=145.0, layout="4室2厅2卫",
        appraisal=1_680_000, start=1_120_000, stage="二拍",
        risk_tags=["税费自担"], comparable_unit=12300,
        tax=50_000, arrears=8_000, repair=40_000,
        auction_date=(TODAY + timedelta(days=18)).isoformat(), rooms=4,
    )

    # ---------------- 观察清单初始项（覆盖不同跟进阶段） ----------------
    watch_specs = [
        (heroes[0]["id"], "已电话中介，说业主置换急售，周末实地", "contacted"),
        (heroes[1]["id"], "装修旧但户型正，重点核实地板与水管", "watching"),
        (heroes[2]["id"], "老城无电梯，老人自住不合适，暂不推进", "rejected"),
    ]
    for tid, note, status in watch_specs:
        row = query_one("SELECT total_price, community_name, district FROM listing WHERE id=?", (tid,))
        if not row:
            continue
        execute(
            "INSERT OR IGNORE INTO watch_item (target_type,target_id,community_name,district,note,"
            "follow_status,base_price,last_price) VALUES ('listing',?,?,?,?,?,?,?)",
            (tid, row["community_name"], row["district"], note, status, row["total_price"], row["total_price"]),
        )
    first_auction = query_one("SELECT id, community_name, district, landing_price FROM auction ORDER BY id LIMIT 1")
    if first_auction:
        execute(
            "INSERT OR IGNORE INTO watch_item (target_type,target_id,community_name,district,note,"
            "follow_status,base_price,last_price) VALUES ('auction',?,?,?,?,?,?,?)",
            (first_auction["id"], first_auction["community_name"], first_auction["district"],
             "二拍，需先查是否有人居住", "watching",
             first_auction["landing_price"], first_auction["landing_price"]),
        )

    return {
        "communities": len(COMMUNITIES),
        "listings": created + len(heroes),
        "heroes": heroes,
        "auctions": 5,
        "new_homes": new_home_count,
    }


if __name__ == "__main__":  # pragma: no cover
    from ..db import init_db

    init_db(reset=True)
    info = seed_all(clear=True)
    from .pipeline import run_daily_pipeline

    summary = run_daily_pipeline(push=False)
    print("已生成演示数据:", info["listings"], "条挂牌 /", info["auctions"], "条法拍")
    print("流水线:", {k: v for k, v in summary.items() if k != "errors"})
