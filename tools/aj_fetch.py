"""安居客 · 日照 移动站抓取器（tools/aj_fetch.py）。

为什么用 m.anjuke.com：
PC 站（rizhao.anjuke.com）返回 JS 验证壳、服务端无内容；
移动站 `m.anjuke.com/rz/sale/` 用 iPhone UA 可直接拿到服务端渲染的真实列表。

礼貌约束：≥4s 间隔、只抓公开列表页公开字段、不带登录态、失败跳过。

用法：
    python tools/aj_fetch.py --pages 3
输出：data/aj_ershoufang.json（与 ke_ershoufang.json 同构，可直接喂导入接口）
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "data" / "aj_ershoufang.json"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
INTERVAL = 4.0

LIST_URL = "https://m.anjuke.com/rz/sale/"


def _get(url: str) -> str | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            final = r.geturl()
            html = r.read().decode("utf-8", errors="ignore")
            if "antibot" in final or "verifycode" in final:
                print(f"  [反爬] {url} 被引到验证页，跳过")
                return None
            return html
    except Exception as exc:
        print(f"  [失败] {url} -> {type(exc).__name__}: {exc}")
        return None


def _first(pattern: str, text: str, default: str | None = None) -> str | None:
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else default


def parse_list(html: str) -> list[dict]:
    """每条房源在一个 class="cell-wrap" 的 <a> 块里。"""
    rows = []
    # 前瞻切分保留 <a 起始（href 在 class 之前，普通 split 会把 href 切进上一块）
    blocks = re.split(r'(?=<a [^>]*class="cell-wrap)', html)[1:]
    for b in blocks:
        url = _first(r'href="(https://m\.anjuke\.com/rz/sale/A\d+/\?[^"]+)"', b)
        if not url:
            continue
        pid = _first(r'/sale/A(\d+)/', url)
        title = _first(r'class="content-title"[^>]*>([^<]+)</span>', b)
        total_wan = _first(r'class="content-price"[^>]*>([\d.]+)</span>\s*<span[^>]*>万', b)
        unit = _first(r'class="house-avg-price"[^>]*>([\d,]+)元/㎡', b)
        descs = re.findall(r'class="content-desc"[^>]*>\s*([^<]+?)\s*</span>', b)

        layout = area = orientation = district = plate = None
        for d in descs:
            if re.match(r"\d+室\d*厅?$", d):
                layout = d
            elif d.endswith("㎡") or d.endswith("平"):
                m = re.search(r"([\d.]+)", d)
                area = float(m.group(1)) if m else None
            elif re.fullmatch(r"[南北东西]{1,4}", d):
                orientation = d
            elif not district:
                district = d
            elif not plate:
                plate = d

        # 小区名藏在图片 alt 里：「东海花园西区(3A,3b,5-10号)3室2厅115㎡36.8万二手房图片」
        community = None
        alt = _first(r'alt="([^"]+)"', b) or ""
        m = re.match(r"(.+?)\d+室\d*厅?[\d.]+㎡[\d.]+万二手房图片$", alt)
        if m:
            community = m.group(1).strip()
        if not community and title:
            # 退路：标题开头到第一个空格（很多标题以小区名开头），并去掉分隔符
            m = re.match(r"([\u4e00-\u9fa5A-Za-z0-9·\(\)（）]+)", title)
            community = m.group(1) if m else None

        if not (pid and community and total_wan):
            continue

        rows.append({
            "community_name": community,
            "title": (title or "").strip() or None,
            "layout": layout,
            "area": area,
            "total_wan": float(total_wan),
            "unit_price_hint": int(unit.replace(",", "")) if unit else None,
            "orientation": orientation,
            "district": district,
            "plate": plate,
            "source_url": url.split("?")[0] + f"?from=anjuke_list",
            "source_ref": pid,
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=3, help="抓取列表页数")
    ap.add_argument("--max-pages", type=int, default=10)
    args = ap.parse_args()

    rows: list[dict] = []
    seen: set[str] = set()
    empty_pages = 0
    for page in range(1, args.pages + 1):
        url = LIST_URL if page == 1 else f"https://m.anjuke.com/rz/sale/p{page}/"
        print(f"[列表] 第 {page} 页 {url}")
        html = _get(url)
        if not html:
            empty_pages += 1
            if empty_pages >= 2:
                print("  连续失败，停止翻页")
                break
            time.sleep(INTERVAL)
            continue
        got = parse_list(html)
        fresh = 0
        for r in got:
            key = r.get("source_ref") or r["source_url"]
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
            fresh += 1
        print(f"  解析 {len(got)} 条，新增 {fresh} 条（累计 {len(rows)}）")
        if not got:
            empty_pages += 1
            if empty_pages >= 2:
                print("  连续空页，停止翻页")
                break
        time.sleep(INTERVAL)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[完成] {len(rows)} 条 -> {OUT}")


if __name__ == "__main__":
    main()
