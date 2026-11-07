"""每日增量计算流水线：统计 → 打分 → 异动识别 → 落库 → 推送。

这是「引擎」层，把纯函数（scoring / anomaly）与 IO 串起来。
单条房源失败不影响整批（异常隔离）。
"""
from __future__ import annotations

import json
import logging
import time as time_mod
from datetime import date, datetime, timedelta
from typing import Any

from ..config import settings
from ..db import get_conn, query_all, query_one
from . import anomaly, scoring
from .alert_sender import AlertSender, load_config

logger = logging.getLogger("rzprobe.pipeline")


# --------------------------------------------------------------------------
# 数据加载
# --------------------------------------------------------------------------
def load_listings(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM listing"
    if active_only:
        sql += " WHERE status='active'"
    rows = query_all(sql)
    for r in rows:
        try:
            r["defect_tags"] = json.loads(r.get("defect_tags") or "[]")
        except (json.JSONDecodeError, TypeError):
            r["defect_tags"] = []
    return rows


def load_histories() -> dict[int, list[dict]]:
    rows = query_all("SELECT * FROM price_history ORDER BY observed_at ASC, id ASC")
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(int(r["listing_id"]), []).append(r)
    return grouped


def load_auctions(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM auction"
    if active_only:
        sql += " WHERE status='active'"
    rows = query_all(sql)
    for r in rows:
        try:
            r["risk_tags"] = json.loads(r.get("risk_tags") or "[]")
        except (json.JSONDecodeError, TypeError):
            r["risk_tags"] = []
    return rows


def load_communities() -> dict[int, dict]:
    return {int(r["id"]): r for r in query_all("SELECT * FROM community")}


def load_pref() -> dict:
    row = query_one("SELECT * FROM user_pref WHERE id=1")
    if not row:
        return dict(scoring.DEFAULT_PREF)
    try:
        row["district_pref"] = json.loads(row.get("district_pref") or "[]")
    except (json.JSONDecodeError, TypeError):
        row["district_pref"] = scoring.DEFAULT_PREF["district_pref"]
    return row


def load_thresholds() -> dict:
    return {
        "sudden_drop_pct": settings.SUDDEN_DROP_PCT,
        "deep_dip_pct": settings.DEEP_DIP_PCT,
        "long_listed_days": settings.LONG_LISTED_DAYS,
        "auction_net_discount": settings.AUCTION_NET_DISCOUNT,
    }


# --------------------------------------------------------------------------
# 落库
# --------------------------------------------------------------------------
def upsert_leak_score(result: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO leak_score
              (target_type,target_id,score,s_price,s_motive,s_defect,s_liquidity,s_fit,
               confidence,grade,sample_n,deviation_pct,why_cheap,why_good,why_trap,computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))
            ON CONFLICT(target_type,target_id) DO UPDATE SET
              score=excluded.score, s_price=excluded.s_price, s_motive=excluded.s_motive,
              s_defect=excluded.s_defect, s_liquidity=excluded.s_liquidity, s_fit=excluded.s_fit,
              confidence=excluded.confidence, grade=excluded.grade, sample_n=excluded.sample_n,
              deviation_pct=excluded.deviation_pct, why_cheap=excluded.why_cheap,
              why_good=excluded.why_good, why_trap=excluded.why_trap,
              computed_at=datetime('now','localtime')
            """,
            (
                result["target_type"], int(result["target_id"]), result["score"],
                result["s_price"], result["s_motive"], result["s_defect"],
                result["s_liquidity"], result["s_fit"], result["confidence"],
                result["grade"], result["sample_n"], result.get("deviation_pct"),
                result["why_cheap"], result["why_good"], result["why_trap"],
            ),
        )


def insert_event(event: dict) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO anomaly_event
                  (event_key,event_date,target_type,target_id,community_name,district,
                   event_type,severity,total_price,unit_price,ref_unit_price,drop_pct,detail_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key) DO UPDATE SET
                  severity=excluded.severity, drop_pct=excluded.drop_pct,
                  detail_json=excluded.detail_json, total_price=excluded.total_price,
                  unit_price=excluded.unit_price, ref_unit_price=excluded.ref_unit_price
                """,
                (
                    event["event_key"], event["event_date"], event["target_type"], event["target_id"],
                    event.get("community_name"), event.get("district"), event["event_type"],
                    event["severity"], event.get("total_price"), event.get("unit_price"),
                    event.get("ref_unit_price"), event.get("drop_pct"),
                    json.dumps(event.get("detail", {}), ensure_ascii=False),
                ),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("写入异动事件失败 %s: %s", event.get("event_key"), exc)
        return False


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run_daily_pipeline(push: bool = True, today: date | None = None) -> dict:
    """每日增量计算。返回统计摘要（前端/日志都能看）。"""
    started = time_mod.time()
    today = today or date.today()

    # 真实采集必须发生在读数据之前，否则新抓到的房源这一轮算不到分。
    # CRAWL_ENABLED=0 时 collect_enabled_sources 直接返回 skipped，不联网、不写假数据。
    pre_errors: list[str] = []
    crawl_report = None
    if settings.CRAWL_ENABLED:
        try:
            from .sources import collect_enabled_sources
            crawl_report = collect_enabled_sources()
        except Exception as exc:  # noqa: BLE001  采集失败不能拖垮整条管线
            logger.exception("采集通道执行失败")
            pre_errors.append(f"crawl: {type(exc).__name__}: {exc}")

    cfg = load_config()
    thresholds = load_thresholds()
    pref = load_pref()
    communities = load_communities()
    listings = load_listings()
    histories = load_histories()
    auctions = load_auctions()

    stats = anomaly.compute_market_stats(listings)
    sender = AlertSender(cfg) if push else None

    summary = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": today.isoformat(),
        "listing_total": len(listings),
        "auction_total": len(auctions),
        "scored": 0,
        "events": 0,
        "red": 0,
        "yellow": 0,
        "white": 0,
        "pushed": 0,
        "skipped": 0,
        "crawl": crawl_report,
        "errors": list(pre_errors),
    }
    pending_digest: list[dict] = []
    seen_board_events: set[tuple] = set()   # 板块背离按「小区」去重，避免同小区刷屏

    # ---------------- 二手房 ----------------
    for li in listings:
        try:
            comp = anomaly.get_comparables(stats, li)
            community = communities.get(int(li["community_id"])) if li.get("community_id") else None
            result = scoring.score_listing(li, comp, community, pref, settings.SCORE_MIN_SAMPLE)
            upsert_leak_score(result)
            summary["scored"] += 1

            raw_events = anomaly.detect_listing_events(
                li, histories.get(int(li["id"]), []), comp, stats, today, thresholds
            )
            for ev in raw_events:
                if ev["event_type"] == "board_divergence":
                    key = (li.get("district"), li.get("community_name"))
                    if key in seen_board_events:
                        continue        # 同一小区当天只报一次板块背离
                    seen_board_events.add(key)
                event = {
                    "event_key": anomaly.build_event_key(ev["event_type"], "listing", li["id"], today),
                    "event_date": today.isoformat(),
                    "target_type": "listing",
                    "target_id": int(li["id"]),
                    "community_name": li.get("community_name"),
                    "district": li.get("district"),
                    "event_type": ev["event_type"],
                    "severity": result["grade"],     # 严重度统一由评分分级决定
                    "total_price": li.get("total_price"),
                    "unit_price": li.get("unit_price"),
                    "ref_unit_price": comp.get("median_unit"),
                    "drop_pct": ev.get("drop_pct"),
                    "detail": {**ev.get("detail", {}), "strength": anomaly.EVENT_STRENGTH.get(ev["event_type"], 0.5)},
                }
                if insert_event(event):
                    summary["events"] += 1

            summary[result["grade"]] += 1

            # 推送判定
            if sender and result["grade"] in ("red", "yellow"):
                if result["grade"] == "yellow" and int(cfg.get("yellow_daily", 1)):
                    pending_digest.append({"item": li, "score": result})
                else:
                    res = sender.push_target("listing", li, result)
                    if res.get("status") in ("sent", "logged"):
                        summary["pushed"] += 1
                    else:
                        summary["skipped"] += 1
        except Exception as exc:  # noqa: BLE001  单条异常隔离
            logger.exception("处理二手房 %s 失败", li.get("id"))
            summary["errors"].append(f"listing#{li.get('id')}: {type(exc).__name__}: {exc}")

    # ---------------- 法拍 ----------------
    for au in auctions:
        try:
            comp = {"median_unit": 0.0, "n": 0}
            if au.get("comparable_unit_price"):
                comp = {"median_unit": float(au["comparable_unit_price"]), "n": 5}
            else:
                # 用工小区二手可比回填
                same = [
                    li for li in listings
                    if li.get("community_name") == au.get("community_name")
                ]
                if same:
                    units = sorted(float(x["unit_price"]) for x in same if x.get("unit_price"))
                    comp = {"median_unit": units[len(units) // 2], "n": len(units)}

            community = next(
                (c for c in communities.values() if c.get("name") == au.get("community_name")), None
            )
            result = scoring.score_auction(au, comp, pref, community)
            upsert_leak_score(result)
            summary["scored"] += 1

            for ev in anomaly.detect_auction_events(au, comp, thresholds, today):
                event = {
                    "event_key": anomaly.build_event_key(ev["event_type"], "auction", au["id"], today),
                    "event_date": today.isoformat(),
                    "target_type": "auction",
                    "target_id": int(au["id"]),
                    "community_name": au.get("community_name"),
                    "district": au.get("district"),
                    "event_type": ev["event_type"],
                    "severity": result["grade"],
                    "total_price": result.get("landing_price"),
                    "unit_price": result.get("landing_unit"),
                    "ref_unit_price": comp.get("median_unit"),
                    "drop_pct": ev.get("drop_pct"),
                    "detail": {**ev.get("detail", {}), "strength": anomaly.EVENT_STRENGTH.get(ev["event_type"], 0.5)},
                }
                if insert_event(event):
                    summary["events"] += 1

            summary[result["grade"]] += 1

            if sender and result["grade"] in ("red", "yellow"):
                if result["grade"] == "yellow" and int(cfg.get("yellow_daily", 1)):
                    pending_digest.append({"item": au, "score": result})
                else:
                    res = sender.push_target("auction", au, result)
                    if res.get("status") in ("sent", "logged"):
                        summary["pushed"] += 1
                    else:
                        summary["skipped"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("处理法拍 %s 失败", au.get("id"))
            summary["errors"].append(f"auction#{au.get('id')}: {type(exc).__name__}: {exc}")

    # ---------------- 黄级每日汇总 ----------------
    if sender and pending_digest:
        res = sender.push_digest(pending_digest)
        if res.get("status") in ("sent", "logged"):
            summary["pushed"] += 1

    # ---------------- 小区价格快照 + 关注小区波动提醒 ----------------
    from .community_trend import snapshot_all, watched_moves
    try:
        summary["community_snapshots"] = snapshot_all(today)
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"community_snapshot: {exc}")

    if sender:
        try:
            for mv in watched_moves():
                sender.send_message(
                    {
                        "title": mv["title"],
                        "text": mv["text"],
                        "markdown": mv["markdown"],
                        "score": 100,
                    },
                    "community", mv["community_id"], "yellow", 0, force=True,
                )
                summary["pushed"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"watched_moves: {exc}")

    # ---------------- 观察清单价格快照 ----------------
    try:
        refresh_watch_prices()
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"watchlist: {exc}")

    summary["elapsed_ms"] = int((time_mod.time() - started) * 1000)
    logger.info("每日流水线完成: %s", {k: v for k, v in summary.items() if k != "errors"})
    return summary


def refresh_watch_prices() -> int:
    """更新观察清单里的最新价（周报对比用）。"""
    items = query_all("SELECT * FROM watch_item")
    updated = 0
    with get_conn() as conn:
        for it in items:
            if it["target_type"] == "listing":
                row = query_one("SELECT total_price FROM listing WHERE id=?", (it["target_id"],))
                price = (row or {}).get("total_price")
            elif it["target_type"] == "community":
                row = query_one(
                    "SELECT median_unit AS total_price FROM community_price_snapshot "
                    "WHERE community_id=? ORDER BY observed_date DESC LIMIT 1", (it["target_id"],),
                )
                price = (row or {}).get("total_price")
            else:
                row = query_one("SELECT landing_price AS total_price FROM auction WHERE id=?", (it["target_id"],))
                price = (row or {}).get("total_price")
            if price:
                conn.execute(
                    "UPDATE watch_item SET last_price=?, last_checked=datetime('now','localtime') WHERE id=?",
                    (price, it["id"]),
                )
                updated += 1
    return updated


# --------------------------------------------------------------------------
# 观察周报
# --------------------------------------------------------------------------
def build_weekly_report(week_start: date | None = None) -> dict:
    """每周生成「观察周报」：哪些更值得看、哪些已涨价剔除。"""
    today = date.today()
    week_start = week_start or (today - timedelta(days=today.weekday()))
    items = query_all(
        """
        SELECT w.*, s.score, s.grade, s.deviation_pct, s.confidence
        FROM watch_item w
        LEFT JOIN leak_score s ON s.target_type=w.target_type AND s.target_id=w.target_id
        WHERE w.follow_status != 'done'
        """
    )
    better: list[dict] = []
    worse: list[dict] = []
    for it in items:
        base = float(it.get("base_price") or 0)
        last = float(it.get("last_price") or base)
        change = (last - base) / base if base > 0 else 0.0
        entry = {
            "id": it["id"],
            "target_type": it["target_type"],
            "target_id": it["target_id"],
            "community_name": it.get("community_name"),
            "district": it.get("district"),
            "base_price": base,
            "last_price": last,
            "change_pct": round(change, 4),
            "score": it.get("score"),
            "grade": it.get("grade"),
            "deviation_pct": it.get("deviation_pct"),
            "follow_status": it.get("follow_status"),
            "note": it.get("note"),
        }
        if it["target_type"] == "community":
            # 小区关注跟踪的是全小区中位单价，本来就没有「捡漏分」。
            # 不能因为 score 为 NULL 就被判成「建议剔除」，否则关注一个小区
            # 第二天就莫名其妙出现在剔除名单里。
            (worse if change >= 0.01 else better).append(entry)
        elif change <= -0.01:
            better.append(entry)          # 继续下探
        elif change >= 0.01:
            worse.append(entry)           # 涨价，建议剔除
        else:
            better.append(entry) if (it.get("score") or 0) >= 60 else worse.append(entry)

    report = {
        "week_start": week_start.isoformat(),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(items),
        "still_worth": sorted(better, key=lambda x: -(x.get("score") or 0)),
        "drop_out": worse,
        "new_comparable": query_all(
            """
            SELECT e.* FROM anomaly_event e
            WHERE e.event_date >= ? AND e.event_type IN ('deep_dip','sudden_drop','auction_deal')
            ORDER BY e.drop_pct DESC LIMIT 20
            """,
            ((today - timedelta(days=7)).isoformat(),),
        ),
    }
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO weekly_report (week_start, content_json) VALUES (?,?)",
            (week_start.isoformat(), json.dumps(report, ensure_ascii=False)),
        )
    return report
