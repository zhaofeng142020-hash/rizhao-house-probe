"""每日调价明细报告：涨价明细 + 降价明细 + 汇总统计。

数据源：price_history 表（drop_pct < 0 为降价，> 0 为涨价，= 0 为首次观测）。
报告逐日生成并落库 daily_price_report 表，网页端通过 /api/reports/daily 读取，
每次刷新后自动生成当日报告并推送摘要。

口径约定：
- 只统计真实观测到的价格变化（同一房源两次观测价格不同），首次入库不算涨跌。
- 明细字段：小区 / 户型 / 面积 / 原价 / 现价 / 变化金额 / 变化幅度 / 来源 / 链接。
- 汇总字段：涨价套数与幅度均值、降价套数与幅度均值、最大涨跌幅房源。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from ..db import execute, query_all, query_one

logger = logging.getLogger("rzprobe.report")

SOURCE_NAMES = {
    "ke": "贝壳找房", "anjuke": "安居客", "wuba": "58同城",
    "fangtianxia": "房天下", "anjuke_old": "安居客",
    "zhiye": "日照房产网", "ali": "阿里资产", "jd": "京东资产",
    "manual": "人工录入",
}


def _label(source_key: str | None) -> str:
    if not source_key:
        return "未知来源"
    return SOURCE_NAMES.get(source_key, source_name_of(source_key))


def source_name_of(key: str) -> str:
    row = query_one(
        "SELECT source_name FROM listing WHERE source=? AND source_name IS NOT NULL LIMIT 1",
        (key,),
    )
    return row["source_name"] if row else key


def _source_label(item: dict) -> str:
    return item.get("source_name") or SOURCE_NAMES.get(item.get("source") or "", item.get("source") or "未知")


def collect_day_changes(day: str) -> dict:
    """汇总某一天的涨跌明细。

    从 price_history 里取 observed_at=day 且 drop_pct != 0 的记录，
    关联 listing 取展示字段。drop_pct < 0 为降价（旧价 → 新价下降），> 0 为涨价。
    """
    rows = query_all(
        """
        SELECT ph.listing_id, ph.price, ph.unit_price, ph.drop_pct,
               ph.observed_at,
               l.community_name, l.layout, l.area, l.total_price,
               l.source, l.source_name, l.source_url, l.source_kind,
               l.rooms, l.halls
        FROM price_history ph
        LEFT JOIN listing l ON l.id = ph.listing_id
        WHERE ph.observed_at = ? AND ph.drop_pct != 0
        ORDER BY ph.drop_pct ASC
        """,
        (day,),
    )
    up: list[dict] = []
    down: list[dict] = []
    for r in rows:
        old_price = float(r["price"]) / (1 + float(r["drop_pct"])) if r["drop_pct"] else 0
        delta = float(r["price"]) - old_price
        entry = {
            "listing_id": r["listing_id"],
            "community_name": r["community_name"] or "",
            "layout": r["layout"] or "",
            "area": r["area"],
            "old_price": round(old_price, 2),
            "new_price": float(r["price"]),
            "delta": round(delta, 2),
            "delta_pct": round(float(r["drop_pct"]) * 100, 2),
            "unit_price": r["unit_price"],
            "source": _source_label(r),
            "source_url": r["source_url"] or "",
            "source_kind": r["source_kind"] or "",
        }
        if r["drop_pct"] > 0:
            up.append(entry)
        elif r["drop_pct"] < 0:
            down.append(entry)

    up.sort(key=lambda x: -x["delta_pct"])
    down.sort(key=lambda x: x["delta_pct"])

    def _avg(items: list[dict]) -> float:
        return round(sum(abs(i["delta_pct"]) for i in items) / len(items), 2) if items else 0.0

    def _extreme(items: list[dict], keyfn) -> dict | None:
        return max(items, key=keyfn) if items else None

    summary = {
        "up_count": len(up),
        "down_count": len(down),
        "up_avg_pct": _avg(up),
        "down_avg_pct": _avg(down),
        "up_max": _extreme(up, lambda x: x["delta_pct"]),
        "down_max": _extreme(down, lambda x: x["delta"]),
        "total_tracked": int(query_one(
            "SELECT COUNT(*) AS n FROM price_history WHERE observed_at=?", (day,)
        )["n"]),
        "changed_total": len(up) + len(down),
    }
    return {"day": day, "summary": summary, "up": up, "down": down}


def save_report(day: str, data: dict) -> int:
    """报告落库（幂等：同一天重复生成时覆盖）。返回记录 id。"""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    existing = query_one("SELECT id FROM daily_price_report WHERE day=?", (day,))
    if existing:
        execute(
            "UPDATE daily_price_report SET payload=?, updated_at=datetime('now','localtime') WHERE id=?",
            (payload, int(existing["id"])),
        )
        return int(existing["id"])
    return execute(
        "INSERT INTO daily_price_report (day, payload) VALUES (?,?)", (day, payload)
    )


def generate_today(push: bool = False) -> dict:
    """生成当天报告：入库 + 返回。刷新调度器每次刷新后调用。"""
    day = date.today().isoformat()
    data = collect_day_changes(day)
    save_report(day, data)
    logger.info("每日调价报告已生成：%s 涨 %s / 降 %s",
                day, data["summary"]["up_count"], data["summary"]["down_count"])
    if push and (data["summary"]["up_count"] or data["summary"]["down_count"]):
        _push_report(data)
    return data


def get_report(day: str) -> dict | None:
    """读某天报告；没有落库的就地计算（不落库），保证任意历史日期可查。"""
    row = query_one("SELECT payload FROM daily_price_report WHERE day=?", (day,))
    if row:
        try:
            return json.loads(row["payload"])
        except Exception:  # noqa: BLE001
            pass
    return collect_day_changes(day)


def list_report_days(limit: int = 30) -> list[dict]:
    """近 N 天有调价记录的日期 + 涨跌套数概览（网页日历/下拉用）。"""
    rows = query_all(
        """
        SELECT observed_at AS day,
               SUM(CASE WHEN drop_pct > 0 THEN 1 ELSE 0 END) AS up_count,
               SUM(CASE WHEN drop_pct < 0 THEN 1 ELSE 0 END) AS down_count
        FROM price_history
        GROUP BY observed_at
        ORDER BY observed_at DESC LIMIT ?
        """,
        (max(1, min(limit, 90)),),
    )
    return rows


def _fmt_price(v: float) -> str:
    return f"{v / 10000:.1f}万" if v >= 10000 else f"{v:.0f}元"


def _push_report(data: dict) -> dict | None:
    """把当日报告摘要推送到企微/站内。失败不影响主流程。"""
    try:
        from .alert_sender import AlertSender
    except ImportError:
        return None
    s = data["summary"]
    lines = [f"【每日调价报告】{data['day']}"]
    lines.append(f"涨价 {s['up_count']} 套（平均 +{s['up_avg_pct']}%），"
                 f"降价 {s['down_count']} 套（平均 {s['down_avg_pct']}%）")
    for tag, items, arrow in (("涨", data["up"], "↑"), ("降", data["down"], "↓")):
        for i in items[:3]:
            lines.append(f"· {arrow} {i['community_name']} {i['layout']} {i['area']}㎡："
                         f"{_fmt_price(i['old_price'])} → {_fmt_price(i['new_price'])}"
                         f"（{i['delta_pct']:+.1f}%，{_source_label_cached(i['source'])}）")
        if len(items) > 3:
            lines.append(f"· {tag}价共 {len(items)} 套，详见网页「每日调价报告」")
    text = "\n".join(lines)
    try:
        return AlertSender().send_message(
            {"title": f"每日调价报告 {data['day']}", "text": text, "markdown": text, "score": 100},
            "report", int(datetime.now().timestamp()), "yellow", 0, force=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("报告推送失败：%s", exc)
        return None


def _source_label_cached(label: str) -> str:
    return label
