"""API 与推送模块的集成测试（使用临时库）。"""
from __future__ import annotations

from datetime import datetime

from app.services import alert_sender, templates


def test_health_and_config(client):
    assert client.get("/api/meta/health").json()["ok"] is True
    cfg = client.get("/api/config").json()
    assert "disclaimer" in cfg


def test_unknown_api_path_returns_json_404(client):
    """写错的接口路径不能落到 SPA 兜底，必须明确 404 且是 JSON。"""
    resp = client.get("/api/definitely-not-exists")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "detail" in resp.json()


def test_dashboard_shape(client):
    data = client.get("/api/meta/dashboard").json()
    assert data["counts"]["listings"] > 0
    assert data["districts"]
    assert isinstance(data["top"], list)


def test_listings_filters(client):
    data = client.get("/api/listings", params={"limit": 5, "grade": "red"}).json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["grade"] == "red"
        assert item["score"] >= 75


def test_listing_detail_payload(client):
    listing_id = client.get("/api/listings", params={"limit": 1}).json()["items"][0]["id"]
    data = client.get(f"/api/listings/{listing_id}").json()
    assert data["item"]["id"] == listing_id
    assert data["score"]["radar"]
    assert data["comparables"]["n"] >= 1
    assert data["history"]
    assert data["risk_checklist"]
    assert len(data["verify_steps"]) == 3
    assert data["score"]["median_unit"] > 0


def test_anomalies_and_ranking(client):
    events = client.get("/api/anomalies", params={"days": 30}).json()
    assert events["total"] >= 1
    assert events["items"][0]["event_label"]
    ranking = client.get("/api/anomalies/ranking", params={"limit": 5}).json()
    assert ranking["items"]
    assert ranking["items"][0]["score"] >= ranking["items"][-1]["score"]


def test_auction_detail(client):
    auction_id = client.get("/api/auctions").json()["items"][0]["id"]
    data = client.get(f"/api/auctions/{auction_id}").json()
    assert data["item"]["landing_price"] > data["item"]["start_price"]
    assert len(data["waterfall"]) == 5
    assert any(c["key"] == "occupy" for c in data["risk_checklist"])


def test_watchlist_crud(client):
    listing_id = client.get("/api/listings", params={"limit": 1}).json()["items"][0]["id"]
    assert client.post("/api/watchlist", json={"target_type": "listing", "target_id": listing_id}).json()["ok"]
    items = client.get("/api/watchlist").json()["items"]
    item = next(i for i in items if i["target_id"] == listing_id)
    assert client.patch(f"/api/watchlist/{item['id']}", json={"follow_status": "viewed", "note": "已看"}).json()["ok"]
    assert client.delete(f"/api/watchlist/{item['id']}").json()["ok"]


def test_alert_config_roundtrip(client):
    res = client.put("/api/alerts/config", json={"min_score": 72, "quiet_hours": "22:00-06:00"}).json()
    assert res["config"]["min_score"] == 72
    assert res["config"]["quiet_hours"] == "22:00-06:00"
    # 还原，避免影响其他用例
    client.put("/api/alerts/config", json={"min_score": 60, "quiet_hours": "23:00-07:00"})


def test_alert_test_falls_back_to_log_channel(client):
    """未配置任何微信通道时必须降级为站内提醒，且不报错。"""
    res = client.post("/api/alerts/test").json()
    assert res["ok"] is True
    assert res["channel"] in ("log", "wecom_webhook", "wecom_app", "mp_template")


def test_manual_push_writes_alert_log(client):
    listing_id = client.get("/api/listings", params={"limit": 1}).json()["items"][0]["id"]
    res = client.post(f"/api/alerts/push/listing/{listing_id}").json()
    assert res["status"] in ("sent", "logged")
    logs = client.get("/api/alerts/logs").json()
    assert logs["items"]


def test_run_pipeline_endpoint(client):
    res = client.post("/api/alerts/run-pipeline", params={"push": False}).json()
    assert res["scored"] > 0
    assert res["errors"] == []


# =============================================================================
# 推送模块单测
# =============================================================================
def test_dedup_blocks_second_push(client):
    item = {"id": 999, "community_name": "测试小区", "layout": "3室2厅1卫", "area": 100,
            "total_price": 900_000, "unit_price": 9000, "district": "新市区",
            "price_changes": 2, "consecutive_drops": 2, "list_days": 200, "defect_tags": []}
    score = {"score": 82, "grade": "red", "confidence": "high", "deviation_pct": 0.18,
             "median_unit": 11_000, "why_cheap": "低于中枢", "why_trap": "假房源风险",
             "risk_level": "low"}
    sender = alert_sender.AlertSender({**alert_sender.load_config(), "dedup_hours": 24})
    first = sender.push_target("listing", item, score)
    second = sender.push_target("listing", item, score)
    assert first["status"] in ("sent", "logged")
    assert second["status"] == "skipped"
    assert second["reason"] == "dedup"


def test_min_score_blocks_low_score(client):
    sender = alert_sender.AlertSender({**alert_sender.load_config(), "min_score": 90})
    ok, reason = sender.should_push("yellow", 65)
    assert ok is False and "最低推送分数" in reason


def test_quiet_hours_logic():
    sender = alert_sender.AlertSender({**alert_sender.load_config(), "quiet_hours": "23:00-07:00"})
    assert sender.in_quiet_hours(datetime(2026, 11, 5, 2, 0)) is True
    assert sender.in_quiet_hours(datetime(2026, 11, 5, 12, 0)) is False


def test_templates_contain_required_sections():
    item = {"community_name": "安泰·未来城", "layout": "3室2厅1卫", "area": 106,
            "total_price": 946_000, "unit_price": 8925, "district": "新市区",
            "price_changes": 3, "consecutive_drops": 3, "list_days": 214,
            "defect_tags": ["装修旧"], "median_unit": 10_740}
    score = {"score": 78, "grade": "red", "confidence": "high", "deviation_pct": 0.169,
             "median_unit": 10_740, "why_cheap": "x", "why_good": "y", "why_trap": "z",
             "risk_level": "low"}
    msg = templates.build_listing_messages(item, score)
    text = msg["text"]
    assert "日照房价探针" in text
    assert "安泰·未来城" in text and "106" in text and "94.6万" in text
    assert "低 16.9%" in text or "16.9%" in text
    assert "调价3次" in text and "挂牌214天" in text
    assert "1. 实地看房查渗漏/采光/噪音" in text
    assert "2. 问中介近3月同户型真实成交价" in text
    assert "3. 查学区占用" in text
    assert "挂牌价≠成交价" in text
    assert "🔴" in msg["title"]


def test_template_digest():
    items = [{"item": {"community_name": "A", "district": "新市区", "layout": "3室2厅1卫",
                       "area": 100, "total_price": 900_000},
              "score": {"score": 65, "deviation_pct": 0.08}}]
    msg = templates.build_digest(items)
    assert "每日汇总" in msg["title"]
    assert "每日观察汇总" in msg["text"]
