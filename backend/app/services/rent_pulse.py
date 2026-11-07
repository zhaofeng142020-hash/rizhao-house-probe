"""真实租售比：贝壳日照租房页每周低频抓取 → 每小区租金中位 → 年租金回报率。

为什么单独建 rent_sample：挂牌租金也是卖方报价，但它是「持有成本/回报」
的唯一可得口径。租售比 = 最近一批租金中位（元/㎡·月）×12 ÷ 该小区在售
挂牌单价中位 —— 只用于横向比较「哪类小区持有更划算」，不构成投资建议。

礼貌约束与 live_refresh 同一口径：只抓公开列表页、≥5s 间隔、失败跳过、
不登录不带 Cookie；租房页目前无验证码（实测 2026-11-07，2419 套在租）。
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import date

from ..db import execute, query_all, query_one

logger = logging.getLogger("rzprobe.rent")

RENT_SOURCE = {
    "key": "ke_zu",
    "name": "贝壳 · 日照租房",
    "list": "https://rz.zu.ke.com/zufang/",
    "page": "https://rz.zu.ke.com/zufang/pg{n}/",
}
RENT_INTERVAL = 5.0


def _first(pattern: str, text: str, default=None):
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else default


def _clean(s):
    import html as _html
    return _html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_ke_zufang(html: str) -> list[dict]:
    """解析贝壳租房列表页。返回按房源编号去重的样本行。"""
    rows: list[dict] = []
    seen: set[str] = set()
    for b in re.split(r'(?=<div class="content__list--item")', html)[1:]:
        ref = _first(r'data-house_code="([^"]+)"', b) or ""
        href = _first(r'href="(/zufang/[A-Za-z0-9]+.html)"', b) or ""
        price = _first(r'content__list--item-price"><em>([\d.]+)</em>', b)
        if not (ref and price):
            continue
        if ref in seen:
            continue
        seen.add(ref)
        des_html = _first(r'content__list--item--des">(.*?)</p>', b, "") or ""
        des = _clean(des_html)
        # des 形如「东港区-高新区-湖西头三区 / 121.00㎡ / 南 北 / 3室2厅1卫」
        segs = [s.strip() for s in des.split("/") if s.strip()]
        head = [x.strip() for x in re.split(r"-", segs[0])] if segs else []
        district = head[0] if head else None
        community = None
        if len(head) >= 3:
            community = head[-1]
        if not community:
            community = _first(r'<a title="([^"]+)" href="/zufang/c\d+/"', b)
        if not community:
            # 标题形如「整租·湖西头三区 3室2厅 南/北」
            m = re.search(r"[整合]租·([^\s]+)", _first(r'title="([^"]+)"', b, "") or "")
            community = m.group(1) if m else None
        if not (community and price):
            continue
        area = None
        for s in segs[1:]:
            m = re.search(r"([\d.]+)㎡", s)
            if m:
                area = float(m.group(1))
                break
        layout = next((s for s in segs if re.search(r"\d+室\d*厅?", s)), None)
        rent = float(price)
        rows.append({
            "community_name": community.strip(),
            "district": district,
            "layout": layout,
            "area": area,
            "rent_month": rent,
            "rent_pm": round(rent / area, 2) if area else None,
            "source_ref": ref,
            "source_url": "https://rz.zu.ke.com" + href if href.startswith("/") else href,
        })
    return rows


def fetch_rent_rows(pages: int = 3) -> tuple[list[dict], str]:
    """抓 N 页租房列表。返回 (去重后的样本, 说明)。失败页跳过，不硬闯。"""
    from ..services.live_refresh import DESKTOP_UA, _get

    rows: list[dict] = []
    seen: set[str] = set()
    ok_pages = 0
    for page in range(1, max(1, pages) + 1):
        url = RENT_SOURCE["list"] if page == 1 else RENT_SOURCE["page"].format(n=page)
        try:
            html = _get(url, DESKTOP_UA)
        except Exception as exc:  # noqa: BLE001 单页失败不影响整批
            logger.warning("租房抓取失败 %s -> %s", url, exc)
            break
        if not html:
            break
        got = parse_ke_zufang(html)
        fresh = 0
        for r in got:
            if r["source_ref"] in seen:
                continue
            seen.add(r["source_ref"])
            rows.append(r)
            fresh += 1
        ok_pages += 1
        logger.info("租房第 %s 页：解析 %s 条", page, len(got))
        if not got:
            break
        if page < pages:
            time.sleep(RENT_INTERVAL)
    return rows, f"抓取 {ok_pages} 页"


def store_rents(rows: list[dict], day=None) -> dict:
    """样本入库：每套房源一行（同编号更新租金与采集日）。"""
    day = (day or date.today()).isoformat()
    saved = skipped = 0
    for r in rows:
        fp = hashlib.md5(f"ke_zu|{r['source_ref']}".encode("utf-8")).hexdigest()
        c = query_one("SELECT id FROM community WHERE name=? ORDER BY id LIMIT 1", (r["community_name"],))
        if not c:
            c = query_one("SELECT id FROM community WHERE name LIKE ? ORDER BY id LIMIT 1",
                          (f"%{r['community_name']}%",))
        cid = int(c["id"]) if c else None
        existing = query_one("SELECT rent_month, collected_date FROM rent_sample WHERE fingerprint=?", (fp,))
        if existing and existing["collected_date"] == day:
            skipped += 1
            continue
        execute(
            """
            INSERT INTO rent_sample (fingerprint,source,source_ref,source_url,community_id,
              community_name,district,layout,area,rent_month,rent_pm,collected_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              rent_month=excluded.rent_month, rent_pm=excluded.rent_pm,
              area=excluded.area, community_id=excluded.community_id,
              district=excluded.district, collected_date=excluded.collected_date
            """,
            (fp, RENT_SOURCE["key"], r["source_ref"], r["source_url"], cid,
             r["community_name"], r["district"], r["layout"], r["area"],
             r["rent_month"], r["rent_pm"], day),
        )
        saved += 1
    return {"saved": saved, "skipped": skipped, "day": day}


def _median(vals):
    xs = sorted(float(v) for v in vals if v)
    if not xs:
        return None
    n = len(xs)
    return round(xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2, 1)


def refresh_rents(pages: int = 3) -> dict:
    """抓取 → 入库 → 返回租售比汇总（调度周任务与管理台按钮共用）。"""
    rows, note = fetch_rent_rows(pages)
    if not rows:
        return {"ok": False, "note": "未抓到租房样本（" + note + "）", "summary": rent_summary()}
    st = store_rents(rows)
    return {"ok": True, "fetched": len(rows), **st, "note": note, "summary": rent_summary()}


def rent_summary() -> dict:
    """每小区租金中位 + 租售比（年化租金回报率）。只用最近一次采集批次。"""
    latest = query_one("SELECT MAX(collected_date) AS d FROM rent_sample")
    latest_day = (latest or {}).get("d")
    if not latest_day:
        return {"collected_date": None, "communities": [], "city": {},
                "note": "暂无租金样本。接入后每周自动更新一次。"}
    samples = query_all("SELECT * FROM rent_sample WHERE collected_date=?", (latest_day,))
    by_comm: dict[str, list[dict]] = {}
    for s in samples:
        by_comm.setdefault(s["community_name"], []).append(s)

    # 每小区在售挂牌单价中位（用于回报率分母）
    def listing_median(name: str):
        rows = query_all(
            "SELECT unit_price FROM listing WHERE community_name=? AND status='active'", (name,))
        return _median([r["unit_price"] for r in rows if r.get("unit_price")])

    items = []
    for name, group in by_comm.items():
        rents = [g["rent_month"] for g in group if g.get("rent_month")]
        pms = [g["rent_pm"] for g in group if g.get("rent_pm")]
        med_pm = _median(pms)
        med_unit = listing_median(name)
        yield_pct = round(med_pm * 12 / med_unit * 100, 2) if (med_pm and med_unit) else None
        items.append({
            "community_name": name,
            "district": group[0].get("district"),
            "sample_n": len(group),
            "median_rent": _median(rents),
            "median_rent_pm": med_pm,
            "listing_median_unit": round(med_unit) if med_unit else None,
            "rent_yield_pct": yield_pct,
        })
    items.sort(key=lambda x: -(x["rent_yield_pct"] or 0))
    yields = [i["rent_yield_pct"] for i in items if i["rent_yield_pct"] is not None]
    city_pm = _median([s["rent_pm"] for s in samples if s.get("rent_pm")])
    return {
        "collected_date": latest_day,
        "communities": items,
        "city": {
            "sample_n": len(samples),
            "median_rent_pm": city_pm,
            "yield_median": round(sum(yields) / len(yields), 2) if yields else None,
        },
        "note": "租售比 = 租金中位(元/㎡·月)×12 ÷ 在售挂牌单价中位。租金与挂牌价都是报价口径，"
                "仅用于小区间横向比较，不构成投资建议。",
    }
