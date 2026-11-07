"""新增功能的回归测试：管理鉴权、购房成本、议价锚点、倒挂、小区与面积专题。"""
from __future__ import annotations

from app.services.afford import bargain_anchor, buy_cost, deed_tax, monthly_payment, rent_view


# --------------------------------------------------------------------------
# 管理鉴权：读开放、写必须带口令
# --------------------------------------------------------------------------
def test_read_is_public_but_write_requires_token(anon_client):
    # 只读接口任何人都能看（家人朋友拿到链接也能查房源）
    assert anon_client.get("/api/listings?limit=3").status_code == 200
    assert anon_client.get("/api/communities").status_code == 200
    assert anon_client.get("/api/anomalies/today").status_code == 200

    # 写接口必须带口令
    r = anon_client.post("/api/watchlist", json={"target_type": "listing", "target_id": 1})
    assert r.status_code == 401
    assert r.json()["code"] == "ADMIN_REQUIRED"
    assert r.headers["content-type"].startswith("application/json")


def test_wrong_token_is_rejected(anon_client):
    r = anon_client.post(
        "/api/watchlist",
        json={"target_type": "listing", "target_id": 1},
        headers={"X-Admin-Token": "WRONG-TOKEN"},
    )
    assert r.status_code == 401


def test_login_and_write_with_token(anon_client, client):
    from app import config

    token_expected = config.settings.ADMIN_TOKEN
    assert token_expected, "测试环境必须固定管理员口令"

    bad = anon_client.post("/api/admin/login", json={"password": "nope"})
    assert bad.status_code == 401

    ok = anon_client.post("/api/admin/login", json={"password": token_expected})
    assert ok.status_code == 200
    token = ok.json()["token"]
    assert token == token_expected

    # 用换来的 token 可以写
    r = anon_client.post(
        "/api/watchlist",
        json={"target_type": "listing", "target_id": 2, "note": "测试"},
        headers={"X-Admin-Token": token},
    )
    assert r.status_code == 200


def test_admin_status_reports_schedule_and_secret_masked(client):
    data = client.get("/api/admin/status").json()
    assert data["counts"]["listing"] > 0
    assert "db_path" in data["data"]
    assert data["schedule"]["daily_at"]
    assert data["schedule"]["next_daily_run"]
    # 密钥不能以明文出现在状态接口里
    assert "token" not in data["security"]

    integ = client.get("/api/admin/integration").json()
    assert any(i["key"] == "wecom_webhook" for i in integ["items"])
    for item in integ["items"]:
        if item["secret"] and item["display"]:
            assert item["display"].startswith("•"), "敏感项回显必须脱敏"


# --------------------------------------------------------------------------
# 购房成本
# --------------------------------------------------------------------------
def test_monthly_payment_matches_reference():
    # 100 万本金、年化 3.1%、30 年，等额本息约 4270 元/月
    m = monthly_payment(1_000_000, 0.031, 30)
    assert 4260 < m < 4280
    # 零利率退化为本金均摊
    assert monthly_payment(360_000, 0, 30) == 1000.0
    # 无贷款时月供为 0，不能除零
    assert monthly_payment(0, 0.031, 30) == 0


def test_deed_tax_by_area_and_home_count():
    # 首套 90㎡ 以下 1%，以上 1.5%
    assert deed_tax(1_000_000, 88, is_first_home=True) == 10_000
    assert deed_tax(1_000_000, 130, is_first_home=True) == 15_000
    # 二套 90㎡ 以上 2%
    assert deed_tax(1_000_000, 130, is_first_home=True, homes_owned=2) == 20_000


def test_buy_cost_cash_needed_includes_everything():
    c = buy_cost(1_200_000, 138, is_first_home=True, is_new=False, decoration="精装")
    assert c["down_payment"] == 360_000
    assert c["loan"] == 840_000
    assert c["monthly"] > 0
    # 现金门槛 = 首付 + 契税 + 中介费 + 维修基金 + 装修/软装
    expected = c["down_payment"] + c["tax"] + c["agent_fee"] + c["maintenance"] + c["decoration_cost"]
    assert abs(c["cash_needed"] - expected) < 1
    assert c["cash_needed"] > c["down_payment"], "只算首付会低估真实门槛"


