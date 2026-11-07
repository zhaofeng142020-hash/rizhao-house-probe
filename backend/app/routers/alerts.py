"""微信推送接口：配置 / 通道状态 / 测试 / 日志（站内铃铛）/ 手动推送 / 手动跑流水线。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import get_conn, query_all, query_one
from ..services import alert_sender
from ..services.pipeline import run_daily_pipeline

router = APIRouter(prefix="/api/alerts", tags=["微信推送"])


class ConfigIn(BaseModel):
    enabled: int | None = None
    channel: str | None = None
    min_score: float | None = None
    push_red: int | None = None
    push_yellow: int | None = None
    red_realtime: int | None = None
    yellow_daily: int | None = None
    quiet_hours: str | None = None
    dedup_hours: int | None = None


@router.get("/config")
def get_config():
    cfg = alert_sender.load_config()
    sender = alert_sender.AlertSender(cfg)
    return {
        "config": cfg,
        "channels": sender.channel_status(),
        "active_channel": sender.resolve_channel().key,
        "quiet_now": sender.in_quiet_hours(),
        "env_ready": {
            "wecom_webhook": bool(alert_sender.settings.WECOM_WEBHOOK),
            "wecom_app": bool(alert_sender.settings.WECOM_CORP_ID and alert_sender.settings.WECOM_AGENT_SECRET),
            "mp_template": bool(alert_sender.settings.MP_APPID and alert_sender.settings.MP_TEMPLATE_ID),
            "telegram": bool(alert_sender.settings.TELEGRAM_BOT_TOKEN),
            "email": bool(alert_sender.settings.SMTP_HOST),
        },
    }


@router.put("/config")
def put_config(payload: ConfigIn):
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    if patch.get("channel") and patch["channel"] not in (
        "auto", "wecom_webhook", "wecom_app", "mp_template", "telegram", "email", "log"
    ):
        raise HTTPException(400, "不支持的通道")
    if patch.get("quiet_hours") and "-" not in patch["quiet_hours"]:
        raise HTTPException(400, "静默时段格式应为 23:00-07:00")
    return {"ok": True, "config": alert_sender.save_config(patch)}


@router.post("/test")
def test_push():
    """发一条测试消息。未配置微信时不报错，走站内降级。"""
    return alert_sender.test_channel()


@router.get("/logs")
def logs(limit: int = Query(50, ge=1, le=200), unread_only: bool = False):
    where = "WHERE is_read=0" if unread_only else ""
    rows = query_all(f"SELECT * FROM alert_log {where} ORDER BY id DESC LIMIT ?", (limit,))
    unread = query_one("SELECT COUNT(*) AS n FROM alert_log WHERE is_read=0 AND status IN ('sent','logged')")
    return {"items": rows, "unread": int((unread or {}).get("n") or 0)}


@router.post("/logs/read-all")
def read_all():
    with get_conn() as conn:
        conn.execute("UPDATE alert_log SET is_read=1 WHERE is_read=0")
    return {"ok": True}


@router.post("/push/{target_type}/{target_id}")
def push_one(target_type: str, target_id: int, force: bool = True):
    """手动推送某条房源/法拍（调试用，force=True 跳过静默时段）。"""
    if target_type not in ("listing", "auction"):
        raise HTTPException(400, "target_type 只能是 listing / auction")
    score_row = query_one(
        "SELECT * FROM leak_score WHERE target_type=? AND target_id=?", (target_type, target_id)
    )
    if not score_row:
        raise HTTPException(404, "该对象还没有评分，请先运行流水线")
    table = "listing" if target_type == "listing" else "auction"
    item = query_one(f"SELECT * FROM {table} WHERE id=?", (target_id,))
    if not item:
        raise HTTPException(404, "对象不存在")

    score = {
        "score": score_row["score"], "grade": score_row["grade"],
        "confidence": score_row["confidence"], "deviation_pct": score_row["deviation_pct"],
        "why_cheap": score_row["why_cheap"], "why_trap": score_row["why_trap"],
        "why_good": score_row["why_good"], "median_unit": None,
        "risk_level": "low" if score_row["s_defect"] >= 75 else "mid",
        "landing_price": item.get("landing_price"), "landing_unit": None,
        "start_discount": item.get("start_discount"), "net_discount": item.get("net_discount"),
    }
    return alert_sender.AlertSender().push_target(target_type, item, score) | {"forced": force}


@router.post("/run-pipeline")
def run_pipeline(push: bool = True):
    """手动触发一次增量计算（等同于凌晨定时任务）。"""
    return run_daily_pipeline(push=push)
