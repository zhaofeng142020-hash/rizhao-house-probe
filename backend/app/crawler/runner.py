"""抓取调度器：去重入库 + 调价追踪 + 异常隔离 + 抓取日志。

关键设计：
  - 指纹去重（spec 口径）：md5(source+小区+户型+面积+总价) —— 同一次快照不重复入库
  - 调价追踪：同「站点+小区+户型+面积」视为同一套房源，价格变了就追加 price_history
    并更新 first_price / price_changes / consecutive_drops / list_days
  - 异常隔离：单个站点抛错只写 crawl_log，继续跑下一个站点
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta

from ..config import settings
from ..db import execute, get_conn, query_all, query_one
from .auction import AUCTION_SOURCES, RawAuction
from .secondhand import SECONDHAND_SOURCES, RawListing

logger = logging.getLogger("rzprobe.crawler.runner")

# 采集落库时写明「来自哪个平台」，前端来源卡片才不会出现一个裸的 key
SOURCE_NAMES = {
    "fangtianxia": "房天下 · 日照二手房",
    "anjuke": "安居客 · 日照二手房",
    "zhiye": "日照房产网 · 二手房",
    "ali": "阿里资产 · 司法拍卖",
    "jd": "京东资产 · 司法拍卖",
}


def _log_crawl(source: str, task: str, status: str, found: int, saved: int,
               error: str = "", elapsed_ms: int = 0) -> None:
    execute(
        "INSERT INTO crawl_log (source,task,status,items_found,items_saved,error,elapsed_ms) "
        "VALUES (?,?,?,?,?,?,?)",
        (source, task, status, found, saved, error[:500], elapsed_ms),
    )


def _community_id(name: str, district: str) -> int:
    row = query_one("SELECT id FROM community WHERE name=? AND district=?", (name, district))
    if row:
        return int(row["id"])
    return execute(
        "INSERT INTO community (name,district,is_extended,liquidity_score) VALUES (?,?,?,?)",
        (name, district, 1 if district in ("莒县", "五莲", "岚山") else 0, 50),
    )


def save_listing(item: RawListing) -> str:
    """入库一条挂牌。返回 'insert' / 'update' / 'dup'。"""
    data = item.to_db()
    if not data["community_name"] or data["total_price"] <= 0 or data["area"] <= 0:
        return "dup"

    if query_one("SELECT id FROM listing WHERE fingerprint=?", (data["fingerprint"],)):
        return "dup"  # 同快照已存在（spec 指纹去重）

    # 同一套房源（不含总价）是否已存在 → 说明是调价
    same = query_one(
        "SELECT * FROM listing WHERE source=? AND community_name=? AND layout=? AND CAST(area AS INT)=? "
        "ORDER BY id DESC LIMIT 1",
        (item.source, item.community_name, item.layout, int(item.area)),
    )
    today = date.today().isoformat()
    collected = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    platform = SOURCE_NAMES.get(item.source, item.source)
    if same:
        old_price = float(same.get("total_price") or 0)
        new_price = float(data["total_price"])
        drop_pct = (new_price - old_price) / old_price if old_price > 0 else 0
        consec = int(same.get("consecutive_drops") or 0)
        consec = consec + 1 if drop_pct < 0 else 0
        with get_conn() as conn:
            conn.execute(
                "UPDATE listing SET fingerprint=?,total_price=?,unit_price=?,price_changes=price_changes+?,"
                "consecutive_drops=?,last_drop_days_ago=?,last_seen=?,list_days=?,"
                "defect_tags=?,raw_json=?,source_url=?,source_kind='crawled',source_name=?,"
                "collected_at=?,updated_at=datetime('now','localtime') WHERE id=?",
                (
                    data["fingerprint"], new_price, data["unit_price"], 1 if abs(drop_pct) > 1e-9 else 0,
                    consec, 0 if drop_pct < 0 else None, today,
                    (date.today() - datetime.strptime(
                        str(same.get("first_seen") or today)[:10], "%Y-%m-%d").date()).days,
                    json.dumps(data["defect_tags"], ensure_ascii=False),
                    json.dumps(data["raw_json"], ensure_ascii=False),
                    data["source_url"], platform, collected, int(same["id"]),
                ),
            )
            conn.execute(
                "INSERT INTO price_history (listing_id,price,unit_price,observed_at,drop_pct,days_gap,kind) "
                "VALUES (?,?,?,?,?,?,?)",
                (int(same["id"]), new_price, data["unit_price"], today, round(drop_pct, 4), None, "listing"),
            )
        return "update"

    cid = _community_id(item.community_name, item.district)
    execute(
        """
        INSERT INTO listing
          (fingerprint,source,source_url,community_id,community_name,district,title,layout,rooms,halls,
           area,total_price,unit_price,floor_text,orientation,decoration,build_year,
           first_price,first_seen,last_seen,list_days,price_changes,consecutive_drops,defect_tags,
           status,raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            data["fingerprint"], data["source"], data["source_url"], cid, data["community_name"],
            data["district"], data["title"], data["layout"], data["rooms"], data["halls"],
            data["area"], data["total_price"], data["unit_price"], data["floor_text"],
            data["orientation"], data["decoration"], data["build_year"],
            data["total_price"], today, today, 0, 0, 0,
            json.dumps(data["defect_tags"], ensure_ascii=False), "active",
            json.dumps(data["raw_json"], ensure_ascii=False),
        ),
    )
    # 采集来的房源必须标成 crawled，否则会被默认值当成演示样本
    execute(
        "UPDATE listing SET source_kind='crawled', source_name=?, collected_at=? WHERE fingerprint=?",
        (platform, collected, data["fingerprint"]),
    )
    new_id = query_one("SELECT id FROM listing WHERE fingerprint=?", (data["fingerprint"],))
    if new_id:
        execute(
            "INSERT INTO price_history (listing_id,price,unit_price,observed_at,drop_pct,kind) "
            "VALUES (?,?,?,?,?,?)",
            (int(new_id["id"]), data["total_price"], data["unit_price"], today, 0, "listing"),
        )
    return "insert"


