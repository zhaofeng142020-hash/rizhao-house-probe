"""司法拍卖公告解析器：阿里拍卖 / 京东拍卖。

只解析公开公告列表里的公开字段（标题、评估价、起拍价、阶段、开拍时间、保证金、位置）。
不登录、不参与竞拍、不抓取任何竞买人信息。

到手价预估口径（写入 auction 表前统一计算）：
    到手价 = 起拍价 + 税费预估 + 欠费预估 + 维修预留
    税费预估：住宅常见 3%~5%（契税+个税+增值税差额，法拍常由买方全担），默认按 4.5%
    欠费预估：按面积 × 15 元/㎡ 兜底
    维修预留：按面积 × 400 元/㎡ 兜底（法拍房多半需要整修）
真实数据接入后，这三项应改为人工核对后回填，不能一直用兜底值。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .base import PoliteFetcher, fingerprint, parse_area, parse_price, soup_of, text_of
from .secondhand import guess_district

logger = logging.getLogger("rzprobe.crawler.auction")

TAX_RATE_DEFAULT = 0.045      # 税费兜底比例
ARREARS_PER_SQM = 15.0        # 欠费兜底 元/㎡
REPAIR_PER_SQM = 400.0        # 维修预留 元/㎡

RISK_KEYWORDS: dict[str, list[str]] = {
    "占用": ["占用", "有人居住", "被执行人居住", "现有人住"],
    "租赁": ["租赁", "租约", "带租", "买卖不破租赁"],
    "腾退难": ["不负责腾退", "腾退", "拒不腾房", "强制腾退"],
    "税费自担": ["税费由买受人", "税费自担", "一切税费"],
    "产权瑕疵": ["产权瑕疵", "无法过户", "小产权", "查封", "轮候查封"],
    "欠费": ["欠费", "物业费欠", "拖欠"],
    "限购": ["限购", "需符合当地购房资格"],
}


@dataclass
class RawAuction:
    source: str
    title: str = ""
    community_name: str = ""
    district: str = ""
    area: float = 0.0
    layout: str = ""
    appraisal_price: float = 0.0
    start_price: float = 0.0
    stage: str = "一拍"
    auction_date: str = ""
    deposit: float = 0.0
    risk_tags: list[str] = field(default_factory=list)
    source_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def finalize(self) -> "RawAuction":
        if not self.district:
            self.district = guess_district(self.title)
        if not self.community_name:
            # 从标题里猜小区名：取「小区/花园/苑/城/府/家园/公馆」等后缀前的片段
            m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9·]{2,18}(?:花园|小区|苑|家园|公馆|华府|名苑|未来城|学府|广场|大厦|公寓))", self.title)
            self.community_name = m.group(1) if m else self.title[:16]
        if not self.risk_tags:
            self.risk_tags = [tag for tag, kws in RISK_KEYWORDS.items() if any(k in self.title for k in kws)]
        if not self.stage:
            if "变卖" in self.title:
                self.stage = "变卖"
            elif "二拍" in self.title or "第二次" in self.title:
                self.stage = "二拍"
            else:
                self.stage = "一拍"
        return self

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.source, self.community_name, self.stage, self.area, self.start_price)

    def to_db(self, comparable_unit: float = 0.0) -> dict:
        area = self.area or 0.0
        tax = round((self.start_price or 0) * TAX_RATE_DEFAULT)
        arrears = round(area * ARREARS_PER_SQM)
        repair = round(area * REPAIR_PER_SQM)
        landing = (self.start_price or 0) + tax + arrears + repair
        landing_unit = landing / area if area > 0 else 0
        net_discount = (comparable_unit - landing_unit) / comparable_unit if comparable_unit > 0 and landing_unit > 0 else 0
        start_discount = ((self.appraisal_price - self.start_price) / self.appraisal_price
                          if self.appraisal_price > 0 else 0)
        risk_level = "high" if {"产权瑕疵", "占用"} & set(self.risk_tags) else ("mid" if self.risk_tags else "low")
        return {
            "fingerprint": self.fingerprint,
            "source": self.source,
            "source_url": self.source_url,
            "title": self.title,
            "community_name": self.community_name,
            "district": self.district,
            "area": area,
            "layout": self.layout,
            "appraisal_price": self.appraisal_price,
            "start_price": self.start_price,
            "stage": self.stage,
            "auction_date": self.auction_date,
            "deposit": self.deposit,
            "tax_est": tax,
            "arrears_est": arrears,
            "repair_reserve": repair,
            "landing_price": landing,
            "comparable_unit_price": comparable_unit or None,
            "start_discount": round(start_discount, 4),
            "net_discount": round(net_discount, 4),
            "risk_tags": self.risk_tags,
            "risk_level": risk_level,
            "raw_json": self.raw,
        }


class BaseAuctionSource:
    key = "auction_base"
    label = "法拍基础源"
    base_url = ""
    page_url_tpl = ""

    def __init__(self) -> None:
        self.fetcher = PoliteFetcher(source=self.key)

    def build_urls(self, pages: int) -> list[str]:
        return [self.page_url_tpl.format(n=n) for n in range(1, pages + 1) if self.page_url_tpl]

    def parse_list(self, html: str) -> list[RawAuction]:  # pragma: no cover
        raise NotImplementedError

    def fetch(self, pages: int = 1) -> list[RawAuction]:
        out: list[RawAuction] = []
        for url in self.build_urls(pages):
            html = self.fetcher.get(url, referer=self.base_url)
            if not html:
                continue
            try:
                out.extend(self.parse_list(html))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] 法拍列表解析失败 %s: %s", self.key, url, exc)
        return out


class AlipaimaiSource(BaseAuctionSource):
    """阿里司法拍卖（sf.taobao.com）公开公告列表。"""

    key = "ali"
    label = "阿里司法拍卖"
    base_url = "https://sf.taobao.com/"
    page_url_tpl = (
        "https://sf.taobao.com/list/0.htm?spm=a213w.7398504.filter.1&city=%E6%97%A5%E7%85%A7&page={n}"
    )

    def parse_list(self, html: str) -> list[RawAuction]:
        soup = soup_of(html)
        if soup is None:
            return []
        nodes = soup.select(".item-wrap") or soup.select("li.item") or soup.select(".sf-item")
        out: list[RawAuction] = []
        for node in nodes:
            title = text_of(node, [".item-title", "h4", ".title"])
            if not title:
                continue
            blob = node.get_text(" ", strip=True)
            appraisal = parse_price(_search(blob, r"评估价[:：]?\s*([\d,.]+万?)"))
            start = parse_price(_search(blob, r"(?:起拍价|当前价)[:：]?\s*([\d,.]+万?)"))
            area = parse_area(_search(blob, r"(\d+(?:\.\d+)?)\s*(?:㎡|平方米|平米)"))
            link = node.select_one("a")
            item = RawAuction(
                source=self.key, title=title, area=area,
                appraisal_price=appraisal, start_price=start,
                auction_date=_search(blob, r"(\d{4}[-年]\d{1,2}[-月]\d{1,2})") or "",
                source_url=(link.get("href", "") if link else ""),
                raw={"html_snippet": blob[:600]},
            ).finalize()
            if item.start_price > 0:
                out.append(item)
        return out


class JdAuctionSource(BaseAuctionSource):
    """京东拍卖司法频道公开公告列表。"""

    key = "jd"
    label = "京东司法拍卖"
    base_url = "https://auction.jd.com/"
    page_url_tpl = "https://auction.jd.com/sifa_list.html?provinceId=&page={n}"

    def parse_list(self, html: str) -> list[RawAuction]:
        soup = soup_of(html)
        if soup is None:
            return []
        nodes = soup.select(".auction-item") or soup.select(".w1") or soup.select("li.p-item")
        out: list[RawAuction] = []
        for node in nodes:
            title = text_of(node, [".p-name", "h3", ".title"])
            if not title:
                continue
            blob = node.get_text(" ", strip=True)
            item = RawAuction(
                source=self.key, title=title,
                area=parse_area(_search(blob, r"(\d+(?:\.\d+)?)\s*(?:㎡|平方米|平米)")),
                appraisal_price=parse_price(_search(blob, r"评估价[:：]?\s*([\d,.]+万?)")),
                start_price=parse_price(_search(blob, r"起拍价[:：]?\s*([\d,.]+万?)")),
                auction_date=_search(blob, r"(\d{4}-\d{1,2}-\d{1,2})") or "",
                raw={"html_snippet": blob[:600]},
            ).finalize()
            if item.start_price > 0:
                out.append(item)
        return out


def _search(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    return m.group(1) if m else ""


AUCTION_SOURCES: dict[str, BaseAuctionSource] = {
    s.key: s for s in [AlipaimaiSource(), JdAuctionSource()]
}
