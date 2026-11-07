"""异动识别引擎（纯函数，无 IO，可单测）。

输入：一条挂牌/法拍的字段 + 其调价历史 + 市场统计（市场统计由 market_stats 预先算好）
输出：异动事件列表（0~N 条）

事件类型：
  sudden_drop       突发降价：单次降幅 ≥5%，或 7 日内连降 2 次
  deep_dip          深跌：当前单价低于同小区同户型近3月中位 ≥12%
  long_listed       久挂让利：挂牌 >180 天且近 30 天有降价
  miskill           错杀信号：单价偏低但硬伤可控（仅房龄/装修旧/空置）
  board_divergence  板块背离：板块横盘/下行，个盘逆势低价
  new_home          新房信号：新盘特价/抵账房/尾盘，单价低于周边二手 5% 以上
  auction_deal      法拍信号：起拍净折扣 ≥15%，且占用/税费风险为中低
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .scoring import CONTROLLABLE_DEFECTS, DEFECT_PENALTY, clamp

EVENT_LABELS: dict[str, str] = {
    "sudden_drop": "突发降价",
    "deep_dip": "深跌",
    "long_listed": "久挂让利",
    "miskill": "错杀候选",
    "board_divergence": "板块背离",
    "new_home": "新房倒挂",
    "auction_deal": "法拍机会",
}

# 各类事件的可信权重（用于「异动强度」排序，不进总分）
EVENT_STRENGTH: dict[str, float] = {
    "sudden_drop": 0.9,
    "deep_dip": 1.0,
    "long_listed": 0.7,
    "miskill": 0.8,
    "board_divergence": 0.6,
    "new_home": 0.8,
    "auction_deal": 0.95,
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def layout_group(listing: dict) -> str:
    """户型归一：'3室2厅2卫' / '3室' / '三室' → '3室'。"""
    rooms = listing.get("rooms")
    if rooms:
        return f"{int(_f(rooms))}室"
    layout = str(listing.get("layout") or "")
    zh = {"一": "1", "两": "2", "二": "2", "三": "3", "四": "4", "五": "5"}
    for k, v in zh.items():
        if k in layout:
            layout = layout.replace(k, v)
    for token in layout.replace(" ", "").split("室"):
        if token and token[-1].isdigit():
            return f"{token[-1]}室"
    area = _f(listing.get("area"))
    if area and area < 75:
        return "2室"
    if area and area >= 140:
        return "4室"
    return "3室"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _summarize(values: list[float]) -> dict:
    values = [v for v in values if v > 0]
    if not values:
        return {"n": 0, "median": 0.0, "median_unit": 0.0, "p25": 0.0, "p75": 0.0,
                "min": 0.0, "max": 0.0, "values": []}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2),
        "median_unit": round(statistics.median(values), 2),
        "p25": round(_percentile(values, 0.25), 2),
        "p75": round(_percentile(values, 0.75), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "values": sorted(round(v, 2) for v in values),
    }


def compute_market_stats(listings: Iterable[dict]) -> dict:
    """预先算好三张统计表：小区×户型 / 小区 / 板块。全部按「单价」统计。"""
    by_comm_layout: dict[tuple, list[float]] = defaultdict(list)
    by_comm: dict[Any, list[float]] = defaultdict(list)
    by_district: dict[str, list[float]] = defaultdict(list)
    board_change: dict[str, list[float]] = defaultdict(list)

    for li in listings:
        if li.get("status") not in (None, "active"):
            continue
        unit = _f(li.get("unit_price"))
        if unit <= 0:
            continue
        comm_id = li.get("community_id")
        district = li.get("district") or ""
        by_comm_layout[(comm_id, layout_group(li))].append(unit)
        by_comm[comm_id].append(unit)
        by_district[district].append(unit)
        # 用于判断板块趋势：当前单价 / 首次挂牌单价 - 1
        first = _f(li.get("first_price"))
        area = _f(li.get("area"))
        if first > 0 and area > 0:
            board_change[district].append(unit / (first / area) - 1)

    return {
        "by_comm_layout": {k: _summarize(v) for k, v in by_comm_layout.items()},
        "by_comm": {k: _summarize(v) for k, v in by_comm.items()},
        "by_district": {k: _summarize(v) for k, v in by_district.items()},
        "board_change_pct": {
            k: round(statistics.median(v), 4) for k, v in board_change.items() if v
        },
    }


def get_comparables(stats: dict, listing: dict) -> dict:
    """取该房源的可比中枢。优先小区×户型，样本不足时退到小区级。"""
    comm_id = listing.get("community_id")
    key = (comm_id, layout_group(listing))
    comp = (stats.get("by_comm_layout") or {}).get(key)
    if comp and comp.get("n", 0) >= 1:
        return comp
    fallback = (stats.get("by_comm") or {}).get(comm_id)
    if fallback and fallback.get("n", 0) >= 1:
        return {**fallback, "fallback": True}
    return (stats.get("by_district") or {}).get(listing.get("district") or "", {}) or {}


# =============================================================================
# 单条挂牌的异动识别
# =============================================================================
def detect_listing_events(
    listing: dict,
    history: list[dict],
    comp: dict,
    stats: dict,
    today: date | None = None,
    cfg: dict | None = None,
) -> list[dict]:
    """返回异动事件列表。cfg 里可覆盖阈值（SUDDEN_DROP_PCT / DEEP_DIP_PCT / LONG_LISTED_DAYS）。"""
    today = today or date.today()
    cfg = cfg or {}
    sudden_pct = _f(cfg.get("sudden_drop_pct"), 0.05)
    deep_pct = _f(cfg.get("deep_dip_pct"), 0.12)
    long_days = int(_f(cfg.get("long_listed_days"), 180))

    events: list[dict] = []
    unit = _f(listing.get("unit_price"))
    total = _f(listing.get("total_price"))
    area = _f(listing.get("area"))
    if unit <= 0 and area > 0:
        unit = total / area
    median_unit = _f(comp.get("median_unit"))
    sample_n = int(_f(comp.get("n")))
    deviation = (median_unit - unit) / median_unit if median_unit > 0 and unit > 0 else 0.0

    tags = listing.get("defect_tags") or []
    if isinstance(tags, str):
        import json
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []
    hard = [t for t in tags if t not in CONTROLLABLE_DEFECTS]
    defect_score = clamp(100 - sum(DEFECT_PENALTY.get(t, 6) for t in tags))

    # ---------------- 1. 突发降价 ----------------
    drops = [h for h in history if _f(h.get("drop_pct")) < 0]
    max_single_drop = min((_f(h.get("drop_pct")) for h in drops), default=0.0)  # 负数
    recent_drops = []
    for h in drops:
        try:
            d = datetime.strptime(str(h.get("observed_at"))[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if (today - d).days <= 7:
            recent_drops.append(h)

    if abs(max_single_drop) >= sudden_pct:
        events.append({
            "event_type": "sudden_drop",
            "severity_hint": "red" if abs(max_single_drop) >= sudden_pct * 1.6 else "yellow",
            "drop_pct": round(abs(max_single_drop), 4),
            "detail": {
                "single_drop_pct": round(abs(max_single_drop), 4),
                "threshold_pct": sudden_pct,
                "trigger": "单次降幅超阈值",
                "drops": [{"date": h.get("observed_at"), "price": h.get("price"),
                           "drop_pct": h.get("drop_pct")} for h in drops[-5:]],
            },
        })
    elif len(recent_drops) >= 2:
        events.append({
            "event_type": "sudden_drop",
            "severity_hint": "yellow",
            "drop_pct": round(abs(sum(_f(h.get("drop_pct")) for h in recent_drops)), 4),
            "detail": {
                "trigger": "7 日内连降 2 次以上",
                "recent_drop_count": len(recent_drops),
                "drops": [{"date": h.get("observed_at"), "price": h.get("price"),
                           "drop_pct": h.get("drop_pct")} for h in recent_drops],
            },
        })

    # ---------------- 2. 深跌 ----------------
    if median_unit > 0 and deviation >= deep_pct:
        events.append({
            "event_type": "deep_dip",
            "severity_hint": "red" if deviation >= deep_pct * 1.5 and not hard else "yellow",
            "drop_pct": round(deviation, 4),
            "detail": {
                "deviation_pct": round(deviation, 4),
                "median_unit": median_unit,
                "unit_price": round(unit, 1),
                "sample_n": sample_n,
                "threshold_pct": deep_pct,
            },
        })

    # ---------------- 3. 久挂让利 ----------------
    list_days = int(_f(listing.get("list_days")))
    last_drop_days_ago = listing.get("last_drop_days_ago")
    if list_days > long_days and (last_drop_days_ago is not None and int(_f(last_drop_days_ago, 999)) <= 30):
        events.append({
            "event_type": "long_listed",
            "severity_hint": "yellow",
            "drop_pct": round(max(0.0, (_f(listing.get("first_price")) - total) / _f(listing.get("first_price"), 1)), 4),
            "detail": {
                "list_days": list_days,
                "price_changes": int(_f(listing.get("price_changes"))),
                "last_drop_days_ago": int(_f(last_drop_days_ago)),
                "first_price": _f(listing.get("first_price")),
            },
        })

    # ---------------- 4. 错杀候选（偏低 + 硬伤可控） ----------------
    if median_unit > 0 and 0.03 <= deviation < deep_pct and not hard and tags:
        events.append({
            "event_type": "miskill",
            "severity_hint": "yellow",
            "drop_pct": round(deviation, 4),
            "detail": {
                "deviation_pct": round(deviation, 4),
                "controllable_tags": tags,
                "defect_score": defect_score,
                "note": "硬伤集中在可改善项，价格却已被压低",
            },
        })
    elif median_unit > 0 and deviation >= deep_pct and not hard:
        events.append({
            "event_type": "miskill",
            "severity_hint": "yellow",
            "drop_pct": round(deviation, 4),
            "detail": {
                "deviation_pct": round(deviation, 4),
                "controllable_tags": tags,
                "defect_score": defect_score,
                "note": "深跌且无不可逆硬伤，属错杀候选（需核实是否假房源）",
            },
        })

    # ---------------- 5. 板块背离 ----------------
    district = listing.get("district") or ""
    board_change = _f((stats.get("board_change_pct") or {}).get(district), 0.0)
    district_median = _f(((stats.get("by_district") or {}).get(district) or {}).get("median_unit"))
    comm_median = _f(((stats.get("by_comm") or {}).get(listing.get("community_id")) or {}).get("median_unit"))
    if district_median > 0 and comm_median > 0 and board_change <= 0.02:
        gap = (district_median - comm_median) / district_median
        # 双重条件：整个小区低于板块中枢 5% 以上，且本套单价也低于板块中枢 5% 以上
        # （只有"个盘逆势低价"才值得报，否则只是整个小区本来就便宜）
        if gap >= 0.05 and unit < district_median * 0.95:
            events.append({
                "event_type": "board_divergence",
                "severity_hint": "yellow",
                "drop_pct": round(gap, 4),
                "detail": {
                    "board_change_pct": round(board_change, 4),
                    "district_median_unit": district_median,
                    "community_median_unit": comm_median,
                    "gap_pct": round(gap, 4),
                    "note": f"{district}整体横盘/下行（{board_change * 100:+.1f}%），本小区低于板块中枢 {gap * 100:.1f}%",
                },
            })

    # ---------------- 6. 新房倒挂 ----------------
    if listing.get("is_new_home") or listing.get("source") == "newhome":
        if median_unit > 0 and unit < median_unit * 0.95:
            events.append({
                "event_type": "new_home",
                "severity_hint": "yellow",
                "drop_pct": round((median_unit - unit) / median_unit, 4),
                "detail": {
                    "unit_price": round(unit, 1),
                    "nearby_secondhand_median": median_unit,
                    "note": "新房/抵账房/尾盘报价低于周边二手 5% 以上，注意交付与产权性质",
                },
            })

    return events


# =============================================================================
# 单条法拍的异动识别
# =============================================================================
def detect_auction_events(auction: dict, comp: dict, cfg: dict | None = None,
                          today: date | None = None) -> list[dict]:
    cfg = cfg or {}
    threshold = _f(cfg.get("auction_net_discount"), 0.15)
    events: list[dict] = []

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
    landing = _f(auction.get("landing_price")) or (
        start + _f(auction.get("tax_est")) + _f(auction.get("arrears_est")) + _f(auction.get("repair_reserve"))
    )
    comp_unit = _f(comp.get("median_unit")) or _f(auction.get("comparable_unit_price"))
    landing_unit = landing / area if area > 0 else 0
    net_discount = (comp_unit - landing_unit) / comp_unit if comp_unit > 0 and landing_unit > 0 else 0.0
    start_discount = (appraisal - start) / appraisal if appraisal > 0 else 0.0

    high_risk = {"占用", "租赁", "腾退难", "产权瑕疵", "限购"} & set(tags)
    if net_discount >= threshold and not high_risk:
        events.append({
            "event_type": "auction_deal",
            "severity_hint": "red" if net_discount >= threshold * 1.5 else "yellow",
            "drop_pct": round(net_discount, 4),
            "detail": {
                "net_discount": round(net_discount, 4),
                "start_discount": round(start_discount, 4),
                "landing_price": landing,
                "landing_unit": round(landing_unit, 1),
                "comparable_unit": comp_unit,
                "stage": auction.get("stage"),
                "risk_tags": tags,
                "note": "净折扣按到手价（起拍+税费+欠费+维修预留）对比同小区二手可比单价计算",
            },
        })
    elif net_discount >= threshold and high_risk:
        # 折扣大但风险高：仍然记录，但只进「仅记录」档
        events.append({
            "event_type": "auction_deal",
            "severity_hint": "white",
            "drop_pct": round(net_discount, 4),
            "detail": {
                "net_discount": round(net_discount, 4),
                "risk_tags": tags,
                "note": "折扣达标但存在占用/腾退/产权类高危标签，按『坑』优先处理",
            },
        })
    return events


def build_event_key(event_type: str, target_type: str, target_id: Any, day: date) -> str:
    """事件去重键：同一房源同一类型每天一条。"""
    return f"{event_type}|{target_type}|{target_id}|{day.isoformat()}"
