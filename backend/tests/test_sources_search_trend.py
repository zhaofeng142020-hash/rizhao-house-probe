"""来源真实性 / 小区搜索 / 关注波动 的回归测试。

重点不是「接口能返回 200」，而是三件容易悄悄坏掉的事：
  1. 演示数据绝不能被展示成来自真实平台；
  2. 真实房源必须带原始链接才准导入，且不会被样本重灌洗掉；
  3. 小区价格序列要真的有历史点，而不是一条平线。
"""
from __future__ import annotations

import pytest


def test_sources_overview_tells_the_truth(client):
    r = client.get("/api/sources").json()
    assert r["total"] == 220
    assert r["demo"] == 220 and r["real"] == 0
    assert "演示样本" in r["honesty_note"]
    assert len(r["sources"]) == 8
    verified = {x["key"] for x in r["sources"] if x["verified"]}
    # 58 已在 2026-11-06 实跑通：复用会话由服务端低频抓首屏，实抓 117 套
    assert verified == {"ke", "anjuke", "wuba", "gov"}, "只有实际跑通并能取到数据的通道才能标已验证"
    assert all(x["verified"] is False for x in r["sources"] if x["key"] not in verified), \
        "未验证的通道不能标成已核验"
    assert all(s["real_count"] == 0 for s in r["sources"])


def test_demo_listing_never_pretends_to_be_from_a_real_platform(client):
    item = client.get("/api/listings?limit=1").json()["items"][0]
    detail = client.get(f"/api/listings/{item['id']}").json()
    src = detail["item"]["source_info"]
    assert src["kind"] == "demo"
    assert src["label"] == "演示样本"
    assert src["platform"] == "" and src["url"] is None
    assert src["verified"] is False


def test_community_search_escapes_wildcards_and_matches_alias(client):
    # % 若没转义会匹配全部小区，这是个静默但危险的错误
    assert client.get("/api/communities", params={"keyword": "%"}).json()["total"] == 0
    assert client.get("/api/communities", params={"keyword": "_"}).json()["total"] == 0

    all_rows = client.get("/api/communities").json()
    assert all_rows["total"] == 15
    name = all_rows["items"][0]["name"]
    hit = client.get("/api/communities", params={"keyword": name[:2]}).json()
    assert hit["total"] >= 1
    assert any(x["name"] == name for x in hit["items"])
    # 板块也能搜
    district = all_rows["items"][0]["district"]
    assert client.get("/api/communities", params={"keyword": district[:2]}).json()["total"] >= 1


def test_community_trend_has_real_history_points(client):
    cid = client.get("/api/communities").json()["items"][0]["id"]
    t = client.get(f"/api/communities/{cid}/trend").json()
    assert t["points"] >= 5, "必须真的回推出历史序列，不能只有今天一个点"
    assert t["backfill_points"] >= 1
    assert t["current"] and t["first"]
    assert "回推" in t["quality_note"]
    assert t["disclaimer"]
    assert t["series"] == sorted(t["series"], key=lambda p: p["date"])


def test_follow_and_unfollow_community(client):
    cid = client.get("/api/communities").json()["items"][0]["id"]
    assert client.get("/api/communities/followed").json()["total"] == 0

    r = client.post(f"/api/communities/{cid}/watch").json()
    assert r["ok"] and r["watched"] and r["base_unit"] > 0

    followed = client.get("/api/communities/followed").json()
    assert followed["total"] == 1
    assert followed["items"][0]["points"] >= 1
    assert "change_30d" in followed["items"][0]

    # 观察清单里小区行要有价格曲线，不能是空数组
    rows = client.get("/api/watchlist").json()["items"]
    comm = [x for x in rows if x["target_type"] == "community"]
    assert len(comm) == 1 and len(comm[0]["curve"]) >= 1

    assert client.delete(f"/api/communities/{cid}/watch").json()["watched"] is False
    assert client.get("/api/communities/followed").json()["total"] == 0


def test_import_requires_original_url(client):
    before = client.get("/api/sources").json()["total"]
    bad = client.post("/api/admin/sources/import", json={
        "rows": [{"community_name": "测试小区", "area": 120, "total_wan": 100,
                  "source_url": "不是链接"}]
    })
    assert bad.status_code == 200
    body = bad.json()
    assert body["items_saved"] == 0
    assert "source_url" in body["errors"][0]["reason"]
    assert client.get("/api/sources").json()["total"] == before


