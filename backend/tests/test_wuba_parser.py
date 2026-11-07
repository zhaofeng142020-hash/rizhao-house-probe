"""58 同城列表页解析器的回归测试。

重点是「数据真实性」而不是「能解析出东西」：
  - 按 class 精确取小区名，不能把地址里的「莒县城」当成小区；
  - 总价 / 面积 与页面展示的单价对不上时，宁可丢弃也不能写入错价；
  - 干净原始链接 + 平台房源编号，保证每日刷新能按同一套房记调价；
  - 58 必须进入每日自动刷新的源列表（否则全自动就是空话）。
"""
from __future__ import annotations

from app.services import live_refresh


def card(*, community="生活印象", total="98", area="166", unit="5904",
         ref="89265790143581", title="生活印象166四室两厅毛坯带车位",
         with_area=True, with_total=True, with_community=True) -> str:
    comm = (f'<div class="property-content-info property-content-info-comm">'
            f'<p class="property-content-info-comm-name">{community}</p>'
            f'<p class="property-content-info-comm-address"><span>东港</span>'
            f'<span>新市区</span><span>东营路188号</span></p></div>') if with_community else ''
    price = (f'<div class="property-price"><p class="property-price-total">'
             f'<span class="property-price-total-num">{total}</span>'
             f'<span class="property-price-total-text">万</span></p>'
             f'<p class="property-price-average"> {unit}元/㎡ </p></div>') if with_total else ''
    area_p = f'<p class="property-content-info-text"> {area}㎡ </p>' if with_area else ''
    return f'''<div class="property" data-v-5369c928="">
      <a class="property-ex" href="https://rizhao.58.com/ershoufang/{ref}x.shtml?auction=200&amp;from=list"></a>
      <div class="property-content">
        <h3 class="property-content-title-name" title="{title}">{title}</h3>
        <div class="property-content-info">
          <p class="property-content-info-text property-content-info-attribute">
            <span>4</span> <span>室</span> <span>2</span> <span>厅</span> <span>1</span> <span>卫</span></p>
          {area_p}
          <p class="property-content-info-text">南北</p>
          <p class="property-content-info-text"> 高层(共32层) </p>
          <p class="property-content-info-text"> 2016年建造 </p>
        </div>
        {comm}
      </div>
      {price}
    </div>'''


def test_parse_wuba_extracts_precise_fields():
    rows = live_refresh.parse_wuba(card())
    assert len(rows) == 1
    r = rows[0]
    assert r["community_name"] == "生活印象"
    assert r["district"] == "新市区"          # 板块口径，不是「东港」也不是地址
    assert r["total_wan"] == 98.0 and r["area"] == 166.0
    assert r["layout"] == "4室2厅"
    assert r["orientation"] == "南北"
    assert r["floor_text"] == "高层(共32层)"
    assert r["build_year"] == 2016
    assert r["source_url"] == "https://rizhao.58.com/ershoufang/89265790143581x.shtml"
    assert r["source_ref"] == "89265790143581"
    assert r["title"] == "生活印象166四室两厅毛坯带车位"


def test_parse_wuba_rejects_price_unit_mismatch():
    # 页面写 9999 元/㎡，但 98万/166㎡ 只有 5904 元/㎡ —— 说明抓串了字段
    assert live_refresh.parse_wuba(card(unit="9999")) == []


def test_parse_wuba_skips_incomplete_cards():
    html = card() + card(with_community=False, ref="89265790143582") \
        + card(with_total=False, ref="89265790143583") \
        + card(with_area=False, ref="89265790143584")
    rows = live_refresh.parse_wuba(html)
    assert len(rows) == 1 and rows[0]["source_ref"] == "89265790143581"


def test_parse_wuba_does_not_leak_price_from_next_card():
    # 缺价格的卡片不能「借」下一张卡片的价格
    html = card(with_total=False, ref="89265790143583") + card(ref="89265790143585")
    rows = live_refresh.parse_wuba(html)
    assert len(rows) == 1 and rows[0]["source_ref"] == "89265790143585"


def test_wuba_is_part_of_daily_refresh():
    assert "wuba" in live_refresh.SOURCES
    assert live_refresh.SOURCES["wuba"]["list"] == "https://rizhao.58.com/ershoufang/"
    assert live_refresh.DEFAULT_PAGES["wuba"] == 1   # 只抓首屏，不去撞风控
    assert live_refresh._parser_for("wuba") is live_refresh.parse_wuba
    assert live_refresh.WUBA_INTERVAL >= 4           # 礼貌间隔


def test_refresh_all_includes_wuba(monkeypatch):
    seen = []

    def fake(key, pages=None):
        seen.append(key)
        return {"source": key, "fetched": 1, "saved": 1}

    monkeypatch.setattr(live_refresh, "refresh_source", fake)
    # refresh_all 里保存刷新结果失败会被自身 try/except 吞掉，不影响源列表校验
    report = live_refresh.refresh_all()
    assert seen == ["ke", "anjuke", "wuba"]
    assert set(report["sources"]) == {"ke", "anjuke", "wuba"}


def test_wuba_verify_page_is_treated_as_blocked(monkeypatch):
    """58 有时不跳转、直接返回验证壳页，必须如实记为被拦而不是「抓到 0 条」。"""
    monkeypatch.setattr(live_refresh, "_get", lambda *a, **kw: "<html>访问过于频繁，请输入验证码</html>")
    try:
        live_refresh.fetch_source(live_refresh.SOURCES["wuba"], 1)
        raise AssertionError("应当抛出 CaptchaBlocked")
    except live_refresh.CaptchaBlocked:
        pass
