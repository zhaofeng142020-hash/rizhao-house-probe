"""纯函数单测：异动识别 + 捡漏评分。

这些测试不需要数据库，直接喂 dict，跑得飞快，适合改模型时做回归。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import anomaly, scoring

TODAY = date(2026, 11, 5)


def make_listing(**over):
    """构造一条普通挂牌，测试里按需覆盖字段。"""
    base = {
        "id": 1, "community_id": 1, "community_name": "测试小区", "district": "新市区",
        "layout": "3室2厅1卫", "rooms": 3, "area": 100.0, "total_price": 1_000_000,
        "unit_price": 10_000, "first_price": 1_100_000, "floor_text": "8/18层",
        "orientation": "南北", "build_year": 2015, "age": 11, "has_elevator": 1,
        "list_days": 60, "price_changes": 0, "consecutive_drops": 0,
        "last_drop_days_ago": None, "defect_tags": [], "rent_month": 2200, "status": "active",
    }
    base.update(over)
    if "unit_price" not in over and base["area"]:
        base["unit_price"] = base["total_price"] / base["area"]
    return base


# =============================================================================
# 价格维度
# =============================================================================
def test_price_score_increases_with_deviation():
    low, _ = scoring.score_price(0.03, sample_n=6)
    high, _ = scoring.score_price(0.18, sample_n=6)
    assert high > low


def test_price_score_penalises_extreme_deviation():
    """极端低价（>35%）必须压分：这种房子大概率有坑。"""
    normal, notes = scoring.score_price(0.30, sample_n=6)
    extreme, extreme_notes = scoring.score_price(0.50, sample_n=6)
    assert extreme <= 70
    assert any("极端低价" in n or "超过 35%" in n for n in extreme_notes)
    assert normal > extreme or normal >= 70


def test_price_score_shrinks_when_sample_too_small():
    """样本不足时向 50 分收缩，避免拿一个孤儿样本当锚。"""
    solid, _ = scoring.score_price(0.20, sample_n=8)
    thin, notes = scoring.score_price(0.20, sample_n=1)
    assert thin < solid
    assert any("样本" in n for n in notes)


def test_price_score_none_deviation_is_neutral():
    score, notes = scoring.score_price(None, sample_n=0)
    assert 40 <= score <= 50
    assert notes


# =============================================================================
# 硬伤与风险
# =============================================================================
def test_defect_score_drops_with_hard_defects():
    clean, _ = scoring.score_defect([])
    one, _ = scoring.score_defect(["顶楼"])
    many, _ = scoring.score_defect(["顶楼", "临街", "无电梯", "房龄>25"])
    assert clean > one > many


def test_defect_score_is_not_full_without_tags():
    """没有标注硬伤 ≠ 没硬伤，基线必须留有余量。"""
    clean, notes = scoring.score_defect([])
    assert clean < 100
    assert "仍需实地核验" in notes[0]


@pytest.mark.parametrize("tags,expected", [
    ([], "low"),
    (["装修旧", "北向"], "low"),            # 可改善项不影响风险等级
    (["顶楼"], "mid"),
    (["顶楼", "无电梯"], "high"),           # 两个不可逆硬伤
    (["产权瑕疵"], "high"),
    (["占用"], "high"),
])
def test_risk_level(tags, expected):
    assert scoring.risk_level_from_defect(scoring.score_defect(tags)[0], tags) == expected


def test_liquidity_penalises_extended_district():
    core, _ = scoring.score_liquidity({"district": "新市区", "total_price": 1_000_000})
    far, _ = scoring.score_liquidity({"district": "莒县", "total_price": 1_000_000, "status": "active"})
    assert core > far


# =============================================================================
# 动机：区分急售让利和硬伤甩卖
# =============================================================================
def test_motive_rewards_consecutive_drops():
    calm = make_listing(price_changes=0, consecutive_drops=0, list_days=20)
    eager = make_listing(price_changes=3, consecutive_drops=3, list_days=220,
                         last_drop_days_ago=5, total_price=850_000)
    calm_score, _ = scoring.score_motive(calm, 65)
    eager_score, _ = scoring.score_motive(eager, 65)
    assert eager_score > calm_score


def test_motive_discounts_when_accompanied_by_hard_defects():
    """同样在降价，但硬伤房源是甩卖而不是让利，动机分要打折。"""
    listing = make_listing(price_changes=3, consecutive_drops=3, list_days=220, total_price=800_000)
    healthy, _ = scoring.score_motive(listing, 65)
    defective, notes = scoring.score_motive(listing, 40)
    assert defective < healthy
    assert any("甩卖" in n for n in notes)


# =============================================================================
# 分级
# =============================================================================
@pytest.mark.parametrize("score,conf,risk,expected", [
    (80, "high", "low", "red"),
    (80, "low", "low", "yellow"),      # 样本不足不给红
    (80, "high", "mid", "yellow"),     # 分数够但风险中
    (80, "high", "high", "white"),     # 风险高一律仅记录
    (65, "high", "low", "yellow"),
    (50, "high", "low", "white"),
])
def test_grade_rules(score, conf, risk, expected):
    assert scoring.grade_of(score, conf, risk) == expected


def test_confidence_downgrades_on_thin_sample():
    high, _ = scoring.score_confidence(6, True, True)
    low, notes = scoring.score_confidence(1, True, True)
    assert high == "high" and low == "low"
    assert any("样本" in n for n in notes)


# =============================================================================
# 整体打分
# =============================================================================
def test_score_listing_full_payload():
    listing = make_listing(total_price=820_000, list_days=210, price_changes=3,
                           consecutive_drops=3, last_drop_days_ago=6, defect_tags=["装修旧"])
    comp = {"median_unit": 11_000, "p25": 10_400, "p75": 11_800, "n": 6}
    result = scoring.score_listing(listing, comp)
    assert 0 <= result["score"] <= 100
    assert result["grade"] in ("red", "yellow", "white")
    assert len(result["radar"] if "radar" in result else result["breakdown"]) == 5
    assert result["why_cheap"] and result["why_trap"]
    assert result["deviation_pct"] > 0.2


def test_score_listing_extreme_low_price_is_not_top_scored():
    """把「垃圾房」和「真漏」区分开：40% 偏离 + 多重硬伤，分数不该进红。"""
    junk = make_listing(total_price=560_000, defect_tags=["临街", "暗卫", "房龄>25"],
                        list_days=88, price_changes=2, consecutive_drops=2)
    comp = {"median_unit": 10_000, "n": 6}
    result = scoring.score_listing(junk, comp)
    assert result["grade"] != "red"
    assert result["risk_level"] == "high"


def test_explanations_mention_price_gap():
    listing = make_listing(total_price=800_000)
    comp = {"median_unit": 10_000, "n": 6}
    result = scoring.score_listing(listing, comp)
    assert "低" in result["why_cheap"]


# =============================================================================
# 异动识别
# =============================================================================
def _stats(listings):
    return anomaly.compute_market_stats(listings)


def test_market_stats_median_and_percentiles():
    # 面积 100㎡，总价 = 单价*100，保证 unit_price 依次为 900/1000/1100/1200/1300
    listings = [make_listing(id=i, total_price=p * 100, unit_price=p)
                for i, p in enumerate([900, 1000, 1100, 1200, 1300])]
    stats = _stats(listings)
    comp = stats["by_comm_layout"][(1, "3室")]
    assert comp["n"] == 5
    assert comp["median"] == 1100


def test_detect_sudden_drop_single():
    listing = make_listing()
    history = [
        {"price": 1_100_000, "observed_at": "2026-06-01", "drop_pct": 0},
        {"price": 1_000_000, "observed_at": "2026-09-01", "drop_pct": -0.0909},
    ]
    events = anomaly.detect_listing_events(listing, history, {"median_unit": 10_000, "n": 5}, _stats([listing]), TODAY)
    types = [e["event_type"] for e in events]
    assert "sudden_drop" in types


def test_detect_sudden_drop_two_in_seven_days():
    listing = make_listing()
    history = [
        {"price": 1_100_000, "observed_at": "2026-09-01", "drop_pct": 0},
        {"price": 1_080_000, "observed_at": "2026-11-01", "drop_pct": -0.018},
        {"price": 1_060_000, "observed_at": "2026-11-04", "drop_pct": -0.019},
    ]
    events = anomaly.detect_listing_events(listing, history, {"median_unit": 10_000, "n": 5}, _stats([listing]), TODAY)
    drop_events = [e for e in events if e["event_type"] == "sudden_drop"]
    assert drop_events
    assert "7 日" in drop_events[0]["detail"]["trigger"]


def test_detect_deep_dip_threshold():
    # 单价 8000 vs 中位 10000 → 偏离 20%，超过 12% 阈值
    listing = make_listing(total_price=800_000)
    events = anomaly.detect_listing_events(listing, [], {"median_unit": 10_000, "n": 5}, _stats([listing]), TODAY)
    assert any(e["event_type"] == "deep_dip" for e in events)


def test_no_deep_dip_when_price_normal():
    listing = make_listing(total_price=990_000)
    events = anomaly.detect_listing_events(listing, [], {"median_unit": 10_000, "n": 5}, _stats([listing]), TODAY)
    assert not any(e["event_type"] == "deep_dip" for e in events)


def test_detect_long_listed_requires_recent_drop():
    hot = make_listing(list_days=400, last_drop_days_ago=10)
    cold = make_listing(list_days=400, last_drop_days_ago=200)
    stats = _stats([hot])
    assert any(e["event_type"] == "long_listed"
               for e in anomaly.detect_listing_events(hot, [], {"median_unit": 10_000, "n": 5}, stats, TODAY))
    assert not any(e["event_type"] == "long_listed"
                   for e in anomaly.detect_listing_events(cold, [], {"median_unit": 10_000, "n": 5}, stats, TODAY))


def test_detect_miskill_requires_controllable_defects():
    soft = make_listing(total_price=930_000, defect_tags=["装修旧"])
    hard = make_listing(total_price=930_000, defect_tags=["产权瑕疵"])
    stats = _stats([soft])
    soft_events = anomaly.detect_listing_events(soft, [], {"median_unit": 10_000, "n": 5}, stats, TODAY)
    hard_events = anomaly.detect_listing_events(hard, [], {"median_unit": 10_000, "n": 5}, stats, TODAY)
    assert any(e["event_type"] == "miskill" for e in soft_events)
    assert not any(e["event_type"] == "miskill" for e in hard_events)


def test_detect_new_home_underpricing():
    listing = make_listing(source="newhome", total_price=900_000)
    events = anomaly.detect_listing_events(listing, [], {"median_unit": 10_000, "n": 5}, _stats([listing]), TODAY)
    assert any(e["event_type"] == "new_home" for e in events)


def test_detect_auction_deal_and_high_risk_downgrade():
    good = {"id": 1, "start_price": 700_000, "appraisal_price": 1_000_000, "area": 100,
            "tax_est": 30_000, "arrears_est": 10_000, "repair_reserve": 20_000,
            "risk_tags": ["税费自担"], "stage": "二拍"}
    risky = {**good, "risk_tags": ["占用", "腾退难"]}
    comp = {"median_unit": 11_000, "n": 5}
    good_events = anomaly.detect_auction_events(good, comp)
    risky_events = anomaly.detect_auction_events(risky, comp)
    assert good_events and good_events[0]["severity_hint"] in ("red", "yellow")
    assert risky_events and risky_events[0]["severity_hint"] == "white"


def test_score_auction_uses_landing_price():
    auction = {"id": 1, "start_price": 700_000, "appraisal_price": 1_000_000, "area": 100,
               "tax_est": 30_000, "arrears_est": 10_000, "repair_reserve": 20_000,
               "risk_tags": ["税费自担"], "stage": "二拍", "district": "新市区"}
    result = scoring.score_auction(auction, {"median_unit": 11_000, "n": 5})
    # 到手价 = 76 万，到手单价 7600，对 11000 的净折扣应显著为正
    assert result["landing_price"] == 760_000
    assert result["net_discount"] > 0.25
    assert 0 <= result["score"] <= 100
