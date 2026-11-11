"""每日调价报告 API：网页端查看涨跌明细。

公开读接口（与 /api/sources 同口径——数据本身公开可核验）：
  GET /api/reports/daily?day=YYYY-MM-DD   某天报告（缺省=今天，历史日期就地计算）
  GET /api/reports/days                   有调价记录的日期列表
  GET /api/reports/latest                 最近一份有内容的报告
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from ..db import query_one
from ..services import price_report

router = APIRouter(prefix="/api/reports", tags=["调价报告"])


@router.get("/daily")
def daily_report(day: str | None = Query(default=None, description="YYYY-MM-DD，缺省今天")):
    day = (day or date.today().isoformat()).strip()
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD") from None
    return price_report.get_report(day)


@router.get("/days")
def report_days(limit: int = 30):
    return {"items": price_report.list_report_days(limit=limit)}


@router.get("/latest")
def latest_report():
    row = query_one(
        "SELECT day FROM daily_price_report ORDER BY day DESC LIMIT 1"
    )
    if row:
        return price_report.get_report(row["day"])
    # 还没生成过报告时回退到「今天有数据就算今天」
    today = date.today().isoformat()
    data = price_report.collect_day_changes(today)
    if data["summary"]["changed_total"]:
        return data
    # 今天没有就往前找最近 7 天有调价的
    for i in range(1, 8):
        d = (date.today() - timedelta(days=i)).isoformat()
        data = price_report.collect_day_changes(d)
        if data["summary"]["changed_total"]:
            return data
    return price_report.collect_day_changes(today)


@router.get("/range")
def range_report(days: int = 7):
    """近 N 天涨跌趋势汇总（图表用）。"""
    days = max(1, min(days, 30))
    out = []
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        data = price_report.collect_day_changes(d)
        s = data["summary"]
        out.append({
            "day": d,
            "up_count": s["up_count"],
            "down_count": s["down_count"],
            "up_avg_pct": s["up_avg_pct"],
            "down_avg_pct": s["down_avg_pct"],
        })
    out.reverse()
    return {"items": out}