def test_buy_cost_bare_new_home_adds_full_decoration():
    bare = buy_cost(1_000_000, 100, is_new=True, decoration="毛坯")
    fitted = buy_cost(1_000_000, 100, is_new=True, decoration="精装")
    assert bare["decoration_cost"] > fitted["decoration_cost"]
    # 新房不收中介费
    assert bare["agent_fee"] == 0


def test_rent_view():
    r = rent_view(2000, 1_200_000)
    assert r["annual_rent"] == 24_000
    assert r["yield_pct"] == 2.0
    assert r["payback_years"] == 50.0
    assert rent_view(None, 1_000_000)["yield_pct"] is None


# --------------------------------------------------------------------------
# 议价锚点
# --------------------------------------------------------------------------
def test_bargain_never_suggests_above_listing_price():
    """回归：曾经出现「建议出价高于挂牌价」的严重逻辑错误。"""
    cases = [
        dict(total_price=1_200_000, area=138, unit_price=8696, comp_median_unit=10740,
             list_days=214, price_changes=3, consecutive_drops=3, first_price=1_400_000,
             liquidity_score=50, defect_count=1),
        dict(total_price=1_350_000, area=118, unit_price=11441, comp_median_unit=11_500,
             list_days=45, price_changes=0, first_price=1_350_000, liquidity_score=88),
        dict(total_price=900_000, area=100, unit_price=9000, comp_median_unit=None,
             list_days=300, price_changes=4, consecutive_drops=2, liquidity_score=40),
    ]
    for c in cases:
        a = bargain_anchor(**c)
        assert a["suggest_offer"] <= c["total_price"]
        assert a["fair_low"] <= a["fair_high"]
        assert 0 <= a["room_pct"] <= 12
        assert a["reasons"], "必须给出可读的谈判理由"


def test_bargain_room_grows_with_days_and_drops():
    base = dict(total_price=1_000_000, area=110, unit_price=9091,
                comp_median_unit=9_500, liquidity_score=70)
    fresh = bargain_anchor(**base, list_days=5, price_changes=0)
    stale = bargain_anchor(**base, list_days=400, price_changes=5, consecutive_drops=3)
    assert stale["room_pct"] > fresh["room_pct"]


def test_bargain_reduces_room_when_already_cheap():
    cheap = bargain_anchor(total_price=800_000, area=110, unit_price=7272,
                           comp_median_unit=11_000, list_days=200, price_changes=3,
                           liquidity_score=50)
    # 已明显低于市场锚，可谈空间要被压缩，否则建议出价会脱离现实
    assert cheap["room_pct"] < 5


# --------------------------------------------------------------------------
# 接口：小区、新房、面积专题
# --------------------------------------------------------------------------
def test_community_list_has_geo_and_turnover(client):
    items = client.get("/api/communities").json()["items"]
    assert items, "应有小区数据"
    one = items[0]
    for key in ("lat", "lng", "listing_n", "median_unit", "months_to_clear", "target_area_n"):
        assert key in one
    # 去化月数必须为正或 None，不能是 0（0 会让前端显示「0 个月卖完」）
    for i in items:
        assert i["months_to_clear"] is None or i["months_to_clear"] > 0


def test_community_detail_groups_by_layout(client):
    cid = client.get("/api/communities").json()["items"][0]["id"]
    d = client.get(f"/api/communities/{cid}").json()
    assert d["stats"]["listing_n"] == len(d["listings"])
    assert d["stats"]["liquidity_note"]
    assert d["layout_groups"], "同户型竞争格局不能为空"
    assert sum(g["n"] for g in d["layout_groups"]) == len(d["listings"])
    assert "new_homes" in d and "nearby" in d


def test_community_404(client):
    assert client.get("/api/communities/999999").status_code == 404


def test_new_home_gap_adjusts_for_decoration(client):
    data = client.get("/api/new-homes").json()
    assert data["items"], "应有新房数据"
    for item in data["items"]:
        if item["adjusted_gap"] is None:
            continue
        # 毛坯房的调整后倒挂必须低于名义倒挂（装修要花钱，便宜程度被吃掉）
        if item["is_bare"]:
            assert item["adjusted_gap"] < item["nominal_gap"]
        assert item["verdict"] in {"opportunity", "caution", "slight", "neutral", "premium", "unknown"}
        assert item["verdict_text"]


def test_new_home_detail_has_scenarios(client):
    nid = client.get("/api/new-homes").json()["items"][0]["id"]
    d = client.get(f"/api/new-homes/{nid}").json()
    assert d["scenarios"]
    assert d["checklist"]


