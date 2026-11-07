"""小区维度接口：小区档案、在售房源汇总、单价分布、去化月数。

为什么单独做小区维度：
买房从来不是「买一套房」，是「买一个小区 + 一个板块」。
同一个小区里所有在挂房源放在一起看，才能回答三个买家真正关心的问题——
    这个小区现在有几套在卖？（供给）
    卖得多快？（近 90 天成交 → 去化月数，比「流动性分数」更硬）
    同户型之间差在哪？（同户型竞争格局，直接决定你能砍多少）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import execute, query_all, query_one
from . import common

router = APIRouter(prefix="/api/communities", tags=["小区"])


def _like(term: str) -> str:
    """LIKE 通配符转义：用户输入的 % / _ 应当按字面匹配，而不是当通配符。"""
    return "%" + term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def months_to_clear(listing_n: int, deal_90d: int) -> float | None:
    """去化月数 = 在售套数 / 月均成交套数。近 90 天零成交返回 None（不是 0）。"""
    if deal_90d and int(deal_90d) > 0 and listing_n > 0:
        return round(int(listing_n) / (int(deal_90d) / 3.0), 1)
    return None


def _community_rent(community_name: str, listing_median_unit: float | None) -> dict:
    """该小区最近一批租房样本 → 租金中位与年化租售比（无样本返回说明）。"""
    latest = query_one("SELECT MAX(collected_date) AS d FROM rent_sample")
    day = (latest or {}).get("d")
    if not day:
        return {"available": False, "note": "暂无租金样本，接入租金抓取后此处显示租售比。"}
    rows = query_all(
        "SELECT rent_month, rent_pm FROM rent_sample WHERE community_name=? AND collected_date=?",
        (community_name, day),
    )
    if not rows:
        return {"available": False, "note": "该小区最近一批租房样本里没有房源。"}
    def _med(vals):
        xs = sorted(float(v) for v in vals if v)
        if not xs:
            return None
        n = len(xs)
        return round(xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2, 1)
    med_rent = _med([r["rent_month"] for r in rows])
    med_pm = _med([r["rent_pm"] for r in rows])
    yield_pct = round(med_pm * 12 / listing_median_unit * 100, 2) if (med_pm and listing_median_unit) else None
    return {
        "available": True,
        "collected_date": day,
        "sample_n": len(rows),
        "median_rent": med_rent,
        "median_rent_pm": med_pm,
        "rent_yield_pct": yield_pct,
        "note": "租售比 = 租金中位×12 ÷ 在售挂牌单价中位，横向比较用。",
    }


def liquidity_note(months: float | None, is_extended: int = 0) -> str:
    """把去化月数翻译成人话（自住者真正关心的「好不好脱手」）。"""
    if months is None:
        base = "近 90 天无成交记录，转手难度大，自住可以、投资要慎重"
    elif months <= 6:
        base = f"去化约 {months} 个月，属于好卖的小区"
    elif months <= 12:
        base = f"去化约 {months} 个月，供需正常"
    elif months <= 24:
        base = f"去化约 {months} 个月，卖得偏慢，砍价空间相对大"
    else:
        base = f"去化约 {months} 个月，供给远大于需求，买入后想卖会很难"
    if is_extended:
        base += "；属扩展区，二手接盘面更窄，流动性要额外打折"
    return base


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    n = len(values)
    mid = n // 2
    return round(values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2, 1)


@router.get("")
def list_communities(
    district: str | None = None,
    keyword: str | None = None,
):
    """小区清单（地图与列表共用）。带坐标、在售套数、中位单价、去化月数。"""
    from ..config import settings

    # target_area_n 固定用「重点关注面积段」的口径，不跟着筛选参数走，
    # 否则前端在地图上看到「18 套大户」其实是全部在售，会误导。
    ta_min = float(settings.TARGET_AREA_MIN)
    ta_max = float(settings.TARGET_AREA_MAX)

    where = ["1=1"]
    params: list = []
    if district:
        where.append("c.district=?")
        params.append(district)
    if keyword and keyword.strip():
        kw = keyword.strip()
        # 名称 / 别名 / 板块 / 对口学校都能搜到；ESCAPE 保证 % 和 _ 按字面匹配
        where.append(
            "(c.name LIKE ? ESCAPE '\\' OR COALESCE(c.alias,'') LIKE ? ESCAPE '\\' "
            "OR c.district LIKE ? ESCAPE '\\' OR COALESCE(c.school_district,'') LIKE ? ESCAPE '\\' "
            "OR EXISTS (SELECT 1 FROM listing l2 WHERE l2.community_id=c.id "
            "           AND l2.title LIKE ? ESCAPE '\\'))"
        )
        params.extend([_like(kw)] * 5)

    rows = query_all(
        f"""
        SELECT c.*,
               (SELECT COUNT(*) FROM watch_item w WHERE w.target_type='community' AND w.target_id=c.id) AS watched,
               (SELECT COUNT(*) FROM listing l WHERE l.community_id=c.id AND l.status='active') AS listing_n,
               (SELECT COUNT(*) FROM listing l WHERE l.community_id=c.id AND l.status='active'
                    AND l.area BETWEEN ? AND ?) AS target_area_n,
               (SELECT ROUND(AVG(l.unit_price)) FROM listing l WHERE l.community_id=c.id AND l.status='active') AS avg_unit,
               (SELECT MAX(s.score) FROM listing l JOIN leak_score s
                    ON s.target_type='listing' AND s.target_id=l.id
                    WHERE l.community_id=c.id AND l.status='active') AS top_score,
               (SELECT COUNT(*) FROM listing l JOIN leak_score s
                    ON s.target_type='listing' AND s.target_id=l.id
                    WHERE l.community_id=c.id AND l.status='active' AND s.grade='red') AS red_n
        FROM community c
        WHERE {' AND '.join(where)}
        ORDER BY listing_n DESC, avg_unit DESC
        """,
        (ta_min, ta_max, *params),
    )

    items = []
    for r in rows:
        units = [
            float(x["unit_price"])
            for x in query_all(
                "SELECT unit_price FROM listing WHERE community_id=? AND status='active'", (r["id"],)
            )
            if x.get("unit_price")
        ]
        months = months_to_clear(int(r["listing_n"] or 0), int(r["deal_active_90d"] or 0))
        items.append({
            "id": r["id"],
            "name": r["name"],
            "district": r["district"],
            "is_extended": int(r["is_extended"] or 0),
            "lat": r["lat"],
            "lng": r["lng"],
            "build_year": r["build_year"],
            "has_elevator": int(r["has_elevator"] or 0),
            "school_district": r["school_district"],
            "liquidity_score": r["liquidity_score"],
            "deal_active_90d": r["deal_active_90d"],
            "listing_n": int(r["listing_n"] or 0),
            "target_area_n": int(r["target_area_n"] or 0),
            "avg_unit": round(float(r["avg_unit"] or 0)),
            "median_unit": _median(units),
            "top_score": round(float(r["top_score"]), 1) if r["top_score"] else None,
            "red_n": int(r["red_n"] or 0),
            "months_to_clear": months,
            "watched": int(r["watched"] or 0) > 0,
        })
    return {"total": len(items), "items": items}


# ---------------------------------------------------------------------------
# 关注小区 + 房价波动
# 注意：这些固定路径必须注册在 /{community_id} 之前，否则会被当成 id 解析成 422
# ---------------------------------------------------------------------------
@router.get("/followed")
def followed_communities():
    from ..services.community_trend import followed_overview

    return followed_overview()


@router.get("/{community_id}/trend")
def community_trend(community_id: int, days: int = 180):
    from ..services.community_trend import trend

    data = trend(community_id, max(30, min(days, 365)))
    if not data:
        raise HTTPException(404, "小区不存在")
    return data


@router.post("/{community_id}/watch")
def watch_community(community_id: int):
    """关注小区。基准价用当前中位单价，后续波动按这个基准对比。"""
    c = query_one("SELECT id,name,district FROM community WHERE id=?", (community_id,))
    if not c:
        raise HTTPException(404, "小区不存在")
    snap = query_one(
        "SELECT median_unit FROM community_price_snapshot WHERE community_id=? "
        "ORDER BY observed_date DESC LIMIT 1", (community_id,),
    )
    if not snap:
        from ..services.community_trend import snapshot_all, backfill_from_history
        backfill_from_history()
        snapshot_all()
        snap = query_one(
            "SELECT median_unit FROM community_price_snapshot WHERE community_id=? "
            "ORDER BY observed_date DESC LIMIT 1", (community_id,),
        )
    base = float((snap or {}).get("median_unit") or 0)
    execute(
        "INSERT INTO watch_item (target_type,target_id,community_name,district,follow_status,"
        "base_price,last_price) VALUES ('community',?,?,?,'watching',?,?) "
        "ON CONFLICT(target_type,target_id) DO NOTHING",
        (community_id, c["name"], c["district"], base, base),
    )
    return {"ok": True, "watched": True, "community_id": community_id, "base_unit": base}


@router.delete("/{community_id}/watch")
def unwatch_community(community_id: int):
    execute(
        "DELETE FROM watch_item WHERE target_type='community' AND target_id=?",
        (community_id,),
    )
    return {"ok": True, "watched": False}


@router.get("/{community_id}")
def community_detail(community_id: int):
    """一个小区的全部在售房源 + 分布 + 异动 + 同板块参照。"""
    c = query_one("SELECT * FROM community WHERE id=?", (community_id,))
    if not c:
        raise HTTPException(404, "小区不存在")

    rows = query_all(
        f"{common.LISTING_SELECT} WHERE l.community_id=? AND l.status='active' "
        "ORDER BY l.unit_price ASC",
        (community_id,),
    )
    listings = [common.normalize_listing(r) for r in rows]

    units = [float(x["unit_price"]) for x in listings if x.get("unit_price")]
    totals = [float(x["total_price"]) for x in listings if x.get("total_price")]
    areas = [float(x["area"]) for x in listings if x.get("area")]
    months = months_to_clear(len(listings), int(c["deal_active_90d"] or 0))
    watched = bool(query_one(
        "SELECT 1 FROM watch_item WHERE target_type='community' AND target_id=?", (community_id,)
    ))
    from ..services.community_trend import trend as trend_of
    trend = trend_of(community_id, days=90)

    # 同户型竞争格局：同一户型有几套在挂、价差多大
    by_layout: dict[str, list[dict]] = {}
    for item in listings:
        by_layout.setdefault(item.get("layout") or "未知户型", []).append(item)
    layout_groups = []
    for layout, group in sorted(by_layout.items(), key=lambda kv: -len(kv[1])):
        u = sorted(float(g["unit_price"]) for g in group if g.get("unit_price"))
        layout_groups.append({
            "layout": layout,
            "n": len(group),
            "median_unit": _median(u),
            "min_unit": round(u[0]) if u else None,
            "max_unit": round(u[-1]) if u else None,
            "spread_pct": round((u[-1] - u[0]) / u[0] * 100, 1) if len(u) >= 2 and u[0] else None,
            "listings": sorted(group, key=lambda x: float(x.get("unit_price") or 0)),
        })

    # 该小区当前在售房源命中的异动
    events = query_all(
        "SELECT e.*, s.score FROM anomaly_event e "
        "LEFT JOIN leak_score s ON s.target_type=e.target_type AND s.target_id=e.target_id "
        "WHERE e.community_name=? ORDER BY e.event_date DESC LIMIT 30",
        (c["name"],),
    )
    from ..services.anomaly import EVENT_LABELS
    for ev in events:
        ev["detail"] = common.loads(ev.get("detail_json"), {})
        ev["event_label"] = EVENT_LABELS.get(ev["event_type"], ev["event_type"])

    # 同板块其他小区（地图上邻居，方便横向比价）
    nearby = query_all(
        "SELECT id, name, district, lat, lng, build_year, liquidity_score, deal_active_90d, "
        "is_extended FROM community WHERE district=? AND id<>? ORDER BY liquidity_score DESC LIMIT 12",
        (c["district"], community_id),
    )
    for n in nearby:
        n["listing_n"] = int(
            (query_one(
                "SELECT COUNT(*) AS n FROM listing WHERE community_id=? AND status='active'", (n["id"],)
            ) or {}).get("n") or 0
        )
        n["median_unit"] = _median([
            float(x["unit_price"])
            for x in query_all(
                "SELECT unit_price FROM listing WHERE community_id=? AND status='active'", (n["id"],)
            )
            if x.get("unit_price")
        ])

    # 同板块新盘参照（新房倒挂判断用）
    new_homes = query_all(
        "SELECT id, name, avg_unit_price, area_min, area_max, delivery_date, decoration, sale_status "
        "FROM new_home WHERE district=? AND status='active' ORDER BY avg_unit_price ASC",
        (c["district"],),
    )
    for nh in new_homes:
        nh["gap_pct"] = (
            round((1 - float(nh["avg_unit_price"]) / _median(units)) * 100, 1)
            if nh.get("avg_unit_price") and _median(units) else None
        )

    return {
        "community": {
            "id": c["id"], "name": c["name"], "district": c["district"],
            "is_extended": int(c["is_extended"] or 0),
            "lat": c["lat"], "lng": c["lng"],
            "build_year": c["build_year"], "has_elevator": int(c["has_elevator"] or 0),
            "school_district": c["school_district"],
            "liquidity_score": c["liquidity_score"],
            "deal_active_90d": c["deal_active_90d"],
        },
        "stats": {
            "listing_n": len(listings),
            "avg_unit": round(sum(units) / len(units)) if units else None,
            "median_unit": _median(units),
            "min_unit": round(min(units)) if units else None,
            "max_unit": round(max(units)) if units else None,
            "median_total": _median(totals),
            "median_area": _median(areas),
            "months_to_clear": months,
            "liquidity_note": liquidity_note(months, int(c["is_extended"] or 0)),
            "red_n": sum(1 for x in listings if x.get("grade") == "red"),
            "yellow_n": sum(1 for x in listings if x.get("grade") == "yellow"),
            "target_area_n": sum(1 for x in listings if 120 <= float(x.get("area") or 0) <= 150),
        },
        "listings": listings,
        "watched": watched,
        "trend": trend,
        "rent": _community_rent(c["name"], _median(units)),
        "layout_groups": layout_groups,
        "events": events,
        "nearby": nearby,
        "new_homes": new_homes,
        "disclaimer": "在售与成交数据来自公开页面，成交套数为统计口径估算，仅用于比较流动性，不作为投资依据。",
    }
