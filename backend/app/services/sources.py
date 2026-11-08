"""房源来源目录 + 真实房源导入。

一条原则：**不为了「显得全」而编造房源**。

当前库里 220 套挂牌是固定随机种子生成的演示样本，用来展示研判逻辑，
不是任何平台的真实挂牌。所以这里做三件事，而不是往库里塞假房源：

1. 用 `source_kind` 把数据性质标死（demo / crawled / manual），演示样本在
   详情页一律显示「演示样本」，绝不显示成「来自房天下」；
2. 把代码里已有的 5 个采集通道列成一个可查询的「来源目录」，如实报告
   每个通道的接入状态、真实条数、最近采集时间、以及选择器是否验证过；
3. 提供一条真正能把房源变全的路：人工/表格导入真实挂牌，强制要求每条
   附原始页面 URL，落库时记批次、记采集时间，可逐条回溯核验。

自动采集（crawler/）默认关闭且选择器未经真实站点验证，因此在目录里
明确标 `verified=False`，不会把「写了解析器」说成「已经接上数据」。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from ..db import get_conn, query_all, query_one, scalar
from ..config import settings

# ---------------------------------------------------------------------------
# 来源目录：与 crawler/secondhand.py / auction.py 里的 key 一一对应
# ---------------------------------------------------------------------------
SOURCE_REGISTRY: list[dict] = [
    {
        "key": "gov", "name": "日照房管业务网 · 二手房签约公示", "kind": "official",
        "home": "https://www.rzfcfw.cn/plugins/esfczfxx/htqygs.jsp",
        "parser": "tools/../services/live_refresh.fetch_official_deals",
        "verified": True,
        "note": "政府公开 JSON 接口，免验证码、全自动（实测 841 条）；但价格字段为空、数据停更于 2023 年，仅作核验参考，不参与挂牌价计算",
    },
    {
        "key": "ke", "name": "贝壳找房 · 日照二手房", "kind": "secondhand",
        "home": "https://rz.ke.com/ershoufang/",
        "parser": "tools/ke_fetch.py",
        "verified": True,
        "note": "已实际验证可解析：列表页 + 详情页，2026-11 实抓 149 套在售；列表页会混入其它城市推荐，已按域名严格过滤",
    },
    {
        "key": "anjuke", "name": "安居客 · 日照二手房", "kind": "secondhand",
        "home": "https://m.anjuke.com/rz/sale/",
        "parser": "tools/aj_fetch.py",
        "verified": True,
        "note": "已实际验证：PC 站是 JS 验证壳不可用，但移动站（iPhone UA）可解析；2026-11 实抓 175 套在售，含区/板块",
    },
    {
        "key": "wuba", "name": "58同城 · 日照二手房", "kind": "secondhand",
        "home": "https://rizhao.58.com/ershoufang/",
        "parser": "services/live_refresh.parse_wuba",
        "verified": True,
        "note": "已实际验证：复用你在浏览器里正常访问后的会话 Cookie，服务端低频抓 SSR 列表页首屏即可解析（2026-11 实抓 117 套）。不破解验证码；连续翻页会触发 58 的 antibot，因此只抓第一页、每天两次。字段按卡片 class 精确提取，并用「总价/面积≈页面单价」交叉校验，对不上的丢弃",
    },
    {
        "key": "fangtianxia", "name": "房天下 · 日照二手房", "kind": "secondhand",
        "home": "https://rizhao.esf.fang.com/",
        "parser": "FangtianxiaSource",
        "note": "当前网络无法连通（代理 502），待排查",
    },
    {
        "key": "zhiye", "name": "日照房产网 · 二手房", "kind": "secondhand",
        "home": "https://www.rzfc.com.cn/secondhand/",
        "parser": "ZhiyeSource",
        "note": "站点当前连接超时，疑似下线或改版，待复查",
    },
    {
        "key": "ali", "name": "阿里资产 · 司法拍卖", "kind": "auction",
        "home": "https://sf.taobao.com/",
        "parser": "AlipaimaiSource",
        "note": "需 JS 渲染 + 签名，暂无法接入",
    },
    {
        "key": "jd", "name": "京东资产 · 司法拍卖", "kind": "auction",
        "home": "https://auction.jd.com/sifa.html",
        "parser": "JdAuctionSource",
        "note": "页面为 JS 壳（服务端无内容），暂无法接入",
    },
]
SOURCE_KEYS = {s["key"] for s in SOURCE_REGISTRY}

KIND_LABEL = {"secondhand": "二手挂牌", "auction": "司法拍卖", "new_home": "新房",
              "manual": "人工录入", "official": "政府公示"}

# 采集通道的通用前置条件，坦诚写清楚，避免用户以为打开开关就有真数据
CRAWL_PRECONDITIONS = [
    "站点需允许抓取：采集器会先读 robots.txt，不允许就整站跳过",
    "页面结构会改版，当前选择器是按公开页面结构写的，尚未在真实站点逐项验证",
    "需在服务端设置 CRAWL_ENABLED=1，并在管理台开启对应通道",
    "首批数据必须人工核对 3~5 条（价格、面积、原始链接是否能打开）再正式使用",
]


def _mask_source_platform(key: str) -> str:
    return next((s["name"] for s in SOURCE_REGISTRY if s["key"] == key), key)


def source_info(row: dict) -> dict:
    """把一条房源的来源字段整理成前端能直接渲染、且不会撒谎的结构。

    demo 数据即使 source 写着 fangtianxia，也只显示「演示样本」，
    platform 一律留空、不给链接 —— 那是随机生成的样本，不是房天下的挂牌。
    """
    kind = (row.get("source_kind") or "demo").strip().lower()
    url = (row.get("source_url") or "").strip()
    if kind not in ("demo", "crawled", "manual"):
        kind = "demo"
    if kind == "demo":
        return {
            "kind": "demo",
            "label": "演示样本",
            "platform": "",
            "url": None,
            "ref": None,
            "collected_at": None,
            "verified": False,
            "note": "固定种子生成的示意数据，用于展示研判逻辑，不是真实挂牌信息，不可用于议价或决策。",
        }
    platform = (row.get("source_name") or "").strip() or _mask_source_platform(str(row.get("source") or ""))
    return {
        "kind": kind,
        "label": "人工录入" if kind == "manual" else "自动采集",
        "platform": platform,
        "url": url or None,
        "ref": (row.get("source_ref") or "").strip() or None,
        "collected_at": row.get("collected_at") or None,
        "verified": bool(url),
        "note": (
            "来源页面已记录，请以原始页面为准复核价格与面积。" if url
            else "缺少原始页面链接，无法回溯核验，请谨慎使用。"
        ),
    }


def _last_refresh() -> dict | None:
    """最近一次自动/手动刷新的结果（含被验证码拦截的说明）。"""
    from . import auth
    raw = auth.get_setting("refresh_last")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def data_quality() -> dict:
    """数据质量面板：每个来源最近一次成功/失败、样本新鲜度、验证码情况。

    目的：让「数据还能不能信」一眼可见 —— 哪个源断了、断了几天、是不是被
    验证码拦的，都如实列出，不做任何美化。
    """
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    per_source: dict[str, dict] = {}

    freshness = query_all(
        "SELECT source, MAX(last_seen) AS last_seen, COUNT(*) AS n FROM listing "
        "WHERE source_kind='crawled' GROUP BY source"
    )
    for r in freshness:
        per_source.setdefault(str(r["source"]), {}).update({
            "listing_n": int(r["n"] or 0),
            "last_seen": r["last_seen"],
        })

    ok_log = query_all(
        "SELECT source, MAX(created_at) AS at FROM crawl_log "
        "WHERE status IN ('ok','partial') GROUP BY source"
    )
    for r in ok_log:
        per_source.setdefault(str(r["source"]), {})["last_ok_crawl"] = r["at"]

    fail_log = query_all(
        "SELECT source, COUNT(*) AS n, MAX(created_at) AS at FROM crawl_log "
        "WHERE status='failed' AND substr(created_at,1,10)>=? GROUP BY source",
        (since,),
    )
    for r in fail_log:
        per_source.setdefault(str(r["source"]), {}).update({
            "failed_7d": int(r["n"] or 0),
            "last_failed_at": r["at"],
        })

    last_refresh = _last_refresh() or {}
    captcha_sources = [
        k for k, v in (last_refresh.get("sources") or {}).items() if v.get("captcha")
    ]

    items = []
    for key, v in sorted(per_source.items()):
        items.append({
            "source": key,
            "label": _mask_source_platform(key),
            **v,
            "failed_7d": v.get("failed_7d", 0),
            "captcha_blocked_now": key in captcha_sources,
        })
    # 真实成交锚点覆盖度：法拍成交是唯一能拿到真实成交价的渠道，
    # 这里如实报「攒了多少条」，演示样本排除，不跟挂牌数据量混算。
    deal_where = (
        "FROM auction WHERE deal_price IS NOT NULL AND deal_price > 0 "
        "AND COALESCE(source_kind, 'demo') <> 'demo'"
    )
    deal_anchor_n = int(scalar(f"SELECT COUNT(*) {deal_where}", default=0))
    deal_latest = scalar(f"SELECT MAX(deal_date) {deal_where}", default=None)

    return {
        "sources": items,
        "captcha_blocked_now": captcha_sources,
        "deal_anchor_n": deal_anchor_n,
        "deal_latest": deal_latest,
        "note": "last_seen=该来源房源最近一次出现在抓取结果里的日期；"
                "被验证码拦截的来源当天会如实标记，不会假装抓到了数据。"
                "deal_anchor_n=已回填的真实法拍成交锚点数（人工/CSV 录入，非自动采集）。",
    }


def overview() -> dict:
    """来源总览：如实报告「有多少是真的」。"""
    total = int(scalar("SELECT COUNT(*) FROM listing WHERE status='active'", default=0))
    demo = int(scalar("SELECT COUNT(*) FROM listing WHERE status='active' AND source_kind='demo'", default=0))
    real = total - demo
    manual = int(scalar("SELECT COUNT(*) FROM listing WHERE status='active' AND source_kind='manual'", default=0))
    crawled = int(scalar("SELECT COUNT(*) FROM listing WHERE status='active' AND source_kind='crawled'", default=0))
    auction_real = int(scalar("SELECT COUNT(*) FROM auction WHERE source_kind<>'demo'", default=0))

    counts = {
        r["source"]: int(r["n"])
        for r in query_all(
            "SELECT source, COUNT(*) AS n FROM listing WHERE source_kind<>'demo' GROUP BY source"
        )
    }
    last_crawl = {
        r["source"]: r["at"]
        for r in query_all(
            "SELECT source, MAX(created_at) AS at FROM crawl_log GROUP BY source"
        )
    }
    sources = []
    for s in SOURCE_REGISTRY:
        enabled_key = f"source_enabled.{s['key']}"
        enabled_row = query_one("SELECT value FROM app_setting WHERE key=?", (enabled_key,))
        sources.append({
            **s,
            "kind_label": KIND_LABEL.get(s["kind"], s["kind"]),
            "enabled": bool(enabled_row and enabled_row["value"] == "1"),
            "crawl_ready": bool(settings.CRAWL_ENABLED),
            "verified": bool(s.get("verified", False)),
            "real_count": counts.get(s["key"], 0),
            "last_crawl_at": last_crawl.get(s["key"]),
        })

    # 云端调度 / 推送通道：本机关机后，用户打开网站也能确认「服务端仍在按点跑」
    from ..scheduler import job_snapshot, scheduler_running
    from . import alert_sender

    times = [t.strip() for t in settings.REFRESH_AT.split(",") if t.strip()]
    refresh_jobs = [j for j in job_snapshot()
                    if str(j.get("id", "")).startswith("live_refresh") and j.get("next_run")]
    next_run = min((j["next_run"] for j in refresh_jobs), default=None)
    try:
        sender = alert_sender.AlertSender(alert_sender.load_config())
        active = sender.resolve_channel()
        push = {"key": active.key, "label": active.label, "external": active.key != "log"}
    except Exception:  # noqa: BLE001 通道状态读不到不能拖垮来源总览
        push = {"key": "log", "label": "站内提醒（降级）", "external": False}

    return {
        "total": total,
        "demo": demo,
        "real": real,
        "manual": manual,
        "crawled": crawled,
        "auction_real": auction_real,
        "crawl_enabled": bool(settings.CRAWL_ENABLED),
        "crawl_wired": True,
        "sources": sources,
        "schedule": {
            "enabled": bool(settings.LIVE_REFRESH_ENABLED and settings.ENABLE_SCHEDULER),
            "running": bool(scheduler_running()),
            "times": times,
            "timezone": settings.TZ,
            "next_run": next_run,
            "runs_in_cloud": True,
            "needs_local_pc": False,
            "note": "抓取、入库、重算、提醒都在云端服务进程里执行，本机电脑关机不影响。"
                    "本机仅在 58 会话失效时用于打开一次页面重新同步。",
        },
        "push": {
            **push,
            "note": "当前只写站内提醒，异动需打开网站查看" if not push["external"]
                    else "异动会主动推送到外部通道",
        },
        "last_refresh": _last_refresh(),
        "quality": data_quality(),
        "preconditions": CRAWL_PRECONDITIONS,
        "honesty_note": (
            "当前在挂房源全部为演示样本，来源标签仅作字段示意，与任何真实平台无关。"
            if real == 0 else
            f"当前 {total} 套在挂中 {real} 套来自已记录来源（人工 {manual} / 采集 {crawled}），"
            f"其余 {demo} 套为演示样本。"
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def collect_enabled_sources(pages: int = 1) -> dict:
    """按管理台勾选的通道真实抓取。只在这里把 crawler 接进生产链路。

    CRAWL_ENABLED=0 时直接返回 skipped（不会偷偷联网，也不会写假数据）。
    """
    if not settings.CRAWL_ENABLED:
        return {"status": "skipped", "reason": "CRAWL_ENABLED=0", "sources": {}}
    rows = query_all("SELECT key,value FROM app_setting WHERE key LIKE 'source_enabled.%'")
    enabled = {r["key"].split(".", 1)[1] for r in rows if r["value"] == "1"}
    if not enabled:
        return {"status": "skipped", "reason": "管理台尚未开启任何采集通道", "sources": {}}

    from ..crawler.runner import run_auctions, run_secondhand

    # crawler/ 包只认这些 key；ke 等脚本型通道不在这里跑，
    # 勾了也只会在结果里如实说明，不会假装采集过。
    CRAWLER_SECONDHAND = {"fangtianxia", "anjuke", "zhiye"}
    CRAWLER_AUCTION = {"ali", "jd"}
    second = sorted(k for k in enabled if k in CRAWLER_SECONDHAND)
    auctions = sorted(k for k in enabled if k in CRAWLER_AUCTION)
    not_supported = sorted(enabled - CRAWLER_SECONDHAND - CRAWLER_AUCTION)
    if not second and not auctions:
        reason = "管理台尚未开启任何采集通道" if not enabled else \
            f"勾选的通道不适用于在线采集：{', '.join(not_supported)}（请用对应脚本定期抓取）"
        return {"status": "skipped", "reason": reason, "sources": {}}
    report: dict = {}
    if second:
        report.update(run_secondhand(sources=second, pages=pages).get("sources", {}))
    if auctions:
        report.update(run_auctions(sources=auctions, pages=pages).get("sources", {}))
    return {"status": "done", "enabled": sorted(enabled), "sources": report}


def set_source_enabled(key: str, enabled: bool) -> dict:
    if key not in SOURCE_KEYS:
        raise ValueError(f"未知来源：{key}")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO app_setting (key,value,is_secret) VALUES (?,?,0) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now','localtime')",
            (f"source_enabled.{key}", "1" if enabled else "0"),
        )
    return {"ok": True, "key": key, "enabled": enabled}


# ---------------------------------------------------------------------------
# 真实房源导入
# ---------------------------------------------------------------------------
URL_RE = re.compile(r"^https?://", re.I)

REQUIRED_FIELDS = ("community_name", "area")


class ImportErrorRow(Exception):
    """单行校验失败，带行号，便于前端逐条展示。"""


def _validate_row(row: dict, idx: int) -> dict:
    if not isinstance(row, dict):
        raise ImportErrorRow(f"第 {idx + 1} 行不是对象")
    for field in REQUIRED_FIELDS:
        if row.get(field) in (None, ""):
            raise ImportErrorRow(f"第 {idx + 1} 行缺少必填字段 {field}")

    # 总价支持「万元」和「元」两种写法，万元优先（人工录入更不容易少个零）
    if row.get("total_wan") not in (None, ""):
        total = float(row["total_wan"]) * 10000
    elif row.get("total_price") not in (None, ""):
        total = float(row["total_price"])
    else:
        raise ImportErrorRow(f"第 {idx + 1} 行缺少 total_wan（万元）或 total_price（元）")
    area = float(row["area"])
    if not (20 <= area <= 600):
        raise ImportErrorRow(f"第 {idx + 1} 行面积 {area}㎡ 不合理（应在 20~600）")
    if not (100000 <= total <= 100000000):
        raise ImportErrorRow(f"第 {idx + 1} 行总价 {total:.0f} 元不合理")

    url = str(row.get("source_url") or "").strip()
    if not URL_RE.match(url) or not urlparse(url).netloc:
        raise ImportErrorRow(f"第 {idx + 1} 行缺少有效的 source_url（原始挂牌页链接，必填）")

    return {
        "community_name": str(row["community_name"]).strip(),
        "district": str(row.get("district") or "").strip(),
        "layout": str(row.get("layout") or "").strip(),
        "area": round(area, 2),
        "total": round(total),
        "unit": round(total / area),
        "source_url": url,
        "source_ref": str(row.get("source_ref") or "").strip(),
        "list_days": max(0, int(row.get("list_days") or 0)),
        "build_year": (
            int(row["build_year"]) if str(row.get("build_year") or "").strip().isdigit() else None
        ),
        "floor_text": str(row.get("floor_text") or "").strip(),
        "decoration": str(row.get("decoration") or "").strip(),
        "orientation": str(row.get("orientation") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "defect_tags": row.get("defect_tags") or [],
    }


def import_listings(rows: list[dict], source_key: str,
                    source_name: str | None = None,
                    source_kind: str = "manual") -> dict:
    """导入真实挂牌。每条必须有原始页面链接；重复指纹自动跳过。

    source_kind：manual（人工/表格整理）或 crawled（自动采集脚本产出）。
    贝壳等脚本抓的数据标 crawled 才是诚实口径。

    返回逐行结果，前端可以把「第几行为什么没进」直接显示给用户。
    """
    if not isinstance(rows, list) or not rows:
        raise ValueError("需要非空数组")
    if len(rows) > 500:
        raise ValueError("单批最多 500 条，请分批导入")
    if source_key not in SOURCE_KEYS and source_key != "manual":
        raise ValueError(f"未知来源：{source_key}")
    if source_kind not in ("manual", "crawled"):
        raise ValueError(f"source_kind 只能是 manual / crawled，收到：{source_kind}")

    platform = (source_name or "").strip() or (
        "人工录入" if source_key == "manual" else _mask_source_platform(source_key)
    )
    checked: list[tuple[int, dict]] = []
    errors: list[dict] = []
    for idx, raw in enumerate(rows):
        try:
            checked.append((idx, _validate_row(raw, idx)))
        except ImportErrorRow as exc:
            errors.append({"row": idx + 1, "reason": str(exc)})
        except (TypeError, ValueError) as exc:
            errors.append({"row": idx + 1, "reason": f"数值格式错误：{exc}"})

    today = date.today()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved = skipped = updated = price_updates = 0
    saved_ids: list[int] = []

    with get_conn() as conn:
        for idx, item in checked:
            # 小区：先精确匹配，再退到包含匹配；都没有就按用户给的板块新建，
            # 不静默丢弃，也不假装它本来就在库里。
            c = conn.execute(
                "SELECT * FROM community WHERE name=? ORDER BY id LIMIT 1",
                (item["community_name"],),
            ).fetchone()
            if not c:
                c = conn.execute(
                    "SELECT * FROM community WHERE name LIKE ? ORDER BY id LIMIT 1",
                    (f"%{item['community_name']}%",),
                ).fetchone()
            if c:
                cid = int(c["id"])
                district = c["district"]
                # 小区档案有建成年优先；档案缺失时用导入数据里的（如贝壳带的建成年）
                build_year = c["build_year"] or item.get("build_year")
                has_elevator = int(c["has_elevator"] or 1)
            else:
                district = item["district"] or "待核实"
                build_year = item.get("build_year")
                has_elevator = 1
                cur = conn.execute(
                    "INSERT INTO community (name,district,is_extended,has_elevator,liquidity_score,"
                    "deal_active_90d) VALUES (?,?,0,?,50,0)",
                    (item["community_name"], district, has_elevator),
                )
                cid = int(cur.lastrowid)

            # 同一套房的判定：
            #   有平台房源编号（source_ref）时用它 —— 价格变了还是同一套房；
            #   没有编号时才退回「小区+户型+面积+总价」指纹（会漏掉调价，仅作兜底）。
            # 这一步是「每天更新两次房价」能成立的前提：按指纹去重会把每次调价
            # 都当成新房源，越抓越重复。
            fp = hashlib.md5(
                f"{source_key}|{item['community_name']}|{item['layout']}|"
                f"{int(round(item['area']))}|{int(item['total'])}".encode("utf-8")
            ).hexdigest()
            ref = (item.get("source_ref") or "").strip()
            existing = None
            if ref:
                existing = conn.execute(
                    "SELECT id,total_price,unit_price,first_price,price_changes,consecutive_drops "
                    "FROM listing WHERE source=? AND source_ref=? ORDER BY id LIMIT 1",
                    (source_key, ref),
                ).fetchone()
            if not existing and ref:
                # 自愈：早期导入的老记录没有平台编号，用指纹命中后把编号补上，
                # 下一次抓取就能正常跟踪这套房的调价，而不是永远靠指纹兜底。
                dup = conn.execute(
                    "SELECT id,source_ref FROM listing WHERE fingerprint=? ORDER BY id LIMIT 1", (fp,)
                ).fetchone()
                if dup and not (dup["source_ref"] or "").strip():
                    conn.execute("UPDATE listing SET source_ref=? WHERE id=?", (ref, dup["id"]))
                    existing = conn.execute(
                        "SELECT id,total_price,unit_price,first_price,price_changes,consecutive_drops "
                        "FROM listing WHERE id=?", (dup["id"],)
                    ).fetchone()
            if not existing and conn.execute(
                "SELECT 1 FROM listing WHERE fingerprint=?", (fp,)
            ).fetchone():
                skipped += 1
                errors.append({"row": idx + 1, "reason": "重复房源（同小区/户型/面积/总价已存在），已跳过"})
                continue

            if existing:
                # 已存在：更新价格 / 挂牌天数；价格变了就记一次调价
                lid = int(existing["id"])
                old_total = float(existing["total_price"] or 0)
                new_total = float(item["total"])
                changed = abs(old_total - new_total) >= 1
                if changed and old_total > 0:
                    drop_pct = round((new_total - old_total) / old_total, 4)
                    conn.execute(
                        "UPDATE listing SET total_price=?,unit_price=?,last_seen=?,list_days=?,"
                        "source_url=?,collected_at=?,status='active',"
                        "price_changes=price_changes+1,"
                        "consecutive_drops=CASE WHEN ?<0 THEN consecutive_drops+1 ELSE 0 END "
                        "WHERE id=?",
                        (new_total, item["unit"], today.isoformat(), item["list_days"],
                         item["source_url"], now, drop_pct, lid),
                    )
                    conn.execute(
                        "INSERT INTO price_history (listing_id,price,unit_price,observed_at,drop_pct,kind,note) "
                        "VALUES (?,?,?,?,?,'listing',?)",
                        (lid, new_total, item["unit"], today.isoformat(), drop_pct,
                         "每日抓取发现调价"),
                    )
                    price_updates += 1
                else:
                    conn.execute(
                        "UPDATE listing SET last_seen=?,list_days=?,source_url=?,collected_at=?,"
                        "status='active' WHERE id=?",
                        (today.isoformat(), item["list_days"], item["source_url"], now, lid),
                    )
                if not (existing["first_price"] or 0):
                    conn.execute("UPDATE listing SET first_price=? WHERE id=?", (new_total, lid))
                updated += 1
                continue

            first_seen = (today - timedelta(days=item["list_days"])).isoformat()
            cur = conn.execute(
                """
                INSERT INTO listing
                  (fingerprint,source,source_url,source_kind,source_name,source_ref,collected_at,
                   community_id,community_name,district,title,layout,rooms,halls,
                   area,total_price,unit_price,floor_text,orientation,decoration,
                   build_year,age,has_elevator,first_price,first_seen,last_seen,list_days,
                   price_changes,consecutive_drops,defect_tags,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    fp, source_key, item["source_url"], source_kind, platform,
                    item["source_ref"], now,
                    cid, item["community_name"], district,
                    item["title"] or f"{item['community_name']} {item['layout']} {item['area']:g}㎡",
                    item["layout"], None, None,
                    item["area"], item["total"], item["unit"],
                    item["floor_text"] or None, item["orientation"] or None,
                    item["decoration"] or None, build_year,
                    (today.year - build_year) if build_year else None,
                    has_elevator, item["total"], first_seen, today.isoformat(),
                    item["list_days"], 0, 0,
                    json.dumps(item["defect_tags"], ensure_ascii=False), "active",
                ),
            )
            lid = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO price_history (listing_id,price,unit_price,observed_at,drop_pct,kind,note) "
                "VALUES (?,?,?,?,0,'listing',?)",
                (lid, item["total"], item["unit"], today.isoformat(), "导入首次记录"),
            )
            saved += 1
            saved_ids.append(lid)

        # 批次是审计记录：全批被拒也要留痕，但必须写清楚「没写进任何房源」，
        # 否则用户在批次列表看到「收到 1 条」会误以为导入过。
        if saved == 0 and updated == 0:
            note = "本批全部未通过字段校验或为重复房源，未写入任何房源"
        else:
            bits = []
            if saved:
                bits.append(f"新增 {saved} 套")
            if updated:
                bits.append(f"更新 {updated} 套")
            if price_updates:
                bits.append(f"其中调价 {price_updates} 套")
            if errors:
                bits.append(f"跳过 {len(errors)} 行")
            note = "，".join(bits) or None
        conn.execute(
            "INSERT INTO source_import_batch (source_key,source_name,items_in,items_saved,items_skipped,note) "
            "VALUES (?,?,?,?,?,?)",
            (source_key, platform, len(rows), saved, len(rows) - saved, note),
        )

    return {
        "ok": True,
        "source_key": source_key,
        "source_name": platform,
        "items_in": len(rows),
        "items_saved": saved,
        "items_updated": updated,
        "price_changes": price_updates,
        "items_skipped": skipped,
        "saved_ids": saved_ids,
        "errors": errors,
        "note": "导入后请到「管理台 → 重算」跑一次，新房源才会参与评分与异动检测。",
    }
