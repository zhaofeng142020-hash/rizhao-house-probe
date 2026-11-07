"""云端定时任务的兜底：有人打开网站时，发现漏跑就自动补一次。

为什么要这一层：定时刷新（APScheduler）跑在云端 **服务进程内存里**。
如果托管平台把闲置容器回收/休眠，进程没了，07:30 / 19:30 的任务也就不会触发。
但只要有人（用手机也行）打开一次网站，这里就会检查「距上次成功刷新多久」，
超过阈值就后台补跑完整的一轮：抓三源 → 入库记调价 → 重算 → 推送。

这样即使容器重启过，也不依赖本机电脑开机、不需要额外服务器或定时任务。
检查本身只读一条配置，且 5 分钟节流一次，不会给每次请求添负担。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger("rzprobe.freshness")

# 定时刷新间隔 12 小时（07:30 / 19:30），超过 20 小时视为至少漏跑了一轮
STALE_AFTER = timedelta(hours=20)
CHECK_GAP = timedelta(minutes=5)
ATTEMPT_GAP = timedelta(minutes=30)

_LOCK = threading.Lock()
_last_check: datetime | None = None
_last_attempt: datetime | None = None


def last_refresh_at() -> datetime | None:
    """从持久化的 app_setting 里读上次成功刷新时间（容器重启也还在）。"""
    from . import auth

    raw = auth.get_setting("refresh_last")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return datetime.strptime(data["at"], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def is_stale(now: datetime | None = None) -> bool:
    """是否漏跑了刷新。全新部署（没有历史记录）不算漏跑，交给首次定时任务。"""
    at = last_refresh_at()
    if at is None:
        return False
    return (now or datetime.now()) - at > STALE_AFTER


def _run_catchup() -> None:
    # 延迟 import：避免 services 与 routers 在模块加载期循环依赖
    from ..routers.admin import _REFRESH_STATE, _do_refresh

    if _REFRESH_STATE.get("running"):
        logger.info("已有刷新在跑，跳过补抓")
        return
    _REFRESH_STATE.update({
        "running": True, "result": None, "error": None,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    try:
        _do_refresh(None, True)
        logger.info("访问触发的补抓完成")
    except Exception:  # noqa: BLE001 兜底失败不能影响正常访问
        logger.exception("访问触发的补抓失败")
        _REFRESH_STATE["running"] = False


def maybe_catchup(now: datetime | None = None) -> dict:
    """只读接口调用：非阻塞，立即返回状态字典；需要补抓时起后台线程。"""
    global _last_check, _last_attempt

    now = now or datetime.now()
    with _LOCK:
        if _last_check and now - _last_check < CHECK_GAP:
            return {"triggered": False, "checked": False}
        _last_check = now

    if not is_stale(now):
        return {"triggered": False, "checked": True}

    with _LOCK:
        if _last_attempt and now - _last_attempt < ATTEMPT_GAP:
            return {"triggered": False, "checked": True, "note": "补抓近期已尝试过"}
        _last_attempt = now

    threading.Thread(target=_run_catchup, daemon=True).start()
    logger.warning("距上次刷新已超过 %s 小时，访问触发补抓", STALE_AFTER.total_seconds() / 3600)
    return {"triggered": True, "checked": True, "note": "已后台补跑一轮抓取/重算/推送"}
