"""「本机关机后云端照跑」相关改动的回归测试。

覆盖四件容易悄悄坏掉的事：
  1. 页面反复加载不能连着触发整轮抓取（冷却 + crawl=0 只重算不抓取）；
  2. 云端容器被回收后，有人打开网站能自动补跑一轮；
  3. 来源总览 / 运行状态如实告诉用户「跑在云端、不需要本机开机」；
  4. 修掉 CRAWL_ENABLED=0 那句会让人误以为「没在抓数据」的旧文案。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.routers import admin as admin_router
from app.services import freshness


def _reset_state():
    admin_router._REFRESH_STATE.update({
        "running": False, "started_at": None, "finished_at": None,
        "result": None, "error": None,
    })


def test_refresh_cooldown_blocks_repeat_trigger(client, monkeypatch):
    """扩展/页面重复加载时，10 分钟内不再抓一轮，避免撞 58 的「访问过于频繁」。"""
    called = []
    monkeypatch.setattr(admin_router, "_do_refresh", lambda pages, crawl=True: called.append(crawl))
    _reset_state()
    admin_router._REFRESH_STATE["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        r = client.post("/api/admin/refresh-sources").json()
        assert r.get("skipped") is True
        assert r.get("cooldown_seconds", 0) > 0
        assert "定时任务不受影响" in r["note"]
        assert not called
    finally:
        _reset_state()


def test_force_bypasses_cooldown(client, monkeypatch):
    """管理台「立即刷新」按钮带 force=1，用户主动点击不受防抖影响。"""
    called = []
    monkeypatch.setattr(admin_router, "_do_refresh", lambda pages, crawl=True: called.append(crawl))
    monkeypatch.setattr(admin_router, "_cooldown_left", lambda: 999)
    _reset_state()
    try:
        r = client.post("/api/admin/refresh-sources?force=1&wait=1").json()
        assert r["ok"] is True
        assert called == [True]
    finally:
        _reset_state()


def test_crawl_false_only_recomputes(client, monkeypatch):
    """网页端刚回传房源时只重算+推送，不再抓三个站点。"""
    called = []
    monkeypatch.setattr(admin_router, "_do_refresh",
                        lambda pages, crawl=True: called.append((pages, crawl)))
    monkeypatch.setattr(admin_router, "_cooldown_left", lambda: 0)
    _reset_state()
    try:
        r = client.post("/api/admin/refresh-sources?crawl=0&wait=1").json()
        assert r["ok"] is True
        assert called == [(None, False)]
    finally:
        _reset_state()


def test_overview_reports_cloud_schedule(client):
    d = client.get("/api/sources").json()
    sch = d["schedule"]
    assert sch["runs_in_cloud"] is True
    assert sch["needs_local_pc"] is False
    assert sch["times"] == ["07:30", "19:30"]
    assert "push" in d and "external" in d["push"]
    assert "关机" in sch["note"]


def test_status_distinguishes_two_switches(client):
    """CRAWL_ENABLED=0 的旧文案会说「不会去真实站点抓新数据」，必须改掉。"""
    d = client.get("/api/admin/status").json()
    assert d["live_refresh"]["runs_in_cloud"] is True
    assert d["live_refresh"]["needs_local_pc"] is False
    assert "独立通道" in d["crawl"]["note"]
    assert "不会去真实站点抓新数据" not in d["crawl"]["note"]


def test_freshness_detects_missed_run(monkeypatch):
    now = datetime.now()
    monkeypatch.setattr(freshness, "last_refresh_at", lambda: now - timedelta(hours=25))
    assert freshness.is_stale(now) is True
    monkeypatch.setattr(freshness, "last_refresh_at", lambda: now - timedelta(hours=2))
    assert freshness.is_stale(now) is False
    # 全新部署没有历史刷新记录，不算漏跑，交给首次定时任务
    monkeypatch.setattr(freshness, "last_refresh_at", lambda: None)
    assert freshness.is_stale(now) is False


def test_catchup_starts_background_job(monkeypatch):
    """漏跑时起后台线程补一轮；不阻塞用户打开网站。"""
    started = []

    class FakeThread:
        def __init__(self, target, daemon=False):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(freshness, "is_stale", lambda now=None: True)
    monkeypatch.setattr(freshness.threading, "Thread", FakeThread)
    monkeypatch.setattr(freshness, "_last_check", None)
    monkeypatch.setattr(freshness, "_last_attempt", None)
    out = freshness.maybe_catchup()
    assert out["triggered"] is True
    assert len(started) == 1


def test_catchup_does_nothing_when_fresh(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target, daemon=False):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(freshness, "is_stale", lambda now=None: False)
    monkeypatch.setattr(freshness.threading, "Thread", FakeThread)
    monkeypatch.setattr(freshness, "_last_check", None)
    assert freshness.maybe_catchup()["triggered"] is False
    assert not started