def test_target_area_supports_120_150(client):
    d = client.get("/api/target?min_area=120&max_area=150").json()
    assert d["summary"]["listing_n"] > 0, "120-150㎡ 必须有样本，否则专题页是空的"
    for x in d["listings"]:
        assert 120 <= float(x["area"]) <= 150
    for x in d["auctions"]:
        assert 120 <= float(x["area"]) <= 150
    assert d["summary"]["note"]
    assert d["summary"]["size_premium"] is not None


def test_listing_detail_includes_afford(client):
    lid = client.get("/api/listings?limit=1").json()["items"][0]["id"]
    d = client.get(f"/api/listings/{lid}").json()
    aff = d["afford"]
    assert aff["cost"]["monthly"] >= 0
    assert aff["cost"]["cash_needed"] > 0
    assert aff["bargain"]["suggest_offer"] <= d["item"]["total_price"]
    assert 0 <= aff["rent"]["yield_pct"] < 20


# --------------------------------------------------------------------------
# 数据备份：用户资产能导出、能恢复，房源数据不进备份
# --------------------------------------------------------------------------
def test_backup_export_contains_assets_only(client):
    d = client.get("/api/admin/backup/export").json()
    assert d["kind"] == "rizhao-house-radar-assets"
    assert d["sensitive"] is True
    # 用户资产在，可重建的房源数据不在（否则备份会被撑爆）
    assert set(d["tables"]) == {"watch_item", "user_pref", "alert_config", "app_setting"}
    assert "listing" not in d["tables"]
    assert d["counts"]["user_pref"] == 1


def test_backup_export_requires_token(anon_client):
    # 备份里有口令和企微密钥，即使是 GET 也不能裸奔
    assert anon_client.get("/api/admin/backup/export").status_code == 401


def test_backup_roundtrip_restores_assets(client):
    # 先加一条观察记录
    lid = client.get("/api/listings?limit=1").json()["items"][0]["id"]
    client.post("/api/watchlist", json={"target_type": "listing", "target_id": lid, "note": "备份测试"})
    before = len(client.get("/api/watchlist").json()["items"])
    assert before >= 1

    dump = client.get("/api/admin/backup/export").json()
    assert dump["counts"]["watch_item"] == before

    # 清空后从备份恢复
    client.post("/api/admin/backup/import", params={"replace": True}, json=dump)
    after = client.get("/api/watchlist").json()["items"]
    assert len(after) == before
    assert any(x.get("note") == "备份测试" for x in after)


def test_backup_import_rejects_bad_file(client):
    r = client.post("/api/admin/backup/import", json={"hello": "world"})
    assert r.status_code == 400
    assert "格式" in r.json()["detail"]


def test_backup_status_and_snapshot(client):
    s = client.get("/api/admin/backup").json()
    assert "snapshot_dir" in s
    assert s["asset_counts"]["user_pref"] == 1

    r = client.post("/api/admin/backup/snapshot")
    assert r.status_code == 200
    assert r.json()["file"].endswith(".db")


def test_backup_remaps_when_target_id_broken(client):
    """重灌演示数据后 listing 的 id 会变，观察记录靠 fingerprint 重新定位。

    这是真踩过的坑：第一版按 id 恢复，重灌后 4 条观察全指向不存在的 id，
    被当成悬空引用清掉了——日志还写着「已还原 4 条」，看起来很成功。
    """
    lid = client.get("/api/listings?limit=1").json()["items"][0]["id"]
    client.post("/api/watchlist", json={"target_type": "listing", "target_id": lid, "note": "重映射测试"})

    dump = client.get("/api/admin/backup/export").json()
    row = next((x for x in dump["tables"]["watch_item"] if x.get("note") == "重映射测试"), None)
    assert row is not None
    assert row["_ref"], "备份里必须带稳定标识，否则换库就找不回来了"

    # 模拟「id 失效」：把 target_id 改成一个不存在的值，只留 _ref
    row["target_id"] = 99999999
    r = client.post("/api/admin/backup/import", params={"replace": True}, json=dump).json()
    assert r["lost"] == 0
    assert r["remapped"] >= 1

    items = client.get("/api/watchlist").json()["items"]
    hit = [x for x in items if x.get("note") == "重映射测试"]
    assert hit, "按 fingerprint 应该能重新找回同一套房"
    assert hit[0]["target_id"] == lid, "应映射回原来的房源，而不是别的一套"


