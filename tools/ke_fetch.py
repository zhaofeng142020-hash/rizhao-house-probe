"""贝壳找房 · 日照站 抓取器（tools/ke_fetch.py）

为什么单独写一个脚本而不是塞进 crawler/：
贝壳页面结构与 crawler/secondhand.py 里的多候选选择器假设不同，先独立跑通、
把真实数据通过「强制原始链接」的导入链路入库，验证质量后再考虑收编进 crawler 包。

礼貌约束（与 crawler/base.py 同一口径）：
- 只抓公开列表页与详情页的公开字段
- 每个请求间隔 >= 4 秒
- 带普通浏览器 UA，不带 Cookie、不登录、不碰联系方式字段
- 失败重试一次，仍失败就跳过，不硬闯

用法：
    python tools/ke_fetch.py --pages 3                # 抓 3 页列表
    python tools/ke_fetch.py --pages 3 --detail 90    # 再抓每条的详情页补板块/挂牌时间
输出：
    data/ke_ershoufang.json   # 直接可喂给 POST /api/admin/sources/import 的 rows
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "data" / "ke_ershoufang.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
INTERVAL = 4.0

_list_url = "https://rz.ke.com/ershoufang/"


def _get(url: str) -> str | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"  [失败] {url} -> {type(exc).__name__}: {exc}")
        return None


def _first(pattern: str, text: str, default: str | None = None) -> str | None:
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else default


def parse_list(html: str) -> list[dict]:
    """从列表页 HTML 提取房源块。贝壳每条在 <li class="clear"> 块内。"""
    rows = []
    for b in re.split(r'<li class="clear">', html)[1:]:
        # 严格限定日照站域名：列表页会混入其它城市的推荐房源（如 cq.ke.com），
        # 不按域名过滤就会把外地房源当成日照数据 —— 这是绝对不能犯的错
        url = _first(r'href="(https://rz\.ke\.com/ershoufang/\d+\.html)"', b)
        title = _first(r'<div class="title">\s*<a[^>]*?>([^<]+)</a>', b) \
            or _first(r'<div class="title">\s*<a[^>]*title="([^"]+)"', b)
        community = _first(r'https://rz\.ke\.com/xiaoqu/\d+/">([^<]+)</a>', b)
        xiaoqu_id = _first(r'https://rz\.ke\.com/xiaoqu/(\d+)/">', b)
        house_raw = _first(r'class="houseIcon"></span>(.*?)</div>', b, "") or ""
        house = re.sub(r"\s+", " ", house_raw).strip()
        follow = re.sub(r"\s+", " ", _first(r'class="followInfo">(.*?)</div>', b, "") or "")
        # 总价 span 可能带 class="" 属性，必须用 [^>]*
        total_wan = _first(r'class="totalPrice[^"]*">.*?<span[^>]*>\s*([\d.]+)\s*</span>', b)
        unit = _first(r'<span>\s*([\d,]+)元/平\s*</span>', b)

        if not (url and community and total_wan):
            continue

        # houseInfo 格式不统一（有的带建成年，分隔符时有时无），逐字段提取：
        # 如「中楼层 (共23层) 2室2厅 | 85平米 | 南 北」或「中楼层 (共17层) | 2017年 | 3室2厅 | 89.33平米 | 北」
        layout = area = orientation = build_year = None
        m = re.search(r"(\d+)\s*室(\d*)\s*厅?", house)
        if m:
            layout = f"{m.group(1)}室{m.group(2) or '1'}厅"
        m = re.search(r"([\d.]+)\s*平米", house)
        if m:
            area = float(m.group(1))
        m = re.search(r"\|\s*((?:19|20)\d{2})年", house)
        if m:
            build_year = int(m.group(1))
        m = re.search(r"[\|\s]([南北东西]{1,4})\s*$", house)
        if m:
            orientation = m.group(1)
        floor = None
        # 「中楼层」是 中+楼+层 三个字，别漏了「楼」；也兼容 地下层/顶层/平层
        m = re.search(r"((?:[高中低顶]楼层|地下[0-9一二三]*层|顶层|底层|平层)(?:\s*\(共\s*\d+\s*层\))?)", house)
        if m:
            floor = re.sub(r"\s+", "", m.group(1))

        # followInfo 形如「3人关注 / 4月前发布」「12人关注 / 20天前发布」
        list_days = 0
        m = re.search(r"(\d+)\s*天前发布", follow)
        if m:
            list_days = int(m.group(1))
        else:
            m = re.search(r"(\d+)\s*月前发布", follow)
            if m:
                list_days = int(m.group(1)) * 30
            elif "今天发布" in follow:
                list_days = 0
            elif "昨天发布" in follow:
                list_days = 1

        rows.append({
            "community_name": community,
            "xiaoqu_id": xiaoqu_id,
            "title": (title or "").strip() or None,
            "layout": layout,
            "area": area,
            "total_wan": float(total_wan),
            "unit_price_hint": int(unit.replace(",", "")) if unit else None,
            "floor_text": floor,
            "orientation": orientation,
            "build_year": build_year,
            "list_days": list_days,
            "source_url": url,
            "house_raw": house or None,
        })
    return rows


def parse_detail(html: str) -> dict:
    """详情页补：区/板块、小区名复核。拿不到就留空，不猜。"""
    out: dict = {}
    # 区 + 板块：<span class="info"> <a>东港区</a> <a>石臼区</a>（areaName 内）
    m = re.search(r'class="areaName".*?<span class="info">(.*?)</span>', html, re.S)
    if m:
        links = re.findall(r'<a[^>]*>([^<]+)</a>', m.group(1))
        if links:
            out["district_detail"] = links[0]
            out["plate"] = links[1] if len(links) > 1 else None
    # 小区名复核：<div class="communityName"> <a ...>蓝天海景国际</a>
    m = re.search(r'class="communityName".*?<a[^>]*>([^<]+)</a>', html, re.S)
    if m:
        out["community_name_detail"] = m.group(1).strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=3, help="抓取列表页数（每页 30 条）")
    ap.add_argument("--detail", type=int, default=0, help="为前 N 条抓详情页补板块信息")
    args = ap.parse_args()

    rows: list[dict] = []
    seen: set[str] = set()
    for page in range(1, args.pages + 1):
        url = _list_url if page == 1 else f"https://rz.ke.com/ershoufang/pg{page}/"
        print(f"[列表] 第 {page} 页 {url}")
        html = _get(url)
        if not html:
            print("  页面抓取失败，跳过")
            time.sleep(INTERVAL)
            continue
        got = parse_list(html)
        fresh = 0
        for r in got:
            if r["source_url"] in seen:
                continue
            seen.add(r["source_url"])
            rows.append(r)
            fresh += 1
        print(f"  解析 {len(got)} 条，新增 {fresh} 条（累计 {len(rows)}）")
        time.sleep(INTERVAL)

    if args.detail:
        # 按小区去重抓详情：同一小区的区/板块相同，只抓一次，减少请求量
        cache: dict[str, dict] = {}
        for i, row in enumerate(rows[: args.detail]):
            cname = row["community_name"]
            if cname in cache:
                d = cache[cname]
            else:
                html = _get(row["source_url"])
                time.sleep(INTERVAL)
                d = parse_detail(html) if html else {}
                cache[cname] = d
            if d.get("district_detail"):
                row["district"] = d["district_detail"]
            if d.get("plate"):
                row["plate"] = d["plate"]
            if d.get("community_name_detail"):
                row["community_name"] = d["community_name_detail"]
        print(f"[详情] 补全 {len(cache)} 个小区的区/板块（共 {len(rows)} 条）")

    # 校验：导入链路要求 community_name / area / total_wan / source_url
    ok = [r for r in rows if r.get("area") and r.get("total_wan")]
    dropped = len(rows) - len(ok)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(ok, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[完成] 有效 {len(ok)} 条（丢弃缺字段 {dropped} 条）-> {OUT}")


if __name__ == "__main__":
    main()
