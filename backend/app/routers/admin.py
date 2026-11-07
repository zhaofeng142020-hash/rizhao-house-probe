"""管理接口：登录、运行状态、企业微信配置、手动触发。

权限模型（简单但够用）：
- 读数据：任何人可看（发给家人朋友看房源没问题）
- 改数据、改配置、改推送：只有持口令的人
口令来源优先级：ADMIN_TOKEN 环境变量 > 数据库 app_setting > 首次自动生成
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..db import query_one
from ..services import auth, backup
from ..services.alert_sender import AlertSender
from ..scheduler import job_snapshot, scheduler_running

router = APIRouter(prefix="/api/admin", tags=["管理"])


# --------------------------------------------------------------------------
# 登录
# --------------------------------------------------------------------------
class LoginIn(BaseModel):
    password: str


# 口令可能被设得比较短（好记），所以这里加一层失败限速：
# 同一 IP 10 分钟内错 5 次就锁 10 分钟。内存实现，单进程有效，够本场景用。
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 300            # 统计窗口（秒）：错 5 次锁 5 分钟，自己打错也不至于等太久
_login_fails: dict[str, list[float]] = {}


def _locked_seconds(key: str) -> int:
    """还剩多少秒不能重试；0 表示可以试。"""
    now = time.time()
    hits = [t for t in _login_fails.get(key, []) if now - t < LOGIN_WINDOW]
    _login_fails[key] = hits
    if len(hits) >= LOGIN_MAX_FAILS:
        return int(LOGIN_WINDOW - (now - hits[0])) + 1
    return 0


@router.post("/login")
def login(payload: LoginIn, request: Request):
    """用口令换 token。前端存 localStorage，之后写操作带 X-Admin-Token。"""
    if not auth.admin_required():
        return {"ok": True, "token": "", "note": "未启用口令校验（ADMIN_ENABLED=0），所有人可写，建议开启"}

    key = (request.client.host if request.client else "?")
    wait = _locked_seconds(key)
    if wait > 0:
        raise HTTPException(429, f"口令错误次数过多，请 {max(1, wait // 60)} 分钟后再试")

    if not auth.verify_token(payload.password):
        _login_fails.setdefault(key, []).append(time.time())
        left = LOGIN_MAX_FAILS - len(_login_fails[key])
        raise HTTPException(401, f"口令不正确（还可再试 {max(0, left)} 次）")

    _login_fails.pop(key, None)
    return {"ok": True, "token": auth.current_token(), "note": "登录成功，本浏览器将记住这个口令"}


@router.get("/auth-check")
def auth_check(x_admin_token: str | None = Header(None, alias="X-Admin-Token")):
    return {
        "required": auth.admin_required(),
        "authed": (not auth.admin_required()) or auth.verify_token(x_admin_token),
    }


# --------------------------------------------------------------------------
# 运行状态：配置在哪、多久更新、现在是什么状态
# --------------------------------------------------------------------------
@router.get("/status")
def status():
    counts = {
        "listing": int(query_one("SELECT COUNT(*) AS n FROM listing WHERE status='active'")["n"]),
        "auction": int(query_one("SELECT COUNT(*) AS n FROM auction WHERE status='active'")["n"]),
        "new_home": int(query_one("SELECT COUNT(*) AS n FROM new_home WHERE status='active'")["n"]),
        "community": int(query_one("SELECT COUNT(*) AS n FROM community")["n"]),
        "score": int(query_one("SELECT COUNT(*) AS n FROM leak_score")["n"]),
        "event": int(query_one("SELECT COUNT(*) AS n FROM anomaly_event")["n"]),
        "alert": int(query_one("SELECT COUNT(*) AS n FROM alert_log")["n"]),
    }
    last_score = query_one("SELECT MAX(computed_at) AS t FROM leak_score")
    last_event = query_one("SELECT MAX(created_at) AS t FROM anomaly_event")
    last_crawl = query_one("SELECT * FROM crawl_log ORDER BY id DESC LIMIT 1")
    jobs = job_snapshot()

    # 下次更新时间：优先取调度器真实计划，调度器关闭时按配置推算
    next_daily = next((j["next_run"] for j in jobs if j["id"] == "daily_pipeline"), None)
    if not next_daily:
        hh, _, mm = settings.DAILY_RUN_AT.partition(":")
        now = datetime.now()
        target = now.replace(hour=int(hh or 2), minute=int(mm or 30), second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        next_daily = target.isoformat()

    sender = AlertSender()
    channels = sender.channel_status()
    active = sender.resolve_channel()

    return {
        "now": datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
        "data": {
            "db_path": str(settings.DB_PATH),
            "last_score_at": (last_score or {}).get("t"),
            "last_event_at": (last_event or {}).get("t"),
            "last_crawl": last_crawl,
        },
        "schedule": {
            "enabled": settings.ENABLE_SCHEDULER,
            "running": scheduler_running(),
            "daily_at": settings.DAILY_RUN_AT,
            "next_daily_run": next_daily,
            "jobs": jobs,
            "weekly": "每周一 08:30 生成观察周报",
        },
        # CRAWL_ENABLED 只控制旧的 crawler/ 采集器；真实房源刷新是独立通道，
        # 由 LIVE_REFRESH_ENABLED / REFRESH_AT 控制。旧文案会让人误以为没在抓，这里如实区分。
        "crawl": {
            "enabled": settings.CRAWL_ENABLED,
            "note": (
                "CRAWL_ENABLED=0 只关闭旧的 crawler/ 采集通道；真实房源刷新走独立通道"
                "（见下方 live_refresh），不受它影响，贝壳 / 安居客 / 58 仍按每天两次抓取。"
                if not settings.CRAWL_ENABLED else
                "CRAWL_ENABLED=1：旧采集通道已开启；真实房源刷新另见 live_refresh。"
            ),
        },
        "live_refresh": {
            "enabled": bool(settings.LIVE_REFRESH_ENABLED and settings.ENABLE_SCHEDULER),
            "times": [t.strip() for t in settings.REFRESH_AT.split(",") if t.strip()],
            "running": scheduler_running(),
            "runs_in_cloud": True,
            "needs_local_pc": False,
            "note": "在云端服务进程内按点抓取三源、入库记调价、重算并推送；本机电脑关机不影响。"
                    "58 会话失效时才需在本机打开一次页面重新同步。",
        },
        "push": {
            "channel_in_use": active.key,
            "channel_label": active.label,
            "channels": channels,
        },
        "security": {
            "admin_required": auth.admin_required(),
            "token_source": "环境变量 ADMIN_TOKEN" if settings.ADMIN_TOKEN else (
                "数据库（自动生成）" if auth.get_setting("admin_token") else "未初始化"
            ),
            "token_file": str(auth.TOKEN_FILE),
        },
    }


# --------------------------------------------------------------------------
# 在线配置（企微 / 公众号 / 高德地图）：随时可填，保存即生效，不用重启
# 字段清单只有一处：services/auth.py 的 INTEGRATION_FIELDS
# --------------------------------------------------------------------------

@router.get("/integration")
def get_integration():
    return {
        **auth.integration_snapshot(),
        "channel_status": AlertSender().channel_status(),
        "how_to": [
            "手机装「企业微信」，用手机号注册（个人也能注册，企业名称随便填，不用营业执照）",
            "建一个内部群（至少 2 人才能建群；建完可以把另一个人移出，群和机器人会保留）",
            "进群 → 右上角「…」→「消息推送」（旧版叫「群机器人」）→ 添加 → 起个名字",
            "点开这个机器人 → 「Webhook 地址」→ 复制，整条粘到上面，点保存",
            "点「发送测试消息」，群里出现卡片即接通；没收到看下面的报错翻译",
            "之后评分达标的房源（默认 ≥60 分红/黄级）会自动推到这个群",
        ],
    }


@router.put("/integration")
def put_integration(payload: dict[str, str | None]):
    """在线保存集成配置。

    这里刻意不用写死字段的 Pydantic 模型：字段清单已经在 auth.INTEGRATION_FIELDS 里
    定义过一次，模型再抄一遍就会出现「表单有、后端静默丢弃」的问题（加地图 Key 时踩过）。
    save_integration 只接受 SETTING_TO_ATTR 里的键，未知键直接忽略。
    """
    saved = auth.save_integration(payload)
    sender = AlertSender()
    return {
        "ok": True,
        "applied": saved,
        **auth.integration_snapshot(),
        "channel_status": sender.channel_status(),
    }


@router.post("/integration/test")
def test_integration():
    # test_channel 是模块级函数（会顺带写一条 alert_log，方便在铃铛里看到）
    from ..services.alert_sender import test_channel as _test

    return _test()


# --------------------------------------------------------------------------
# 手动触发
# --------------------------------------------------------------------------
@router.post("/run-now")
def run_now(push: bool = True):
    from ..services.pipeline import run_daily_pipeline
    summary = run_daily_pipeline(push=push)
    return {"ok": True, **{k: v for k, v in summary.items() if k != "errors"}}


# --------------------------------------------------------------------------
# 数据备份：你自己攒的东西（观察清单 / 偏好 / 推送配置）不能丢
# --------------------------------------------------------------------------
def _require_token(token: str | None) -> None:
    """GET 接口不受写中间件保护，但备份含口令与密钥，必须单独校验。"""
    if auth.admin_required() and not auth.verify_token(token):
        raise HTTPException(401, "需要管理口令")


@router.get("/backup")
def backup_status():
    """备份现状：快照目录、最近几份、各资产表条数。"""
    return backup.backup_status()


@router.get("/backup/export")
def backup_export(x_admin_token: str | None = Header(None, alias="X-Admin-Token")):
    """下载一份资产备份（含口令与企微密钥，属于敏感文件）。"""
    _require_token(x_admin_token)
    data = backup.export_assets()
    from fastapi.responses import JSONResponse

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return JSONResponse(
        content=data,
        headers={
            "Content-Disposition": f'attachment; filename="rizhao-assets-{stamp}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/backup/import")
def backup_import(payload: dict, replace: bool = True):
    """从备份文件恢复。默认整体替换，只动资产表，不碰房源数据。"""
    try:
        return backup.import_assets(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/backup/snapshot")
def backup_snapshot():
    """手动打一份整库快照（每天定时任务里也会自动打）。"""
    path = backup.snapshot(tag="manual")
    if not path:
        raise HTTPException(500, "快照失败，请查看服务端日志")
    return {
        "ok": True,
        "file": path.name,
        "size_kb": round(path.stat().st_size / 1024, 1),
        "snapshots": backup.list_snapshots(),
    }


# --------------------------------------------------------------------------
# 演示数据清除 + 小区档案补全（真实数据落地用）
# --------------------------------------------------------------------------
@router.post("/purge-demo")
def purge_demo():
    """把演示样本全部清除，只留真实来源的数据。

    用户明确要求「换成真实的」之后，保留 220 套假数据只会稀释可信度。
    这里一次清干净：listing / auction / new_home 的 demo 行、随之孤立的
    评分 / 异动 / 价格历史 / 观察项，以及不再被任何真实数据引用的小区。
    清完把 seed_version 置为 purged-demo —— 就算库看起来「空了」也不再重灌。
    """
    from ..db import get_conn

    purged: dict[str, int] = {}
    with get_conn() as conn:
        for table in ("listing", "auction", "new_home"):
            cur = conn.execute(f"DELETE FROM {table} WHERE source_kind='demo'")
            purged[table] = cur.rowcount
        # 孤儿引用清理：房源删了，评分/异动/价格历史/观察项不能留悬空
        for tbl, tt in (("leak_score", "listing"), ("anomaly_event", "listing")):
            conn.execute(
                f"DELETE FROM {tbl} WHERE target_type='{tt}' "
                f"AND target_id NOT IN (SELECT id FROM listing)"
            )
        for tbl, tt in (("leak_score", "auction"), ("anomaly_event", "auction")):
            conn.execute(
                f"DELETE FROM {tbl} WHERE target_type='{tt}' "
                f"AND target_id NOT IN (SELECT id FROM auction)"
            )
        conn.execute("DELETE FROM price_history WHERE listing_id NOT IN (SELECT id FROM listing)")
        conn.execute("DELETE FROM watch_item WHERE target_type='listing' AND target_id NOT IN (SELECT id FROM listing)")
        conn.execute("DELETE FROM watch_item WHERE target_type='auction' AND target_id NOT IN (SELECT id FROM auction)")
        conn.execute("DELETE FROM watch_item WHERE target_type='community' AND target_id NOT IN (SELECT id FROM community)")
        # 小区：只保留仍被真实在售房源 / 法拍 / 新盘引用的
        cur = conn.execute(
            """
            DELETE FROM community WHERE id NOT IN (
                SELECT DISTINCT community_id FROM listing WHERE community_id IS NOT NULL
            )
            AND name NOT IN (SELECT DISTINCT community_name FROM auction WHERE community_name IS NOT NULL)
            AND name NOT IN (SELECT DISTINCT name FROM new_home WHERE name IS NOT NULL)
            """
        )
        purged["community"] = cur.rowcount
    auth.set_setting("seed_version", "purged-demo")
    left = query_one(
        "SELECT (SELECT COUNT(*) FROM listing) AS listing,"
        " (SELECT COUNT(*) FROM auction) AS auction,"
        " (SELECT COUNT(*) FROM new_home) AS new_home,"
        " (SELECT COUNT(*) FROM community) AS community"
    )
    return {"ok": True, "purged": purged, "left": left}


class CommunityUpsertRow(BaseModel):
    name: str
    district: str | None = None
    lat: float | None = None
    lng: float | None = None
    build_year: int | None = None
    address: str | None = None
    xiaoqu_id: str | None = None


class CommunityUpsertIn(BaseModel):
    rows: list[CommunityUpsertRow]


# 日照市域粗界：坐标超出就当脏数据拒收，防止把外地小区画进日照地图
_LAT_RANGE = (34.0, 37.5)
_LNG_RANGE = (117.0, 121.0)


@router.post("/communities/upsert")
def upsert_communities(payload: CommunityUpsertIn):
    """批量补全小区档案（坐标 / 板块 / 建成年 / 地址）。

    只填空缺、按名匹配，不覆盖已有真实档案；小区不存在则新建。
    """
    from ..db import get_conn

    updated = created = skipped = 0
    with get_conn() as conn:
        for r in payload.rows:
            name = (r.name or "").strip()
            if not name:
                skipped += 1
                continue
            lat = lng = None
            if r.lat is not None and r.lng is not None:
                if _LAT_RANGE[0] <= r.lat <= _LAT_RANGE[1] and _LNG_RANGE[0] <= r.lng <= _LNG_RANGE[1]:
                    lat, lng = r.lat, r.lng
                else:
                    skipped += 1
                    continue
            found = conn.execute(
                "SELECT id, lat, lng, district, build_year FROM community WHERE name=? ORDER BY id LIMIT 1",
                (name,),
            ).fetchone()
            row = found or {"id": None, "lat": None, "lng": None,
                            "district": None, "build_year": None}
            sets, params = [], []
            if lat is not None and not row["lat"]:
                sets += ["lat=?", "lng=?"]
                params += [lat, lng]
            if r.district and (not found or not row["district"] or row["district"] == "待核实"):
                sets.append("district=?")
                params.append(r.district)
            if r.build_year and not row["build_year"]:
                sets.append("build_year=?")
                params.append(r.build_year)
            if found:
                if sets:
                    params.append(found["id"])
                    conn.execute(f"UPDATE community SET {', '.join(sets)} WHERE id=?", params)
                    updated += 1
                else:
                    skipped += 1
            else:
                cols = "name,district,lat,lng,build_year,is_extended,has_elevator,liquidity_score,deal_active_90d"
                cur = conn.execute(
                    f"INSERT INTO community ({cols}) VALUES (?,?,?,?,?,0,1,50,0)",
                    (name, r.district or "待核实", lat, lng, r.build_year),
                )
                created += 1
    total = query_one("SELECT COUNT(*) AS n FROM community WHERE lat IS NOT NULL")
    return {"ok": True, "updated": updated, "created": created, "skipped": skipped,
            "communities_with_geo": int(total["n"] if total else 0)}


# --------------------------------------------------------------------------
# 真实房源刷新（每天两次的自动任务也可手动触发）
# --------------------------------------------------------------------------
# 刷新是长任务（抓 8 页 + 入库 + 重算，通常 60~120 秒），
# 网关只有 60 秒超时，所以默认放后台跑，前端轮询 /refresh-status。
_REFRESH_STATE: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "result": None, "error": None,
}

# 手动 / 扩展触发的冷却：58 对「短时间连续访问列表页」很敏感，
# 页面反复加载（或扩展重载）不能连着触发整轮抓取，否则正是「访问过于频繁」验证码的成因。
# 定时任务直接调用 refresh_all()，不经过这个端点，因此不受冷却影响。
_REFRESH_COOLDOWN_SECONDS = 600


def _cooldown_left() -> int:
    """距上次刷新结束还剩多少秒冷却；返回 0 表示现在可以刷新。"""
    from datetime import datetime

    finished = _REFRESH_STATE.get("finished_at")
    if not finished:
        return 0
    try:
        t = datetime.strptime(finished, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return 0
    return max(0, int(_REFRESH_COOLDOWN_SECONDS - (datetime.now() - t).total_seconds()))


def _do_refresh(pages: int | None, crawl: bool = True) -> None:
    from datetime import datetime

    from ..services.live_refresh import refresh_all
    from ..services.pipeline import run_daily_pipeline

    try:
        if crawl:
            report = refresh_all(pages)
        else:
            # 浏览器刚把「你已经看到」的房源回传进来，此刻只需要重算 + 推送，
            # 不要再抓一次三个站点（尤其是 58）。定时抓取仍按 07:30 / 19:30 执行。
            report = {
                "sources": {}, "errors": [],
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "note": "仅重算与推送：本次未抓取外部站点（房源由网页端回传）",
            }
        try:
            summary = run_daily_pipeline(push=True)
        except Exception as exc:  # noqa: BLE001 抓取成功也要留下结果
            summary = {"error": f"{type(exc).__name__}: {exc}"}
        pipeline_view = {k: summary.get(k) for k in
                         ("listing_total", "scored", "events", "red", "yellow", "pushed")}
        _REFRESH_STATE["result"] = {"refresh": report, "pipeline": pipeline_view}
        try:
            from ..services.live_refresh import push_refresh_summary
            push_refresh_summary(report, pipeline_view)
        except Exception:  # noqa: BLE001
            logger.exception("刷新摘要推送失败")
    except Exception as exc:  # noqa: BLE001
        _REFRESH_STATE["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _REFRESH_STATE["running"] = False
        _REFRESH_STATE["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@router.post("/refresh-sources")
def refresh_sources(pages: int | None = None, wait: bool = False,
                    crawl: bool = True, force: bool = False):
    """立刻抓一次贝壳 + 安居客 + 58 同城（入库记调价）并重算。

    默认异步：立即返回 started，前端轮询 /refresh-status。
    - crawl=false：只重算 + 推送，不抓外部站点。网页端回传房源后用这个，
      避免页面反复加载连着抓三次 58、触发「访问过于频繁」验证。
    - force=true：跳过 10 分钟冷却（管理台「立即刷新」按钮带此参数）。
    - wait=true：同步执行（本地脚本/调试用，注意网关 60s 超时），同样跳过冷却。
    """
    import threading
    from datetime import datetime

    if _REFRESH_STATE["running"]:
        return {"ok": True, "running": True, "note": "已在刷新中，请稍后查看状态"}
    left = 0 if (force or wait) else _cooldown_left()
    if left > 0:
        return {"ok": True, "skipped": True, "running": False,
                "cooldown_seconds": left,
                "note": f"距上次刷新不足 10 分钟，{left} 秒后可再触发（每日定时任务不受影响）"}
    _REFRESH_STATE.update({
        "running": True, "result": None, "error": None,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # finished_at 保留为「上次完成时间」，供冷却判断，不在这里清空
    })
    if wait:
        _do_refresh(pages, crawl)
        return {"ok": True, "running": False, **_REFRESH_STATE}
    threading.Thread(target=_do_refresh, args=(pages, crawl), daemon=True).start()
    note = "已开始刷新，完成后 /refresh-status 可查" if crawl else \
        "已开始重算与推送（本次不抓取外部站点）"
    return {"ok": True, "started": True, "crawl": crawl, "note": note}


@router.get("/refresh-status")
def refresh_status():
    """刷新进度 / 上次结果。前端按钮用它做轮询。"""
    return {"ok": True, **_REFRESH_STATE}


@router.get("/net-check")
def net_check():
    """服务端出网自检：能不能从这里访问到房源站点。

    线上托管环境若不允许出网，每日自动抓取就无法在服务端完成，
    需要改用本地定时任务推送。这个接口让这件事有明确答案，而不是靠猜。
    """
    import time
    import urllib.request

    from ..services.live_refresh import DESKTOP_UA, MOBILE_UA

    targets = [
        ("贝壳列表", "https://rz.ke.com/ershoufang/", DESKTOP_UA),
        ("安居客移动站", "https://m.anjuke.com/rz/sale/", MOBILE_UA),
        ("58同城", "https://rizhao.58.com/ershoufang/", DESKTOP_UA),
    ]
    out = []
    for name, url, ua in targets:
        t0 = time.time()
        item = {"name": name, "url": url}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read()
            item.update({
                "ok": True, "status": r.status, "final": r.geturl(),
                "bytes": len(body), "ms": int((time.time() - t0) * 1000),
            })
        except Exception as exc:  # noqa: BLE001
            item.update({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                         "ms": int((time.time() - t0) * 1000)})
        out.append(item)
    return {"ok": all(x["ok"] for x in out), "targets": out}


class SessionCookieIn(BaseModel):
    cookie: str


@router.put("/sources/{key}/cookies")
def save_session_cookie(key: str, payload: SessionCookieIn):
    """保存「用户自己在浏览器里正常访问后」的会话 Cookie。

    这是自动抓取的合法前提：验证码由人完成，程序只复用他的会话。
    程序不会识别、破解任何验证码。
    """
    from ..services.live_refresh import set_session_cookie

    return set_session_cookie(key, payload.cookie)


@router.get("/sources/cookies")
def list_session_cookies():
    from ..services.live_refresh import get_session_cookie

    out = {}
    for key in ("ke", "anjuke", "wuba"):
        c = get_session_cookie(key)
        out[key] = {"has_cookie": bool(c), "length": len(c)}
    return {"ok": True, "sources": out}


@router.get("/sources/session-check")
def session_check(key: str = "wuba"):
    """检查某通道保存的会话现在是否还能免验证抓到页面。"""
    from ..services.live_refresh import check_session
    try:
        return {"ok": True, **check_session(key)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/official-deals")
def official_deals(kind: str = "sign"):
    """政府公开签约/备案公示（免验证码、全自动的另一种数据通道）。"""
    from ..services.live_refresh import fetch_official_deals
    try:
        return {"ok": True, **fetch_official_deals(kind, 1)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# --------------------------------------------------------------------------
# 法拍成交结果回填：日照没有公开成交页，法拍是少数能拿到「真实成交价」的
# 公开渠道。拍卖结束后把成交价录进来，就成了真实价格锚点（可手动/CSV 回填）。
# --------------------------------------------------------------------------
class AuctionResultIn(BaseModel):
    deal_price: float
    deal_date: str | None = None


@router.post("/auctions/{auction_id}/result")
def save_auction_result(auction_id: int, payload: AuctionResultIn):
    """回填某场法拍的实际成交价，并写入一条 auction_result 异动（真实锚点）。"""
    from ..db import execute, query_one
    from ..services.anomaly import build_event_key
    from ..services.pipeline import insert_event

    au = query_one("SELECT * FROM auction WHERE id=?", (auction_id,))
    if not au:
        raise HTTPException(404, "法拍房源不存在")
    if payload.deal_price is None or float(payload.deal_price) <= 0:
        raise HTTPException(400, "成交价必须为正数")
    deal_price = float(payload.deal_price)
    deal_date = (payload.deal_date or date.today().isoformat())[:10]
    execute(
        "UPDATE auction SET deal_price=?,deal_date=?,status='sold' WHERE id=?",
        (deal_price, deal_date, auction_id),
    )
    appraisal = float(au.get("appraisal_price") or 0)
    start = float(au.get("start_price") or 0)
    area = float(au.get("area") or 0)
    detail = {
        "deal_price": deal_price,
        "deal_date": deal_date,
        "deal_discount_pct": round((1 - deal_price / appraisal) * 100, 1) if appraisal else None,
        "over_start_pct": round((deal_price - start) / start * 100, 1) if start else None,
    }
    insert_event({
        "event_key": build_event_key("auction_result", "auction", auction_id, date.today()),
        "event_date": date.today().isoformat(),
        "target_type": "auction",
        "target_id": auction_id,
        "community_name": au.get("community_name"),
        "district": au.get("district"),
        "event_type": "auction_result",
        "severity": "white",
        "total_price": deal_price,
        "unit_price": round(deal_price / area) if area else None,
        "ref_unit_price": au.get("comparable_unit_price"),
        "drop_pct": round(-(detail["deal_discount_pct"] or 0) / 100, 4),
        "detail": detail,
    })
    return {"ok": True, "auction_id": auction_id, **detail}
