"""市场脉搏：下架追踪 + 全城日快照 + 大盘汇总。

为什么需要它（审查结论）：
  日照这个城市太小，贝壳/安居客上都没有公开的成交记录页 —— 实测：
    · https://rz.ke.com/chengjiao/  会被重定向回二手房列表（无成交数据）
    · https://m.anjuke.com/rz/chengjiao/  直接 404
    · 贝壳小区行情页的「参考均价」也是空值
  既然拿不到「真成交价」，就用能稳定、全自动、无验证码拿到的三件事逼近真实：
    1) 下架追踪：一套房上次还在、连续多日不再出现 → 标记 delisted。
       下架 ≠ 成交（也可能是中介撤牌），但「下架量」是目前唯一能全自动拿到的
       成交活跃度代理，口径必须诚实标注。
    2) 全城日快照：在售总量、中位挂牌单价、当日新增、当日下架，落成时间序列。
    3) 去化近似：在售总量 / 近 30 天日均下架量，明确写成「近似」，不当成交数用。

数据边界：只统计 source_kind='crawled' 的真实采集房源，演示样本不混入大盘。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Iterable

from ..db import get_conn, query_all, query_one

logger = logging.getLogger("rzprobe.market")


def _median(values: Iterable[float]) -> float | None:
    vals = sorted(float(v) for v in values if v)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return round(vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2, 1)


# --------------------------------------------------------------------------
# 哪些来源「今天成功抓过」——只有确认抓取成功的来源才允许判定下架，
# 否则某天没抓这个站，会把整站房源误判成下架。
# --------------------------------------------------------------------------
def _fetched_today_sources(day: date) -> set[str]:
    prefix = day.isoformat()
    got: set[str] = set()
    for r in query_all(
        "SELECT DISTINCT source FROM crawl_log "
        "WHERE substr(created_at,1,10)=? AND status IN ('ok','partial')",
        (prefix,),
    ):
        if r.get("source"):
            got.add(str(r["source"]))
    for r in query_all(
        "SELECT DISTINCT source_key FROM source_import_batch WHERE substr(created_at,1,10)=?",
        (prefix,),
    ):
        if r.get("source_key"):
            got.add(str(r["source_key"]))
    return got


# --------------------------------------------------------------------------
# 下架检测
# --------------------------------------------------------------------------
def detect_delistings(min_gap_days: int = 10, day: date | None = None) -> dict:
    """把「今天成功抓过的来源里、已多日不再出现的在售房源」标记为 delisted。

    保守策略：只有该来源今天确实抓到过、且这套房 last_seen 已早于
    (今天 - min_gap_days) 才判定。宁可晚几天标记，也不把「没翻到那一页」
    误报成下架。
    """
    day = day or date.today()
    fetched = _fetched_today_sources(day)
    if not fetched:
        return {"marked": 0, "sources": {}, "fetched_sources": [],
                "note": "今日无成功抓取的来源，跳过下架判定（不猜）"}

    cutoff = (day - timedelta(days=min_gap_days)).isoformat()
    placeholders = ",".join("?" for _ in fetched)
    rows = query_all(
        f"SELECT id, source, community_id, community_name, last_seen FROM listing "
        f"WHERE status='active' AND source_kind='crawled' AND source IN ({placeholders})",
        tuple(sorted(fetched)),
    )
    by_source: dict[str, int] = {}
    ids: list[int] = []
    for r in rows:
        last_seen = str(r.get("last_seen") or "")
        if not last_seen:          # 从没记过 last_seen 的旧数据不动，避免误伤
            continue
        if last_seen < cutoff:
            ids.append(int(r["id"]))
            by_source[str(r["source"])] = by_source.get(str(r["source"]), 0) + 1

    if ids:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" for _ in chunk)
                conn.execute(
                    f"UPDATE listing SET status='delisted', updated_at=? WHERE id IN ({ph})",
                    (now, *chunk),
                )
        logger.info("下架判定：共标记 %s 套，按来源 %s", len(ids), by_source)
    return {"marked": len(ids), "sources": by_source,
            "fetched_sources": sorted(fetched), "min_gap_days": min_gap_days,
            "note": "下架 ≠ 成交，可能是中介撤牌；仅作成交活跃度的保守代理"}


# --------------------------------------------------------------------------
# 全城日快照
# --------------------------------------------------------------------------
def record_market_daily(day: date | None = None, delist_n: int | None = None) -> dict:
    """把今天的全城大盘落一份快照（只统计真实采集的 crawled 房源）。"""
    day = day or date.today()
    day_s = day.isoformat()
    rows = query_all(
        "SELECT unit_price, first_seen FROM listing "
        "WHERE status='active' AND source_kind='crawled'"
    )
    listing_n = len(rows)
    units = [float(r["unit_price"]) for r in rows if r.get("unit_price")]
    med = _median(units)
    new_n = sum(1 for r in rows if str(r.get("first_seen") or "")[:10] == day_s)
    if delist_n is None:
        row = query_one(
            "SELECT COUNT(*) AS n FROM listing WHERE status='delisted' "
            "AND substr(updated_at,1,10)=?", (day_s,),
        )
        delist_n = int((row or {}).get("n") or 0)

    note = None if listing_n else "暂无真实采集房源，大盘为空（演示数据不计入）"
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO market_daily (day,listing_n,median_unit,new_n,delist_n,note)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(day) DO UPDATE SET
              listing_n=excluded.listing_n, median_unit=excluded.median_unit,
              new_n=excluded.new_n, delist_n=excluded.delist_n, note=excluded.note
            """,
            (day_s, listing_n, med, new_n, int(delist_n), note),
        )

    # 近 90 天下架量写回各小区的 deal_active_90d，让小区页「去化月数」用真实口径。
    try:
        _refresh_deal_activity(day)
    except Exception:  # noqa: BLE001 不影响大盘快照本身
        logger.warning("回填小区成交活跃度失败", exc_info=True)

    return {"day": day_s, "listing_n": listing_n, "median_unit": med,
            "new_n": new_n, "delist_n": int(delist_n)}


