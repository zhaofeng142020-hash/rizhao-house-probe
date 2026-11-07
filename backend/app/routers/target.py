"""重点关注面积段（默认 120-150㎡）专题接口。

为什么单独做一个面积段专题：
120-150㎡ 在日照属于典型改善型。它有三个反直觉的特征，必须让用户先知道：
  1) 总价高但客群窄 —— 转手周期普遍比 90-110㎡ 长；
  2) 同小区里，大面积单价通常低于小面积（「大面积折价」），
     所以「面积大单价低」不一定是漏，可能只是市场对它的正常定价；
  3) 正因为难卖，议价空间反而更大 —— 这才是真正能用上的地方。
本接口把这三件事用数据算出来，而不是空口提示。
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..db import query_all, query_one
from ..services.afford import buy_cost
from . import common

router = APIRouter(prefix="/api/target", tags=["重点关注"])

SIZE_NOTE = (
    "120-150㎡ 属改善型：总价高、客群窄，转手普遍慢于 90-110㎡。"
    "同小区里大面积单价常低于小面积 3%~8%，所以「单价便宜」先别当成漏，"
    "但正因为难卖，议价空间也比小面积更大。"
)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    return round(values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2, 1)


def size_premium(min_area: float, max_area: float) -> dict | None:
    """同小区内「大面积 vs 小面积」的单价差（负数=大面积更便宜，属正常市场现象）。"""
    rows = query_all(
        "SELECT community_id, area, unit_price FROM listing "
        "WHERE status='active' AND unit_price>0 AND community_id IS NOT NULL"
    )
    big: dict[int, list[float]] = {}
    small: dict[int, list[float]] = {}
    for r in rows:
        area = float(r["area"] or 0)
        cid = int(r["community_id"])
        if min_area <= area <= max_area:
            big.setdefault(cid, []).append(float(r["unit_price"]))
        elif area < min_area:
            small.setdefault(cid, []).append(float(r["unit_price"]))
    # 只统计同小区两边都有样本的，否则不可比
    diffs = []
    for cid, b in big.items():
        s = small.get(cid) or []
        if len(b) >= 1 and len(s) >= 1:
            bm, sm = _median(b), _median(s)
            if bm and sm:
                diffs.append((bm - sm) / sm * 100)
    if not diffs:
        return None
    return {
        "communities": len(diffs),
        "avg_gap_pct": round(sum(diffs) / len(diffs), 1),
        "note": (
            f"同小区内 {min_area:.0f}-{max_area:.0f}㎡ 的单价平均比小面积"
            f"{'低' if sum(diffs) / len(diffs) < 0 else '高'} "
            f"{abs(sum(diffs) / len(diffs)):.1f}%（{len(diffs)} 个小区可比）"
        ),
    }


@router.get("")
def target_area(
    min_area: float = Query(120, description="面积下限"),
    max_area: float = Query(150, description="面积上限"),
    district: str | None = None,
    sort: str = Query("score", description="score / unit / total / area"),
):
    where = ["l.status='active'", "l.area BETWEEN ? AND ?"]
    params: list = [min_area, max_area]
    if district:
        where.append("l.district=?")
        params.append(district)
    order = {
        "score": "COALESCE(s.score,0) DESC",
        "unit": "l.unit_price ASC",
        "total": "l.total_price ASC",
        "area": "l.area DESC",
    }.get(sort, "COALESCE(s.score,0) DESC")

    listings = [
        common.normalize_listing(r)
        for r in query_all(
            f"{common.LISTING_SELECT} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 200",
            tuple(params),
        )
    ]

    aw = ["a.status='active'", "a.area BETWEEN ? AND ?"]
    ap: list = [min_area, max_area]
    if district:
        aw.append("a.district=?")
        ap.append(district)
    auctions = [
        common.normalize_auction(r)
        for r in query_all(
            f"{common.AUCTION_SELECT} WHERE {' AND '.join(aw)} ORDER BY COALESCE(s.score,0) DESC",
            tuple(ap),
        )
    ]

    nw = ["n.status='active'", "COALESCE(n.area_max, 999)>=?", "COALESCE(n.area_min, 0)<=?"]
    np_: list = [min_area, max_area]
    if district:
        nw.append("n.district=?")
        np_.append(district)
    new_homes = query_all(
        f"SELECT * FROM new_home n WHERE {' AND '.join(nw)} ORDER BY n.avg_unit_price ASC", tuple(np_)
    )
    for n in new_homes:
        n["risk_tags"] = common.loads(n.get("risk_tags"), [])
        a = max(min_area, float(n.get("area_min") or min_area))
        total = round(float(n["avg_unit_price"] or 0) * a)
        c = buy_cost(total, a, is_new=True, decoration=n.get("decoration"))
        n["entry_area"] = a
        n["entry_total"] = total
        n["entry_monthly"] = c["monthly"]
        n["entry_cash"] = c["cash_needed"]

    units = [float(x["unit_price"]) for x in listings if x.get("unit_price")]
    totals = [float(x["total_price"]) for x in listings if x.get("total_price")]

    # 给每套二手附上「月供 / 现金门槛」：改善型总价高，月供比单价更能决定买不买得起
    pref = query_one("SELECT * FROM user_pref WHERE id=1") or {}
    down_ratio = float(pref.get("down_ratio") or 0.30)
    years = int(pref.get("loan_years") or 30)
    rate = float(pref.get("loan_rate") or 0.031)
    for x in listings:
        c = buy_cost(
            float(x.get("total_price") or 0), float(x.get("area") or 0),
            down_ratio=down_ratio, years=years, annual_rate=rate,
            is_first_home=bool(pref.get("is_first_home", 1)),
            decoration=x.get("decoration"),
        )
        x["monthly"] = c["monthly"]
        x["cash_needed"] = c["cash_needed"]

    return {
        "range": {"min": min_area, "max": max_area},
        "summary": {
            "listing_n": len(listings),
            "auction_n": len(auctions),
            "new_home_n": len(new_homes),
            "median_unit": _median(units),
            "median_total": _median(totals),
            "red_n": sum(1 for x in listings if x.get("grade") == "red"),
            "yellow_n": sum(1 for x in listings if x.get("grade") == "yellow"),
            "size_premium": size_premium(min_area, max_area),
            "note": SIZE_NOTE,
            "loan": {"down_ratio": down_ratio, "years": years, "rate": rate},
        },
        "listings": listings,
        "auctions": auctions,
        "new_homes": new_homes,
        "disclaimer": "面积段结论来自本库在挂数据，样本随抓取更新变化；大面积是否适合你，取决于家庭结构与持有年限。",
    }
