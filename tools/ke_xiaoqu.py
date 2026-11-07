"""抓贝壳小区档案（tools/ke_xiaoqu.py）：坐标 / 地址 / 建成年 / 楼栋数。

坐标是贝壳页面上的 resblockPosition（GCJ-02，与高德同系），可直接用于地图。
礼貌约束与 ke_fetch.py 相同：间隔 >= 4 秒、只抓公开字段、失败跳过。

用法：
    python tools/ke_xiaoqu.py
输入：data/ke_xiaoqu_ids.json   （{小区名: xiaoqu_id}，由 ke_fetch.py 生成）
输出：data/ke_communities.json  （[{name, xiaoqu_id, lat, lng, address, build_year, buildings}]）
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ke_fetch import _get, INTERVAL  # 复用请求函数与礼貌间隔

BASE = Path(__file__).resolve().parent.parent
IDS_FILE = BASE / "data" / "ke_xiaoqu_ids.json"
OUT = BASE / "data" / "ke_communities.json"


def parse_xiaoqu(html: str) -> dict:
    out: dict = {}
    m = re.search(r"resblockPosition:\s*'([\d.]+)\s*,\s*([\d.]+)'", html)
    if m:
        out["lng"] = float(m.group(1))
        out["lat"] = float(m.group(2))
    # 小区名复核（防止上游把 ID 当名字传进来）
    m = re.search(r"resblockName:\s*'([^']+)'", html)
    if m:
        out["name"] = m.group(1).strip()
    # 板块：meta description「位于东港区石臼区」
    m = re.search(r"位于(东港区|岚山区|莒县|五莲县)([^,，。;；\"']+)", html)
    if m:
        out["region"] = m.group(1)
        out["plate"] = m.group(2).strip()
    # 地址：页面内嵌 JSON 的 "address":"黄海三路89号"
    m = re.search(r'"address":"([^"]+)"', html)
    if m:
        out["address"] = m.group(1).encode("utf-8").decode("unicode_escape") \
            if "\\u" in m.group(1) else m.group(1)
    # 建成年代 / 楼栋总数（xiaoquInfoItem 列表）
    m = re.search(r'建成年代</span>\s*<span class="xiaoquInfoContent">\s*([^<]+)', html)
    if m:
        y = re.search(r"((?:19|20)\d{2})", m.group(1))
        if y:
            out["build_year"] = int(y.group(1))
    m = re.search(r'楼栋总数</span>\s*<span class="xiaoquInfoContent">\s*([^<]+)', html)
    if m:
        out["buildings"] = m.group(1).strip()
    return out


def main() -> None:
    ids: dict[str, str | None] = json.loads(IDS_FILE.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for i, (name, qid) in enumerate(ids.items(), 1):
        info: dict = {"name": name, "xiaoqu_id": qid}
        if qid:
            html = _get(f"https://rz.ke.com/xiaoqu/{qid}/")
            time.sleep(INTERVAL)
            if html:
                info.update(parse_xiaoqu(html))
        rows.append(info)
        got = "坐标OK" if info.get("lat") else "无坐标"
        print(f"[{i}/{len(ids)}] {name}: {got} {info.get('address') or ''}")

    ok = [r for r in rows if r.get("lat") and r.get("lng")]
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[完成] {len(ok)}/{len(rows)} 个小区有坐标 -> {OUT}")


if __name__ == "__main__":
    main()