def _refresh_deal_activity(day: date) -> None:
    """把「近 90 天下架套数」作为成交活跃度写回 community.deal_active_90d。

    只在确实存在真实采集房源时执行，避免把演示数据的活跃度清零。
    """
    if not query_one("SELECT 1 FROM listing WHERE source_kind='crawled' LIMIT 1"):
        return
    cutoff = (day - timedelta(days=90)).isoformat()
    rows = query_all(
        "SELECT community_id, COUNT(*) AS n FROM listing "
        "WHERE status='delisted' AND community_id IS NOT NULL "
        "AND substr(updated_at,1,10)>=? GROUP BY community_id",
        (cutoff,),
    )
    with get_conn() as conn:
        conn.execute("UPDATE community SET deal_active_90d=0")
        for r in rows:
            conn.execute(
                "UPDATE community SET deal_active_90d=? WHERE id=?",
                (int(r["n"] or 0), int(r["community_id"])),
            )


# --------------------------------------------------------------------------
# 大盘汇总（前端仪表盘 + 推送用）
# --------------------------------------------------------------------------
def _change(series: list[dict], field: str, days: int, today: date) -> float | None:
    if len(series) < 2:
        return None
    target = (today - timedelta(days=days)).isoformat()
    base = None
    for p in series:
        if p["date"] <= target:
            base = p
    if base is None:
        base = series[0]
    if base["date"] == series[-1]["date"]:
        return None
    a, b = base.get(field), series[-1].get(field)
    if not a or b is None:
        return None
    return round((b - a) / a * 100, 2)


def market_overview(days: int = 180) -> dict:
    """全城大盘：库存/中位价/新增/下架的时间序列 + 各周期变化 + 去化近似。"""
    today = date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    rows = query_all("SELECT * FROM market_daily ORDER BY day")
    series = [
        {
            "date": r["day"],
            "listing_n": int(r["listing_n"] or 0),
            "median_unit": r["median_unit"],
            "new_n": int(r["new_n"] or 0),
            "delist_n": int(r["delist_n"] or 0),
        }
        for r in rows if r["day"] >= cutoff
    ]
    current = series[-1] if series else None
    last30 = (today - timedelta(days=30)).isoformat()
    delist_30d = sum(p["delist_n"] for p in series if p["date"] >= last30)
    new_7d = sum(p["new_n"] for p in series if p["date"] >= (today - timedelta(days=7)).isoformat())
    delist_7d = sum(p["delist_n"] for p in series if p["date"] >= (today - timedelta(days=7)).isoformat())
    listing_n = current["listing_n"] if current else 0
    clearance = None
    if listing_n and delist_30d > 0:
        # 月均下架 ≈ 近 30 天下架总量；去化月数 = 在售 / 月均下架
        clearance = round(listing_n / delist_30d, 1)

    return {
        "current": current,
        "series": series,
        "points": len(series),
        "change_7d": _change(series, "median_unit", 7, today),
        "change_30d": _change(series, "median_unit", 30, today),
        "new_7d": new_7d,
        "delist_7d": delist_7d,
        "delist_30d": delist_30d,
        "clearance_months": clearance,
        "clearance_note": (
            "去化近似 = 在售总量 / 近 30 天下架量。下架是成交的保守代理（含中介撤牌），"
            "只能看方向，不能当成交套数。"
            if clearance is not None else
            "下架样本不足，暂无法估算去化速度。"
        ),
        "quality_note": (
            f"共 {len(series)} 个日快照；中位价为全城在售挂牌单价中位数（crawled 口径）。"
            "挂牌价≠成交价，下架≠成交。"
        ),
        "disclaimer": "大盘口径全部来自真实采集房源；演示样本不计入。数据用于观察趋势，不构成投资建议。",
    }