def save_auction(item: RawAuction) -> str:
    data = item.to_db(comparable_unit=_comparable_unit(item.community_name))
    if data["start_price"] <= 0:
        return "dup"
    if query_one("SELECT id FROM auction WHERE fingerprint=?", (data["fingerprint"],)):
        return "dup"
    execute(
        """
        INSERT INTO auction
          (fingerprint,source,source_url,title,community_name,district,area,layout,appraisal_price,
           start_price,stage,auction_date,deposit,tax_est,arrears_est,repair_reserve,landing_price,
           comparable_unit_price,start_discount,net_discount,risk_tags,risk_level,status,raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            data["fingerprint"], data["source"], data["source_url"], data["title"], data["community_name"],
            data["district"], data["area"], data["layout"], data["appraisal_price"], data["start_price"],
            data["stage"], data["auction_date"], data["deposit"], data["tax_est"], data["arrears_est"],
            data["repair_reserve"], data["landing_price"], data["comparable_unit_price"],
            data["start_discount"], data["net_discount"],
            json.dumps(data["risk_tags"], ensure_ascii=False), data["risk_level"], "active",
            json.dumps(data["raw_json"], ensure_ascii=False),
        ),
    )
    execute(
        "UPDATE auction SET source_kind='crawled', source_name=?, collected_at=? WHERE fingerprint=?",
        (SOURCE_NAMES.get(item.source, item.source),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), data["fingerprint"]),
    )
    return "insert"


def _comparable_unit(community_name: str) -> float:
    rows = [
        float(r["unit_price"]) for r in query_all(
            "SELECT unit_price FROM listing WHERE community_name=? AND status='active'", (community_name,)
        ) if r.get("unit_price")
    ]
    if not rows:
        return 0.0
    rows.sort()
    return rows[len(rows) // 2]


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------
def run_secondhand(sources: list[str] | None = None, pages: int = 1) -> dict:
    """跑二手房抓取。默认跑全部站点，单站失败隔离。"""
    if not settings.CRAWL_ENABLED:
        _log_crawl("all", "secondhand", "skipped", 0, 0, "CRAWL_ENABLED=0，未启用真实抓取（当前用演示数据）")
        return {"status": "skipped", "reason": "CRAWL_ENABLED=0", "sources": {}}

    keys = sources or list(SECONDHAND_SOURCES)
    report: dict = {}
    for key in keys:
        src = SECONDHAND_SOURCES.get(key)
        if not src:
            report[key] = {"status": "unknown_source"}
            continue
        started = time.time()
        try:
            items = src.fetch(pages)
            saved = sum(1 for it in items if save_listing(it) != "dup")
            status = "ok" if items else "partial"
            _log_crawl(key, "secondhand", status, len(items), saved, "", int((time.time() - started) * 1000))
            report[key] = {"status": status, "found": len(items), "saved": saved}
        except Exception as exc:  # noqa: BLE001  异常隔离
            logger.exception("[%s] 抓取失败", key)
            _log_crawl(key, "secondhand", "failed", 0, 0, f"{type(exc).__name__}: {exc}",
                       int((time.time() - started) * 1000))
            report[key] = {"status": "failed", "error": str(exc)}
    return {"status": "done", "sources": report}


def run_auctions(sources: list[str] | None = None, pages: int = 1) -> dict:
    if not settings.CRAWL_ENABLED:
        _log_crawl("all", "auction", "skipped", 0, 0, "CRAWL_ENABLED=0，未启用真实抓取")
        return {"status": "skipped", "reason": "CRAWL_ENABLED=0", "sources": {}}

    keys = sources or list(AUCTION_SOURCES)
    report: dict = {}
    for key in keys:
        src = AUCTION_SOURCES.get(key)
        if not src:
            report[key] = {"status": "unknown_source"}
            continue
        started = time.time()
        try:
            items = src.fetch(pages)
            saved = sum(1 for it in items if save_auction(it) != "dup")
            status = "ok" if items else "partial"
            _log_crawl(key, "auction", status, len(items), saved, "", int((time.time() - started) * 1000))
            report[key] = {"status": status, "found": len(items), "saved": saved}
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] 法拍抓取失败", key)
            _log_crawl(key, "auction", "failed", 0, 0, f"{type(exc).__name__}: {exc}",
                       int((time.time() - started) * 1000))
            report[key] = {"status": "failed", "error": str(exc)}
    return {"status": "done", "sources": report}


def run_all(pages: int = 1) -> dict:
    return {"secondhand": run_secondhand(pages=pages), "auction": run_auctions(pages=pages)}