def test_import_real_listing_is_flagged_and_verifiable(client):
    good = client.post("/api/admin/sources/import", json={
        "source_key": "manual",
        "source_name": "中介门店实勘",
        "rows": [{
            "community_name": "核验测试小区", "district": "东港区", "layout": "3室2厅",
            "area": 130, "total_wan": 118, "source_url": "https://example.com/house/9988",
            "source_ref": "9988", "list_days": 30,
        }],
    }).json()
    assert good["items_saved"] == 1
    lid = good["saved_ids"][0]

    ov = client.get("/api/sources").json()
    assert ov["real"] == 1 and ov["manual"] == 1
    assert ov["demo"] == 220 and ov["total"] == 221
    assert "演示样本" not in ov["honesty_note"] or "1 套" in ov["honesty_note"]

    src = client.get(f"/api/listings/{lid}").json()["item"]["source_info"]
    assert src["kind"] == "manual" and src["verified"] is True
    assert src["platform"] == "中介门店实勘"
    assert src["url"] == "https://example.com/house/9988"
    assert src["ref"] == "9988" and src["collected_at"]

    # 有真实数据后，样本版本重灌必须被拦住，不能把用户录入的东西洗掉
    from app import main as app_main
    from app.services import auth, seed as seed_mod
    auth.set_setting("seed_version", "v-old")
    called = {"n": 0}

    def fake_seed_all(clear=False):
        called["n"] += 1
        return {"communities": 0, "listings": 0, "auctions": 0, "new_homes": 0}

    original = seed_mod.seed_all
    seed_mod.seed_all = fake_seed_all
    try:
        app_main.bootstrap()
    finally:
        seed_mod.seed_all = original
    assert called["n"] == 0, "已导入真实房源时绝不能重灌演示数据"
    assert client.get(f"/api/listings/{lid}").status_code == 200


def test_demo_seed_does_not_fake_a_source_url():
    """演示样本的来源链接必须为空，否则前端会渲染成可点的假链接。"""
    from app.db import query_all
    rows = query_all("SELECT source_url FROM listing WHERE source_kind='demo'")
    assert rows and all(not (r["source_url"] or "").strip() for r in rows)


@pytest.mark.parametrize("area,total_wan", [(0, 100), (700, 100), (120, 0.5), (120, 20000)])
def test_import_rejects_implausible_numbers(client, area, total_wan):
    r = client.post("/api/admin/sources/import", json={
        "rows": [{"community_name": "数值校验", "area": area, "total_wan": total_wan,
                  "source_url": "https://example.com/x"}]
    }).json()
    assert r["items_saved"] == 0
    assert r["errors"]


def test_weekly_report_does_not_drop_watched_communities(client):
    """小区关注没有捡漏分，不能因为 score 为 NULL 就被判「建议剔除」。"""
    cid = client.get("/api/communities").json()["items"][0]["id"]
    client.post(f"/api/communities/{cid}/watch")
    rows = client.get("/api/watchlist").json()["items"]
    wid = next(x["id"] for x in rows if x["target_type"] == "community")

    report = client.get("/api/watchlist/weekly-report").json()
    assert wid not in {x["id"] for x in report["drop_out"]}
    assert wid in {x["id"] for x in report["still_worth"]}


def test_source_toggle_and_crawl_are_honest(client):
    """勾选通道不等于接上数据；CRAWL_ENABLED=0 时绝不能偷偷联网。"""
    r = client.put("/api/admin/sources/fangtianxia/enable", json={"enabled": True}).json()
    assert r["enabled"] is True
    s = next(x for x in client.get("/api/sources").json()["sources"]
             if x["key"] == "fangtianxia")
    assert s["enabled"] is True
    assert s["verified"] is False and s["real_count"] == 0

    before = len(client.get("/api/admin/sources/batches").json()["items"])
    crawl = client.post("/api/admin/sources/crawl").json()
    assert crawl["status"] == "skipped"
    assert "CRAWL_ENABLED" in crawl["reason"]
    # 跳过采集不能凭空写一条批次，否则批次列表会假装有导入
    assert len(client.get("/api/admin/sources/batches").json()["items"]) == before


def test_search_page_api_shapes(client):
    """搜索页依赖的三个列表接口返回结构必须稳定，避免前端兼容猜测。"""
    ds = client.get("/api/meta/districts").json()
    assert isinstance(ds["items"], list)
    assert all("district" in x for x in ds["items"])

    kw = client.get("/api/communities").json()["items"][0]["name"][:2]
    listings = client.get("/api/listings", params={"keyword": kw, "limit": 8}).json()
    homes = client.get("/api/new-homes", params={"keyword": kw}).json()
    assert isinstance(listings["items"], list) and "total" in listings
    assert isinstance(homes["items"], list)
    # 关键字里的 % / _ 必须按字面匹配，不能变成全表扫描
    assert client.get("/api/listings", params={"keyword": "%"}).json()["total"] == 0


