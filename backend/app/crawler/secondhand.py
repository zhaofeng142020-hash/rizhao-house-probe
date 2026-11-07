"""二手房挂牌解析器：房天下 / 安居客 / 日照置业网。

⚠️ 说明：
  - 只解析公开列表页的公开字段；不登录、不携带 Cookie、不请求详情页里的联系方式。
  - 站点 DOM 会改版，选择器均做成「多候选」，任一命中即可；全不命中时返回空列表，
    由 runner 记为 partial，不影响其他站点（异常隔离）。
  - 首次接入真实站点前，请先人工确认 robots.txt 与站点条款，并用 CRAWL_ENABLED=1 小流量验证。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .base import (
    PoliteFetcher, detect_defects, fingerprint, parse_area, parse_layout, parse_price, soup_of, text_of,
)

logger = logging.getLogger("rzprobe.crawler.secondhand")

# 板块关键词 → 板块（用于没有结构化板块字段时的兜底推断）
DISTRICT_KEYWORDS: dict[str, list[str]] = {
    "新市区": ["新市区", "万平口", "北京路", "昭阳路", "临沂路", "海曲东路"],
    "东港区": ["东港", "海曲路", "兴海路", "正阳路", "望海路"],
    "石臼": ["石臼", "海滨", "秦楼"],
    "老城": ["老城", "东关", "日照商城", "迎宾路", "日照路"],
    "山海天": ["山海天", "两城", "教授花园", "碧海路", "卧龙山"],
    "高新区": ["高新区", "香河", "河山", "兖矿"],
    "开发区": ["开发区", "奎山", "疏港路", "临港"],
    "莒县": ["莒县", "沭河", "浮来"],
    "五莲": ["五莲", "洪凝"],
    "岚山": ["岚山", "港口", "安东卫"],
}


def guess_district(*texts: str) -> str:
    blob = " ".join(t for t in texts if t)
    for district, kws in DISTRICT_KEYWORDS.items():
        if any(kw in blob for kw in kws):
            return district
    return "东港区"  # 日照市区默认归属


@dataclass
class RawListing:
    """解析后的中间结构（还没进库）。"""
    source: str
    title: str = ""
    community_name: str = ""
    district: str = ""
    layout: str = ""
    rooms: int = 0
    halls: int = 0
    area: float = 0.0
    total_price: float = 0.0
    unit_price: float = 0.0
    floor_text: str = ""
    orientation: str = ""
    decoration: str = ""
    build_year: int | None = None
    defect_tags: list[str] = field(default_factory=list)
    source_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def finalize(self) -> "RawListing":
        if self.area > 0 and self.total_price > 0:
            self.unit_price = round(self.total_price / self.area)
        elif self.unit_price > 0 and self.area > 0:
            self.total_price = round(self.unit_price * self.area)
        if not self.layout and self.rooms:
            self.layout = f"{self.rooms}室"
        if not self.district:
            self.district = guess_district(self.title, self.community_name)
        if not self.defect_tags:
            self.defect_tags = detect_defects(self.title, self.decoration)
        return self

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.source, self.community_name, self.layout, self.area, self.total_price)

    def to_db(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "source": self.source,
            "source_url": self.source_url,
            "community_name": self.community_name,
            "district": self.district,
            "title": self.title,
            "layout": self.layout,
            "rooms": self.rooms,
            "halls": self.halls,
            "area": self.area,
            "total_price": self.total_price,
            "unit_price": self.unit_price,
            "floor_text": self.floor_text,
            "orientation": self.orientation,
            "decoration": self.decoration,
            "build_year": self.build_year,
            "defect_tags": self.defect_tags,
            "first_price": self.total_price,
            "list_days": 0,
            "raw_json": self.raw,
        }


class BaseSecondhandSource:
    key = "base"
    label = "基础房源源"
    base_url = ""
    page_url_tpl = ""

    def __init__(self) -> None:
        self.fetcher = PoliteFetcher(source=self.key)

    def build_urls(self, pages: int) -> list[str]:
        return [self.page_url_tpl.format(n=n) for n in range(1, pages + 1) if self.page_url_tpl]

    def parse_list(self, html: str) -> list[RawListing]:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def fetch(self, pages: int = 1) -> list[RawListing]:
        items: list[RawListing] = []
        for url in self.build_urls(pages):
            html = self.fetcher.get(url, referer=self.base_url)
            if not html:
                continue
            try:
                items.extend(self.parse_list(html))
            except Exception as exc:  # noqa: BLE001  单页解析失败不影响其他页
                logger.warning("[%s] 解析失败 %s: %s", self.key, url, exc)
        return items


class FangtianxiaSource(BaseSecondhandSource):
    """房天下日照二手房列表页。"""

    key = "fangtianxia"
    label = "房天下·日照二手房"
    base_url = "https://rizhao.esf.fang.com/"
    page_url_tpl = "https://rizhao.esf.fang.com/house/i3{n}/"

    def parse_list(self, html: str) -> list[RawListing]:
        soup = soup_of(html)
        if soup is None:
            return []
        # 房天下列表项常见容器：.shop_list dl / .list rel / div.houseList
        nodes = soup.select(".shop_list dl") or soup.select("dl.clearfix") or soup.select(".houseList .item")
        out: list[RawListing] = []
        for node in nodes:
            title = text_of(node, ["dd.p_title a", "h4 a", ".tit a"])
            community = text_of(node, [".plotName a", "p.mt10 a", ".community a", "dd p:nth-of-type(2) a"])
            layout_text = text_of(node, ["p:nth-of-type(2) span:nth-of-type(1)", ".house_type", ".tl"])
            area_text = text_of(node, ["p:nth-of-type(2) span:nth-of-type(2)", ".area", ".fl"])
            price_text = text_of(node, [".price b", ".totalPrice", ".price"])
            href = ""
            link = node.select_one("dd.p_title a") or node.select_one("h4 a") or node.select_one("a")
            if link and link.get("href"):
                href = link["href"]
                if href.startswith("/"):
                    href = "https://rizhao.esf.fang.com" + href
            if not (title or price_text):
                continue
            layout, rooms, halls, _ = parse_layout(layout_text)
            item = RawListing(
                source=self.key, title=title, community_name=community,
                layout=layout, rooms=rooms, halls=halls,
                area=parse_area(area_text), total_price=parse_price(price_text),
                floor_text=text_of(node, ["p:nth-of-type(3) span:nth-of-type(1)", ".floor"]),
                orientation=text_of(node, [".direction", "p:nth-of-type(2) span:nth-of-type(3)"]),
                decoration=text_of(node, [".renovation", ".decoration"]),
                source_url=href,
                raw={"html_snippet": str(node)[:600]},
            ).finalize()
            if item.total_price > 0 and item.area > 0:
                out.append(item)
        return out


class AnjukeSource(BaseSecondhandSource):
    """安居客日照二手房列表页（反爬较强，仅做公开列表解析，失败即跳过）。"""

    key = "anjuke"
    label = "安居客·日照二手房"
    base_url = "https://rizhao.anjuke.com/"
    page_url_tpl = "https://rizhao.anjuke.com/sale/p{n}/"

    def parse_list(self, html: str) -> list[RawListing]:
        soup = soup_of(html)
        if soup is None:
            return []
        nodes = soup.select(".property") or soup.select(".list-content .zu-itemmod") or soup.select("[data-id]")
        out: list[RawListing] = []
        for node in nodes:
            title = text_of(node, [".property-content-title-name", "h3 a", ".title a", "h3"])
            community = text_of(node, [".property-content-info-comm-name", ".comm-address", ".details-item a"])
            info_text = text_of(node, [".property-content-info-text", ".property-content-info", ".details-item"])
            layout, rooms, halls, _ = parse_layout(info_text)
            area_text = text_of(node, [".property-content-info-text span:nth-of-type(2)"]) or info_text
            price_text = text_of(node, [".property-price-total", ".price", ".pro-price"])
            link = node.select_one("a")
            href = link.get("href", "") if link else ""
            if not (title or price_text):
                continue
            item = RawListing(
                source=self.key, title=title, community_name=community,
                layout=layout, rooms=rooms, halls=halls,
                area=parse_area(area_text), total_price=parse_price(price_text),
                floor_text=info_text, source_url=href,
                raw={"html_snippet": str(node)[:600]},
            ).finalize()
            if item.total_price > 0 and item.area > 0:
                out.append(item)
        return out


class ZhiyeSource(BaseSecondhandSource):
    """日照置业网（本地站，结构较简单；域名请以站点实际为准）。"""

    key = "zhiye"
    label = "日照置业网"
    base_url = "https://www.rzfc.com.cn/"
    page_url_tpl = "https://www.rzfc.com.cn/secondhand/list-{n}.html"

    def parse_list(self, html: str) -> list[RawListing]:
        soup = soup_of(html)
        if soup is None:
            return []
        nodes = soup.select(".list-item") or soup.select("li.house-item") or soup.select("table.list tr")[1:]
        out: list[RawListing] = []
        for node in nodes:
            title = text_of(node, ["h3 a", ".title a", "a.tit"])
            community = text_of(node, [".community", ".t-name"])
            info = node.get_text(" ", strip=True)
            layout, rooms, halls, _ = parse_layout(info)
            price_text = text_of(node, [".price", ".total", "span.red"])
            link = node.select_one("a")
            href = link.get("href", "") if link else ""
            if href.startswith("/"):
                href = "https://www.rzfc.com.cn" + href
            if not title and not price_text:
                continue
            item = RawListing(
                source=self.key, title=title, community_name=community or title[:12],
                layout=layout, rooms=rooms, halls=halls,
                area=parse_area(info), total_price=parse_price(price_text),
                source_url=href, raw={"html_snippet": str(node)[:600]},
            ).finalize()
            if item.total_price > 0 and item.area > 0:
                out.append(item)
        return out


SECONDHAND_SOURCES: dict[str, BaseSecondhandSource] = {
    s.key: s for s in [FangtianxiaSource(), AnjukeSource(), ZhiyeSource()]
}
