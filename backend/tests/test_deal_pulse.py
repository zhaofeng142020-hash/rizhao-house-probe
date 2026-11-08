"""法拍成交闭环测试：成交指标 / 汇总口径 / 回填幂等 / 批量导入 / 列表与首页过滤。

不变量：
  1. 演示样本不进真实成交汇总，口径声明固定带上「法拍成交价 ≠ 二手成交价」；
  2. 同一标的重复回填只保留一条 auction_result 事件；
  3. 已成交（sold）标的仍可查，但不再出现在首页「法拍机会」与捡漏排行里；
  4. 非法成交价 / 日期不落库，批量导入单行失败不影响其它行。

共享临时测试库，跑完必须把自己插入的数据清干净（见 conftest 与 HANDOFF 第 8 节）。
"""
from __future__ import annotations

from datetime import date

import pytest as _pytest

from app.db import execute, query_all, query_one
from app.services.deal_pulse import deal_metrics, deal_summary
from app.services.sources import data_quality

FP = "fp-deal-%"


@_pytest.fixture(autouse=True)
def _cleanup(temp_db):
    yield
    execute(
        "DELETE FROM leak_score WHERE target_type='auction' AND target_id IN "
        "(SELECT id FROM auction WHERE fingerprint LIKE ?)", (FP,)
    )
    execute("DELETE FROM anomaly_event WHERE event_key LIKE 'auction_result|auction|%'")
    execute("DELETE FROM listing WHERE fingerprint LIKE ?", (FP,))
    execute("DELETE FROM auction WHERE fingerprint LIKE ?", (FP,))


def _add_auction(tag, *, source_kind="crawled", deal_price=None, deal_date=None,
                 status="active", area=100.0, start_price=500000.0,
                 appraisal_price=1000000.0):
    return execute(
        "INSERT INTO auction (fingerprint,source,source_kind,title,community_name,district,"
        "area,start_price,appraisal_price,stage,deal_price,deal_date,status,source_url) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"fp-deal-{tag}", "ali", source_kind, f"成交测试标的{tag}", "成交测试花园", "东港区",
         area, start_price, appraisal_price, "一拍", deal_price, deal_date, status,
         "https://example.com/auction/1"),
    )


def _add_score(auction_id, grade="red", score=90):
    execute(
        "INSERT INTO leak_score (target_type,target_id,score,s_price,s_motive,s_defect,"
        "s_liquidity,s_fit,confidence,grade,sample_n) VALUES ('auction',?,?,?,?,?,?,?,'mid',?,5)",
        (auction_id, score, 90, 80, 80, 80, 80, grade),
    )


def _add_listing(tag, unit_price):
    return execute(
        "INSERT INTO listing (fingerprint,source,source_kind,community_name,district,area,"
        "total_price,unit_price,status) VALUES (?,?,?,?,?,?,?,?,'active')",
        (f"fp-deal-l-{tag}", "ke", "crawled", "成交测试花园", "东港区",
         100.0, unit_price * 100, unit_price),
    )


# --------------------------------------------------------------------------
# 纯函数
# --------------------------------------------------------------------------
def test_deal_metrics_pure():
    m = deal_metrics({
        "deal_price": 720000, "deal_date": "2026-10-01 00:00:00", "area": 100.0,
        "appraisal_price": 1000000, "start_price": 500000,
    }, 11000)
    assert m["deal_price"] == 720000.0
    assert m["deal_date"] == "2026-10-01"
    assert m["deal_unit"] == 7200.0
    assert m["appraisal_discount_pct"] == 28.0
    assert m["over_start_pct"] == 44.0
    assert m["listing_median_unit"] == 11000.0
    assert m["deal_vs_listing_pct"] == round((11000 - 7200) / 11000 * 100, 2)

    # 分母为 0 / NULL 一律 None，不除零、不臆造
    zero = deal_metrics({"deal_price": 100, "area": 0, "appraisal_price": 0,
                         "start_price": None}, None)
    assert zero["deal_unit"] is None
    assert zero["appraisal_discount_pct"] is None
    assert zero["over_start_pct"] is None
    assert zero["deal_vs_listing_pct"] is None

    # 成交价高于评估价时折价率为负，中位数必须保留负数
    assert deal_metrics({"deal_price": 1200000, "appraisal_price": 1000000})[
        "appraisal_discount_pct"] == -20.0


