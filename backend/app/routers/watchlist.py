"""观察清单接口：收藏 / 跟进状态 / 备注 / 价格曲线。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import execute, get_conn, query_all, query_one
from ..services.pipeline import build_weekly_report
from . import common

router = APIRouter(prefix="/api/watchlist", tags=["观察清单"])

FOLLOW_STATUS = ["watching", "contacted", "viewed", "rejected", "done"]


class WatchIn(BaseModel):
    target_type: str = Field("listing", pattern="^(listing|auction|community)$")
    target_id: int
    note: str | None = None


class WatchPatch(BaseModel):
    note: str | None = None
    follow_status: str | None = None


def _price_of(target_type: str, target_id: int) -> tuple[float, str, str]:
    if target_type == "listing":
        row = query_one("SELECT total_price, community_name, district FROM listing WHERE id=?", (target_id,))
        return (float((row or {}).get("total_price") or 0),
                (row or {}).get("community_name") or "", (row or {}).get("district") or "")
    if target_type == "community":
        # 小区关注跟踪的是「中位挂牌单价」，不是某一套房的总价
        c = query_one("SELECT name,district FROM community WHERE id=?", (target_id,))
        snap = query_one(
            "SELECT median_unit FROM community_price_snapshot WHERE community_id=? "
            "ORDER BY observed_date DESC LIMIT 1", (target_id,),
        )
        if not snap:
            from ..services.community_trend import snapshot_all
            snapshot_all()
            snap = query_one(
                "SELECT median_unit FROM community_price_snapshot WHERE community_id=? "
                "ORDER BY observed_date DESC LIMIT 1", (target_id,),
            )
        return (float((snap or {}).get("median_unit") or 0),
                (c or {}).get("name") or "", (c or {}).get("district") or "")
    row = query_one("SELECT landing_price, community_name, district FROM auction WHERE id=?", (target_id,))
    return (float((row or {}).get("landing_price") or 0),
            (row or {}).get("community_name") or "", (row or {}).get("district") or "")


@router.get("")
def list_watch():
    rows = query_all(
        """
        SELECT w.*, s.score, s.grade, s.confidence, s.deviation_pct, s.why_cheap, s.why_trap,
               COALESCE(l.layout, a.layout) AS layout,
               COALESCE(l.area, a.area) AS area,
               COALESCE(l.unit_price, a.landing_price / NULLIF(a.area,0)) AS unit_price,
               COALESCE(l.price_changes,0) AS price_changes,
               COALESCE(l.list_days,0) AS list_days,
               COALESCE(l.consecutive_drops,0) AS consecutive_drops,
               COALESCE(l.defect_tags, a.risk_tags) AS tags,
               cm.name AS community_display_name
        FROM watch_item w
        LEFT JOIN leak_score s ON s.target_type=w.target_type AND s.target_id=w.target_id
        LEFT JOIN listing l ON w.target_type='listing' AND l.id=w.target_id
        LEFT JOIN auction a ON w.target_type='auction' AND a.id=w.target_id
        LEFT JOIN community cm ON w.target_type='community' AND cm.id=w.target_id
        ORDER BY COALESCE(s.score,0) DESC, w.id DESC
        """
    )
    for r in rows:
        base = float(r.get("base_price") or 0)
        last = float(r.get("last_price") or base)
        r["change_pct"] = round((last - base) / base, 4) if base > 0 else 0.0
        r["tags"] = common.loads(r.get("tags"), [])
        if r["target_type"] == "community":
            r["community_name"] = r.get("community_display_name") or r.get("community_name")
            r["unit_label"] = "元/㎡"
        else:
            r["unit_label"] = "总价"
        r["curve"] = _price_curve(r["target_type"], r["target_id"])
    return {"items": rows, "status_options": FOLLOW_STATUS}


def _price_curve(target_type: str, target_id: int) -> list[dict]:
    if target_type == "community":
        rows = query_all(
            "SELECT observed_date AS date, median_unit AS price FROM community_price_snapshot "
            "WHERE community_id=? ORDER BY observed_date", (target_id,),
        )
        return [{"date": r["date"], "price": float(r["price"])} for r in rows if r.get("price")]
    if target_type != "listing":
        row = query_one("SELECT start_price, landing_price, auction_date FROM auction WHERE id=?", (target_id,))
        if not row:
            return []
        return [
            {"date": "起拍", "price": float(row["start_price"] or 0)},
            {"date": "到手价预估", "price": float(row["landing_price"] or 0)},
        ]
    rows = query_all(
        "SELECT observed_at AS date, price FROM price_history WHERE listing_id=? ORDER BY observed_at",
        (target_id,),
    )
    return [{"date": r["date"], "price": float(r["price"])} for r in rows]


@router.post("")
def add_watch(payload: WatchIn):
    price, community, district = _price_of(payload.target_type, payload.target_id)
    if price <= 0 and payload.target_type != "community":
        raise HTTPException(404, "目标不存在")
    execute(
        "INSERT INTO watch_item (target_type,target_id,community_name,district,note,follow_status,"
        "base_price,last_price) VALUES (?,?,?,?,?,'watching',?,?) "
        "ON CONFLICT(target_type,target_id) DO UPDATE SET note=excluded.note",
        (payload.target_type, payload.target_id, community, district, payload.note, price, price),
    )
    return {"ok": True, "item": query_one(
        "SELECT * FROM watch_item WHERE target_type=? AND target_id=?",
        (payload.target_type, payload.target_id))}


@router.patch("/{item_id}")
def update_watch(item_id: int, payload: WatchPatch):
    if payload.follow_status and payload.follow_status not in FOLLOW_STATUS:
        raise HTTPException(400, f"follow_status 必须是 {FOLLOW_STATUS}")
    sets, params = [], []
    if payload.note is not None:
        sets.append("note=?")
        params.append(payload.note)
    if payload.follow_status is not None:
        sets.append("follow_status=?")
        params.append(payload.follow_status)
    if not sets:
        raise HTTPException(400, "没有要更新的字段")
    params.append(item_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE watch_item SET {', '.join(sets)} WHERE id=?", params)
    return {"ok": True, "item": query_one("SELECT * FROM watch_item WHERE id=?", (item_id,))}


@router.delete("/{item_id}")
def remove_watch(item_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM watch_item WHERE id=?", (item_id,))
    return {"ok": True}


@router.get("/weekly-report")
def weekly_report():
    return build_weekly_report()
