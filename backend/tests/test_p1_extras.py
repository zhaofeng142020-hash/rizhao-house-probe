"""P1/P2 增量测试：租金抓取与租售比 / 降价时机规律 / 利率等价 / 数据质量。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest as _pytest

from app.db import execute, query_one
from app.services import market_pulse  # noqa: F401  确保模块可导入
from app.services.anomaly import drop_timing
from app.services.afford import monthly_payment, rate_equivalence
from app.services.rent_pulse import parse_ke_zufang, rent_summary, store_rents

SAMPLE_HTML = """
<div class="content__list--item" data-house_code="ZU123">
  <a class="content__list--item--aside" href="/zufang/ZU123.html" title="整租·湖西头三区 3室2厅 南/北"></a>
  <div class="content__list--item--main">
    <p class="content__list--item--des">
      <a href="/zufang/donggangqu/">东港区</a>-<a href="/zufang/gaoxinqu27/">高新区</a>-<a title="湖西头三区" href="/zufang/c88520000000300/">湖西头三区</a>
      <i>/</i> 121.00㎡ <i>/</i>南 北 <i>/</i> 3室2厅1卫
    </p>
    <span class="content__list--item-price"><em>833</em> 元/月</span>
  </div>
</div>
<div class="content__list--item" data-house_code="ZU456">
  <a class="content__list--item--aside" href="/zufang/ZU456.html" title="合租·蓝天海景国际 4室1厅 南"></a>
  <div class="content__list--item--main">
    <p class="content__list--item--des">
      <a href="/zufang/donggangqu/">东港区</a>-<a href="/zufang/shijiu/">石臼</a>-<a title="蓝天海景国际" href="/zufang/c777/">蓝天海景国际</a>
      <i>/</i> 80.00㎡ <i>/</i>南 <i>/</i> 2室1厅
    </p>
    <span class="content__list--item-price"><em>1200</em> 元/月</span>
  </div>
</div>
"""


@_pytest.fixture(autouse=True)
def _cleanup(temp_db):
    yield
    execute("DELETE FROM rent_sample")
    execute("DELETE FROM price_history WHERE note LIKE 'pytest%'")
    execute("DELETE FROM market_daily")
    execute("DELETE FROM listing WHERE fingerprint LIKE 'fp-rent-%' OR fingerprint LIKE 'fp-drop-%'")
    # 只删除没有房源挂在上面的小区，避免误删种子数据
    execute("DELETE FROM community WHERE name='湖西头三区' AND NOT EXISTS "
            "(SELECT 1 FROM listing l WHERE l.community_id=community.id)")


def test_parse_ke_zufang():
    rows = parse_ke_zufang(SAMPLE_HTML)
    assert len(rows) == 2
    a, b = rows
    assert a["community_name"] == "湖西头三区"
    assert a["rent_month"] == 833.0
    assert a["area"] == 121.0
    assert a["rent_pm"] == round(833 / 121, 2)
    assert a["district"] == "东港区"
    assert a["layout"] and "3室2厅" in a["layout"]
    assert a["source_ref"] == "ZU123"
    assert b["community_name"] == "蓝天海景国际"


def test_store_and_rent_summary(temp_db):
    today = date.today().isoformat()
    # 小区档案里有「湖西头三区」吗？没有就造一个，确保挂牌单价能对上
    if not query_one("SELECT 1 FROM community WHERE name='湖西头三区'"):
        execute("INSERT INTO community (name,district,liquidity_score) VALUES ('湖西头三区','东港区',60)")
    execute(
        "INSERT INTO listing (fingerprint,source,source_kind,community_name,district,area,"
        "total_price,unit_price,status) VALUES ('fp-rent-1','ke','crawled','湖西头三区','东港区',"
        "100.0,1200000.0,12000.0,'active')"
    )
    rows = parse_ke_zufang(SAMPLE_HTML)
    st = store_rents(rows, day=date.today())
    assert st["saved"] >= 1
    s = rent_summary()
    assert s["collected_date"] == today
    names = {c["community_name"] for c in s["communities"]}
    assert "湖西头三区" in names
    item = next(c for c in s["communities"] if c["community_name"] == "湖西头三区")
    assert item["median_rent"] == 833.0
    assert item["rent_yield_pct"] is not None
    # 年化回报 = 6.88(元/㎡·月)*12 / 12000 ≈ 0.69%（这批样本租金低，口径如此）
    assert 0.5 < item["rent_yield_pct"] < 1.0
    # 幂等：同一天重复入库不重复计样本
    st2 = store_rents(rows, day=date.today())
    assert st2["skipped"] >= 1
    assert rent_summary()["city"]["sample_n"] == len(rows)


def test_drop_timing(temp_db):
    today = date.today()
    lid = execute(
        "INSERT INTO listing (fingerprint,source,source_kind,community_name,district,area,"
        "total_price,unit_price,status) VALUES ('fp-drop-1','ke','crawled','降价盘','东港区',"
        "100.0,1000000.0,10000.0,'active')"
    )
    for i, (ago, pct) in enumerate([(3, -0.06), (10, -0.08)]):
        d = (today - timedelta(days=ago)).isoformat()
        execute(
            "INSERT INTO price_history (listing_id,price,unit_price,observed_at,drop_pct,kind,note) "
            "VALUES (?,?,?,?,?,?,?)",
            (lid, 900000 - i * 1000, 9000, d, pct, "listing", "pytest-drop"),
        )
    t = drop_timing(365)
    assert t["total_drops"] >= 2
    assert len(t["months"]) == 12 and len(t["weekdays"]) == 7
    assert sum(x["n"] for x in t["months"]) == t["total_drops"]


def test_rate_equivalence_monotonic():
    rows = rate_equivalence(1000000, 0.30, 30, 0.031)
    assert rows, "应有利率档位输出"
    anchor = [r for r in rows if abs(r["rate"] - 0.031) < 1e-6][0]
    assert abs(anchor["diff_vs_base_wan"]) < 0.2       # 基准档位应约等于原价
    prices = [r["affordable_price"] for r in rows]
    assert prices == sorted(prices, reverse=True)      # 利率越低，可承受房价越高
    # 月供口径核对：锚定档位的月供应与基准月供一致
    assert anchor["monthly_payment"] == round(monthly_payment(1000000 * 0.7, 0.031, 30))


def test_quality_and_apis(client):
    r = client.get("/api/market/rents")
    assert r.status_code == 200
    assert "communities" in r.json()
    r2 = client.get("/api/meta/rate-table?price=1200000")
    assert r2.status_code == 200
    assert len(r2.json()["rows"]) >= 20
    r3 = client.get("/api/anomalies/drop-timing")
    assert r3.status_code == 200
    assert "months" in r3.json()
