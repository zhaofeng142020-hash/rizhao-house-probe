"""抓取基础设施：礼貌抓取器 + 字段清洗 + 去重指纹。

合规红线（写死在代码里，避免以后被"顺手"放宽）：
  1. 单站请求间隔 ≥ 3 秒（settings.CRAWL_MIN_INTERVAL）
  2. 先读 robots.txt，Disallow 的路径直接放弃
  3. 不登录、不碰任何个人隐私字段；验证码由 captcha/ 模块本地自动识别通关（无人工参与）
  4. 只抓公开列表页/详情页，遇到 403/429 立刻停止该站点
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from ..config import settings

logger = logging.getLogger("rzprobe.crawler")


# --------------------------------------------------------------------------
# 字段清洗
# --------------------------------------------------------------------------
def parse_price(text: str | None) -> float:
    """'128万' / '128.5 万' / '1,050,000元' / '9800元/㎡' → 元（总价）"""
    if not text:
        return 0.0
    s = str(text).replace(",", "").replace("，", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*万", s)
    if m:
        return round(float(m.group(1)) * 10000, 2)
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m:
        value = float(m.group(1))
        # 纯数字且很小（如 128）多半是"万"单位省略
        return round(value * 10000, 2) if value < 1000 else round(value, 2)
    return 0.0


def parse_area(text: str | None) -> float:
    if not text:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)", str(text).replace(",", ""))
    return round(float(m.group(1)), 1) if m else 0.0


def parse_layout(text: str | None) -> tuple[str, int, int, int]:
    """'3室2厅2卫' → ('3室2厅2卫', 3, 2, 2)"""
    if not text:
        return "", 0, 0, 0
    s = str(text)
    rooms = re.search(r"(\d+)\s*室", s)
    halls = re.search(r"(\d+)\s*厅", s)
    baths = re.search(r"(\d+)\s*卫", s)
    return (
        s.strip(),
        int(rooms.group(1)) if rooms else 0,
        int(halls.group(1)) if halls else 0,
        int(baths.group(1)) if baths else 0,
    )


def detect_defects(title: str, extra: str = "") -> list[str]:
    """从标题/描述里提取硬伤标签（宁可多标，让人工去核）。"""
    text = f"{title} {extra}"
    rules = {
        "顶楼": ["顶楼", "顶层"],
        "腰线": ["腰线"],
        "临街": ["临街", "临马路", "临主街"],
        "暗卫": ["暗卫"],
        "户型异形": ["异形", "斜角", "刀把"],
        "无电梯": ["无电梯", "步梯", "无梯"],
        "学区占用": ["学区占用", "学位已用", "学位占用"],
        "采光差": ["采光差", "无采光"],
        "北向": ["北向", "朝北"],
        "装修旧": ["毛坯", "简装", "老装修"],
    }
    tags: list[str] = []
    for tag, kws in rules.items():
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags


def fingerprint(source: str, community: str, layout: str, area: float, total_price: float) -> str:
    """去重指纹 = md5(source + 小区 + 户型 + 面积(取整) + 总价(取整))。"""
    raw = f"{source}|{community}|{layout}|{int(round(area))}|{int(round(total_price))}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 礼貌抓取器
# --------------------------------------------------------------------------
@dataclass
class PoliteFetcher:
    source: str
    min_interval: float = field(default_factory=lambda: settings.CRAWL_MIN_INTERVAL)
    timeout: float = field(default_factory=lambda: settings.CRAWL_TIMEOUT)
    _last_request_at: float = 0.0
    _robots: urllib.robotparser.RobotFileParser | None = None
    _robots_host: str = ""
    blocked: bool = False

    def _load_robots(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        if self._robots and self._robots_host == host:
            return self._robots
        try:
            import requests  # noqa: PLC0415
            resp = requests.get(host + "/robots.txt", timeout=self.timeout, headers={"User-Agent": settings.CRAWL_UA})
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines() if resp.ok else [])
            self._robots, self._robots_host = rp, host
            return rp
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] robots.txt 读取失败，按保守策略放弃抓取: %s", self.source, exc)
            return None

    def can_fetch(self, url: str) -> bool:
        rp = self._load_robots(url)
        if rp is None:
            return False
        allowed = rp.can_fetch(settings.CRAWL_UA, url) or rp.can_fetch("*", url)
        if not allowed:
            logger.warning("[%s] robots.txt 禁止抓取：%s", self.source, url)
        return allowed

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.time()

    def get(self, url: str, referer: str | None = None) -> str | None:
        """返回 HTML 文本；任何异常返回 None（异常隔离在调用方）。"""
        if self.blocked:
            return None
        if not self.can_fetch(url):
            return None
        self._throttle()
        try:
            import requests  # noqa: PLC0415
        except ImportError:
            logger.error("requests 未安装，无法抓取。请 pip install requests beautifulsoup4")
            return None
        headers = {"User-Agent": settings.CRAWL_UA, "Accept-Language": "zh-CN,zh;q=0.9"}
        if referer:
            headers["Referer"] = referer
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code in (403, 429):
                logger.warning("[%s] 被限流/拒绝(%s)，停止本站抓取", self.source, resp.status_code)
                self.blocked = True
                return None
            if not resp.ok:
                logger.warning("[%s] HTTP %s: %s", self.source, resp.status_code, url)
                return None
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] 请求失败 %s: %s", self.source, url, exc)
            return None


def soup_of(html: str | None):
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except ImportError:
        logger.error("beautifulsoup4 未安装，无法解析。请 pip install beautifulsoup4 lxml")
        return None
    return BeautifulSoup(html, "lxml") if _has_lxml() else BeautifulSoup(html, "html.parser")


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401, PLC0415
        return True
    except ImportError:
        return False


def text_of(node: Any, selectors: list[str], default: str = "") -> str:
    """按候选选择器依次尝试取文本——站点改版时不至于整站失效。"""
    if node is None:
        return default
    for sel in selectors:
        try:
            found = node.select_one(sel)
        except Exception:  # noqa: BLE001
            continue
        if found and found.get_text(strip=True):
            return found.get_text(strip=True)
    return default
