#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日照房价探针 · 捡漏雷达 —— 单文件演示版（零第三方依赖）

用法：
    python standalone/demo.py            # 启动并自动打开浏览器
    python standalone/demo.py 8020       # 指定端口

特点：
    · 只用 Python 标准库（http.server + json），不需要 pip install 任何东西
    · 内嵌演示数据（14 套挂牌 + 2 条法拍）与完整前端（Vue3 + Element Plus + ECharts，走 CDN）
    · 完整实现「异动识别 + 捡漏评分 + 三问解释」，与标准工程版算法口径一致
    · 端口被占用时自动顺延

⚠️ 演示数据为示意，不代表真实挂牌；挂牌价≠成交价，捡漏分仅作观察参考。
标准工程版（分层/爬虫/调度/推送）见 ../backend 与 ../frontend。
"""
from __future__ import annotations

import json
import random
import statistics
import sys
import threading
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

TODAY = date.today()
random.seed(20261105)

# ============================================================================
# 一、演示数据（示意，非真实挂牌）
# ============================================================================
COMMUNITIES = {
    # 小区名: (板块, 基准单价, 建成年, 有无电梯, 对口学校, 流动性, 近90天成交)
    "安泰·未来城": ("新市区", 11800, 2015, 1, "日照市金海岸小学", 88, 26),
    "华润中心凯旋门": ("新市区", 12400, 2017, 1, "日照市实验学校", 90, 31),
    "城建·时代名苑": ("东港区", 10200, 2010, 1, "东港区第三小学", 76, 15),
    "石臼海滨小区": ("石臼", 8600, 2003, 0, "石臼小学", 62, 7),
    "教授花园": ("山海天", 9200, 2008, 1, "山海天实验小学", 58, 6),
    "香河佳苑": ("高新区", 7000, 2006, 0, "高新区实验学校", 50, 4),
    "岚山港口家园": ("岚山", 4500, 2007, 0, "岚山区实验小学", 26, 1),
}

DISTRICT_LIQ = {"新市区": 88, "东港区": 80, "石臼": 68, "老城": 62,
                "山海天": 58, "高新区": 52, "开发区": 48, "岚山": 28}
EXTENDED = {"莒县", "五莲", "岚山"}

DEFECT_PENALTY = {
    "顶楼": 16, "腰线": 10, "临街": 15, "暗卫": 8, "户型异形": 16, "无电梯": 22,
    "房龄>25": 20, "学区占用": 24, "装修旧": 6, "空置": 5, "北向": 6, "采光差": 12,
    "占用": 45, "租赁": 30, "腾退难": 40, "产权瑕疵": 50, "欠费": 12, "限购": 20, "税费自担": 10,
}
CONTROLLABLE = {"装修旧", "空置", "北向", "采光差", "房龄>25", "腰线", "暗卫"}
FATAL = {"产权瑕疵", "占用", "腾退难", "租赁", "限购"}
SERIOUS = {"无电梯", "学区占用", "房龄>25", "临街", "户型异形", "顶楼", "欠费"}

# (小区, 户型, 室数, 面积, 单价, 硬伤, 挂牌天数, 调价次数, 累计降幅)
RAW_LISTINGS = [
    # —— 新市区 安泰·未来城 3室2厅1卫：围绕中枢的 4 套可比 ——
    ("安泰·未来城", "3室2厅1卫", 3, 104.0, 11800, [], 42, 0, 0.00),
    ("安泰·未来城", "3室2厅1卫", 3, 107.5, 12100, ["顶楼"], 88, 1, 0.04),
    ("安泰·未来城", "3室2厅1卫", 3, 105.0, 12350, [], 15, 0, 0.00),
    ("安泰·未来城", "3室2厅1卫", 3, 106.0, 8924, ["装修旧"], 214, 3, 0.19),   # ← 主角：深跌+连降
    ("安泰·未来城", "2室2厅1卫", 2, 89.0, 11900, [], 60, 1, 0.03),
    ("安泰·未来城", "2室2厅1卫", 2, 88.0, 12500, [], 30, 0, 0.00),
    ("安泰·未来城", "2室2厅1卫", 2, 90.0, 12800, [], 120, 0, 0.00),

    # —— 新市区 华润中心凯旋门 ——
    ("华润中心凯旋门", "3室2厅2卫", 3, 138.0, 12300, [], 65, 0, 0.00),
    ("华润中心凯旋门", "3室2厅2卫", 3, 136.0, 12600, [], 40, 1, 0.02),
    ("华润中心凯旋门", "3室2厅2卫", 3, 137.0, 12900, [], 25, 0, 0.00),
    ("华润中心凯旋门", "3室2厅2卫", 3, 135.0, 10800, ["临街"], 150, 2, 0.11),  # 偏低但临街，风险中

    # —— 东港区 城建·时代名苑 ——
    ("城建·时代名苑", "3室2厅2卫", 3, 118.0, 10200, [], 95, 1, 0.05),
    ("城建·时代名苑", "3室2厅2卫", 3, 120.0, 10500, [], 55, 0, 0.00),
    ("城建·时代名苑", "3室2厅2卫", 3, 115.0, 10800, [], 20, 0, 0.00),
    ("城建·时代名苑", "3室2厅2卫", 3, 112.0, 8600, ["装修旧", "北向"], 126, 1, 0.07),  # 错杀候选

    # —— 石臼 石臼海滨小区（无电梯）——
    ("石臼海滨小区", "2室2厅1卫", 2, 89.0, 8500, [], 130, 0, 0.00),
    ("石臼海滨小区", "2室2厅1卫", 2, 90.0, 8800, [], 70, 1, 0.03),
    ("石臼海滨小区", "2室2厅1卫", 2, 88.0, 9100, [], 25, 0, 0.00),
    ("石臼海滨小区", "2室2厅1卫", 2, 78.0, 6900, ["无电梯"], 402, 4, 0.14),   # 久挂让利

    # —— 山海天 教授花园 ——
    ("教授花园", "3室2厅2卫", 3, 118.0, 9200, [], 110, 0, 0.00),
    ("教授花园", "3室2厅2卫", 3, 121.0, 9500, [], 60, 1, 0.04),
    ("教授花园", "3室2厅2卫", 3, 116.0, 9800, [], 30, 0, 0.00),

    # —— 高新区 香河佳苑（新盘特价，无电梯）——
    ("香河佳苑", "3室2厅1卫", 3, 108.0, 7000, [], 80, 0, 0.00),
    ("香河佳苑", "3室2厅1卫", 3, 106.0, 7300, [], 45, 1, 0.03),
    ("香河佳苑", "3室2厅1卫", 3, 110.0, 7600, [], 20, 0, 0.00),
    ("香河佳苑", "3室2厅1卫", 3, 108.0, 5550, [], 26, 0, 0.00),               # 新房倒挂

    # —— 岚山（扩展区，流动性惩罚）——
    ("岚山港口家园", "3室2厅1卫", 3, 110.0, 4500, [], 200, 1, 0.05),
    ("岚山港口家园", "3室2厅1卫", 3, 112.0, 4600, [], 90, 0, 0.00),
    ("岚山港口家园", "3室2厅1卫", 3, 108.0, 4750, [], 40, 0, 0.00),
    ("岚山港口家园", "3室2厅1卫", 3, 110.0, 3700, ["装修旧"], 240, 2, 0.16),  # 低价但扩展区
]

RAW_AUCTIONS = [
    # (小区, 板块, 面积, 评估价, 起拍价, 阶段, 风险标签, 同小区二手可比单价)
    ("教授花园", "山海天", 118.0, 1_050_000, 680_000, "二拍", ["税费自担"], 9200),
    ("石臼海滨小区", "石臼", 79.0, 560_000, 350_000, "变卖", ["占用", "腾退难", "欠费"], 8600),
]


# ============================================================================
# 二、捡漏评分（与标准工程版同口径，纯函数）
# ============================================================================
def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


WEIGHTS = {"price": 0.30, "motive": 0.15, "defect": 0.20, "liquidity": 0.15, "fit": 0.20}
PREF = {"districts": ["新市区", "东港区", "石臼"], "area": (80, 130), "rooms": 3,
        "elevator": 1, "avoid_top": 1, "max_age": 25}


def build_listings():
    out = []
    for idx, (community, layout, rooms, area, unit, defects, days, changes, drop) in enumerate(RAW_LISTINGS, 1):
        district, base, year, elev, school, liq, deals = COMMUNITIES[community]
        total = round(unit * area / 1000) * 1000
        first = round(total / (1 - drop)) if drop > 0 else total

        # 调价历史：越靠后的调价越贴近今天
        history = [{"date": (TODAY - timedelta(days=days)).isoformat(), "price": first, "drop": 0.0}]
        if changes > 0:
            step = (first - total) / changes
            for i in range(changes):
                frac = min(0.97, ((i + 1) / changes) ** 1.4)
                d = TODAY - timedelta(days=max(3, int(days * (1 - frac))))
                prev = history[-1]["price"]
                new = round((prev - step) / 1000) * 1000
                history.append({"date": d.isoformat(), "price": new,
                                "drop": round((new - prev) / prev, 4)})

        last_drop_days = None
        if changes > 0:
            last_drop_days = (TODAY - date.fromisoformat(history[-1]["date"])).days

        defect_score = clamp(65 - sum(DEFECT_PENALTY.get(t, 6) for t in defects))
        out.append({
            "id": idx, "kind": "listing", "community": community, "district": district,
            "layout": layout, "rooms": rooms, "area": area, "unit_price": unit,
            "total_price": total, "first_price": first, "list_days": days,
            "price_changes": changes, "consecutive_drops": changes if changes >= 2 else (1 if changes else 0),
            "last_drop_days_ago": last_drop_days, "defects": defects, "defect_score": defect_score,
            "has_elevator": 0 if "无电梯" in defects else elev,
            "age": TODAY.year - year, "school": school, "liquidity": liq, "deals_90d": deals,
            "floor": f"{random.randint(1, 17)}/18层", "rent_month": round(area * random.uniform(20, 25)),
            "history": history, "is_new_home": community == "香河佳苑" and unit <= 5600,
        })
    return out


def build_auctions():
    out = []
    for idx, (community, district, area, appraisal, start, stage, risks, comp) in enumerate(RAW_AUCTIONS, 1):
        tax = round(start * 0.045)
        arrears = round(area * 15)
        repair = round(area * 400)
        landing = start + tax + arrears + repair
        landing_unit = landing / area
        net = (comp - landing_unit) / comp
        defect_score = clamp(65 - sum(DEFECT_PENALTY.get(t, 6) for t in risks))
        out.append({
            "id": idx, "kind": "auction", "community": community, "district": district,
            "layout": "3室2厅2卫" if area > 100 else "2室2厅1卫", "rooms": 3 if area > 100 else 2,
            "area": area, "appraisal": appraisal, "start_price": start, "stage": stage,
            "tax": tax, "arrears": arrears, "repair": repair, "landing_price": landing,
            "landing_unit": round(landing_unit), "comp_unit": comp, "net_discount": round(net, 4),
            "risk_tags": risks, "defect_score": defect_score,
            "auction_date": (TODAY + timedelta(days=12)).isoformat(),
            "rent_month": 0, "deals_90d": COMMUNITIES.get(community, (0, 0, 0, 0, 0, 0, 0))[6],
        })
    return out


LISTINGS = build_listings()
AUCTIONS = build_auctions()


def comparables(listing):
    """同小区同户型（按室数）的在挂样本，返回中位数与分布。"""
    peers = [l for l in LISTINGS if l["community"] == listing["community"] and l["rooms"] == listing["rooms"]]
    units = sorted(p["unit_price"] for p in peers)
    if not units:
        return {"n": 0, "median": 0, "values": []}
    return {"n": len(units), "median": statistics.median(units), "values": units,
            "min": units[0], "max": units[-1]}


def score_listing(l):
    comp = comparables(l)
    median = comp["median"]
    dev = (median - l["unit_price"]) / median if median > 0 else 0.0
    n = comp["n"]

    # 1) 价格偏离 30%
    if dev <= 0:
        s_price = clamp(30 + dev * 150, 0, 30)
    else:
        s_price = clamp(30 + dev / 0.20 * 70, 0, 100)
        if dev > 0.35:
            s_price = min(s_price, 70)      # 极端低价优先按"有坑"处理
    if n < 3:
        s_price = 50 + (s_price - 50) * 0.6   # 样本不足向中性收缩

    # 2) 调价动机 15%
    total_drop = (l["first_price"] - l["total_price"]) / l["first_price"] if l["first_price"] else 0
    s_motive = 20.0
    if l["price_changes"]:
        s_motive += 6
    if l["consecutive_drops"] >= 2:
        s_motive += 12
    elif l["consecutive_drops"] == 1:
        s_motive += 4
    s_motive += min(18.0, max(0.0, total_drop) / 0.15 * 18)
    s_motive += 10 if l["list_days"] > 180 else (6 if l["list_days"] > 90 else 0)
    if l["last_drop_days_ago"] is not None and l["last_drop_days_ago"] <= 14:
        s_motive += 6
    if l["defect_score"] < 55 and (l["price_changes"] or total_drop > 0.05):
        s_motive *= 0.55                   # 硬伤甩卖，不是让利
    s_motive = clamp(s_motive)

    # 3) 硬伤可控 20%
    s_defect = l["defect_score"]

    # 4) 流动性 15%
    raw = DISTRICT_LIQ.get(l["district"], 50) * 0.55 + l["liquidity"] * 0.45
    if l["district"] in EXTENDED:
        raw -= 8
    if l["deals_90d"] >= 15:
        raw += 6
    elif l["deals_90d"] <= 2:
        raw -= 6
    s_liq = clamp(50 + (raw - 60) * 0.9, 5, 100)

    # 5) 自住适配 20%
    fit = 50.0
    fit += 8 if l["district"] in PREF["districts"] else (-20 if l["district"] in EXTENDED else 0)
    fit += 8 if PREF["area"][0] <= l["area"] <= PREF["area"][1] else -8
    fit += 8 if l["rooms"] == PREF["rooms"] else -6
    fit += 4 if l["has_elevator"] else -22
    if "顶楼" in l["defects"]:
        fit -= 14
    if "临街" in l["defects"]:
        fit -= 8
    fit += -12 if l["age"] > PREF["max_age"] else 4
    fit += 3 if l["school"] else 0
    s_fit = clamp(fit)

    total = (s_price * 0.30 + s_motive * 0.15 + s_defect * 0.20 + s_liq * 0.15 + s_fit * 0.20)
    total = round(clamp(total), 1)

    hard = [t for t in l["defects"] if t not in CONTROLLABLE]
    risk = "high" if (set(l["defects"]) & FATAL or len(set(l["defects"]) & SERIOUS) >= 2) \
        else ("mid" if set(l["defects"]) & SERIOUS else "low")
    confidence = "high" if n >= 5 else ("mid" if n >= 3 else "low")

    if risk == "high":
        grade = "white"
    elif total >= 75 and confidence != "low":
        grade = "red"
    elif total >= 60:
        grade = "yellow"
    else:
        grade = "white"

    cheap = []
    if dev > 0.02:
        cheap.append(f"挂牌单价 {l['unit_price']:,} 元/㎡，比同小区同户型中位({median:,.0f})低 {dev * 100:.1f}%")
    if hard:
        cheap.append("存在硬伤：" + "、".join(hard))
    if l["consecutive_drops"] >= 2:
        cheap.append(f"已调价 {l['price_changes']} 次、连续降价 {l['consecutive_drops']} 次")
    if l["list_days"] > 180:
        cheap.append(f"挂盘 {l['list_days']} 天未出手")
    if l["district"] in EXTENDED:
        cheap.append(f"{l['district']}属扩展区，二手成交清淡，价格锚不稳")
    if not cheap:
        cheap.append("价格与可比中枢基本持平，便宜的理由不在价格上")

    good = []
    if l["defects"] and not hard:
        good.append("硬伤可控：集中在装修/朝向等可改善项，不是产权或占用")
    if not l["defects"]:
        good.append("未识别到产权/占用类硬伤，折让更可能来自急售")
    if s_motive >= 60:
        good.append("降价节奏符合『急着卖』（连降+长挂），不是『有硬伤才降』")
    if s_fit >= 70:
        good.append("面积/户型/电梯与自住偏好匹配度高")
    if n >= 3:
        good.append(f"同小区同户型有 {n} 套可比样本，中枢比较扎实")
    if not good:
        good.append("暂无支持『真漏』的硬证据，只作观察")

    trap = []
    if risk == "high":
        trap.append("风险等级高：产权/占用类问题一旦成立，钱和房子可能都拿不回")
    if hard:
        trap.append("不可逆硬伤：" + "、".join(hard))
    if n < 3:
        trap.append(f"可比样本仅 {n} 套，所谓『低于中枢』可能只是该户型本身差")
    if dev > 0.35:
        trap.append("偏离超过 35%，优先怀疑假房源/凶宅/查封/数据错误")
    if l["age"] > 25:
        trap.append(f"房龄 {l['age']} 年，贷款年限受限、维修成本上升")
    if not trap:
        trap.append("主要风险是常规项：实地渗漏/采光/噪音、学区占用、真实成交价待核")

    return {
        "score": total, "grade": grade, "risk": risk, "confidence": confidence,
        "deviation": round(dev, 4), "median_unit": round(median, 1), "sample_n": n,
        "radar": [
            {"name": "价格偏离", "value": round(s_price), "weight": 0.30},
            {"name": "调价动机", "value": round(s_motive), "weight": 0.15},
            {"name": "硬伤可控", "value": round(s_defect), "weight": 0.20},
            {"name": "流动性", "value": round(s_liq), "weight": 0.15},
            {"name": "自住适配", "value": round(s_fit), "weight": 0.20},
        ],
        "why_cheap": "；".join(cheap), "why_good": "；".join(good), "why_trap": "；".join(trap),
    }


def score_auction(a):
    s_price = clamp(a["net_discount"] / 0.25 * 100, 0, 100)
    if a["net_discount"] > 0.40:
        s_price = min(s_price, 70)
    s_motive = {"一拍": 45, "二拍": 72, "变卖": 80}.get(a["stage"], 45)
    if set(a["risk_tags"]) & FATAL:
        s_motive *= 0.7
    s_defect = a["defect_score"]
    raw = DISTRICT_LIQ.get(a["district"], 50) - 6 + (2 if a["landing_price"] < 900_000 else 0)
    s_liq = clamp(50 + (raw - 60) * 0.9, 5, 100)
    s_fit = 50 + (8 if a["district"] in PREF["districts"] else 0) + (8 if 80 <= a["area"] <= 130 else -8) + 8
    total = round(clamp((s_price * .3 + s_motive * .15 + s_defect * .2 + s_liq * .15 + clamp(s_fit) * .2) * 0.95), 1)
    risk = "high" if set(a["risk_tags"]) & {"占用", "腾退难", "产权瑕疵"} else \
        ("mid" if a["risk_tags"] else "low")
    grade = "white" if risk == "high" else ("red" if total >= 75 else ("yellow" if total >= 60 else "white"))
    return {
        "score": total, "grade": grade, "risk": risk, "confidence": "mid",
        "net_discount": a["net_discount"], "deviation": a["net_discount"],
        "radar": [
            {"name": "价格偏离", "value": round(s_price), "weight": 0.30},
            {"name": "调价动机", "value": round(s_motive), "weight": 0.15},
            {"name": "硬伤可控", "value": round(s_defect), "weight": 0.20},
            {"name": "流动性", "value": round(s_liq), "weight": 0.15},
            {"name": "自住适配", "value": round(clamp(s_fit)), "weight": 0.20},
        ],
        "why_cheap": f"司法拍卖自带折价；到手单价 {a['landing_unit']:,} 元/㎡，"
                     f"比同小区二手可比({a['comp_unit']:,})低 {a['net_discount'] * 100:.1f}%"
                     + ("；存在风险标签：" + "、".join(a["risk_tags"]) if a["risk_tags"] else ""),
        "why_good": f"已进入{a['stage']}，法院定价通常低于上一轮，竞争者更少"
                    + ("；公告未标注占用/产权瑕疵（仍需核实）" if not a["risk_tags"] else ""),
        "why_trap": "法拍到手价 ≠ 起拍价：税费、欠费、腾退时间都可能吃掉折扣"
                    + ("；本次标的存在 " + "、".join(a["risk_tags"]) + "，按风险高处理" if a["risk_tags"] else ""),
    }


def annotate():
    for l in LISTINGS:
        l["scoring"] = score_listing(l)
    for a in AUCTIONS:
        a["scoring"] = score_auction(a)


annotate()


# ============================================================================
# 三、异动识别（纯函数，规则与标准版一致）
# ============================================================================
def events_of(l):
    s = l["scoring"]
    comp = comparables(l)
    evts = []

    drops = [h for h in l["history"] if h["drop"] < 0]
    if drops and abs(min(h["drop"] for h in drops)) >= 0.05:
        evts.append({"type": "sudden_drop", "label": "突发降价",
                     "pct": abs(min(h["drop"] for h in drops)),
                     "note": "单次降幅超阈值（≥5%）"})
    if s["deviation"] >= 0.12:
        evts.append({"type": "deep_dip", "label": "深跌", "pct": s["deviation"],
                     "note": f"低于同小区同户型近3月中位 {s['deviation'] * 100:.1f}%"})
    if l["list_days"] > 180 and (l["last_drop_days_ago"] or 999) <= 30:
        evts.append({"type": "long_listed", "label": "久挂让利",
                     "pct": (l["first_price"] - l["total_price"]) / l["first_price"],
                     "note": f"挂 {l['list_days']} 天且近 30 天有降价"})
    if 0.03 <= s["deviation"] and not [t for t in l["defects"] if t not in CONTROLLABLE]:
        evts.append({"type": "miskill", "label": "错杀候选", "pct": s["deviation"],
                     "note": "偏低但硬伤可控（装修/朝向类）"})
    if s["deviation"] >= 0.08 and l["district"] in EXTENDED:
        evts.append({"type": "board_divergence", "label": "板块背离", "pct": s["deviation"],
                     "note": f"{l['district']}扩展区，个盘逆势低价"})
    if l["is_new_home"] and s["deviation"] > 0.05:
        evts.append({"type": "new_home", "label": "新房倒挂", "pct": s["deviation"],
                     "note": "新盘特价/抵账房，单价低于周边二手 5% 以上"})
    for e in evts:
        e["score"] = s["score"]
        e["grade"] = s["grade"]
    return evts


def events_of_auction(a):
    s = a["scoring"]
    if s["net_discount"] >= 0.15:
        return [{"type": "auction_deal", "label": "法拍机会", "pct": s["net_discount"],
                 "note": f"起拍净折扣 {s['net_discount'] * 100:.1f}%（按到手价）",
                 "score": s["score"], "grade": s["grade"]}]
    return []


# ============================================================================
# 四、HTTP 服务（标准库）
# ============================================================================
def api_today():
    pool = [l for l in LISTINGS if l["scoring"]["grade"] in ("red", "yellow")]
    pool.sort(key=lambda x: -x["scoring"]["score"])
    auc = [a for a in AUCTIONS if a["scoring"]["grade"] in ("red", "yellow")]
    return {
        "date": TODAY.isoformat(),
        "counts": {
            "listings": len(LISTINGS), "auctions": len(AUCTIONS),
            "red": sum(1 for x in LISTINGS + AUCTIONS if x["scoring"]["grade"] == "red"),
            "yellow": sum(1 for x in LISTINGS + AUCTIONS if x["scoring"]["grade"] == "yellow"),
        },
        "listings": [{"id": l["id"], "kind": "listing", **{k: l[k] for k in
                       ("community", "district", "layout", "area", "total_price", "unit_price",
                        "list_days", "price_changes", "defects")}, **l["scoring"]} for l in pool],
        "auctions": [{"id": a["id"], "kind": "auction", "community": a["community"],
                      "district": a["district"], "layout": a["layout"], "area": a["area"],
                      "landing_price": a["landing_price"], "total_price": a["landing_price"],
                      "unit_price": a["landing_unit"], "defects": a["risk_tags"],
                      "price_changes": 0, "list_days": 0, "stage": a["stage"], **a["scoring"]}
                     for a in auc],
    }


def api_events():
    items = []
    for l in LISTINGS:
        for e in events_of(l):
            items.append({**e, "id": l["id"], "kind": "listing", "community": l["community"],
                          "district": l["district"], "layout": l["layout"], "area": l["area"],
                          "price": l["total_price"]})
    for a in AUCTIONS:
        for e in events_of_auction(a):
            items.append({**e, "id": a["id"], "kind": "auction", "community": a["community"],
                          "district": a["district"], "layout": a["layout"], "area": a["area"],
                          "price": a["landing_price"]})
    items.sort(key=lambda x: (-x["score"], -x["pct"]))
    return {"items": items}


def api_detail(kind, ident):
    pool = LISTINGS if kind == "listing" else AUCTIONS
    obj = next((x for x in pool if x["id"] == ident), None)
    if not obj:
        return {"error": "not found"}
    if kind == "listing":
        comp = comparables(obj)
        hist = obj["history"]
        peers = sorted((p["unit_price"] for p in LISTINGS
                        if p["community"] == obj["community"] and p["rooms"] == obj["rooms"]))
        return {
            "kind": "listing", "item": obj, "scoring": obj["scoring"],
            "history": hist,
            "box": {"values": peers, "median": comp["median"], "n": comp["n"]},
            "events": events_of(obj),
        }
    return {"kind": "auction", "item": obj, "scoring": obj["scoring"],
            "history": [], "box": {"values": [obj["comp_unit"]], "median": obj["comp_unit"], "n": 1},
            "events": events_of_auction(obj)}


class Handler(BaseHTTPRequestHandler):
    server_version = "RZProbeDemo/1.0"

    def log_message(self, fmt, *args):  # 静默访问日志，保持控制台干净
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/" or path == "/index.html":
                return self._send(200, HTML, "text/html; charset=utf-8")
            if path == "/api/today":
                return self._send(200, json.dumps(api_today(), ensure_ascii=False, default=str))
            if path == "/api/events":
                return self._send(200, json.dumps(api_events(), ensure_ascii=False, default=str))
            if path.startswith("/api/detail/"):
                # /api/detail/<kind>/<id>
                parts = path.strip("/").split("/")
                if len(parts) != 4:
                    return self._send(400, json.dumps({"error": "bad path"}, ensure_ascii=False))
                return self._send(200, json.dumps(api_detail(parts[2], int(parts[3])),
                                                  ensure_ascii=False, default=str))
            return self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            return self._send(500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))


# ============================================================================
# 五、前端（CDN：Vue3 + Element Plus + ECharts）
# ============================================================================
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>日照房价探针 · 捡漏雷达（单文件演示）</title>
<link rel="stylesheet" href="https://unpkg.com/element-plus@2.9.1/dist/index.css"/>
<script src="https://unpkg.com/vue@3.5.13/dist/vue.global.prod.js"></script>
<script src="https://unpkg.com/element-plus@2.9.1/dist/index.full.min.js"></script>
<script src="https://unpkg.com/element-plus@2.9.1/dist/locale/zh-cn.min.js"></script>
<script src="https://unpkg.com/echarts@5.5.1/dist/echarts.min.js"></script>
<style>
  :root{--bg:#f5f7fa;--panel:#fff;--text:#16202b;--muted:#6b7786;--line:#e6e9ef;
        --brand:#0e7490;--red:#e11d48;--yellow:#d97706;--good:#059669}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font-size:14px;line-height:1.6;
       font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
  .wrap{max-width:1240px;margin:0 auto;padding:20px 18px 60px}
  .head{display:flex;align-items:center;gap:12px;margin-bottom:14px}
  .logo{width:10px;height:10px;border-radius:50%;background:#22d3ee;box-shadow:0 0 0 4px rgba(34,211,238,.2)}
  .h1{font-size:17px;font-weight:700}
  .sub{color:var(--muted);font-size:12px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
        box-shadow:0 1px 2px rgba(15,23,42,.04),0 8px 24px rgba(15,23,42,.05);padding:16px 18px}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:14px}
  .stat .lab{font-size:12px;color:var(--muted)}
  .stat .val{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
  .lc{border:1px solid var(--line);border-radius:12px;padding:14px 16px;cursor:pointer;
      transition:.16s;position:relative;background:#fff}
  .lc:hover{transform:translateY(-2px);box-shadow:0 12px 30px rgba(15,23,42,.10)}
  .lc:after{content:'';position:absolute;left:0;top:14px;bottom:14px;width:3px;border-radius:0 3px 3px 0;background:var(--line)}
  .lc.red:after{background:var(--red)}.lc.yellow:after{background:var(--yellow)}
  .lc .name{font-weight:650;font-size:14.5px}
  .lc .meta{font-size:12px;color:var(--muted)}
  .price{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
  .unit{font-size:12px;color:var(--muted)}
  .score{border-radius:10px;padding:5px 9px;font-weight:700;font-size:19px;font-variant-numeric:tabular-nums}
  .tag{display:inline-block;padding:1px 7px;border-radius:6px;font-size:11.5px;
       background:#f8fafc;border:1px solid var(--line);color:var(--muted);margin-right:4px}
  .tag.warn{background:rgba(217,119,6,.1);border-color:rgba(217,119,6,.24);color:#a35c05}
  .tag.danger{background:rgba(225,29,72,.08);border-color:rgba(225,29,72,.22);color:#b3123a}
  .chip{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
  .disclaimer{background:#fffaf0;border:1px solid #fde6c3;color:#8a5a12;border-radius:10px;
              padding:10px 14px;font-size:12.5px;margin-bottom:14px}
  .ev{padding:10px 0;border-bottom:1px dashed var(--line)}
  .ev:last-child{border-bottom:none}
  .mini{font-size:12px;color:var(--muted)}
  .kpi{display:flex;gap:22px;flex-wrap:wrap;margin-top:8px}
  .kpi .k{font-size:11px;color:#8fa9b6}.kpi .v{font-size:15px;font-weight:650;font-variant-numeric:tabular-nums}
  .hero{background:linear-gradient(135deg,#0f2b36,#123d4b 60%,#0e7490 150%);color:#fff;
        border-radius:14px;padding:20px 22px;margin-bottom:14px;display:flex;gap:20px;flex-wrap:wrap}
  .hero .t{font-size:19px;font-weight:700}
  .hero .s{color:#a9c2ce;font-size:12.5px}
  .hero .big{font-size:32px;font-weight:700;font-variant-numeric:tabular-nums}
  .exp{border-left:3px solid var(--line);padding:6px 0 6px 12px;margin-bottom:10px}
  .exp.c{border-color:var(--yellow)}.exp.g{border-color:var(--good)}.exp.t{border-color:var(--red)}
  .bar{height:8px;border-radius:999px;background:#eef1f5;overflow:hidden;flex:1}
  .bar>i{display:block;height:100%;background:linear-gradient(90deg,#0e7490,#22d3ee)}
  .brow{display:flex;align-items:center;gap:10px;padding:5px 0;font-size:12.5px}
  .cols{display:grid;grid-template-columns:1.6fr 1fr;gap:14px}
  @media(max-width:900px){.stats{grid-template-columns:1fr 1fr}.cols{grid-template-columns:1fr}}
</style>
</head>
<body>
<div id="app">
  <div class="wrap">
    <div class="head">
      <span class="logo"></span>
      <div>
        <div class="h1">日照房价探针 · 捡漏雷达</div>
        <div class="sub">{{ today }} · 演示数据（示意，非真实挂牌）· {{ counts.listings }} 套在挂 / {{ counts.auctions }} 条法拍</div>
      </div>
      <div style="margin-left:auto">
        <el-button size="small" @click="tab='home'">今日可留意</el-button>
        <el-button size="small" @click="tab='events'">异动专区</el-button>
      </div>
    </div>

    <div class="disclaimer">
      <b>先质疑，再判断：</b>下面每一条都只是「值得看一下」的候选，不是推荐。
      挂牌价 ≠ 成交价，捡漏分仅作观察参考，最终以实地尽调为准。
    </div>

    <div class="stats">
      <div class="card stat"><div class="lab">🔴 重点留意</div><div class="val" style="color:var(--red)">{{ counts.red }}</div><div class="mini">分数≥75 且风险低</div></div>
      <div class="card stat"><div class="lab">🟡 建议观察</div><div class="val" style="color:var(--yellow)">{{ counts.yellow }}</div><div class="mini">进观察清单，先电话后跑腿</div></div>
      <div class="card stat"><div class="lab">异动条数</div><div class="val" style="color:var(--brand)">{{ events.length }}</div><div class="mini">7 类信号合计</div></div>
      <div class="card stat"><div class="lab">在挂 / 法拍</div><div class="val">{{ counts.listings }}<span style="font-size:14px;color:var(--muted)"> / {{ counts.auctions }}</span></div><div class="mini">演示数据</div></div>
    </div>

    <!-- 今日可留意 -->
    <div v-show="tab==='home'">
      <div class="cards">
        <div v-for="it in pool" :key="it.kind+it.id" class="lc" :class="it.grade" @click="open(it)">
          <div style="display:flex;gap:10px;align-items:flex-start">
            <div style="flex:1">
              <div class="name">
                <span v-if="it.kind==='auction'" class="tag danger">法拍</span> {{ it.community }}
              </div>
              <div class="meta">{{ it.district }} · {{ it.layout }} · {{ it.area }}㎡
                <span v-if="it.stage"> · {{ it.stage }}</span></div>
            </div>
            <div class="score" :style="scoreStyle(it)">{{ Math.round(it.score) }}<span style="font-size:10px">分</span></div>
          </div>
          <div style="margin-top:8px;display:flex;align-items:baseline;gap:8px">
            <span class="price">{{ (it.total_price/10000).toFixed(1) }}万</span>
            <span class="unit">单价 {{ Math.round(it.unit_price).toLocaleString() }} 元/㎡</span>
            <span v-if="it.deviation>0" style="margin-left:auto;color:var(--good);font-weight:700;font-size:13px">↓{{ (it.deviation*100).toFixed(1) }}%</span>
          </div>
          <div class="mini" style="margin-top:6px;line-height:1.7">
            <b style="color:var(--yellow)">为什么便宜：</b>{{ cut(it.why_cheap, 58) }}
          </div>
          <div style="margin-top:8px">
            <span class="chip" :style="{color:gmeta(it.grade).c,background:gmeta(it.grade).bg}">{{ gmeta(it.grade).t }}</span>
            <span class="tag">置信度 {{ {high:'高',mid:'中',low:'低'}[it.confidence] }}</span>
            <span v-if="it.price_changes" class="tag warn">调价 {{ it.price_changes }} 次</span>
            <span v-if="it.list_days" class="tag">挂牌 {{ it.list_days }} 天</span>
            <span v-for="d in it.defects" :key="d" class="tag danger">{{ d }}</span>
          </div>
        </div>
      </div>
      <div v-if="!pool.length" class="card" style="text-align:center;color:var(--muted)">没有红/黄级候选</div>
    </div>

    <!-- 异动专区 -->
    <div v-show="tab==='events'" class="card">
      <el-tabs v-model="evTab">
        <el-tab-pane v-for="t in evTabs" :key="t.key" :name="t.key">
          <template #label>
            {{ t.label }}<span v-if="evCount(t.key)" class="tag" style="margin-left:5px">{{ evCount(t.key) }}</span>
          </template>
        </el-tab-pane>
      </el-tabs>
      <div v-if="!evVisible.length" class="mini" style="padding:18px 0;text-align:center">
        该标签下暂无红/黄级异动（演示数据只保留值得看的条目）
      </div>
      <div v-for="e in evVisible" :key="e.type+e.id" class="ev" @click="open(e)" style="cursor:pointer">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span class="chip" :style="{color:gmeta(e.grade).c,background:gmeta(e.grade).bg}">{{ gmeta(e.grade).t }}</span>
          <b>{{ e.community }}</b>
          <span class="mini">{{ e.district }} · {{ e.layout }} · {{ e.area }}㎡</span>
          <span style="margin-left:auto;font-weight:700">{{ Math.round(e.score) }} 分</span>
        </div>
        <div class="mini" style="margin-top:4px">
          当前价 {{ (e.price/10000).toFixed(1) }}万 ·
          {{ e.label }} <b style="color:var(--good)">{{ (e.pct*100).toFixed(1) }}%</b> · {{ e.note }}
        </div>
      </div>
    </div>
  </div>

  <!-- 详情抽屉 -->
  <el-drawer v-model="drawer" size="720px" :title="d && ('房源详情 · ' + d.item.community)">
    <div v-if="d">
      <div class="hero">
        <div style="flex:1;min-width:260px">
          <div class="t">
            <span class="chip" :style="{color:'#fff',background:gmeta(d.scoring.grade).c}">{{ gmeta(d.scoring.grade).t }}</span>
            <span style="margin-left:8px">{{ d.item.community }}</span>
          </div>
          <div class="s">
            {{ d.item.district }} · {{ d.item.layout }} · {{ d.item.area }}㎡
            <span v-if="d.kind==='auction'"> · {{ d.item.stage }} · 到手价预估</span>
            <span v-else> · {{ d.item.floor }} · 房龄 {{ d.item.age }} 年</span>
          </div>
          <div class="kpi">
            <div><div class="k">{{ d.kind==='auction' ? '起拍价' : '挂牌总价' }}</div>
              <div class="v">{{ ((d.kind==='auction'? d.item.start_price : d.item.total_price)/10000).toFixed(1) }}万</div></div>
            <div><div class="k">{{ d.kind==='auction' ? '到手价' : '单价' }}</div>
              <div class="v">{{ d.kind==='auction' ? (d.item.landing_price/10000).toFixed(1)+'万' : Math.round(d.item.unit_price).toLocaleString() }}</div></div>
            <div v-if="d.kind!=='auction'"><div class="k">同户型中位</div><div class="v">{{ Math.round(d.box.median).toLocaleString() }}</div></div>
            <div><div class="k">{{ d.kind==='auction' ? '净折扣' : '低于中枢' }}</div>
              <div class="v" style="color:#34d399">{{ (d.scoring.deviation*100).toFixed(1) }}%</div></div>
            <div v-if="d.kind!=='auction'"><div class="k">挂牌天数</div><div class="v">{{ d.item.list_days }}</div></div>
          </div>
        </div>
        <div style="text-align:right">
          <div class="big">{{ Math.round(d.scoring.score) }}<span style="font-size:13px">分</span></div>
          <div class="mini" style="color:#9fb8c4">
            置信度 {{ {high:'高',mid:'中',low:'低'}[d.scoring.confidence] }} ·
            风险 {{ {low:'低',mid:'中',high:'高'}[d.scoring.risk] }} · 样本 {{ d.scoring.sample_n }}
          </div>
        </div>
      </div>

      <div class="card" style="margin-bottom:12px">
        <div style="font-weight:650;margin-bottom:8px">先问三个问题 <span class="mini">核心逻辑</span></div>
        <div class="exp c"><b style="color:var(--yellow)">① 它为什么便宜？</b><div>{{ d.scoring.why_cheap }}</div></div>
        <div class="exp g"><b style="color:var(--good)">② 为什么可能是真漏？</b><div>{{ d.scoring.why_good }}</div></div>
        <div class="exp t"><b style="color:var(--red)">③ 为什么可能是坑？</b><div>{{ d.scoring.why_trap }}</div></div>
      </div>

      <div class="cols">
        <div>
          <div class="card" style="margin-bottom:12px">
            <div style="font-weight:650;margin-bottom:6px">同小区同户型价格分布<span class="mini">（{{ d.box.n }} 套可比）</span></div>
            <div id="box" style="height:240px"></div>
          </div>
          <div class="card" v-if="d.history.length>1">
            <div style="font-weight:650;margin-bottom:6px">调价时间轴</div>
            <div id="line" style="height:220px"></div>
          </div>
        </div>
        <div>
          <div class="card" style="margin-bottom:12px">
            <div style="font-weight:650;margin-bottom:6px">评分拆解</div>
            <div id="radar" style="height:230px"></div>
            <div v-for="r in d.scoring.radar" :key="r.name" class="brow">
              <span style="width:66px">{{ r.name }}</span>
              <span class="mini" style="width:30px;text-align:right">{{ Math.round(r.weight*100) }}%</span>
              <span class="bar"><i :style="{width:r.value+'%'}"></i></span>
              <span class="mini" style="width:34px;text-align:right">{{ r.value }}</span>
            </div>
          </div>
          <div class="card">
            <div style="font-weight:650;margin-bottom:6px">人工核验 3 步</div>
            <div class="mini" style="line-height:1.9">
              1. 实地看房查渗漏/采光/噪音<br/>
              2. 问中介近 3 月同户型真实成交价<br/>
              3. {{ d.kind==='auction' ? '查占用/腾退/税费' : '查学区占用' }}
            </div>
            <div class="disclaimer" style="margin:10px 0 0">这 3 步不做，任何分数都不算数。</div>
          </div>
        </div>
      </div>
    </div>
  </el-drawer>
</div>

<script>
const { createApp, ref, computed, onMounted, nextTick, watch } = Vue
createApp({
  setup() {
    const today = ref('')
    const counts = ref({ listings: 0, auctions: 0, red: 0, yellow: 0 })
    const pool = ref([])
    const events = ref([])
    const tab = ref('home')
    const evTab = ref('sudden_drop')
    const drawer = ref(false)
    const d = ref(null)
    const charts = {}

    const evTabs = [
      { key: 'sudden_drop', label: '突发降价' }, { key: 'deep_dip', label: '深跌' },
      { key: 'long_listed', label: '久挂让利' }, { key: 'auction_deal', label: '法拍机会' },
      { key: 'miskill', label: '错杀候选' }, { key: 'board_divergence', label: '板块背离' },
      { key: 'new_home', label: '新房倒挂' },
    ]
    const evVisible = computed(() => events.value.filter(e => e.type === evTab.value))
    const evCount = (k) => events.value.filter(e => e.type === k).length

    const gmeta = (g) => ({
      red: { t: '🔴 重点留意', c: '#e11d48', bg: 'rgba(225,29,72,.08)' },
      yellow: { t: '🟡 建议观察', c: '#d97706', bg: 'rgba(217,119,6,.10)' },
      white: { t: '⚪ 仅记录', c: '#64748b', bg: 'rgba(100,116,139,.10)' },
    }[g] || { t: '未评级', c: '#64748b', bg: '#f1f5f9' })
    const scoreStyle = (it) => ({ color: gmeta(it.grade).c, background: gmeta(it.grade).bg })
    const cut = (s, n) => (s && s.length > n ? s.slice(0, n) + '…' : s)

    async function load() {
      const t = await (await fetch('/api/today')).json()
      today.value = t.date
      counts.value = t.counts
      pool.value = [...t.listings, ...t.auctions]
      const e = await (await fetch('/api/events')).json()
      events.value = e.items
    }

    function draw(id, option) {
      const el = document.getElementById(id)
      if (!el) return
      charts[id] = charts[id] || echarts.init(el)
      charts[id].setOption(option, true)
    }

    function renderCharts() {
      if (!d.value) return
      const values = d.value.box.values
      if (values && values.length) {
        // 箱线图需要 [min, Q1, 中位, Q3, max]
        const sorted = [...values].sort((a, b) => a - b)
        const q = (p) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * p)))]
        const boxData = [sorted[0], q(0.25), q(0.5), q(0.75), sorted[sorted.length - 1]]
        draw('box', {
          grid: { left: 50, right: 20, top: 20, bottom: 26 },
          tooltip: { trigger: 'item' },
          xAxis: { type: 'category', data: ['同户型单价'], axisLine: { lineStyle: { color: '#e6e9ef' } } },
          yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: '#eef1f5', type: 'dashed' } },
                   axisLabel: { formatter: v => (v / 1000).toFixed(1) + 'k' } },
          series: [
            { type: 'scatter', symbolSize: 8, itemStyle: { color: 'rgba(14,116,144,.55)' },
              data: values.map((v, i) => [((i % 7) - 3) * 0.04, v]) },
            { type: 'boxplot', boxWidth: [14, 40],
              itemStyle: { color: 'rgba(14,116,144,.10)', borderColor: '#0e7490' },
              data: [boxData] },
          ],
        })
      }
      if (d.value.history.length > 1) {
        draw('line', {
          grid: { left: 56, right: 20, top: 20, bottom: 30 },
          tooltip: { trigger: 'axis' },
          xAxis: { type: 'category', data: d.value.history.map(h => h.date),
                   axisLabel: { fontSize: 10, rotate: 20 } },
          yAxis: { type: 'value', scale: true, axisLabel: { formatter: v => (v / 10000).toFixed(0) } },
          series: [{ type: 'line', step: 'end', data: d.value.history.map(h => h.price),
                     lineStyle: { color: '#0e7490', width: 2 }, symbolSize: 6,
                     areaStyle: { color: 'rgba(14,116,144,.14)' } }],
        })
      }
      const radar = d.value.scoring.radar
      draw('radar', {
        radar: {
          indicator: radar.map(r => ({ name: r.name, max: 100 })),
          radius: '62%', center: ['50%', '54%'],
          axisName: { color: '#6b7786', fontSize: 11 },
          splitLine: { lineStyle: { color: '#e6e9ef' } },
          splitArea: { areaStyle: { color: ['rgba(248,250,252,.9)', 'rgba(255,255,255,0)'] } },
          axisLine: { lineStyle: { color: '#e6e9ef' } },
        },
        series: [{ type: 'radar', data: [{ value: radar.map(r => r.value),
                   areaStyle: { color: 'rgba(14,116,144,.14)' },
                   lineStyle: { color: '#0e7490', width: 2 }, itemStyle: { color: '#0e7490' } }] }],
      })
    }

    async function open(it) {
      const res = await (await fetch(`/api/detail/${it.kind}/${it.id}`)).json()
      if (res.error) return
      d.value = res
      drawer.value = true
      await nextTick()
      setTimeout(renderCharts, 120)   // 等抽屉动画结束再测量容器
    }

    onMounted(load)
    return { today, counts, pool, events, tab, evTab, evTabs, evVisible, evCount,
             drawer, d, open, gmeta, scoreStyle, cut }
  },
}).use(ElementPlus).mount('#app')
</script>
</body>
</html>
"""


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8010
    server = None
    for candidate in range(port, port + 12):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        print("端口全部被占用，请手动指定：python demo.py 8090")
        return

    url = f"http://127.0.0.1:{port}/"
    print("=" * 64)
    print("  日照房价探针 · 捡漏雷达 —— 单文件演示版")
    print(f"  已启动：{url}")
    print(f"  {len(LISTINGS)} 套挂牌 / {len(AUCTIONS)} 条法拍（示意数据）")
    red = sum(1 for x in LISTINGS + AUCTIONS if x["scoring"]["grade"] == "red")
    yellow = sum(1 for x in LISTINGS + AUCTIONS if x["scoring"]["grade"] == "yellow")
    print(f"  今日：🔴 {red} 条 / 🟡 {yellow} 条")
    print("  按 Ctrl+C 停止")
    print("=" * 64)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
