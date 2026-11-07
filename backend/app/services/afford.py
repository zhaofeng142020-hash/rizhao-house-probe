"""购房成本测算与议价锚点（纯函数，无 IO，可直接单测）。

为什么要有这个模块：
捡漏分回答的是「值不值得看」，但自住买房真正下决定的是三件更硬的事——
    我拿得出多少现金？每月还多少？这套到底能谈到多少？
这三件事完全可以用已有字段（总价、面积、挂牌天数、调价次数、同户型中位价）算出来，
不需要任何外部数据源。所以做成纯函数，前后端共用一套口径。

口径说明（山东日照，2024 后政策口径，参数可在 .env / 管理台调整）：
- 契税：首套 ≤90㎡ 1%，>90㎡ 1.5%；二套 ≤90㎡ 1%，>90㎡ 2%；三套及以上 3%
- 中介费：二手默认 2%（买卖双方合计，实操多为买方承担）
- 维修基金：按 90 元/㎡ 估（多层/高层各城市差异大，取中值）
- 毛坯新房另计装修成本（默认 1200 元/㎡），否则「新房便宜」是假便宜
- 月供：等额本息
"""
from __future__ import annotations

from typing import Any

# 维修基金估算（元/㎡）
MAINTENANCE_FUND_PER_SQM = 90.0
# 月供占家庭月收入的上限，超过就属于「压力过大」
COMFORT_DTI = 0.40


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    """等额本息月供。principal 为贷款本金（元）。"""
    n = max(1, int(years) * 12)
    principal = float(principal or 0)
    if principal <= 0:
        return 0.0
    r = float(annual_rate or 0) / 12.0
    if r <= 0:
        return round(principal / n, 2)
    factor = (1 + r) ** n
    return round(principal * r * factor / (factor - 1), 2)


def deed_tax(total_price: float, area: float, is_first_home: bool = True, homes_owned: int = 1) -> float:
    """契税。homes_owned：本次购房后家庭名下套数（1=首套，2=二套，3+=三套）。"""
    price = max(0.0, float(total_price or 0))
    small = float(area or 0) <= 90
    if homes_owned >= 3:
        rate = 0.03
    elif homes_owned == 2:
        rate = 0.01 if small else 0.02
    else:
        rate = 0.01 if small else 0.015
    if not is_first_home and homes_owned == 1:
        rate = 0.01 if small else 0.02      # 非首套按二套口径
    return round(price * rate, 2)


def buy_cost(
    total_price: float,
    area: float,
    *,
    down_ratio: float = 0.30,
    years: int = 30,
    annual_rate: float = 0.031,
    is_first_home: bool = True,
    is_new: bool = False,
    decoration: str | None = None,
    agent_fee_ratio: float = 0.02,
    decoration_cost_per_sqm: float = 1200.0,
) -> dict[str, Any]:
    """一套房买下来的完整现金账。

    返回的 cash_needed 才是真正的门槛（很多人只算首付，签合同时才发现差十几万）。
    """
    price = max(0.0, float(total_price or 0))
    area = max(0.0, float(area or 0))
    down_ratio = _clamp(float(down_ratio or 0.3), 0.05, 1.0)

    down_payment = round(price * down_ratio, 2)
    loan = round(price - down_payment, 2)
    monthly = monthly_payment(loan, annual_rate, years)
    total_interest = round(monthly * int(years) * 12 - loan, 2) if loan > 0 else 0.0

    tax = deed_tax(price, area, is_first_home)
    agent_fee = 0.0 if is_new else round(price * agent_fee_ratio, 2)
    maintenance = round(area * MAINTENANCE_FUND_PER_SQM, 2)

    # 毛坯新房要装修；二手「毛坯」同理，简装/精装只留软装预算
    decoration_cost = 0.0
    is_bare = (decoration or "") in ("毛坯", "") if is_new else (decoration == "毛坯")
    if is_bare and area > 0:
        decoration_cost = round(area * decoration_cost_per_sqm, 2)
    elif area > 0:
        decoration_cost = round(area * decoration_cost_per_sqm * 0.25, 2)   # 软装/家电

    cash_needed = round(down_payment + tax + agent_fee + maintenance + decoration_cost, 2)
    total_cost = round(price + tax + agent_fee + maintenance + decoration_cost + total_interest, 2)

    return {
        "total_price": price,
        "area": area,
        "down_ratio": down_ratio,
        "down_payment": down_payment,
        "loan": loan,
        "years": int(years),
        "annual_rate": float(annual_rate),
        "monthly": monthly,
        "total_interest": total_interest,
        "tax": tax,
        "agent_fee": agent_fee,
        "maintenance": maintenance,
        "decoration_cost": decoration_cost,
        "cash_needed": cash_needed,          # 真正要一次性拿出的现金
        "total_cost": total_cost,            # 含利息的总支出
        "monthly_income_comfort": round(monthly / COMFORT_DTI, 0),  # 月供不超收入 40% 所需的家庭月收入
        "is_new": bool(is_new),
    }


def rent_view(rent_month: float | None, total_price: float) -> dict[str, Any]:
    """租金视角：年租金回报率与静态回本年限（不含空置、税费、维修）。"""
    rent = float(rent_month or 0)
    price = float(total_price or 0)
    if rent <= 0 or price <= 0:
        return {"annual_rent": 0.0, "yield_pct": None, "payback_years": None}
    annual = rent * 12
    return {
        "annual_rent": round(annual, 0),
        "yield_pct": round(annual / price * 100, 2),
        "payback_years": round(price / annual, 1),
    }


