"""路由公共查询与序列化助手。"""
from __future__ import annotations

import json
from typing import Any

from ..db import query_all, query_one

LISTING_SELECT = """
SELECT l.*,
       c.liquidity_score, c.school_district, c.lat, c.lng, c.deal_active_90d, c.is_extended,
       s.score, s.grade, s.confidence, s.s_price, s.s_motive, s.s_defect, s.s_liquidity, s.s_fit,
       s.sample_n, s.deviation_pct, s.why_cheap, s.why_good, s.why_trap, s.computed_at,
       (SELECT COUNT(*) FROM watch_item w WHERE w.target_type='listing' AND w.target_id=l.id) AS watched
FROM listing l
LEFT JOIN community c ON c.id = l.community_id
LEFT JOIN leak_score s ON s.target_type='listing' AND s.target_id=l.id
"""

AUCTION_SELECT = """
SELECT a.*, s.score, s.grade, s.confidence, s.s_price, s.s_motive, s.s_defect,
       s.s_liquidity, s.s_fit, s.sample_n, s.why_cheap, s.why_good, s.why_trap,
       (SELECT COUNT(*) FROM watch_item w WHERE w.target_type='auction' AND w.target_id=a.id) AS watched
FROM auction a
LEFT JOIN leak_score s ON s.target_type='auction' AND s.target_id=a.id
"""


def loads(value: Any, default: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value) if value else default
    except (json.JSONDecodeError, TypeError):
        return default


def normalize_listing(row: dict) -> dict:
    row = dict(row)
    row["defect_tags"] = loads(row.get("defect_tags"), [])
    row["raw_json"] = None
    row["watched"] = int(row.get("watched") or 0) > 0
    # 来源卡片：演示数据一律标「演示样本」，绝不显示成来自某个真实平台
    from ..services.sources import source_info
    row["source_info"] = source_info(row)
    return row


def normalize_auction(row: dict) -> dict:
    row = dict(row)
    row["risk_tags"] = loads(row.get("risk_tags"), [])
    row["raw_json"] = None
    row["watched"] = int(row.get("watched") or 0) > 0
    row.pop("_ignore", None)
    from ..services.sources import source_info
    row["source_info"] = source_info(row)
    return row


# --------------------------------------------------------------------------
# 详情页辅助
# --------------------------------------------------------------------------
def listing_history(listing_id: int) -> list[dict]:
    return query_all(
        "SELECT price, unit_price, observed_at, drop_pct, days_gap, kind "
        "FROM price_history WHERE listing_id=? ORDER BY observed_at ASC, id ASC",
        (listing_id,),
    )


def comparable_pool(listing: dict) -> dict:
    """同小区同户型的可比池（含价格分布，供箱线图使用）。"""
    rooms = listing.get("rooms")
    rows = query_all(
        "SELECT unit_price, total_price, area, floor_text, decoration, build_year, defect_tags, id "
        "FROM listing WHERE community_id=? AND rooms=? AND status='active'",
        (listing.get("community_id"), rooms),
    )
    values = sorted(float(r["unit_price"]) for r in rows if r.get("unit_price"))
    if not values:
        return {"n": 0, "values": [], "layout": f"{rooms}室" if rooms else ""}

    def pct(q: float) -> float:
        if len(values) == 1:
            return values[0]
        pos = (len(values) - 1) * q
        lo, hi = int(pos), min(int(pos) + 1, len(values) - 1)
        return round(values[lo] + (values[hi] - values[lo]) * (pos - lo), 1)

    comps = [normalize_listing(r) for r in rows]
    return {
        "n": len(values),
        "layout": f"{rooms}室" if rooms else "",
        "min": round(values[0], 1),
        "q1": pct(0.25),
        "median": pct(0.5),
        "q3": pct(0.75),
        "max": round(values[-1], 1),
        "values": [round(v, 1) for v in values],
        "items": sorted(comps, key=lambda x: float(x.get("unit_price") or 0))[:30],
    }


RISK_CHECKLIST_RULES: list[tuple[str, str, str, str]] = [
    # (key, 标签, 触发条件说明, 风险级别 high/mid/low)
    ("top_floor", "顶楼", "顶楼：渗漏、夏季暴晒、电梯故障时最难受", "mid"),
    ("street", "临街", "临街：噪音与灰尘，夜间货车影响大", "mid"),
    ("dark_bath", "暗卫", "暗卫：无自然采光通风，易返潮", "low"),
    ("odd_layout", "户型异形", "户型异形：空间利用率低，转手客群窄", "mid"),
    ("no_elevator", "无电梯", "无电梯：老人小孩不便，5 年后转手折价明显", "high"),
    ("old_age", "房龄>25", "房龄超 25 年：贷款年限受限、维修成本上升", "high"),
    ("school_used", "学区占用", "学区占用：学位是否被占用必须去学校/教育局核验", "high"),
    ("seal", "产权瑕疵", "产权瑕疵：查封、抵押、共有人不同意都会导致无法过户", "high"),
    ("occupied", "占用", "占用：有人居住或长租，腾退周期不可控", "high"),
    ("arrears", "欠费", "欠费：物业/供暖/水电欠费可能由买方承担", "mid"),
]


