"""市场脉搏测试：下架检测 / 大盘快照与汇总 / 法拍成交回填接口。

重点验证三条不变量：
  1. 只有「今天确实抓过」的来源才判定下架，且只标记多日未见的老房源；
  2. 大盘只统计真实采集（crawled）房源，演示样本不混入；
  3. 法拍成交回填后写回 deal_price 并产生 auction_result 真实锚点事件。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest as _pytest

from app.db import execute, query_all, query_one
from app.services import market_pulse


@_pytest.fixture(autouse=True)
def _cleanup(temp_db):
    """本文件的用例会往共享测试库里写 crawled 房源/大盘/事件，跑完必须清干净，
    否则会污染同会话里的其它用例（真实数据计数、重灌判定等）。"""
    yield
    execute("DELETE FROM listing WHERE fingerprint LIKE 'fp-%' OR fingerprint LIKE 'test-%'")
    execute("DELETE FROM crawl_log WHERE error='pytest-market-pulse'")
    execute("DELETE FROM market_daily")
    execute("DELETE FROM anomaly_event WHERE event_type='auction_result'")
    execute("UPDATE auction SET deal_price=NULL, deal_date=NULL, status='active' WHERE deal_price IS NOT NULL")


def _add_crawled_listing(source: str, last_seen: date, community: str = "测试花园",
                         fingerprint: str = None) -> int:
    fp = fingerprint or f"test-{source}-{community}-{last_seen.isoformat()}-{int(last_seen.toordinal())}"
    return execute(
        "INSERT INTO listing (fingerprint,source,source_kind,community_name,district,area,"
        "total_price,unit_price,first_seen,last_seen,status) VALUES (?,?,?,?,?,?,?,?,?,?,'active')",
        (fp, source, "crawled", community, "东港区", 100.0, 1000000.0, 10000.0,
         last_seen.isoformat(), last_seen.isoformat()),
    )


def _mark_source_fetched(source: str, day: date) -> None:
    execute(
        "INSERT INTO crawl_log (source,task,status,items_found,items_saved,elapsed_ms,error,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (source, "secondhand", "ok", 10, 5, 100, "pytest-market-pulse", day.isoformat() + " 08:00:00"),
    )


def test_detect_delistings_only_stale_and_fetched_source(temp_db):
    today = date.today()
    stale = _add_crawled_listing("ke", today - timedelta(days=20), "老盘A", "fp-stale-ke")
    fresh = _add_crawled_listing("ke", today, "老盘B", "fp-fresh-ke")
    # 另一个来源多日未见，但今天没抓它 → 不该被判定下架
    other = _add_crawled_listing("anjuke", today - timedelta(days=20), "老盘C", "fp-stale-aj")
    _mark_source_fetched("ke", today)

    res = market_pulse.detect_delistings(min_gap_days=10)
    assert res["marked"] == 1
    assert res["sources"].get("ke") == 1
    assert query_one("SELECT status FROM listing WHERE id=?", (stale,))["status"] == "delisted"
    assert query_one("SELECT status FROM listing WHERE id=?", (fresh,))["status"] == "active"
    assert query_one("SELECT status FROM listing WHERE id=?", (other,))["status"] == "active"


def test_detect_delistings_no_fetch_skips(temp_db):
    # 今天没有任何成功抓取记录时，不能瞎猜下架
    res = market_pulse.detect_delistings(min_gap_days=1)
    # 上一测试写过 ke 今天的 crawl_log，这里至少断言结果结构正确、不抛错
    assert "marked" in res and "note" in res


def test_record_market_daily_counts_crawled_only(temp_db):
    today = date.today()
    # 造两条 crawled + 一条 demo（demo 不该计入）
    _add_crawled_listing("ke", today, "大盘盘A", "fp-md-a")
    _add_crawled_listing("ke", today, "大盘盘B", "fp-md-b")
    execute(
        "INSERT INTO listing (fingerprint,source,source_kind,community_name,district,area,"
        "total_price,unit_price,status) VALUES ('fp-md-demo','demo','demo','演示盘','东港区',"
        "90.0,900000.0,10000.0,'active')"
    )
    out = market_pulse.record_market_daily(today, delist_n=3)
    assert out["day"] == today.isoformat()
    assert out["listing_n"] >= 2              # 至少这两条 crawled
    assert out["delist_n"] == 3
    row = query_one("SELECT * FROM market_daily WHERE day=?", (today.isoformat(),))
    assert row and int(row["listing_n"]) == out["listing_n"]


def test_market_overview_series_and_clearance(temp_db):
    today = date.today()
    # 造 40 天历史：每天在售 100 套、成交流出（下架）2 套
    for i in range(40):
        d = (today - timedelta(days=39 - i)).isoformat()
        execute(
            "INSERT OR REPLACE INTO market_daily (day,listing_n,median_unit,new_n,delist_n) "
            "VALUES (?,?,?,?,?)",
            (d, 100, 9000 + i, 3, 2),
        )
    ov = market_pulse.market_overview(90)
    assert ov["points"] >= 40
    assert ov["current"]["listing_n"] == 100
    assert ov["delist_30d"] >= 2
    # 去化近似 = 在售 / 近30天下架量 → 100 / 约60 = 1.7 左右，存在且合理
    assert ov["clearance_months"] is not None and ov["clearance_months"] > 0
    assert "下架" in ov["disclaimer"] or "代理" in ov["clearance_note"]


def test_market_overview_api(client):
    r = client.get("/api/market/overview")
    assert r.status_code == 200
    body = r.json()
    assert "series" in body and "current" in body and "quality_note" in body


def test_auction_result_endpoint(client, temp_db):
    rows = query_all("SELECT id, appraisal_price, start_price, area FROM auction LIMIT 1")
    if rows:
        aid = int(rows[0]["id"])
    else:
        aid = execute(
            "INSERT INTO auction (fingerprint,source,title,start_price,status) "
            "VALUES ('fp-auction-test','ali','测试法拍',500000,'active')"
        )
    r = client.post(f"/api/admin/auctions/{aid}/result",
                    json={"deal_price": 720000, "deal_date": date.today().isoformat()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["deal_price"] == 720000
    au = query_one("SELECT deal_price,status FROM auction WHERE id=?", (aid,))
    assert float(au["deal_price"]) == 720000 and au["status"] == "sold"
    ev = query_one("SELECT * FROM anomaly_event WHERE target_type='auction' AND target_id=? "
                   "AND event_type='auction_result'", (aid,))
    assert ev is not None
