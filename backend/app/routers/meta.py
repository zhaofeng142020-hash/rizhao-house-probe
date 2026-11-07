"""基础信息接口：仪表盘、板块概览、小区、用户偏好、抓取日志。"""
from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import settings
from ..db import get_conn, query_all, query_one
from . import common

router = APIRouter(prefix="/api/meta", tags=["基础信息"])


@router.get("/health")
def health():
    return {
        "ok": True,
        "db": str(settings.DB_PATH),
        "crawl_enabled": settings.CRAWL_ENABLED,
        "scheduler": settings.ENABLE_SCHEDULER,
        "amap_key_ready": bool(settings.AMAP_KEY),
        "today": date.today().isoformat(),
    }


@router.get("/dashboard")
def dashboard():
    # 首页被打开时也做一次漏跑自检：云端容器重启过就补抓一轮（只读、非阻塞）
    from ..services.freshness import maybe_catchup

    maybe_catchup()
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    counts = {
        "listings": int(query_one("SELECT COUNT(*) AS n FROM listing WHERE status='active'")["n"]),
        "auctions": int(query_one("SELECT COUNT(*) AS n FROM auction WHERE status='active'")["n"]),
        "communities": int(query_one("SELECT COUNT(*) AS n FROM community")["n"]),
        "events_7d": int(query_one("SELECT COUNT(*) AS n FROM anomaly_event WHERE event_date>=?", (week_ago,))["n"]),
        "red": int(query_one("SELECT COUNT(*) AS n FROM leak_score WHERE grade='red'")["n"]),
        "yellow": int(query_one("SELECT COUNT(*) AS n FROM leak_score WHERE grade='yellow'")["n"]),
        "watched": int(query_one("SELECT COUNT(*) AS n FROM watch_item")["n"]),
        "unread_alerts": int(query_one(
            "SELECT COUNT(*) AS n FROM alert_log WHERE is_read=0 AND status IN ('sent','logged')")["n"]),
    }
    top = query_all(
        f"{common.LISTING_SELECT} WHERE l.status='active' AND s.grade IN ('red','yellow') "
        "ORDER BY s.score DESC LIMIT 5"
    )
    recent_events = query_all(
        "SELECT e.*, s.score FROM anomaly_event e "
        "LEFT JOIN leak_score s ON s.target_type=e.target_type AND s.target_id=e.target_id "
        "ORDER BY e.event_date DESC, CASE e.severity WHEN 'red' THEN 0 WHEN 'yellow' THEN 1 ELSE 2 END "
        "LIMIT 10"
    )
    for ev in recent_events:
        ev["detail"] = common.loads(ev.pop("detail_json", None), {})
        from ..services.anomaly import EVENT_LABELS
        ev["event_label"] = EVENT_LABELS.get(ev["event_type"], ev["event_type"])

    last_crawl = query_one("SELECT * FROM crawl_log ORDER BY id DESC LIMIT 1")
    return {
        "counts": counts,
        "top": [common.normalize_listing(r) for r in top],
        "recent_events": recent_events,
        "districts": common.district_overview(),
        "last_crawl": last_crawl,
        "today": today.isoformat(),
    }


@router.get("/districts")
def districts():
    return {"items": common.district_overview()}


@router.get("/communities")
def communities(district: str | None = None):
    where, params = "", []
    if district:
        where = "WHERE district=?"
        params.append(district)
    rows = query_all(
        f"""
        SELECT c.*,
               (SELECT COUNT(*) FROM listing l WHERE l.community_id=c.id AND l.status='active') AS listing_n,
               (SELECT ROUND(AVG(l.unit_price)) FROM listing l WHERE l.community_id=c.id AND l.status='active') AS avg_unit
        FROM community c {where} ORDER BY avg_unit DESC
        """,
        tuple(params),
    )
    return {"items": rows}


# --------------------------------------------------------------------------
# 用户偏好（影响「自住适配」维度）
# --------------------------------------------------------------------------
class PrefIn(BaseModel):
    district_pref: list[str] | None = None
    area_min: float | None = None
    area_max: float | None = None
    rooms_prefer: int | None = None
    need_elevator: int | None = None
    avoid_top_floor: int | None = None
    max_age: int | None = None
    care_school: int | None = None


@router.get("/pref")
def get_pref():
    row = query_one("SELECT * FROM user_pref WHERE id=1") or {}
    row["district_pref"] = common.loads(row.get("district_pref"), [])
    return row


@router.put("/pref")
def put_pref(payload: PrefIn):
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "district_pref" in patch:
        patch["district_pref"] = json.dumps(patch["district_pref"], ensure_ascii=False)
    if patch:
        with get_conn() as conn:
            conn.execute(
                f"UPDATE user_pref SET {', '.join(f'{k}=?' for k in patch)}, "
                f"updated_at=datetime('now','localtime') WHERE id=1",
                list(patch.values()),
            )
    return {"ok": True, "pref": get_pref()}


@router.get("/crawl-logs")
def crawl_logs(limit: int = 30):
    return {"items": query_all("SELECT * FROM crawl_log ORDER BY id DESC LIMIT ?", (limit,))}
