"""捡漏评分模型（纯函数，无 IO，可单测）。

leak_score =
    价格偏离 30%   （低于可比中枢越多越高，设上限防垃圾房）
  + 调价动机 15%   （急售/置换/空置/连降，而不是硬伤甩卖）
  + 硬伤可控 20%   （无产权/占用/严重户型问题则大幅扣分）
  + 流动性   15%   （板块成交活跃、5 年后有人接）
  + 自住适配 20%   （通勤/学区/面积/电梯/房龄按用户偏好）

设计原则：先把低价当嫌疑。所有输入都是普通 dict，不依赖数据库，方便单测与回归。
"""
from __future__ import annotations

from typing import Any

# ---- 权重（透明可解释，改这里就能调模型）-------------------------------------
WEIGHTS: dict[str, float] = {
    "price": 0.30,      # 价格偏离
    "motive": 0.15,     # 调价动机
    "defect": 0.20,     # 硬伤可控
    "liquidity": 0.15,  # 流动性
    "fit": 0.20,        # 自住适配
}

# ---- 硬伤扣分表：分值 = 该硬伤让「硬伤可控」维度下降的点数 --------------------
DEFECT_PENALTY: dict[str, float] = {
    # 可修复 / 可接受（错杀候选常见）
    "装修旧": 6,
    "空置": 5,
    "北向": 6,
    "采光差": 12,
    # 半可控
    "顶楼": 16,
    "腰线": 10,
    "临街": 15,
    "暗卫": 8,
    "户型异形": 16,
    "无电梯": 22,
    "房龄>25": 20,
    "学区占用": 24,
    # 不可控 / 高危（法拍常见）
    "占用": 45,
    "租赁": 30,
    "腾退难": 40,
    "产权瑕疵": 50,
    "欠费": 12,
    "限购": 20,
    "税费自担": 10,
    "凶宅嫌疑": 60,
}

# 属于「硬伤可控/可忍」的标签集合——用于错杀信号判定
CONTROLLABLE_DEFECTS = {"装修旧", "空置", "北向", "采光差", "房龄>25", "腰线", "暗卫"}

# 板块流动性基准（成交活跃度先验，后续可用真实成交数据替换）
DISTRICT_LIQUIDITY: dict[str, float] = {
    "新市区": 88,
    "东港区": 80,
    "石臼": 68,
    "老城": 62,
    "山海天": 58,
    "高新区": 52,
    "开发区": 48,
    "莒县": 34,
    "五莲": 30,
    "岚山": 28,
}
EXTENDED_DISTRICTS = {"莒县", "五莲", "岚山"}


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _f(value: Any, default: float = 0.0) -> float:
    """安全转 float，None/字符串都吃得下。"""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# =============================================================================
# 维度 1：价格偏离（30%）
# =============================================================================
def score_price(deviation_pct: float | None, sample_n: int, min_sample: int = 3) -> tuple[float, list[str]]:
    """deviation_pct = (可比中位单价 - 当前单价) / 可比中位单价，正数表示更便宜。

    返回 (分数, 说明列表)。样本不足时向 50 分收缩（不敢给高分也不敢给低分）。
    """
    notes: list[str] = []
    if deviation_pct is None:
        return 45.0, ["缺少同小区同户型可比样本，价格维度按中性处理"]

    dev = deviation_pct
    if dev <= 0:
        # 高于或等于中枢：越贵越低分（30 分是「价格中性」基线，不构成加分）
        score = clamp(30 + dev * 150, 0, 30)
        notes.append(f"单价高于可比中枢 {abs(dev) * 100:.1f}%，不算便宜")
        return score, notes

    # 0 ~ 20% 偏离线性给分（30 → 100）；20% 以上封顶
    score = clamp(30 + dev / 0.20 * 70, 0, 100)
    notes.append(f"单价低于可比中枢 {dev * 100:.1f}%")

    # 防垃圾房：极端偏离（>35%）大概率是硬伤/产权/凶宅/假房源，直接压到 70 分以下
    if dev > 0.35:
        score = min(score, 70.0)
        notes.append("偏离超过 35%，极端低价优先按『有坑』处理，已压分")

    # 样本不足：向 50 收缩
    if sample_n < min_sample:
        score = 50 + (score - 50) * 0.6
        notes.append(f"可比样本仅 {sample_n} 套（<{min_sample}），价格分向中性收缩")
    return round(score, 1), notes


