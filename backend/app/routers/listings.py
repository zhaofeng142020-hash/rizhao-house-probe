"""房源接口：列表 / 详情 / 可比分布。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import query_all, query_one
from . import common

router = APIRouter(prefix="/api/listings", tags=["房源"])


@router.get("")
def list_listings(
    district: str | None = None,
    rooms: int | None = None,
    min_score: float | None = None,
    grade: str | None = Query(None, description="red / yellow / white"),
    max_total: float | None = Query(None, description="总价上限（元）"),
    min_area: float | None = None,
    max_area: float | None = None,
    keyword: str | None = None,
    sort: str = Query("score", description="score / deviation / drop / days / price"),
    limit: int = Query(50, ge=1, le=300),
    offset: int = Query(0, ge=0),
):
    where = ["l.status='active'"]
    params: list = []
    if district:
        where.append("l.district=?")
        params.append(district)
    if rooms:
        where.append("l.rooms=?")
        params.append(rooms)
    if min_score is not None:
        where.append("COALESCE(s.score,0)>=?")
        params.append(min_score)
    if grade:
        where.append("s.grade=?")
        params.append(grade)
    if max_total:
        where.append("l.total_price<=?")
        params.append(max_total)
    if min_area:
        where.append("l.area>=?")
        params.append(min_area)
    if max_area:
        where.append("l.area<=?")
        params.append(max_area)
    if keyword and keyword.strip():
        kw = "%" + keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(l.community_name LIKE ? ESCAPE '\\' OR l.title LIKE ? ESCAPE '\\')")
        params.extend([kw, kw])

    order = {
        "score": "COALESCE(s.score,0) DESC",
        "deviation": "COALESCE(s.deviation_pct,0) DESC",
        "drop": "l.price_changes DESC, l.list_days DESC",
        "days": "l.list_days DESC",
        "price": "l.total_price ASC",
    }.get(sort, "COALESCE(s.score,0) DESC")

    where_sql = " AND ".join(where)
    rows = query_all(
        f"{common.LISTING_SELECT} WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    total = query_one(
        "SELECT COUNT(*) AS n FROM listing l LEFT JOIN leak_score s "
        f"ON s.target_type='listing' AND s.target_id=l.id WHERE {where_sql}",
        tuple(params),
    )
    return {"total": int((total or {}).get("n") or 0), "items": [common.normalize_listing(r) for r in rows]}


@router.get("/{listing_id}")
def listing_detail(listing_id: int):
    row = query_one(f"{common.LISTING_SELECT} WHERE l.id=?", (listing_id,))
    if not row:
        raise HTTPException(404, "房源不存在")
    item = common.normalize_listing(row)

    history = common.listing_history(listing_id)
    comparables = common.comparable_pool(item)
    score_row = common.get_score_row("listing", listing_id)
    score = common.build_score_payload(score_row, item, "listing")
    # 详情页需要「同小区同户型中位单价」做对照（列表接口不需要，按需补）
    score["median_unit"] = comparables.get("median")
    events = query_all(
        "SELECT * FROM anomaly_event WHERE target_type='listing' AND target_id=? ORDER BY event_date DESC",
        (listing_id,),
    )
    for ev in events:
        ev["detail"] = common.loads(ev.get("detail_json"), {})

    return {
        "item": item,
        "score": score,
        "history": history,
        "comparables": comparables,
        "events": events,
        "risk_checklist": common.build_risk_checklist(item, is_auction=False),
        "verify_steps": common.build_verify_steps(is_auction=False),
        "afford": build_afford(item, comparables),
        "is_watched": bool(item.get("watched")),
    }


def build_afford(item: dict, comparables: dict) -> dict:
    """购房成本 + 议价锚点 + 租金视角。评分回答「值不值得看」，这里回答「买不买得起、能谈到多少」。"""
    from ..config import settings
    from ..db import query_one
    from ..services.afford import bargain_anchor, buy_cost, rent_view

    pref = query_one("SELECT * FROM user_pref WHERE id=1") or {}
    total = float(item.get("total_price") or 0)
    area = float(item.get("area") or 0)

    cost = buy_cost(
        total, area,
        down_ratio=float(pref.get("down_ratio") or settings.DOWN_RATIO),
        years=int(pref.get("loan_years") or settings.LOAN_YEARS),
        annual_rate=float(pref.get("loan_rate") or settings.LOAN_RATE),
        is_first_home=bool(pref.get("is_first_home", 1)),
        is_new=(item.get("source") == "newhome"),
        decoration=item.get("decoration"),
        agent_fee_ratio=settings.AGENT_FEE_RATIO,
        decoration_cost_per_sqm=settings.NEW_DECOR_COST,
    )
    defects = item.get("defect_tags") or []
    anchor = bargain_anchor(
        total_price=total,
        area=area,
        unit_price=item.get("unit_price"),
        comp_median_unit=comparables.get("median"),
        list_days=int(item.get("list_days") or 0),
        price_changes=int(item.get("price_changes") or 0),
        consecutive_drops=int(item.get("consecutive_drops") or 0),
        first_price=item.get("first_price"),
        liquidity_score=float(item.get("liquidity_score") or 60),
        defect_count=len(defects),
    )
    return {
        "cost": cost,
        "rent": rent_view(item.get("rent_month"), total),
        "bargain": anchor,
        "pref": {
            "down_ratio": cost["down_ratio"],
            "years": cost["years"],
            "rate": cost["annual_rate"],
        },
    }