def test_deal_summary_shape_and_disclaimer():
    out = deal_summary(730)
    for key in ("total", "items", "by_period", "summary", "days", "quality_note", "disclaimer"):
        assert key in out
    assert out["total"] == len(out["items"])
    assert isinstance(out["by_period"], list)
    assert "法拍成交价 ≠ 二手成交价" in out["disclaimer"]
    assert out["summary"]["earliest"] is None or isinstance(out["summary"]["earliest"], str)


def test_deal_summary_excludes_demo_and_aggregates(temp_db):
    _add_listing("a", 10000)
    _add_listing("b", 12000)
    _add_auction("c1", deal_price=720000, deal_date="2026-09-05", status="sold")
    _add_auction("c2", deal_price=680000, deal_date="2026-10-05", status="sold")
    _add_auction("c3", deal_price=810000, deal_date="2026-10-20", status="sold")
    _add_auction("demo", source_kind="demo", deal_price=999999,
                 deal_date="2026-10-21", status="sold")

    out = deal_summary(3650)
    assert out["total"] == 3, "演示样本不得混入真实成交锚点"
    titles = {i["title"] for i in out["items"]}
    assert "成交测试标的demo" not in titles
    # items 按成交日期倒序
    dates = [i["deal_date"] for i in out["items"]]
    assert dates == sorted(dates, reverse=True)
    # 2026-10 有两条、2026-09 一条
    periods = {p["period"]: p for p in out["by_period"]}
    assert periods["2026-10"]["n"] == 2
    assert periods["2026-09"]["n"] == 1
    assert out["summary"]["earliest"] == "2026-09-05"
    assert out["summary"]["latest"] == "2026-10-20"
    # 与同小区挂牌中位（11000）对比口径生效
    assert all(i["listing_median_unit"] == 11000.0 for i in out["items"])
    assert all(i["deal_vs_listing_pct"] is not None for i in out["items"])
    assert "样本不足" in out["quality_note"]


