"""日照房价探针 / 捡漏雷达 —— FastAPI 入口。

启动即自动完成：建表 → 空库灌演示数据 → 跑一次增量计算 → 起定时任务。
前端构建产物存在时（frontend/dist）会一起托管，单端口 8000 即可访问全部功能。

一句话原则：先把低价当嫌疑，再判断是否值得看。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, settings
from .db import init_db, query_one
from .routers import (
    admin, alerts, anomalies, auctions, communities, listings, market, meta, newhomes, reports,
    sources, target, watchlist,
)
from .scheduler import shutdown_scheduler, start_scheduler
from .services import auth, backup
from .services.pipeline import run_daily_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rzprobe")

DISCLAIMER = "挂牌价≠成交价，捡漏分仅作观察参考，最终以实地尽调为准。"
# 前端产物位置：优先用打包进 Python 包内的 webui（npm run build 直接输出到这里），
# 兼容旧版 frontend/dist。两者都没有时只提供 API。
WEBUI_DIR = BASE_DIR / "backend" / "app" / "webui"
DIST_DIR = WEBUI_DIR if WEBUI_DIR.exists() else (BASE_DIR / "frontend" / "dist")


def bootstrap() -> None:
    """空库自动灌演示数据并算一遍，保证「克隆下来就能看到界面」。"""
    init_db(reset=False)
    # 管理员口令：没配就生成一个，保证「只有我能改」这件事默认成立
    auth.ensure_admin_token()
    # 管理台保存过的集成配置（企微 webhook 等）覆盖 .env 默认值，无需重启
    auth.apply_runtime_settings()

    from .services.seed import SEED_VERSION, seed_all

    has_listing = query_one("SELECT COUNT(*) AS n FROM listing")
    is_empty = not has_listing or int(has_listing["n"]) == 0
    # 演示模式下，样本版本落后于代码就重灌一次；
    # 接了真实抓取（CRAWL_ENABLED=1）或已导入过真实房源，则永不覆盖用户数据。
    # 「准确真实」的底线：用户亲手录入的挂牌不能因为一次样本升级被静默洗掉。
    real_rows = query_one("SELECT COUNT(*) AS n FROM listing WHERE source_kind<>'demo'")
    has_real_data = bool(real_rows and int(real_rows["n"]) > 0)
    stale_demo = (
        not settings.CRAWL_ENABLED
        and not has_real_data
        and auth.get_setting("seed_version") != SEED_VERSION
    )
    if is_empty or stale_demo:
        # 重灌会清库。用户自己攒的东西（观察清单 / 偏好 / 推送配置 / 企微密钥）
        # 先取出来，灌完再原样还回去——否则一次样本升级就把它们洗掉了。
        salvaged = None
        if not is_empty:
            try:
                saved = backup.export_assets()
                if any(saved["counts"].values()):
                    salvaged = saved
                    logger.info("重灌前暂存用户资产：%s", saved["counts"])
            except Exception:  # noqa: BLE001
                logger.warning("暂存用户资产失败，本次重灌会重置用户数据", exc_info=True)

        logger.info("灌入演示数据（%s）：%s",
                    "空库" if is_empty else "样本版本落后", SEED_VERSION)
        info = seed_all(clear=True)
        logger.info("演示数据完成：%s 个小区 / %s 条挂牌 / %s 条法拍 / %s 个新盘",
                    info["communities"], info["listings"], info["auctions"],
                    info.get("new_homes", 0))

        if salvaged:
            try:
                backup.import_assets(salvaged)
                logger.info("用户资产已还原：%s", salvaged["counts"])
            except Exception:  # noqa: BLE001
                logger.warning("还原用户资产失败", exc_info=True)

        # 版本号必须写在「还原资产」之后：资产备份里也含 app_setting，
        # 里面存的是重灌前的旧 seed_version，先写后还原会被它覆盖回去，
        # 结果每次重启都判定「版本落后」→ 反复重灌（踩过）。
        auth.set_setting("seed_version", SEED_VERSION)

    has_score = query_one("SELECT COUNT(*) AS n FROM leak_score")
    if not has_score or int(has_score["n"]) == 0:
        logger.info("尚无评分结果，先跑一次增量计算…")
        run_daily_pipeline(push=False)

    # 小区价格序列：历史段由调价记录回推一次（幂等），今天的全量快照每次启动都刷新。
    # 没有这一步，「关注小区房价波动」在刚部署时是没有历史曲线可看的。
    try:
        from .services.community_trend import backfill_from_history, snapshot_all
        b = backfill_from_history()
        s = snapshot_all()
        logger.info("小区价格序列就绪：回推 %s 点 / 今日 %s 个小区", b.get("points"), s.get("communities"))
    except Exception:  # noqa: BLE001
        logger.warning("小区价格序列初始化失败", exc_info=True)

    # 今天还没备份就补一份：调度器没开时也有兜底
    try:
        snap = backup.ensure_daily_snapshot()
        if snap:
            logger.info("已生成启动快照：%s", snap.name)
    except Exception:  # noqa: BLE001  备份失败不该拦住服务启动
        logger.warning("启动快照失败", exc_info=True)



@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="日照房价探针 · 捡漏雷达",
    description=(
        "面向日照市区自住购房者的房价监测 + 捡漏预警系统。\n\n"
        f"**{DISCLAIMER}**\n\n"
        "核心原则：对所有『看起来便宜』的房源，默认先质疑『它为什么便宜』，再判断是否值得留意。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(listings.router)
app.include_router(anomalies.router)
app.include_router(auctions.router)
app.include_router(watchlist.router)
app.include_router(alerts.router)
app.include_router(communities.router)
app.include_router(market.router)
app.include_router(newhomes.router)
app.include_router(target.router)
app.include_router(admin.router)
app.include_router(sources.router)
app.include_router(sources.admin_router)
app.include_router(reports.router)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def admin_guard(request: Request, call_next):
    """只拦写操作：读数据谁都能看，改数据必须持口令。

    这样家人朋友拿到链接可以正常查房源、看异动，
    但改偏好、加观察清单、改推送配置、触发重算都必须登录。
    """
    path = request.url.path
    if request.method in WRITE_METHODS and path.startswith("/api/"):
        ok, reason = auth.check_write(
            request.method, path, request.headers.get("X-Admin-Token")
        )
        if not ok:
            return JSONResponse(
                status_code=401,
                content={"detail": reason, "code": "ADMIN_REQUIRED", "login": "/api/admin/login"},
            )
    return await call_next(request)


@app.get("/api/disclaimer", tags=["基础信息"])
def disclaimer():
    return {
        "text": DISCLAIMER,
        "principles": [
            "把所有低价先当嫌疑，再判断是否值得看",
            "宁可漏报，不可把坑包装成漏",
            "挂牌价≠成交价，捡漏分仅作观察参考",
            "所有结论最终以实地尽调为准",
        ],
    }


@app.get("/api/config", tags=["基础信息"])
def public_config():
    """前端需要的公开配置。

    地图 Key / 安全密钥是浏览器端凭据，本来就会随页面下发（打开控制台就能看到），
    不配也完全可用，只是底图退化成示意投影。企微 Webhook 等真密钥不放这里。
    """
    return {
        "amap_key": settings.AMAP_KEY,
        "amap_security_code": settings.AMAP_SECURITY_CODE,
        "app_name": "日照房价探针 · 捡漏雷达",
        "disclaimer": DISCLAIMER,
        "crawl_enabled": settings.CRAWL_ENABLED,
        "demo_only": bool(query_one(
            "SELECT 1 FROM listing WHERE source_kind<>'demo' LIMIT 1"
        ) is None),
    }


# --------------------------------------------------------------------------
# 前端托管（构建后单端口访问）
# --------------------------------------------------------------------------
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(DIST_DIR / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # /api/* 走到这里说明接口路径写错了，必须返回 JSON 404，
        # 绝不能回 index.html —— 否则前端/脚本会把 HTML 当成成功的响应体。
        if full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={
                    "detail": f"接口不存在：/{full_path}",
                    "hint": "可用接口见 /docs 或 /openapi.json",
                },
            )
        target = DIST_DIR / full_path
        if full_path and target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST_DIR / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def index_hint():
        return JSONResponse({
            "app": "日照房价探针 · 捡漏雷达",
            "hint": "后端已启动。前端开发模式请运行 frontend 目录: npm install && npm run dev（默认 5173 端口）",
            "docs": "/docs",
            "api": ["/api/meta/dashboard", "/api/anomalies/today", "/api/anomalies", "/api/alerts/config"],
            "disclaimer": DISCLAIMER,
        })


if __name__ == "__main__":  # pragma: no cover
    import os

    import uvicorn

    # 云上/容器里由平台注入 PORT，本地默认 8000；
    # 监听 0.0.0.0 才能被反向代理和同一局域网内的手机访问。
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