# --------------------------------------------------------------------------
# 议价锚点
# --------------------------------------------------------------------------
def bargain_anchor(
    *,
    total_price: float,
    area: float,
    unit_price: float | None,
    comp_median_unit: float | None,
    list_days: int = 0,
    price_changes: int = 0,
    consecutive_drops: int = 0,
    first_price: float | None = None,
    liquidity_score: float = 60.0,
    defect_count: int = 0,
    is_declining_market: bool = True,
) -> dict[str, Any]:
    """估算合理成交价区间与建议首轮出价。

    这不是精算，也不是「帮你砍价的玄学」，而是把三个公开可核的信号量化：
      1) 同小区同户型中位单价（市场锚）
      2) 挂牌天数 + 调价次数（卖方急迫度）
      3) 个盘流动性（买方市场强度：越难卖，买方越有话语权）
    真实成交价一定还要靠中介报价 + 网签数据核对，这里只给「谈判起点」。
    """
    price = max(0.0, float(total_price or 0))
    area = max(0.0, float(area or 0))
    reasons: list[str] = []

    # ---- 锚 1：市场锚 ----
    market_anchor: float | None = None
    if comp_median_unit and area > 0:
        market_anchor = float(comp_median_unit) * area
        gap = (price - market_anchor) / market_anchor
        if gap > 0.03:
            reasons.append(f"挂牌价高于同户型中位价约 {gap * 100:.1f}%，有回归空间")
        elif gap < -0.03:
            reasons.append(f"挂牌价已低于同户型中位价约 {-gap * 100:.1f}%，本身偏低于市场")
    if market_anchor is None:
        reasons.append("同户型可比样本不足，只能按卖方急迫度估算")

    # ---- 锚 2：卖方急迫度 → 可谈空间 ----
    room = 0.0
    if is_declining_market:
        room += 0.010                                   # 弱市普适性让利
        reasons.append("当前为买方市场，同板块成交普遍低于挂牌 1%~3%")
    if list_days >= 30:
        add = 0.010 if list_days >= 180 else (0.008 if list_days >= 90 else 0.004)
        room += add
        reasons.append(f"已挂牌 {int(list_days)} 天（{'>' if list_days >= 180 else '<'}180 天档），卖方耐心在消耗")
    if price_changes > 0:
        add = min(0.02, 0.004 * int(price_changes))
        room += add
        reasons.append(f"累计调价 {int(price_changes)} 次，说明价格有弹性")
    if consecutive_drops >= 2:
        room += 0.005
        reasons.append(f"已连续降价 {int(consecutive_drops)} 次，卖方让步意愿明确")
    if liquidity_score < 55:
        room += 0.010
        reasons.append(f"个盘流动性 {liquidity_score:.0f} 分偏低，挂牌多、接盘少，买方有话语权")
    if defect_count >= 2:
        room += 0.008
        reasons.append(f"存在 {int(defect_count)} 项硬伤，客群窄，压价理由充分")

    # ---- 反向外推：如果已从首次价降过很多，说明可能已接近卖方底线 ----
    if first_price and first_price > price > 0:
        already = 1 - price / first_price
        if already >= 0.10:
            room *= 0.75
            reasons.append(
                f"已从 {first_price / 10000:.1f} 万降到当前价（累计 {already * 100:.1f}%），"
                "卖方底线可能已接近，继续深压的空间收窄"
            )

    # ---- 挂牌已明显低于市场锚时，本来就便宜，可谈空间要压缩 ----
    if market_anchor and price < market_anchor * 0.97:
        room *= 0.6
        reasons.append("报价已在市场中位之下，继续大幅压价对方宁可撤牌，建议以核验硬伤换取小幅让利")

    room = _clamp(room, 0.0, 0.12)

    # 谈判基准：挂牌价与市场锚取更低的那个（绝不按高于挂牌价去「建议出价」）
    base = price
    if market_anchor and market_anchor < price:
        base = min(price, market_anchor * 1.02)
        reasons.append("以同户型中位价为谈判基准，而不是直接按挂牌价打折")

    suggest_offer = round(base * (1 - room) / 1000) * 1000
    # 硬约束：出价不得高于挂牌价，也不宜低于挂牌价的 85%（否则通常直接谈崩）
    suggest_offer = min(suggest_offer, round(price * 0.995 / 1000) * 1000)
    suggest_offer = max(suggest_offer, round(price * 0.85 / 1000) * 1000)

    fair_low = round(base * (1 - room) / 1000) * 1000
    fair_high = round(base * (1 - room / 3) / 1000) * 1000
    if fair_high < fair_low:
        fair_low, fair_high = fair_high, fair_low

    return {
        "market_anchor": round(market_anchor, 0) if market_anchor else None,
        "market_anchor_unit": round(float(comp_median_unit), 0) if comp_median_unit else None,
        "room_pct": round(room * 100, 2),
        "suggest_offer": suggest_offer,
        "fair_low": fair_low,
        "fair_high": fair_high,
        "reasons": reasons,
        "disclaimer": "仅为谈判起点，不是估值结论；真实成交价以网签与中介成交记录为准。",
    }
