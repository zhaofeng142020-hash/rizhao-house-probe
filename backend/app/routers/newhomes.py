"""新房（在售楼盘）接口：新盘与周边二手的价格倒挂对比。

为什么新房值得单独一屏：
自住买房最常见的一个错觉是「新房 1.1 万，旁边二手 1.2 万，新房便宜」。
但两者不可直接比价——
  · 新房多为毛坯，装修要另花 1000~1500 元/㎡；
  · 新房得房率通常 75%~80%，二手 80%~85%，同样的「建筑面积」，
    新房实际能用的更少；
  · 新房有交付风险（延期、减配、精装降标），二手是现房。
所以这里同时给「名义倒挂」和「调整后倒挂」两个数，
并在调整后仍然倒挂时才认为值得看——否则就是被建筑面积骗了。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from ..db import query_all, query_one
from ..services.afford import buy_cost, monthly_payment
from . import common

router = APIRouter(prefix="/api/new-homes", tags=["新房"])


def adjusted_gap(new_unit: float, used_unit: float, decoration: str | None,
                 decor_cost: float | None = None) -> dict:
    """新房 vs 二手的可比价差。

    名义倒挂 = 1 - 新房均价 / 二手中位（正数表示新房更便宜）
    调整后倒挂 = 换算到「套内 + 含装修」后的口径，负值说明名义便宜其实是假的
    """
    if not new_unit or not used_unit:
        return {"nominal_gap": None, "adjusted_gap": None, "new_eff_unit": None, "used_eff_unit": None}

    decor_cost = settings.NEW_DECOR_COST if decor_cost is None else decor_cost
    is_bare = (decoration or "毛坯") == "毛坯"
    new_landed = new_unit + (decor_cost if is_bare else 0.0)

    new_eff = new_landed / settings.NEW_EFFICIENCY      # 每平米套内要花的钱
    used_eff = used_unit / settings.USED_EFFICIENCY
    return {
        "nominal_gap": round((1 - new_unit / used_unit) * 100, 1),
        "adjusted_gap": round((1 - new_eff / used_eff) * 100, 1),
        "new_eff_unit": round(new_eff),
        "used_eff_unit": round(used_eff),
        "decor_added": decor_cost if is_bare else 0,
        "is_bare": is_bare,
    }


def verdict(adj: float | None, risks: list[str]) -> tuple[str, str]:
    """给出判读结论。调整后仍倒挂且无高危标签才叫机会。"""
    strict_risk = {"交付风险", "烂尾", "教育配套未落地", "土地年限不足"}
    has_strict = bool(strict_risk & set(risks))
    if adj is None:
        return ("unknown", "同板块缺少二手可比样本，无法判断倒挂")
    if adj >= 8 and not has_strict:
        return ("opportunity", "调整装修与得房率后仍明显低于周边二手，属于真倒挂，值得实地看")
    if adj >= 5 and has_strict:
        # 有交付/配套硬风险时门槛降低：价格再便宜也不能当机会讲
        return ("caution", "价格确实低于周边二手，但存在交付或配套硬风险，先查预售资金监管与工程进度")
    if adj >= 2:
        return ("slight", "调整后小幅倒挂，属于正常定价差异，不作为捡漏理由")
    if adj >= -3:
        return ("neutral", "与周边二手基本持平，比的就是产品与交付确定性")
    return ("premium", "调整后比二手还贵，溢价来自「新房」本身，自住喜欢可以买，但别当捡漏")


@router.get("")
def list_new_homes(
    district: str | None = None,
    min_area: float | None = None,
    max_area: float | None = Query(None, description="关注面积段：返回在售面积段与之重叠的楼盘"),
    sort: str = Query("gap", description="gap / price / delivery"),
    keyword: str | None = None,
):
    where = ["n.status='active'"]
    params: list = []
    if district:
        where.append("n.district=?")
        params.append(district)
    if keyword and keyword.strip():
        where.append("(n.name LIKE ? ESCAPE '\\' OR COALESCE(n.developer,'') LIKE ? ESCAPE '\\' "
                     "OR COALESCE(n.address,'') LIKE ? ESCAPE '\\' OR n.district LIKE ? ESCAPE '\\')")
        kw = "%" + keyword.strip().replace("%", "\\%").replace("_", "\\_") + "%"
        params.extend([kw, kw, kw, kw])
    if min_area is not None:
        where.append("COALESCE(n.area_max, 999)>=?")
        params.append(min_area)
    if max_area is not None:
        where.append("COALESCE(n.area_min, 0)<=?")
        params.append(max_area)

    rows = query_all(f"SELECT * FROM new_home n WHERE {' AND '.join(where)}", tuple(params))

    # 同板块二手中位单价（一次算好，避免每行重复查库）
    buckets: dict[str, list[float]] = {}
    for r in query_all(
        "SELECT district AS d, unit_price AS u FROM listing "
        "WHERE status='active' AND unit_price>0"
    ):
        buckets.setdefault(r["d"], []).append(float(r["u"]))

    def median_of(d: str) -> float | None:
        vals = sorted(buckets.get(d) or [])
        if not vals:
            return None
        mid = len(vals) // 2
        return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 1)

    items = []
    for r in rows:
        risks = common.loads(r.get("risk_tags"), [])
        used_unit = median_of(r["district"] or "")
        gap = adjusted_gap(float(r["avg_unit_price"] or 0), float(used_unit or 0),
                           r.get("decoration"))
        v_key, v_text = verdict(gap.get("adjusted_gap"), risks)
        min_area_val = r.get("area_min") or 0
        total_min = round(float(r["avg_unit_price"] or 0) * float(min_area_val)) if min_area_val else None
        # 按最小面积算月供门槛，让「总价看起来不高」的错觉立刻现形
        cost = buy_cost(total_min, min_area_val, is_new=True, decoration=r.get("decoration")) if total_min else None
        items.append({
            **{k: v for k, v in r.items() if k not in ("status",)},
            "risk_tags": risks,
            "used_median_unit": used_unit,
            **gap,
            "verdict": v_key,
            "verdict_text": v_text,
            "entry_total": total_min,
            "entry_monthly": cost["monthly"] if cost else None,
            "entry_cash": cost["cash_needed"] if cost else None,
        })

    order = {
        "gap": lambda x: (x["adjusted_gap"] is None, -(x["adjusted_gap"] or -999)),
        "price": lambda x: x["avg_unit_price"] or 0,
        "delivery": lambda x: x.get("delivery_date") or "9999",
    }.get(sort, lambda x: 0)
    items.sort(key=order)

    return {
        "total": len(items),
        "items": items,
        "note": "倒挂率已按「毛坯加装修成本 + 得房率折算」调整；正数=新房更便宜。",
        "disclaimer": "新房价格与交付时间以售楼处公示与预售许可证为准，本页仅为公开信息汇总。",
    }


@router.get("/{new_home_id}")
def new_home_detail(new_home_id: int):
    row = query_one("SELECT * FROM new_home WHERE id=?", (new_home_id,))
    if not row:
        raise HTTPException(404, "楼盘不存在")
    item = {**row, "risk_tags": common.loads(row.get("risk_tags"), [])}

    used = query_all(
        "SELECT unit_price FROM listing WHERE district=? AND status='active' AND unit_price>0",
        (row["district"],),
    )
    vals = sorted(float(x["unit_price"]) for x in used)
    used_median = vals[len(vals) // 2] if vals else None
    gap = adjusted_gap(float(row["avg_unit_price"] or 0), float(used_median or 0), row.get("decoration"))
    v_key, v_text = verdict(gap.get("adjusted_gap"), item["risk_tags"])

    # 面积段内的到手成本对照
    scenario = []
    for a in [row.get("area_min"), row.get("area_max")]:
        if not a:
            continue
        total = round(float(row["avg_unit_price"] or 0) * float(a))
        c = buy_cost(total, float(a), is_new=True, decoration=row.get("decoration"))
        scenario.append({
            "area": a, "total": total, "monthly": c["monthly"],
            "cash_needed": c["cash_needed"], "decoration": c["decoration_cost"],
        })

    return {
        "item": item,
        **gap,
        "verdict": v_key,
        "verdict_text": v_text,
        "used_median_unit": used_median,
        "scenarios": scenario,
        "checklist": [
            {"label": "五证与预售资金监管", "desc": "查预售许可证编号，并在当地住建局网站核对资金监管账户是否正常"},
            {"label": "交付时间与违约条款", "desc": "合同里的交付日期、延期违约金比例要看清楚，口头承诺不算"},
            {"label": "得房率与公摊", "desc": "问清楚套内面积与公摊比例，同样 120㎡ 实际可能差 8㎡"},
            {"label": "精装标准落地", "desc": "精装交付要写清品牌型号，样板间与实体交付常有差距"},
            {"label": "学区是否已落地", "desc": "新盘学区未划定前，销售说『规划中』就等于没有"},
        ],
        "disclaimer": "新房信息以售楼处公示为准，本页不构成购买建议。",
    }
