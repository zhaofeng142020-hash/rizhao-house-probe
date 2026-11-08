"""真实成交价锚点：法拍成交结果的聚合、折扣曲线与口径说明。

为什么单独一层：
  PR#1 已经把 `auction.deal_price / deal_date` 字段和成交回填接口做出来了，
  但成交价只能靠接口录入、回填完就从界面消失，也没有任何聚合视图。
  这里把「已回填的真实成交价」整理成前端能直接消费的结构，并统一承担口径声明。

口径红线（改这里时别改松）：
  · 法拍成交价 ≠ 二手成交价，不能当成全城成交均价；
  · 下架 ≠ 成交、挂牌价 ≠ 成交价；
  · 成交价来自人工 / CSV 回填，不是自动采集；
  · 演示样本（source_kind='demo'）一律排除，不许把假数据画进真实曲线；
  · 样本少就如实说样本少，不做统计推断。
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Iterable

from ..db import query_all

DISCLAIMER = (
    "法拍成交是本项目目前唯一的真实成交价锚点，但法拍成交价 ≠ 二手成交价，"
    "不能当作全城成交均价。成交价来自人工/CSV 回填，不是自动采集；"
    "挂牌价 ≠ 成交价，下架 ≠ 成交，结论不构成投资建议。"
)
SMALL_SAMPLE_NOTE = (
    "成交样本不足 5 条，折扣曲线不构成统计结论，只能逐条核对个案。"
    "法拍成交价 ≠ 二手成交价，请不要用它推算全城成交均价。"
)


def _median(values: Iterable[float]) -> float | None:
    """中位数：保留 0 与负数（折价率可以为负），只丢 None。"""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return round(vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2, 2)


def _pos(value) -> float | None:
    """正有限数，否则 None —— 挡住 0 / NULL / NaN / inf / 脏字符串，避免除零。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def deal_metrics(row: dict, listing_median_unit: float | None = None) -> dict:
    """纯函数：一条成交记录的全部派生指标。无 IO，可直接单测。

    - deal_unit             成交单价 = 成交总价 / 建筑面积
    - appraisal_discount_pct 相对评估价的折价率（正数=比评估价便宜）
    - over_start_pct        高出起拍价的幅度（竞拍溢价）
    - deal_vs_listing_pct   成交单价相对同小区在挂挂牌单价中位的偏离（正数=比挂牌便宜）
    """
    deal_price = _pos(row.get("deal_price"))
    area = _pos(row.get("area"))
    appraisal = _pos(row.get("appraisal_price"))
    start = _pos(row.get("start_price"))
    med = _pos(listing_median_unit)

    raw_date = str(row.get("deal_date") or "").strip()
    deal_date = raw_date[:10] if raw_date else None

    deal_unit = round(deal_price / area, 1) if deal_price and area else None
    appraisal_discount_pct = (
        round((1 - deal_price / appraisal) * 100, 2) if deal_price and appraisal else None
    )
    over_start_pct = (
        round((deal_price - start) / start * 100, 2) if deal_price and start else None
    )
    deal_vs_listing_pct = (
        round((med - deal_unit) / med * 100, 2) if med and deal_unit else None
    )

    return {
        "deal_price": deal_price,
        "deal_date": deal_date,
        "deal_unit": deal_unit,
        "appraisal_discount_pct": appraisal_discount_pct,
        "over_start_pct": over_start_pct,
        "listing_median_unit": round(med, 1) if med else None,
        "deal_vs_listing_pct": deal_vs_listing_pct,
    }


def _listing_medians() -> dict[str, float | None]:
    """一次查全量在挂挂牌单价，内存分组算中位 —— 避免逐条成交查库（N+1）。"""
    rows = query_all(
        "SELECT community_name, unit_price FROM listing "
        "WHERE status='active' AND unit_price > 0"
    )
    grouped: dict[str, list[float]] = {}
    for r in rows:
        name = (r.get("community_name") or "").strip()
        if not name:
            continue
        grouped.setdefault(name, []).append(float(r["unit_price"]))
    return {name: _median(vals) for name, vals in grouped.items()}


def deal_summary(days: int = 730) -> dict:
    """成交锚点汇总 + 按月折扣曲线。空数据时返回结构完整的空对象。

    days 只按 deal_date 回看；日期未填的行仍逐条列出，但不进月度曲线。
    """
    days = max(1, int(days))
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = query_all(
        """
        SELECT a.* FROM auction a
        WHERE a.deal_price IS NOT NULL AND a.deal_price > 0
          AND COALESCE(a.source_kind, 'demo') <> 'demo'
          AND (a.deal_date IS NULL OR substr(a.deal_date, 1, 10) >= ?)
        ORDER BY a.deal_date DESC, a.id DESC
        """,
        (cutoff,),
    )
    medians = _listing_medians() if rows else {}

    items: list[dict] = []
    for r in rows:
        name = (r.get("community_name") or "").strip()
        m = deal_metrics(r, medians.get(name))
        items.append({
            "auction_id": int(r["id"]),
            "title": r.get("title"),
            "community_name": r.get("community_name"),
            "district": r.get("district"),
            "area": r.get("area"),
            "stage": r.get("stage"),
            "stage_sold": r.get("status"),
            **m,
            "appraisal_price": r.get("appraisal_price"),
            "start_price": r.get("start_price"),
            "source_name": r.get("source_name") or r.get("source"),
            "source_url": r.get("source_url") or None,
        })

    # 按月聚合：只画有日期的点；样本不足的月份照样输出，由前端淡出
    periods: dict[str, list[dict]] = {}
    for it in items:
        d = it.get("deal_date")
        if not d or len(d) < 7:
            continue
        periods.setdefault(d[:7], []).append(it)
    by_period = [
        {
            "period": p,
            "n": len(group),
            "median_discount_pct": _median(
                [x["appraisal_discount_pct"] for x in group
                 if x["appraisal_discount_pct"] is not None]
            ),
            "median_deal_unit": _median(
                [x["deal_unit"] for x in group if x["deal_unit"] is not None]
            ),
        }
        for p, group in sorted(periods.items())
    ]

    dated = sorted(x["deal_date"] for x in items if x.get("deal_date"))
    summary = {
        "median_discount_pct": _median(
            [x["appraisal_discount_pct"] for x in items
             if x["appraisal_discount_pct"] is not None]
        ),
        "median_deal_unit": _median(
            [x["deal_unit"] for x in items if x["deal_unit"] is not None]
        ),
        "median_over_start_pct": _median(
            [x["over_start_pct"] for x in items if x["over_start_pct"] is not None]
        ),
        "earliest": dated[0] if dated else None,
        "latest": dated[-1] if dated else None,
    }
    total = len(items)
    quality_note = (
        SMALL_SAMPLE_NOTE if total < 5 else
        f"共 {total} 条真实成交锚点，来自人工 / CSV 回填；"
        f"其中 {len(by_period)} 个月份有成交日期。"
        "逐条都可在「成交明细」里点原始公告页复核。法拍成交价 ≠ 二手成交价。"
    )

    return {
        "total": total,
        "items": items,
        "by_period": by_period,
        "summary": summary,
        "days": days,
        "quality_note": quality_note,
        "disclaimer": DISCLAIMER,
    }