def _import_one(client, **overrides):
    row = {
        "community_name": "真实小区A", "district": "石臼区", "layout": "3室2厅",
        "area": 120, "total_wan": 100, "source_url": "https://rz.ke.com/ershoufang/9001.html",
    }
    row.update(overrides)
    return client.post("/api/admin/sources/import", json={
        "rows": [row], "source_key": "ke",
        "source_name": "贝壳找房 · 日照二手房", "source_kind": "crawled",
    }).json()


def test_import_crawled_kind_is_labeled_honestly(client):
    r = _import_one(client)
    assert r["items_saved"] == 1
    src = client.get(f"/api/listings/{r['saved_ids'][0]}").json()["item"]["source_info"]
    assert src["kind"] == "crawled"
    assert src["label"] == "自动采集"
    assert src["platform"] == "贝壳找房 · 日照二手房"
    assert src["verified"] is True


def test_purge_demo_keeps_only_real_data(client):
    r = _import_one(client, area=121.5, total_wan=101, source_url="https://rz.ke.com/ershoufang/9002.html")
    lid = r["saved_ids"][0]
    demo_before = client.get("/api/sources").json()["demo"]

    out = client.post("/api/admin/purge-demo", json={}).json()
    assert out["purged"]["listing"] == demo_before, "全部演示房源都应被清除"
    ov = client.get("/api/sources").json()
    assert ov["demo"] == 0 and ov["total"] == ov["real"], "库里只刺真实数据"
    assert client.get(f"/api/listings/{lid}").status_code == 200

    # 清完之后 seed_version 已标记，重启也不允许重灌假数据
    from app.services import auth
    assert auth.get_setting("seed_version") == "purged-demo"


def test_purge_demo_removes_orphan_references(client):
    """清演示数据后，观察项不能有指向已删房源的悬空引用。"""
    item = client.get("/api/listings?limit=1").json()["items"][0]
    client.post("/api/watchlist", json={
        "target_type": "listing", "target_id": item["id"], "note": "test"})
    client.post("/api/admin/purge-demo", json={}).json()
    alive = {x["id"] for x in client.get("/api/listings?limit=300").json()["items"]}
    rows = client.get("/api/watchlist").json()["items"]
    dangling = [x for x in rows if x["target_type"] == "listing" and x["target_id"] not in alive]
    assert not dangling, "演示房源的观察项必须一并清理，不能留悬空引用"


def test_upsert_communities_fills_geo_and_rejects_dirty_coords(client):
    # 已有小小区：导入一条让它存在
    _import_one(client, community_name="坐标测试小区")
    r = client.post("/api/admin/communities/upsert", json={"rows": [
        {"name": "坐标测试小区", "district": "石臼区", "lat": 35.41, "lng": 119.53},
        {"name": "新建小区B", "district": "万象汇", "lat": 35.42, "lng": 119.55},
        # 越界坐标（上海附近）必须拒收，防止外地小区混进日照地图
        {"name": "外地小区", "lat": 31.23, "lng": 121.47},
    ]}).json()
    assert r["ok"] and r["skipped"] == 1

    row = client.get("/api/communities", params={"keyword": "坐标测试小区"}).json()["items"][0]
    assert row["lat"] and row["lng"]
    new_row = client.get("/api/communities", params={"keyword": "新建小区B"}).json()["items"][0]
    assert new_row["district"] == "万象汇"


def test_reimport_same_listing_updates_instead_of_duplicating(client):
    """每天抓同一套房不能越抓越多：同平台编号的再导入必须走「更新」。"""
    r1 = _import_one(client, source_ref="R-2001", community_name="去重测试小区")
    assert r1["items_saved"] == 1
    before = client.get("/api/listings?limit=1").json()["total"]

    r2 = _import_one(client, source_ref="R-2001", community_name="去重测试小区")
    assert r2["items_saved"] == 0
    assert r2["items_updated"] == 1
    assert client.get("/api/listings?limit=1").json()["total"] == before


def test_price_change_is_recorded_in_history(client):
    """房价变了要落一条调价记录，而不是新增一套房。"""
    r1 = _import_one(client, source_ref="R-2002", community_name="调价测试小区", total_wan=100)
    lid = r1["saved_ids"][0]
    h1 = len(client.get(f"/api/listings/{lid}").json()["history"])

    r2 = _import_one(client, source_ref="R-2002", community_name="调价测试小区", total_wan=92)
    assert r2["price_changes"] == 1
    assert r2["items_saved"] == 0

    detail = client.get(f"/api/listings/{lid}").json()
    assert detail["item"]["total_price"] == 920000
    assert len(detail["history"]) == h1 + 1, "调价必须落 price_history"
    assert detail["history"][-1]["price"] == 920000
