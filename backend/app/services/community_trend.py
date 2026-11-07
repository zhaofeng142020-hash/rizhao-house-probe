"""小区房价波动：按日快照 + 由历史调价记录回推的周度序列。

为什么单独建快照表：community 表里没有价格字段，小区中位价是每次请求
从在售 listing 现算的「此刻值」。不落时间序列，就谈不上「波动」。

两条数据来源，口径在返回里如实标注，不混为一谈：
  - daily    当日全量在售房源的中位挂牌单价（每日管线跑一次）
  - backfill 由 price_history 的每次调价 + first_price/first_seen 回推出的
             历史某日「每套房的在挂价」再取中位，每周取一个点。
             这是推算值，不是当日全量快照，不能当成交价用。
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Iterable

from ..config import settings
from ..db import get_conn, query_all, query_one


def _median(values: Iterable[float]) -> float | None:
    vals = sorted(float(v) for v in values if v)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return round(vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2, 1)


def snapshot_all(day: date | None = None) -> dict:
    """把「今天」每个小区的在售价格分布落一份快照。重复跑只覆盖当天。"""
    day = day or date.today()
    rows = query_all(
        """
        SELECT community_id, unit_price, area, total_price
        FROM listing
        WHERE status='active' AND community_id IS NOT NULL AND community_id > 0
        """
    )
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(int(r["community_id"]), []).append(r)

    ta_min = float(settings.TARGET_AREA_MIN)
    ta_max = float(settings.TARGET_AREA_MAX)
    saved = 0
    with get_conn() as conn:
        for cid, items in grouped.items():
            units = [float(x["unit_price"]) for x in items if x.get("unit_price")]
            med = _median(units)
            conn.execute(
                """
                INSERT INTO community_price_snapshot
                  (community_id,observed_date,listing_n,median_unit,avg_unit,min_unit,max_unit,
                   target_area_n,data_kind)
                VALUES (?,?,?,?,?,?,?,?,'daily')
                ON CONFLICT(community_id,observed_date) DO UPDATE SET
                  listing_n=excluded.listing_n, median_unit=excluded.median_unit,
                  avg_unit=excluded.avg_unit, min_unit=excluded.min_unit,
                  max_unit=excluded.max_unit, target_area_n=excluded.target_area_n,
                  data_kind='daily'
                """,
                (
                    cid, day.isoformat(), len(items), med,
                    round(sum(units) / len(units), 1) if units else None,
                    round(min(units), 1) if units else None,
                    round(max(units), 1) if units else None,
                    sum(1 for x in items if ta_min <= float(x["area"] or 0) <= ta_max),
                ),
            )
            saved += 1
    return {"date": day.isoformat(), "communities": saved, "listings": len(rows)}


def backfill_from_history(days: int = 180) -> dict:
    """从已有调价历史回推过去 N 天的周度中位价。

    只使用库里已经存在的记录，不生成任何新价格。若已有 daily 快照的日期
    优先保留 daily，回推点不覆盖它。
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    listings = query_all(
        "SELECT id, community_id, area, first_price, total_price, first_seen FROM listing "
        "WHERE community_id IS NOT NULL AND community_id > 0"
    )
    histories = query_all(
        "SELECT listing_id, observed_at, price FROM price_history ORDER BY observed_at, id"
    )
    by_listing: dict[int, list[tuple[str, float]]] = {}
    for h in histories:
        by_listing.setdefault(int(h["listing_id"]), []).append(
            (str(h["observed_at"]), float(h["price"] or 0))
        )

    # 每套房的价格时间线：首次挂牌价 + 之后的每次调价
    timelines: list[dict] = []
    all_dates: set[str] = set()
    for li in listings:
        first_seen = str(li.get("first_seen") or cutoff)[:10]
        first_price = float(li.get("first_price") or li.get("total_price") or 0)
        events: list[tuple[str, float]] = [(first_seen, first_price)]
        events.extend(by_listing.get(int(li["id"]), []))
        events = sorted({d: p for d, p in events}.items())
        if not events or first_price <= 0:
            continue
        area = float(li.get("area") or 0)
        if area <= 0:
            continue
        timelines.append({"cid": int(li["community_id"]), "area": area, "events": events})
        all_dates.update(d for d, _ in events)

    dates = sorted(d for d in all_dates if d >= cutoff)
    if not dates:
        return {"weeks": 0, "points": 0, "reason": "没有可回推的历史记录"}

    # 每周取该周最后一个有记录的日期，避免图表塞满无意义的日历天
    week_last: dict[str, str] = {}
    for d in dates:
        try:
            dt = date.fromisoformat(d)
        except ValueError:
            continue
        key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        week_last[key] = max(week_last.get(key, d), d)

    points = 0
    with get_conn() as conn:
        for week_day in sorted(week_last.values()):
            grouped: dict[int, list[float]] = {}
            for t in timelines:
                price = None
                for d, p in t["events"]:
                    if d <= week_day:
                        price = p
                    else:
                        break
                if price is None:      # 这周还没挂牌，不计入样本
                    continue
                grouped.setdefault(t["cid"], []).append(price / t["area"])
            for cid, units in grouped.items():
                exists = conn.execute(
                    "SELECT 1 FROM community_price_snapshot WHERE community_id=? AND observed_date=?",
                    (cid, week_day),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO community_price_snapshot "
                    "(community_id,observed_date,listing_n,median_unit,min_unit,max_unit,data_kind) "
                    "VALUES (?,?,?,?,?,?,'backfill')",
                    (cid, week_day, len(units), _median(units),
                     round(min(units), 1), round(max(units), 1)),
                )
                points += 1
    return {"weeks": len(week_last), "points": points, "listings": len(timelines)}


