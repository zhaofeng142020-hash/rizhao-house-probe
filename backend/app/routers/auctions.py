"""法拍接口。

成交闭环：回填成交价会把 status 置为 'sold'。这里显式提供 status 过滤，
让成交标的继续可查（默认仍只出在拍，保持旧行为兼容）；首页/排行则只认 active。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import query_all, query_one
from . import common

router = APIRouter(prefix="/api/auctions", tags=["法拍"])


@router.get("")
def list_auctions(
    district: str | None = None,
    sort: str = Query("score", description="score / discount / date"),
    status: str = Query("active", description="active / sold / all"),
    limit: int = Query(50, ge=1, le=200),
):
    """法拍列表。status=active 为默认（在拍），sold 看已成交，all 看全部。"""
    if status not in ("active", "sold", "all"):
        status = "active"
    where: list[str] = []
    params: list = []
    if status == "active":
        where.append("a.status='active'")
    elif status == "sold":
        where.append("a.status='sold'")
    if district:
        where.append("a.district=?")
        params.append(district)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order = {
        "score": "COALESCE(s.score,0) DESC",
        "discount": "COALESCE(s.net_discount, a.net_discount, 0) DESC",
        "date": "COALESCE(a.deal_date, a.auction_date) ASC",
    }.get(sort, "COALESCE(s.score,0) DESC")
    rows = query_all(
        f"{common.AUCTION_SELECT}{where_sql} ORDER BY {order} LIMIT ?",
        (*params, limit),
    )
    return {"items": [common.normalize_auction(r) for r in rows], "status": status}


@router.get("/{auction_id}")
def auction_detail(auction_id: int):
    row = query_one(f"{common.AUCTION_SELECT} WHERE a.id=?", (auction_id,))
    if not row:
        raise HTTPException(404, "法拍标的")
    item = common.normalize_auction(row)
    score_row = common.get_score_row("auction", auction_id)
    score = common.build_score_payload(score_row, item, "auction")

    start = float(item.get("start_price") or 0)
    tax = float(item.get("tax_est") or 0)
    arrears = float(item.get("arrears_est") or 0)
    repair = float(item.get("repair_reserve") or 0)
    landing = start + tax + arrears + repair

    # 到手价瀑布图数据（前端 ECharts 直接消费）
    waterfall = [
        {"name": "起拍价", "value": start},
        {"name": "税费预估", "value": tax},
        {"name": "欠费预估", "value": arrears},
        {"name": "维修预留", "value": repair},
        {"name": "到手价预估", "value": landing},
    ]

    # 同小区二手可比
    comps = query_all(
        "SELECT id, community_name, unit_price, total_price, area, layout, floor_text "
        "FROM listing WHERE community_name=? AND status='active' ORDER BY unit_price",
        (item.get("community_name"),),
    )
    values = [float(c["unit_price"]) for c in comps if c.get("unit_price")]

    compare = {
        "n": len(values),
        "median_unit": round(sorted(values)[len(values) // 2], 1) if values else 0,
        "items": comps[:20],
    }

    # 成交结果：回填过 deal_price 才组装，用同一套纯函数保证与成交页口径一致
    deal = None
    try:
        if float(item.get("deal_price") or 0) > 0:
            from ..services.deal_pulse import deal_metrics
            deal = deal_metrics(item, compare["median_unit"])
    except (TypeError, ValueError):
        deal = None

    events = query_all(
        "SELECT * FROM anomaly_event WHERE target_type='auction' AND target_id=? ORDER BY event_date DESC",
        (auction_id,),
    )
    for ev in events:
        ev["detail"] = common.loads(ev.get("detail_json"), {})

    return {
        "item": item,
        "score": score,
        "deal": deal,
        "waterfall": waterfall,
        "landing_total": landing,
        "compare": compare,
        "events": events,
        "risk_checklist": common.build_risk_checklist(item, is_auction=True),
        "verify_steps": common.build_verify_steps(is_auction=True),
        "is_watched": bool(item.get("watched")),
    }
