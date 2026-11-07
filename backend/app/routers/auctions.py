"""法拍接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import query_all, query_one
from . import common

router = APIRouter(prefix="/api/auctions", tags=["法拍"])


@router.get("")
def list_auctions(
    district: str | None = None,
    sort: str = Query("score", description="score / discount / date"),
    limit: int = Query(50, ge=1, le=200),
):
    where = ["a.status='active'"]
    params: list = []
    if district:
        where.append("a.district=?")
        params.append(district)
    order = {
        "score": "COALESCE(s.score,0) DESC",
        "discount": "COALESCE(s.net_discount, a.net_discount, 0) DESC",
        "date": "a.auction_date ASC",
    }.get(sort, "COALESCE(s.score,0) DESC")
    rows = query_all(
        f"{common.AUCTION_SELECT} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?",
        (*params, limit),
    )
    return {"items": [common.normalize_auction(r) for r in rows]}


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

    events = query_all(
        "SELECT * FROM anomaly_event WHERE target_type='auction' AND target_id=? ORDER BY event_date DESC",
        (auction_id,),
    )
    for ev in events:
        ev["detail"] = common.loads(ev.get("detail_json"), {})

    return {
        "item": item,
        "score": score,
        "waterfall": waterfall,
        "landing_total": landing,
        "compare": {
            "n": len(values),
            "median_unit": round(sorted(values)[len(values) // 2], 1) if values else 0,
            "items": comps[:20],
        },
        "events": events,
        "risk_checklist": common.build_risk_checklist(item, is_auction=True),
        "verify_steps": common.build_verify_steps(is_auction=True),
        "is_watched": bool(item.get("watched")),
    }