def _change(series: list[dict], days: int) -> float | None:
    """N 天前到现在的变动百分比。取「不晚于目标日」的最近一个快照。"""
    if len(series) < 2 or not series[-1].get("median_unit"):
        return None
    target = (date.today() - timedelta(days=days)).isoformat()
    base = None
    for p in series:
        if p["date"] <= target:
            base = p
    if base is None:
        base = series[0]            # 历史不足 N 天，用最早一点
    if base["date"] == series[-1]["date"] or not base.get("median_unit"):
        return None
    return round((series[-1]["median_unit"] - base["median_unit"]) / base["median_unit"] * 100, 2)


def trend(community_id: int, days: int = 180) -> dict:
    """一个小区的价格走势 + 各周期涨跌 + 数据口径说明。"""
    c = query_one("SELECT id,name,district FROM community WHERE id=?", (community_id,))
    if not c:
        return {}
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = query_all(
        "SELECT observed_date,median_unit,listing_n,data_kind FROM community_price_snapshot "
        "WHERE community_id=? AND observed_date>=? ORDER BY observed_date",
        (community_id, cutoff),
    )
    series = [
        {
            "date": r["observed_date"],
            "median_unit": r["median_unit"],
            "listing_n": int(r["listing_n"] or 0),
            "data_kind": r["data_kind"],
        }
        for r in rows if r.get("median_unit")
    ]
    values = [p["median_unit"] for p in series]
    deltas = [
        (values[i] - values[i - 1]) / values[i - 1] * 100
        for i in range(1, len(values)) if values[i - 1]
    ]
    current = values[-1] if values else None
    backfill_n = sum(1 for p in series if p["data_kind"] == "backfill")

    return {
        "community": dict(c),
        "series": series,
        "points": len(series),
        "current": current,
        "first": values[0] if values else None,
        "high": round(max(values), 1) if values else None,
        "low": round(min(values), 1) if values else None,
        "change_7d": _change(series, 7),
        "change_30d": _change(series, 30),
        "change_90d": _change(series, 90),
        "change_all": (
            round((values[-1] - values[0]) / values[0] * 100, 2)
            if len(values) >= 2 and values[0] else None
        ),
        "volatility": round(statistics.pstdev(deltas), 2) if len(deltas) >= 2 else None,
        "backfill_points": backfill_n,
        "quality_note": (
            f"共 {len(series)} 个观测点，其中 {backfill_n} 个为历史调价记录回推的周度估算值；"
            "回推点只反映发生过调价的样本，不等于当日全量挂牌，更不是成交价。"
        ),
        "disclaimer": "挂牌价≠成交价。小区中位单价随在售房源结构变化，结构性变动不等于同一套房降价。",
    }


def followed_overview() -> dict:
    """「我关注的小区」清单 + 各自最新波动，供观察清单页直接用。"""
    rows = query_all(
        """
        SELECT w.id AS watch_id, w.target_id, w.note, w.follow_status, w.created_at,
               c.name, c.district
        FROM watch_item w
        JOIN community c ON c.id=w.target_id
        WHERE w.target_type='community'
        ORDER BY w.id DESC
        """
    )
    items = []
    for r in rows:
        t = trend(int(r["target_id"]), days=90)
        items.append({
            "watch_id": r["watch_id"],
            "id": r["target_id"],
            "name": r["name"],
            "district": r["district"],
            "note": r["note"],
            "follow_status": r["follow_status"],
            "created_at": r["created_at"],
            "current": t.get("current"),
            "change_7d": t.get("change_7d"),
            "change_30d": t.get("change_30d"),
            "change_90d": t.get("change_90d"),
            "points": t.get("points", 0),
            "series": t.get("series", []),
        })
    return {"total": len(items), "items": items}


def watched_moves(threshold_pct: float = 2.0) -> list[dict]:
    """关注小区里，近 7 天中位价变动超过阈值的，生成一条待推送提醒文案。

    只播报「挂牌中位价变动」这一件可验证的事，不做涨跌原因的臆测。
    """
    out = []
    for item in followed_overview()["items"]:
        ch = item.get("change_7d")
        if ch is None or abs(ch) < threshold_pct:
            continue
        direction = "下探" if ch < 0 else "抬头"
        text = (
            f"【关注小区·价格波动】{item['name']}（{item['district']}）\n"
            f"近 7 天挂牌中位单价 {direction} {abs(ch):.1f}%，"
            f"当前 {item['current']:.0f} 元/㎡。\n"
            "提醒：中位价变动可能来自在售房源结构变化（新挂高价/低价房、低价房成交下架），"
            "不等于同一套房降价。请点进小区看具体是哪几套在调价。\n"
            "挂牌价≠成交价，捡漏分仅作观察参考，最终以实地尽调为准。"
        )
        out.append({
            "community_id": item["id"],
            "name": item["name"],
            "change_7d": ch,
            "current": item["current"],
            "title": f"【日照房价探针】{item['name']} 中位价{direction} {abs(ch):.1f}%",
            "text": text,
            "markdown": f"### 关注小区 · 价格波动\n**{item['name']}** 近 7 天中位单价{direction} **{abs(ch):.1f}%**，"
                        f"当前 {item['current']:.0f} 元/㎡。\n\n"
                        "> 中位价变动可能来自在售房源结构变化，不等于同一套房降价。\n"
                        "挂牌价≠成交价，捡漏分仅作观察参考，最终以实地尽调为准。",
        })
    return out