# =============================================================================
# 维度 2：调价动机（15%）——区分「急售让利」和「硬伤甩卖」
# =============================================================================
def score_motive(listing: dict, defect_score: float) -> tuple[float, list[str]]:
    """调价动机 = 「卖方有多急」的证据强度。

    刻意压低基线（20 分）与各项上限：单纯"降过一次价"不足以证明急售，
    只有「连降 + 长挂 + 累计降幅大」同时成立才接近满分。
    """
    notes: list[str] = []
    changes = int(_f(listing.get("price_changes")))
    consec = int(_f(listing.get("consecutive_drops")))
    list_days = int(_f(listing.get("list_days")))
    first_price = _f(listing.get("first_price") or listing.get("total_price"))
    price = _f(listing.get("total_price"))
    last_drop_days_ago = listing.get("last_drop_days_ago")

    total_drop_pct = (first_price - price) / first_price if first_price > 0 else 0.0
    total_drop_pct = max(0.0, total_drop_pct)

    score = 20.0
    if changes >= 1:
        score += 6
        notes.append(f"累计调价 {changes} 次")
    if consec >= 2:
        score += 12
        notes.append(f"连续降价 {consec} 次，卖方心态在松动")
    elif consec == 1:
        score += 4
    if total_drop_pct > 0:
        score += min(18.0, total_drop_pct / 0.15 * 18.0)
        notes.append(f"较首次挂牌累计降 {total_drop_pct * 100:.1f}%")
    if list_days > 180:
        score += 10
        notes.append(f"已挂牌 {list_days} 天（超 180 天）")
    elif list_days > 90:
        score += 6
        notes.append(f"已挂牌 {list_days} 天")
    if last_drop_days_ago is not None and int(_f(last_drop_days_ago, 999)) <= 14:
        score += 6
        notes.append("近两周内刚降过价")

    # 关键分辨：硬伤房源降价 = 甩卖，不是让利
    if defect_score < 55 and (changes > 0 or total_drop_pct > 0.05):
        score *= 0.55
        notes.append("伴随明显硬伤，更可能是『甩卖』而非急售让利，动机分打折")

    return round(clamp(score), 1), notes


# =============================================================================
# 维度 3：硬伤可控（20%）
# =============================================================================
# 基线扣减：即便没标注任何硬伤，也只能算「初步可控」（未实地核验），
# 因此给 65 分而不是满分——保证「正常房源」总分落在 50 分附近的白色区间。
DEFECT_BASE = 65.0


def score_defect(defect_tags: list[str]) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = DEFECT_BASE
    if not defect_tags:
        return DEFECT_BASE, ["未识别到明确硬伤（仍需实地核验，不能当作无硬伤）"]
    for tag in defect_tags:
        penalty = DEFECT_PENALTY.get(tag, 6)
        score -= penalty
        notes.append(f"硬伤「{tag}」扣 {penalty:g} 分")
    return round(clamp(score), 1), notes


# =============================================================================
# 维度 4：流动性（15%）
# =============================================================================
def _liquidity_curve(raw: float) -> float:
    """把板块/个盘流动性原值（0-100）映射成评分：60 分对应「一般」，越高越好转手。"""
    return clamp(50 + (raw - 60) * 0.9, 5, 100)


def score_liquidity(listing: dict, community: dict | None = None) -> tuple[float, list[str]]:
    notes: list[str] = []
    district = listing.get("district") or (community or {}).get("district") or ""
    base = DISTRICT_LIQUIDITY.get(district, 50.0)

    comm_liq = _f((community or {}).get("liquidity_score"), -1)
    if comm_liq >= 0:
        base = base * 0.55 + comm_liq * 0.45
        notes.append(f"板块基准 {DISTRICT_LIQUIDITY.get(district, 50):g}，个盘流动性 {comm_liq:g}")

    if district in EXTENDED_DISTRICTS:
        base -= 8
        notes.append(f"{district}属扩展区，二手流动性明显弱于市区，接手人少")

    deal = _f((community or {}).get("deal_active_90d"), 0)
    if deal >= 15:
        base += 6
        notes.append(f"近90天成交 {deal:g} 套，活跃")
    elif deal <= 2:
        base -= 6
        notes.append(f"近90天成交仅 {deal:g} 套，转手偏慢")

    # 总价越高、面积越大，接盘面越窄
    total = _f(listing.get("total_price"))
    if total > 2_500_000:
        base -= 6
        notes.append("总价偏高，目标客群窄")
    elif total < 900_000:
        base += 2

    return round(_liquidity_curve(base), 1), notes