# --------------------------------------------------------------------------
# 地图 Key：在线配置 → 立刻能从 /api/config 下发（不用重启）
# --------------------------------------------------------------------------
def test_amap_key_online_config_reaches_frontend(client):
    cfg = client.get("/api/config").json()
    assert "amap_key" in cfg and "amap_security_code" in cfg
    assert cfg["amap_key"] == ""            # 演示环境默认不配

    r = client.put("/api/admin/integration", json={
        "amap_key": "abcdef0123456789abcdef0123456789",
        "amap_security_code": "s3cr3tjscode",
    })
    assert r.status_code == 200
    assert r.json()["map_ready"] is True

    cfg = client.get("/api/config").json()
    assert cfg["amap_key"] == "abcdef0123456789abcdef0123456789"
    assert cfg["amap_security_code"] == "s3cr3tjscode"


def test_amap_security_code_masked_in_admin_snapshot(client):
    client.put("/api/admin/integration", json={"amap_security_code": "s3cr3tjscode"})
    items = client.get("/api/admin/integration").json()["items"]
    jscode = next(x for x in items if x["key"] == "amap_security_code")
    assert jscode["configured"] is True
    assert "s3cr3tjscode" not in jscode["display"]      # 回显只留尾巴
    assert jscode["group"] == "map"


def test_admin_status_does_not_leak_amap_key(client):
    client.put("/api/admin/integration", json={"amap_key": "abcdef0123456789abcdef0123456789"})
    body = client.get("/api/admin/status").text
    assert "abcdef0123456789abcdef0123456789" not in body


def test_bootstrap_writes_seed_version_after_restoring_assets(client, monkeypatch):
    """重灌后版本号必须落到新值，否则每次重启都重灌一遍。

    真踩过：版本号写在「还回用户资产」之前，而资产备份里也含 app_setting，
    里面是重灌前的旧 seed_version，一还原就把它盖回去，重启又判定版本落后。
    """
    from app import main as app_main
    from app.services import auth
    from app.services import seed as seed_mod

    lid = client.get("/api/listings?limit=1").json()["items"][0]["id"]
    client.post("/api/watchlist", json={"target_type": "listing", "target_id": lid, "note": "重灌版本测试"})

    auth.set_setting("seed_version", "v-old-sample")   # 假装是上一版样本留下的
    calls = {"n": 0}

    def fake_seed_all(clear=False):
        calls["n"] += 1
        return {"communities": 0, "listings": 0, "auctions": 0, "new_homes": 0}

    monkeypatch.setattr(seed_mod, "seed_all", fake_seed_all)
    app_main.bootstrap()

    assert calls["n"] == 1, "版本落后应触发一次重灌"
    assert auth.get_setting("seed_version") == seed_mod.SEED_VERSION

    items = client.get("/api/watchlist").json()["items"]
    assert any(x.get("note") == "重灌版本测试" for x in items), "重灌不该洗掉观察清单"


# --------------------------------------------------------------------------
# 企微推送：HTTP 200 不等于发送成功
# --------------------------------------------------------------------------
def test_wecom_errcode_nonzero_is_failure(monkeypatch):
    """key 写错时企微照样返回 HTTP 200，只有 errcode 才是真结果。

    踩过的坑：只看 HTTP 状态，测试按钮显示「发送成功」，手机里什么都没有。
    """
    from app.services import alert_sender

    monkeypatch.setattr(alert_sender, "_post_json",
                        lambda *a, **k: (True, '{"errcode":93000,"errmsg":"invalid webhook url"}'))
    ok, resp = alert_sender._post_wecom("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", {})
    assert ok is False
    assert "93000" in resp
    assert "key 复制不全" in alert_sender._explain_error(resp)


def test_wecom_errcode_zero_is_success(monkeypatch):
    from app.services import alert_sender

    monkeypatch.setattr(alert_sender, "_post_json",
                        lambda *a, **k: (True, '{"errcode":0,"errmsg":"ok"}'))
    ok, _ = alert_sender._post_wecom("https://x", {})
    assert ok is True


def test_wecom_network_error_is_failure(monkeypatch):
    from app.services import alert_sender

    monkeypatch.setattr(alert_sender, "_post_json",
                        lambda *a, **k: (False, "ConnectionError: boom"))
    ok, _ = alert_sender._post_wecom("https://x", {})
    assert ok is False