def build_risk_checklist(item: dict, is_auction: bool = False) -> list[dict]:
    tags = item.get("risk_tags") if is_auction else item.get("defect_tags")
    tags = loads(tags, [])
    tag_set = set(tags)
    checklist: list[dict] = []
    for key, label, desc, level in RISK_CHECKLIST_RULES:
        hit = label in tag_set or (key == "old_age" and "房龄>25" in tag_set)
        if hit:
            checklist.append({"key": key, "label": label, "desc": desc, "level": level, "hit": True})
    # 未命中的通用项：始终提示需要人工核验
    generic = [
        ("leak", "渗漏/水渍", "看顶棚、窗边、卫生间外墙是否有水渍与霉斑", "mid"),
        ("light", "采光时长", "下午 3 点后看房，判断真实采光；北向房冬季阴冷", "mid"),
        ("noise", "噪音", "关窗静听 5 分钟，注意电梯井、变电箱、商铺排烟", "mid"),
        ("school", "学区占用", "到对口学校或教育局查学位是否被占用（6 年一学位）", "high"),
    ]
    existing = {c["label"] for c in checklist}
    for key, label, desc, level in generic:
        if label not in existing:
            checklist.append({"key": key, "label": label, "desc": desc, "level": level, "hit": False})
    if is_auction:
        checklist.extend([
            {"key": "occupy", "label": "是否有人居住", "desc": "法院是否负责腾退？公告里写『不负责腾退』的要格外小心", "level": "high", "hit": True},
            {"key": "lease", "label": "租约是否有效", "desc": "买卖不破租赁：长期租约可能让你买了住不进", "level": "high", "hit": True},
            {"key": "tax", "label": "税费谁承担", "desc": "法拍常由买方承担全部税费，可能吃掉 5%~10% 折扣", "level": "mid", "hit": True},
        ])
    return checklist


def build_verify_steps(is_auction: bool = False) -> list[dict]:
    steps = [
        {"step": 1, "title": "实地看房", "desc": "查渗漏/采光/噪音，白天与傍晚各去一次"},
        {"step": 2, "title": "问真实成交价", "desc": "找 2 家以上中介，问近 3 个月同户型真实成交价（不是挂牌价）"},
    ]
    if is_auction:
        steps.append({"step": 3, "title": "查占用/腾退/税费", "desc": "查是否有人居住、租约是否有效、税费与欠费由谁承担"})
    else:
        steps.append({"step": 3, "title": "查学区占用", "desc": "到对口学校或教育局确认学位是否被占用"})
    return steps


def district_overview() -> list[dict]:
    """板块概览：均价、样本量、流动性、是否扩展区（地图/对比图用）。"""
    rows = query_all(
        """
        SELECT l.district AS district,
               COUNT(*) AS n,
               AVG(l.unit_price) AS avg_unit,
               AVG(l.total_price) AS avg_total,
               AVG(c.liquidity_score) AS liquidity,
               MAX(c.is_extended) AS is_extended,
               AVG(c.lat) AS lat,
               AVG(c.lng) AS lng
        FROM listing l LEFT JOIN community c ON c.id=l.community_id
        WHERE l.status='active'
        GROUP BY l.district
        ORDER BY avg_unit DESC
        """
    )
    out = []
    for r in rows:
        units = sorted(
            float(x["unit_price"])
            for x in query_all(
                "SELECT unit_price FROM listing WHERE district=? AND status='active'", (r["district"],)
            )
            if x.get("unit_price")
        )
        median = units[len(units) // 2] if units else 0
        out.append({
            "district": r["district"], "n": r["n"],
            "median_unit": round(median, 1),
            "avg_total": round(float(r["avg_total"] or 0), 1),
            "liquidity": round(float(r["liquidity"] or 0), 1),
            "is_extended": int(r["is_extended"] or 0),
            "lat": round(float(r["lat"] or 0), 5),
            "lng": round(float(r["lng"] or 0), 5),
        })
    return out


def get_score_row(target_type: str, target_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM leak_score WHERE target_type=? AND target_id=?", (target_type, target_id)
    )


def build_score_payload(score_row: dict | None, item: dict, target_type: str) -> dict:
    """把 leak_score 行重新组装成前端要的「可解释」结构（含雷达图各维度）。"""
    if not score_row:
        return {}
    from ..services.scoring import WEIGHTS

    payload = {
        "score": score_row["score"],
        "grade": score_row["grade"],
        "confidence": score_row["confidence"],
        "sample_n": score_row["sample_n"],
        "deviation_pct": score_row["deviation_pct"],
        "why_cheap": score_row["why_cheap"],
        "why_good": score_row["why_good"],
        "why_trap": score_row["why_trap"],
        "computed_at": score_row["computed_at"],
        "radar": [
            {"name": "价格偏离", "value": score_row["s_price"], "weight": WEIGHTS["price"]},
            {"name": "调价动机", "value": score_row["s_motive"], "weight": WEIGHTS["motive"]},
            {"name": "硬伤可控", "value": score_row["s_defect"], "weight": WEIGHTS["defect"]},
            {"name": "流动性", "value": score_row["s_liquidity"], "weight": WEIGHTS["liquidity"]},
            {"name": "自住适配", "value": score_row["s_fit"], "weight": WEIGHTS["fit"]},
        ],
        "weights": WEIGHTS,
    }
    return payload
