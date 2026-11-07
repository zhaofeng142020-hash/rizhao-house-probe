"""异动接口：事件列表 / 今日可留意 / 异动汇总。"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query

from ..db import query_all, query_one
from ..services.anomaly import EVENT_LABELS
from . import common

router = APIRouter(prefix="/api/anomalies", tags=["异动"])


@router.get("/drop-timing")
def drop_timing(days: int = 365):
    """降价时机规律：近 N 天降价按月份/星期的分布（观察口径）。"""
    from ..services.anomaly import drop_timing as _timing
    return _timing(max(90, min(days, 1095)))


@router.get("")
def list_events(
    event_type: str | None = Query(None, description="sudden_drop/deep_dip/long_listed/miskill/board_divergence/new_home/auction_deal"),
    severity: str | None = None,
    district: str | None = None,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=300),
):
    since = (date.today() - timedelta(days=days)).isoformat()
    where = ["e.event_date >= ?"]
    params: list = [since]
    if event_type:
        where.append("e.event_type=?")
        params.append(event_type)
    if severity:
        where.append("e.severity=?")
        params.append(severity)
    if district:
        where.append("e.district=?")
        params.append(district)

    rows = query_all(
        f"""
        SELECT e.*, s.score, s.confidence,
               COALESCE(l.layout, a.layout) AS layout,
               COALESCE(l.area, a.area) AS area,
               COALESCE(l.total_price, a.landing_price) AS price_now,
               COALESCE(l.price_changes, 0) AS price_changes,
               COALESCE(l.list_days, 0) AS list_days,
               COALESCE(l.consecutive_drops, 0) AS consecutive_drops
        FROM anomaly_event e
        LEFT JOIN leak_score s ON s.target_type=e.target_type AND s.target_id=e.target_id
        LEFT JOIN listing l ON e.target_type='listing' AND l.id=e.target_id
        LEFT JOIN auction a ON e.target_type='auction' AND a.id=e.target_id
        WHERE {' AND '.join(where)}
        ORDER BY CASE e.severity WHEN 'red' THEN 0 WHEN 'yellow' THEN 1 ELSE 2 END,
                 COALESCE(e.drop_pct,0) DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    for r in rows:
        r["detail"] = common.loads(r.pop("detail_json", None), {})
        r["event_label"] = EVENT_LABELS.get(r["event_type"], r["event_type"])
    return {"total": len(rows), "items": rows, "labels": EVENT_LABELS}


@router.get("/summary")
def event_summary(days: int = Query(7, ge=1, le=90)):
    since = (date.today() - timedelta(days=days)).isoformat()
    by_type = query_all(
        "SELECT event_type, COUNT(*) AS n, "
        "SUM(CASE WHEN severity='red' THEN 1 ELSE 0 END) AS red, "
        "SUM(CASE WHEN severity='yellow' THEN 1 ELSE 0 END) AS yellow "
        "FROM anomaly_event WHERE event_date>=? GROUP BY event_type",
        (since,),
    )
    for r in by_type:
        r["label"] = EVENT_LABELS.get(r["event_type"], r["event_type"])
    return {
        "days": days,
        "by_type": by_type,
        "red": int(query_one("SELECT COUNT(*) AS n FROM anomaly_event WHERE event_date>=? AND severity='red'", (since,))["n"]),
        "yellow": int(query_one("SELECT COUNT(*) AS n FROM anomaly_event WHERE event_date>=? AND severity='yellow'", (since,))["n"]),
        "white": int(query_one("SELECT COUNT(*) AS n FROM anomaly_event WHERE event_date>=? AND severity='white'", (since,))["n"]),
    }


@router.get("/today")
def today_picks(
    limit: int = Query(12, ge=1, le=50),
    grade: str | None = Query(None, description="red / yellow / 空=红+黄"),
):
    """首页「今日可留意」：按 leak_score 排序，默认只出红/黄。"""
    where = ["l.status='active'", "s.score IS NOT NULL"]
    params: list = []
    if grade:
        where.append("s.grade=?")
        params.append(grade)
    else:
        where.append("s.grade IN ('red','yellow')")
    rows = query_all(
        f"{common.LISTING_SELECT} WHERE {' AND '.join(where)} "
        "ORDER BY s.score DESC, s.deviation_pct DESC LIMIT ?",
        (*params, limit),
    )
    auctions = query_all(
        f"{common.AUCTION_SELECT} WHERE s.score IS NOT NULL AND s.grade IN ('red','yellow') "
        "ORDER BY s.score DESC LIMIT ?",
        (max(3, limit // 3),),
    )
    return {
        "listings": [common.normalize_listing(r) for r in rows],
        "auctions": [common.normalize_auction(r) for r in auctions],
        "date": date.today().isoformat(),
    }


@router.get("/ranking")
def ranking(limit: int = Query(20, ge=1, le=100), include_white: bool = False):
    """捡漏排行：二手房 + 法拍混排。"""
    grade_cond = "" if include_white else " AND s.grade IN ('red','yellow')"
    listings = query_all(
        f"{common.LISTING_SELECT} WHERE l.status='active' AND s.score IS NOT NULL{grade_cond} "
        "ORDER BY s.score DESC LIMIT ?",
        (limit,),
    )
    auctions = query_all(
        f"{common.AUCTION_SELECT} WHERE s.score IS NOT NULL{grade_cond} "
        "ORDER BY s.score DESC LIMIT ?",
        (limit,),
    )
    mixed = (
        [{"kind": "listing", **common.normalize_listing(r)} for r in listings]
        + [{"kind": "auction", **common.normalize_auction(r)} for r in auctions]
    )
    mixed.sort(key=lambda x: -(x.get("score") or 0))
    return {"items": mixed[:limit]}