# =============================================================================
# 维度 5：自住适配（20%）
# =============================================================================
DEFAULT_PREF: dict[str, Any] = {
    "district_pref": ["新市区", "东港区", "石臼"],
    "area_min": 80,
    "area_max": 130,
    "rooms_prefer": 3,
    "need_elevator": 1,
    "avoid_top_floor": 1,
    "max_age": 25,
    "care_school": 1,
}


def score_fit(listing: dict, pref: dict | None = None) -> tuple[float, list[str]]:
    """自住适配：50 分是「普通能满足」，全部命中偏好才接近 85+。

    这一维度是"过滤器"而不是"加分器"——再合适也不该单独把一套房推上红级。
    """
    p = {**DEFAULT_PREF, **(pref or {})}
    notes: list[str] = []
    score = 50.0

    district = listing.get("district") or ""
    district_pref = p.get("district_pref") or []
    if district in district_pref:
        score += 8
        notes.append(f"{district}在偏好板块内")
    elif district in EXTENDED_DISTRICTS:
        score -= 20
        notes.append(f"{district}为扩展区，自住通勤成本高")

    area = _f(listing.get("area"))
    area_min, area_max = _f(p.get("area_min"), 80), _f(p.get("area_max"), 130)
    if area_min <= area <= area_max:
        score += 8
        notes.append(f"面积 {area:g}㎡ 落在偏好区间 {area_min:g}-{area_max:g}㎡")
    elif area < area_min:
        score -= 2 if area >= area_min - 10 else 12
        notes.append(f"面积 {area:g}㎡ 小于偏好下限")
    else:
        score -= 6 if area <= area_max + 20 else 16
        notes.append(f"面积 {area:g}㎡ 超出偏好上限")

    rooms = int(_f(listing.get("rooms")))
    rooms_prefer = int(_f(p.get("rooms_prefer"), 3))
    if rooms == rooms_prefer:
        score += 8
        notes.append(f"户型 {rooms} 室，符合偏好")
    elif rooms == rooms_prefer - 1:
        score -= 6
        notes.append(f"少一个房间（{rooms} 室），功能可能不够")
    elif rooms >= rooms_prefer + 1:
        score += 3

    if int(_f(p.get("need_elevator"), 1)) == 1:
        if int(_f(listing.get("has_elevator"), 1)) == 0:
            score -= 22
            notes.append("无电梯，与『需要电梯』偏好冲突")
        else:
            score += 4

    tags = listing.get("defect_tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.strip("[]").replace('"', "").split(",") if t]
    if int(_f(p.get("avoid_top_floor"), 1)) == 1 and "顶楼" in tags:
        score -= 14
        notes.append("顶楼，回避项")
    if "临街" in tags:
        score -= 8
        notes.append("临街，噪音风险")

    age = _f(listing.get("age"))
    max_age = _f(p.get("max_age"), 25)
    if age > max_age:
        score -= 12
        notes.append(f"房龄 {age:g} 年超过偏好上限 {max_age:g} 年")
    elif 0 < age <= 10:
        score += 4
        notes.append(f"房龄 {age:g} 年，较新")

    if int(_f(p.get("care_school"), 1)) == 1 and (listing.get("school_district") or ""):
        score += 3
        notes.append(f"对口学校：{listing['school_district']}")

    return round(clamp(score), 1), notes


# =============================================================================
# 置信度
# =============================================================================
def score_confidence(sample_n: int, has_rent: bool, data_complete: bool, min_sample: int = 3) -> tuple[str, list[str]]:
    """高/中/低。样本少自动降级——这是防「拿一个假样本当锚」的关键。"""
    notes: list[str] = []
    if sample_n >= 5 and data_complete:
        conf = "high"
    elif sample_n >= min_sample:
        conf = "mid"
    else:
        conf = "low"
        notes.append(f"同小区同户型样本仅 {sample_n} 套，置信度降为低")
    if not has_rent:
        notes.append("缺少租金锚点，回报率参考缺失")
        if conf == "high":
            conf = "mid"
    if not data_complete:
        notes.append("字段不完整（楼层/朝向/房龄有缺失）")
        if conf == "high":
            conf = "mid"
    return conf, notes


# 不可逆硬伤：命中即高危
FATAL_DEFECTS = {"产权瑕疵", "占用", "腾退难", "凶宅嫌疑", "租赁", "限购"}
# 难改但可忍的硬伤：命中 1 个算中风险，命中 2 个以上算高风险
SERIOUS_DEFECTS = {"无电梯", "学区占用", "房龄>25", "临街", "户型异形", "顶楼", "欠费"}


def risk_level_from_defect(defect_score: float, defect_tags: list[str]) -> str:
    """由硬伤标签推导风险等级。

    注意：这里刻意不看 defect_score 的绝对值，只看「硬伤性质」——
    装修旧可以把分数拉低，但它不该让一套房子被判成高风险。
    """
    tags = set(defect_tags or [])
    if tags & FATAL_DEFECTS:
        return "high"
    serious_hits = tags & SERIOUS_DEFECTS
    if len(serious_hits) >= 2:
        return "high"
    if len(serious_hits) == 1:
        return "mid"
    return "low"


# =============================================================================
# 分级
# =============================================================================
def grade_of(score: float, confidence: str, risk_level: str) -> str:
    """🔴 重点留意 / 🟡 建议观察 / ⚪ 仅记录。

    规则（顺序即优先级）：
      1. 风险高（产权/占用/多重不可逆硬伤）→ 一律 ⚪ 仅记录，绝不主动推送
      2. ≥75 分且风险低且置信度不是低 → 🔴
      3. ≥60 分 → 🟡（样本不足时红级也降到这里）
      4. 其余 → ⚪
    """
    if risk_level == "high":
        return "white"
    if score >= 75 and risk_level == "low" and confidence != "low":
        grade = "red"
    elif score >= 75:
        grade = "yellow"          # 分数够但风险中 → 只进观察清单
    elif score >= 60:
        grade = "yellow"
    else:
        grade = "white"
    if confidence == "low" and grade == "red":
        grade = "yellow"          # 样本不足，不敢报红
    return grade


# =============================================================================
# 解释生成：为什么便宜 / 为什么可能是真漏 / 为什么可能是坑
# =============================================================================
def build_explanations(
    listing: dict,
    deviation_pct: float | None,
    sample_n: int,
    defect_tags: list[str],
    defect_score: float,
    motive_score: float,
    fit_score: float,
    risk_level: str,
    is_auction: bool = False,
) -> dict[str, str]:
    price = _f(listing.get("total_price"))
    area = _f(listing.get("area"))
    unit = _f(listing.get("unit_price"))
    list_days = int(_f(listing.get("list_days")))
    consec = int(_f(listing.get("consecutive_drops")))
    changes = int(_f(listing.get("price_changes")))
    district = listing.get("district") or ""
    age = _f(listing.get("age"))

    # ---- 为什么便宜（先当嫌疑犯）----
    cheap: list[str] = []
    if is_auction:
        cheap.append("司法拍卖本身自带折价：处置周期短、付款周期硬、买家池小")
    if deviation_pct and deviation_pct > 0.02:
        line = f"挂牌单价 {unit:,.0f} 元/㎡，比同小区同户型近3月中位低 {deviation_pct * 100:.1f}%"
        if deviation_pct <= 0.10:
            line += "（幅度接近统计噪声，需确认是否同楼层/同朝向可比）"
        cheap.append(line)
    hard = [t for t in defect_tags if t not in CONTROLLABLE_DEFECTS]
    if hard:
        cheap.append("存在明确硬伤：" + "、".join(hard) + "，这是最直接的降价理由")
    if consec >= 2 or changes >= 2:
        cheap.append(f"已{changes}次调价、其中连续降价{consec}次，说明卖方时间压力大（也说明市场不认这个价）")
    if list_days > 180:
        cheap.append(f"挂盘 {list_days} 天未出手，本身就是一个负向信号")
    if district in EXTENDED_DISTRICTS:
        cheap.append(f"{district}属扩展区，二手成交本就清淡，价格锚不稳")
    if not cheap:
        cheap.append("价格与可比中枢基本持平：便宜的理由不在价格上，更可能是楼层/朝向/装修差异，或挂牌信息本身不准")

    # ---- 为什么可能是真漏 ----
    good: list[str] = []
    if defect_tags and not hard:
        good.append("硬伤可控：主要问题是装修/朝向/楼层这类可改善项，不是产权或占用")
    elif not defect_tags and is_auction:
        good.append("公告未标注占用/产权瑕疵（不等于没有，必须去现场与法院核实）")
    elif not defect_tags:
        good.append("未识别到产权/占用类硬伤，价格折让更可能来自急售而非房屋缺陷（仍需实地核验）")
    if motive_score >= 60:
        if is_auction:
            stage_txt = listing.get("stage") or "二拍"
            good.append(f"已进入{stage_txt}，法院定价通常低于上一轮流拍价，竞争者更少")
        else:
            good.append("降价节奏符合『急着卖』特征（连降+时长），而不是『有硬伤才降』")
    if fit_score >= 70:
        good.append("面积/户型/电梯与自住偏好匹配度较高，自住可直接用，不需要转手博弈")
    if sample_n >= 5:
        if is_auction:
            good.append(f"同小区有 {sample_n} 套二手在挂可比，净折扣口径可追溯")
        else:
            good.append(f"同小区同户型有 {sample_n} 套可比样本，价格中枢比较扎实")
    if _f(listing.get("rent_month")) > 0 and price > 0:
        annual = _f(listing.get("rent_month")) * 12
        good.append(f"租金回报约 {annual / price * 100:.2f}%/年，可作为持有底线的参考")
    if is_auction and listing.get("stage") in ("二拍", "变卖"):
        good.append(f"已进入{listing['stage']}，价格通常比一拍更低，竞争者更少")
    if not good:
        good.append("暂时找不到支持『真漏』的硬证据，只作观察")

    # ---- 为什么可能是坑 ----
    trap: list[str] = []
    if risk_level == "high":
        trap.append("风险等级高：产权/占用类问题一旦成立，钱和房子可能都拿不回来")
    if hard:
        trap.append("硬伤不可逆：" + "、".join(hard))
    if sample_n < 3:
        trap.append(f"可比样本仅 {sample_n} 套，所谓『低于中枢』可能只是该户型本身差")
    if _f(listing.get("unit_price")) > 0 and deviation_pct and deviation_pct > 0.35:
        trap.append("偏离超过 35%，异常幅度过大：优先怀疑假房源、凶宅、查封或数据错误")
    if age > 25:
        trap.append(f"房龄 {age:g} 年，贷款年限受限、维修成本上升")
    if district in EXTENDED_DISTRICTS:
        trap.append(f"{district}流动性弱，买得进不一定卖得出，自住需能接受长期持有")
    if is_auction:
        trap.append("法拍到手价 ≠ 起拍价：税费、欠费、腾退时间都可能吃掉折扣")
    if not trap:
        trap.append("主要风险是常规项：实地渗漏/采光/噪音、学区占用、真实成交价待核")

    return {
        "why_cheap": "；".join(cheap),
        "why_good": "；".join(good),
        "why_trap": "；".join(trap),
    }


# =============================================================================
# 主入口：给一条二手房挂牌打分
# =============================================================================
def score_listing(
    listing: dict,
    comparables: dict | None = None,
    community: dict | None = None,
    pref: dict | None = None,
    min_sample: int = 3,
) -> dict:
    """返回完整评分结果（可直接写 leak_score 表 / 直接返回给前端）。

    comparables: {"median_unit": float, "p25": float, "p75": float, "n": int}
    """
    comp = comparables or {}
    current_unit = _f(listing.get("unit_price"))
    if current_unit <= 0 and _f(listing.get("area")) > 0:
        current_unit = _f(listing.get("total_price")) / _f(listing.get("area"))

    median_unit = _f(comp.get("median_unit"))
    sample_n = int(_f(comp.get("n")))
    deviation = (median_unit - current_unit) / median_unit if median_unit > 0 and current_unit > 0 else None

    tags = listing.get("defect_tags") or []
    if isinstance(tags, str):
        import json
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []

    s_price, n_price = score_price(deviation, sample_n, min_sample)
    s_defect, n_defect = score_defect(tags)
    s_motive, n_motive = score_motive(listing, s_defect)
    s_liquidity, n_liq = score_liquidity(listing, community)
    s_fit, n_fit = score_fit({**listing, "school_district": (community or {}).get("school_district")}, pref)

    total = (
        s_price * WEIGHTS["price"]
        + s_motive * WEIGHTS["motive"]
        + s_defect * WEIGHTS["defect"]
        + s_liquidity * WEIGHTS["liquidity"]
        + s_fit * WEIGHTS["fit"]
    )
    total = round(clamp(total), 1)

    data_complete = all(
        listing.get(k) not in (None, "", 0) for k in ("floor_text", "orientation", "build_year")
    )
    confidence, n_conf = score_confidence(sample_n, bool(_f(listing.get("rent_month"))), data_complete, min_sample)
    risk = risk_level_from_defect(s_defect, tags)
    grade = grade_of(total, confidence, risk)

    explain = build_explanations(listing, deviation, sample_n, tags, s_defect, s_motive, s_fit, risk)

    return {
        "target_type": "listing",
        "target_id": listing.get("id"),
        "score": total,
        "s_price": s_price,
        "s_motive": s_motive,
        "s_defect": s_defect,
        "s_liquidity": s_liquidity,
        "s_fit": s_fit,
        "confidence": confidence,
        "grade": grade,
        "risk_level": risk,
        "sample_n": sample_n,
        "deviation_pct": round(deviation, 4) if deviation is not None else None,
        "median_unit": median_unit or None,
        "why_cheap": explain["why_cheap"],
        "why_good": explain["why_good"],
        "why_trap": explain["why_trap"],
        "breakdown": {
            "价格偏离": {"score": s_price, "weight": WEIGHTS["price"], "notes": n_price},
            "调价动机": {"score": s_motive, "weight": WEIGHTS["motive"], "notes": n_motive},
            "硬伤可控": {"score": s_defect, "weight": WEIGHTS["defect"], "notes": n_defect},
            "流动性": {"score": s_liquidity, "weight": WEIGHTS["liquidity"], "notes": n_liq},
            "自住适配": {"score": s_fit, "weight": WEIGHTS["fit"], "notes": n_fit + n_conf},
        },
        "weights": WEIGHTS,
    }


# =============================================================================
# 法拍评分：折扣按「到手价」计算，风险单独打穿
# =============================================================================
def score_auction(auction: dict, comparables: dict | None = None, pref: dict | None = None,
                  community: dict | None = None) -> dict:
    comp = comparables or {}
    tags = auction.get("risk_tags") or []
    if isinstance(tags, str):
        import json
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []

    start = _f(auction.get("start_price"))
    appraisal = _f(auction.get("appraisal_price"))
    area = _f(auction.get("area"))
    tax = _f(auction.get("tax_est"))
    arrears = _f(auction.get("arrears_est"))
    repair = _f(auction.get("repair_reserve"))
    landing = _f(auction.get("landing_price")) or (start + tax + arrears + repair)

    comp_unit = _f((comparables or {}).get("median_unit") or auction.get("comparable_unit_price"))
    landing_unit = landing / area if area > 0 else 0
    net_discount = (comp_unit - landing_unit) / comp_unit if comp_unit > 0 and landing_unit > 0 else 0.0

    # 价格维度：净折扣越大越高（折扣超 40% 反而压分——异常必有问题）
    s_price = clamp(net_discount / 0.25 * 100, 0, 100)
    if net_discount > 0.40:
        s_price = min(s_price, 70)

    s_defect, n_defect = score_defect(tags)
    # 法拍没有"调价动机"，用处置阶段替代：二拍/变卖 = 卖方（法院）在让利
    stage = auction.get("stage") or "一拍"
    s_motive = {"一拍": 45.0, "二拍": 72.0, "变卖": 80.0}.get(stage, 45.0)
    if set(tags) & FATAL_DEFECTS:
        s_motive *= 0.7  # 有占用/腾退/产权风险时，降价不是让利而是甩包袱

    s_liquidity, _ = score_liquidity(
        {"district": auction.get("district"), "total_price": landing, "area": area}, community
    )
    s_fit, _ = score_fit(
        {
            "district": auction.get("district"),
            "area": area,
            "rooms": auction.get("rooms") or 3,
            "has_elevator": (community or {}).get("has_elevator", 1),
            "age": (2026 - int(community["build_year"])) if (community or {}).get("build_year") else None,
            "school_district": (community or {}).get("school_district"),
            "defect_tags": tags,
        },
        pref,
    )

    total = (
        s_price * WEIGHTS["price"]
        + s_motive * WEIGHTS["motive"]
        + s_defect * WEIGHTS["defect"]
        + s_liquidity * WEIGHTS["liquidity"]
        + s_fit * WEIGHTS["fit"]
    )
    # 法拍整体小幅折让：流程与信息成本已经在「到手价」里体现，这里再打 5% 折扣
    total = round(clamp(total * 0.95), 1)

    risk = risk_level_from_defect(s_defect, tags)
    confidence = "mid" if comp_unit > 0 else "low"
    grade = grade_of(total, confidence, risk)

    listing_like = {
        "id": auction.get("id"),
        "total_price": landing,
        "area": area,
        "unit_price": landing_unit,
        "district": auction.get("district"),
        "list_days": 0,
        "age": auction.get("age"),
        "rent_month": 0,
        "stage": stage,
        "defect_tags": tags,
    }
    explain = build_explanations(
        listing_like, net_discount, 5 if comp_unit > 0 else 1, tags, s_defect, s_motive, s_fit, risk, is_auction=True
    )

    return {
        "target_type": "auction",
        "target_id": auction.get("id"),
        "score": total,
        "s_price": round(s_price, 1),
        "s_motive": round(s_motive, 1),
        "s_defect": s_defect,
        "s_liquidity": s_liquidity,
        "s_fit": s_fit,
        "confidence": confidence,
        "grade": grade,
        "risk_level": risk,
        "sample_n": 5 if comp_unit else 0,
        "deviation_pct": round(net_discount, 4) if net_discount else None,
        "median_unit": comp_unit or None,
        "landing_price": landing,
        "landing_unit": round(landing_unit, 1),
        "start_discount": round((appraisal - start) / appraisal, 4) if appraisal > 0 else None,
        "net_discount": round(net_discount, 4),
        "why_cheap": explain["why_cheap"],
        "why_good": explain["why_good"],
        "why_trap": explain["why_trap"],
        "breakdown": {
            "价格偏离": {"score": round(s_price, 1), "weight": WEIGHTS["price"],
                         "notes": [f"到手价 {landing:,.0f} 元，到手单价 {landing_unit:,.0f} 元/㎡",
                                   f"对比可比单价 {comp_unit:,.0f} 元/㎡，净折扣 {net_discount * 100:.1f}%"]},
            "调价动机": {"score": round(s_motive, 1), "weight": WEIGHTS["motive"],
                         "notes": [f"处置阶段：{stage}"]},
            "硬伤可控": {"score": s_defect, "weight": WEIGHTS["defect"], "notes": n_defect},
            "流动性": {"score": s_liquidity, "weight": WEIGHTS["liquidity"], "notes": []},
            "自住适配": {"score": s_fit, "weight": WEIGHTS["fit"], "notes": []},
        },
        "weights": WEIGHTS,
    }


def score_any(target_type: str, payload: dict, comparables: dict | None = None,
              community: dict | None = None, pref: dict | None = None,
              min_sample: int = 3) -> dict:
    """统一入口，方便 pipeline 与 API 复用。"""
    if target_type == "auction":
        return score_auction(payload, comparables, pref, community)
    return score_listing(payload, comparables, community, pref, min_sample)