# --------------------------------------------------------------------------
# 回填接口
# --------------------------------------------------------------------------
def test_auction_result_endpoint_idempotent(client):
    aid = _add_auction("idem")
    r = client.post(f"/api/admin/auctions/{aid}/result",
                    json={"deal_price": 720000, "deal_date": "2026-10-01"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["deal_price"] == 720000
    assert body["deal_unit"] == 7200.0

    # 再改一次价：同一标的仍只有一条事件，金额被更新
    r2 = client.post(f"/api/admin/auctions/{aid}/result",
                     json={"deal_price": 760000, "deal_date": "2026-10-02"})
    assert r2.status_code == 200, r2.text
    au = query_one("SELECT deal_price,deal_date,status FROM auction WHERE id=?", (aid,))
    assert float(au["deal_price"]) == 760000 and au["status"] == "sold"
    events = query_all(
        "SELECT * FROM anomaly_event WHERE target_type='auction' AND target_id=? "
        "AND event_type='auction_result'", (aid,))
    assert len(events) == 1


def test_auction_result_validation(client):
    aid = _add_auction("invalid")
    for bad in (0, -1, 1e30):
        r = client.post(f"/api/admin/auctions/{aid}/result", json={"deal_price": bad})
        assert r.status_code == 400, (bad, r.text)
    r = client.post(f"/api/admin/auctions/{aid}/result",
                    json={"deal_price": 700000, "deal_date": "2026/10/01"})
    assert r.status_code == 400
    assert query_one("SELECT deal_price FROM auction WHERE id=?", (aid,))["deal_price"] is None
    r404 = client.post("/api/admin/auctions/99999999/result", json={"deal_price": 1})
    assert r404.status_code == 404


def test_batch_import_partial_failure(client):
    a1, a2, a3 = (_add_auction("b1"), _add_auction("b2"), _add_auction("b3"))
    rows = [
        {"auction_id": a1, "deal_price": 720000, "deal_date": "2026-10-01"},
        {"auction_id": a2, "deal_price": -1, "deal_date": "2026-10-02"},
        {"auction_id": a3, "deal_price": 800000, "deal_date": "not-a-date"},
        {"auction_id": 99999999, "deal_price": 1, "deal_date": "2026-10-03"},
        {"deal_price": 1},
    ]
    r = client.post("/api/admin/auctions/results/import", json={"rows": rows})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == 1 and body["skipped"] == 4 and len(body["errors"]) == 4
    assert float(query_one("SELECT deal_price FROM auction WHERE id=?", (a1,))["deal_price"]) == 720000
    assert query_one("SELECT deal_price FROM auction WHERE id=?", (a2,))["deal_price"] is None

    # CSV 入口同样可用（含 BOM 与列头）
    a4 = _add_auction("b4")
    csv_text = f"auction_id,deal_price,deal_date\n{a4},650000,2026-10-04\n"
    r2 = client.post("/api/admin/auctions/results/import", json={"csv": "\ufeff" + csv_text})
    assert r2.status_code == 200, r2.text
    assert r2.json()["saved"] == 1


# --------------------------------------------------------------------------
# 列表 / 首页过滤
# --------------------------------------------------------------------------
def test_list_auctions_status_filter(client):
    active = _add_auction("s-active")
    sold = _add_auction("s-sold", deal_price=700000, deal_date="2026-10-01", status="sold")
    r_active = client.get("/api/auctions?status=active")
    r_sold = client.get("/api/auctions?status=sold")
    r_all = client.get("/api/auctions?status=all")
    assert r_active.status_code == r_sold.status_code == r_all.status_code == 200
    ids_active = {x["id"] for x in r_active.json()["items"]}
    ids_sold = {x["id"] for x in r_sold.json()["items"]}
    ids_all = {x["id"] for x in r_all.json()["items"]}
    assert active in ids_active and sold not in ids_active
    assert sold in ids_sold and all(x["status"] == "sold" for x in r_sold.json()["items"])
    assert active in ids_all and sold in ids_all

    detail = client.get(f"/api/auctions/{sold}")
    assert detail.status_code == 200
    assert detail.json()["deal"]["deal_price"] == 700000.0


def test_sold_excluded_from_today_and_ranking(client):
    sold = _add_auction("sold-rank", deal_price=700000,
                        deal_date=date.today().isoformat(), status="sold")
    _add_score(sold)
    today = client.get("/api/anomalies/today")
    ranking = client.get("/api/anomalies/ranking?include_white=true")
    assert today.status_code == ranking.status_code == 200
    assert sold not in {x["id"] for x in today.json()["auctions"]}
    assert sold not in {x["id"] for x in ranking.json()["items"] if x.get("kind") == "auction"}


def test_market_deals_api(client):
    r = client.get("/api/market/deals")
    assert r.status_code == 200
    body = r.json()
    for key in ("total", "items", "by_period", "summary", "quality_note", "disclaimer"):
        assert key in body
    r2 = client.get("/api/market/deals?days=90")
    assert r2.status_code == 200 and r2.json()["days"] >= 30


def test_quality_includes_deal_anchor(temp_db):
    _add_auction("quality", deal_price=700000, deal_date="2026-10-01", status="sold")
    out = data_quality()
    # 新字段
    assert out["deal_anchor_n"] >= 1
    assert out["deal_latest"] == "2026-10-01"
    # 原有字段一字不改地保留
    for key in ("sources", "captcha_blocked_now", "note"):
        assert key in out
    assert "deal_anchor_n" in out["note"]
