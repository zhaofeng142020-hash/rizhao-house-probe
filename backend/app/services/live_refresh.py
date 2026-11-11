"""真实房源每日刷新：贝壳 + 安居客 + 58 列表页抓取 → 入库 → 记调价。

为什么放在后端而不是只留 tools/ 脚本：
每日两次的自动更新必须在服务进程里跑（调度器直接调用），
所以把已验证的解析逻辑收进来，tools/ 里的脚本继续做离线调试入口。

礼貌约束（与 crawler/base.py 同一口径）：
- 只抓公开列表页的公开字段，≥4 秒一请求，失败跳过不硬闯
- 不带登录态、不碰联系方式；验证码由本地模型自动识别通关（无人工参与）
- 单源失败只记错误，不影响其它源与整体流程
"""
from __future__ import annotations

import logging
import re
import time
import urllib.request

from ..captcha import gateway
from ..config import settings

logger = logging.getLogger("rzprobe.live")

INTERVAL = 4.0
AJ_INTERVAL = 6.0     # 安居客风控更敏感，放慢一些，少翻页
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# 每个源的抓取页数：安居客页数一多就容易触发极验，默认少抓几页
DEFAULT_PAGES = {"ke": 5, "anjuke": 3, "wuba": 1}

KE_SOURCE = {"key": "ke", "name": "贝壳找房 · 日照二手房", "ua": DESKTOP_UA,
             "list": "https://rz.ke.com/ershoufang/",
             "page": "https://rz.ke.com/ershoufang/pg{n}/"}
AJ_SOURCE = {"key": "anjuke", "name": "安居客 · 日照二手房", "ua": MOBILE_UA,
             "list": "https://m.anjuke.com/rz/sale/",
             "page": "https://m.anjuke.com/rz/sale/p{n}/"}
WUBA_SOURCE = {"key": "wuba", "name": "58同城 · 日照二手房", "ua": DESKTOP_UA,
               "list": "https://rizhao.58.com/ershoufang/",
               "page": "https://rizhao.58.com/ershoufang/pn{n}/"}
SOURCES = {"ke": KE_SOURCE, "anjuke": AJ_SOURCE, "wuba": WUBA_SOURCE}


def _parser_for(key: str):
    """延迟绑定：parse_* 定义在本文件后半段，运行时才查表。"""
    return {"ke": parse_ke, "anjuke": parse_aj, "wuba": parse_wuba}[key]


class CaptchaBlocked(Exception):
    """被站点要求人机验证。由验证码模块自动识别通关（见 captcha/gateway.py）。"""


def get_session_cookie(key: str) -> str:
    """读取用户自己通过人机验证后保存的会话 Cookie（没有就返回空）。"""
    from . import auth
    return (auth.get_setting(f"session_cookies.{key}") or "").strip()


def set_session_cookie(key: str, cookie: str) -> dict:
    from . import auth
    cookie = (cookie or "").strip()
    if cookie:
        auth.set_setting(f"session_cookies.{key}", cookie, is_secret=1)
    return {"ok": True, "key": key, "has_cookie": bool(cookie),
            "length": len(cookie)}


def _get(url: str, ua: str, cookie: str = "") -> str | None:
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            final = r.geturl()
            html = r.read().decode("utf-8", errors="ignore")
        low = final.lower()
        if any(k in low for k in ("antibot", "verifycode", "captcha", "geetest")):
            raise CaptchaBlocked(final)
        return html
    except CaptchaBlocked:
        raise
    except Exception as exc:  # noqa: BLE001 单页失败不影响整批
        logger.warning("抓取失败 %s -> %s: %s", url, type(exc).__name__, exc)
        return None


def _first(pattern: str, text: str, default: str | None = None) -> str | None:
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else default


# --------------------------------------------------------------------------
# 贝壳列表页
# --------------------------------------------------------------------------
def parse_ke(html: str) -> list[dict]:
    rows = []
    for b in re.split(r'<li class="clear">', html)[1:]:
        # 严格限定日照站域名：列表页会混入其它城市推荐房源（如 cq.ke.com）
        url = _first(r'href="(https://rz\.ke\.com/ershoufang/\d+\.html)"', b)
        title = _first(r'<div class="title">\s*<a[^>]*?>([^<]+)</a>', b) \
            or _first(r'<div class="title">\s*<a[^>]*title="([^"]+)"', b)
        community = _first(r'https://rz\.ke\.com/xiaoqu/\d+/">([^<]+)</a>', b)
        xiaoqu_id = _first(r'https://rz\.ke\.com/xiaoqu/(\d+)/">', b)
        house = re.sub(r"\s+", " ", _first(r'class="houseIcon"></span>(.*?)</div>', b, "") or "").strip()
        follow = re.sub(r"\s+", " ", _first(r'class="followInfo">(.*?)</div>', b, "") or "")
        total_wan = _first(r'class="totalPrice[^"]*">.*?<span[^>]*>\s*([\d.]+)\s*</span>', b)
        if not (url and community and total_wan):
            continue

        layout = area = orientation = build_year = floor = None
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
        # 「中楼层」是 中+楼+层 三个字，别漏了「楼」
        m = re.search(r"((?:[高中低顶]楼层|地下[0-9一二三]*层|顶层|底层|平层)(?:\s*\(共\s*\d+\s*层\))?)", house)
        if m:
            floor = re.sub(r"\s+", "", m.group(1))

        list_days = 0
        m = re.search(r"(\d+)\s*天前发布", follow)
        if m:
            list_days = int(m.group(1))
        else:
            m = re.search(r"(\d+)\s*月前发布", follow)
            if m:
                list_days = int(m.group(1)) * 30
            elif "昨天发布" in follow:
                list_days = 1

        rows.append({
            "community_name": community,
            "xiaoqu_id": xiaoqu_id,
            "title": (title or "").strip() or None,
            "layout": layout, "area": area,
            "total_wan": float(total_wan),
            "floor_text": floor, "orientation": orientation,
            "build_year": build_year, "list_days": list_days,
            "source_url": url,
            # 平台房源编号：价格变了还能认出是同一套房（每日更新的关键）
            "source_ref": _first(r"/ershoufang/(\d+)\.html", url),
        })
    return rows


# --------------------------------------------------------------------------
# 安居客移动站列表页
# --------------------------------------------------------------------------
def parse_aj(html: str) -> list[dict]:
    rows = []
    # 前瞻切分保留 <a 起始（href 在 class 之前，普通 split 会把 href 切进上一块）
    for b in re.split(r'(?=<a [^>]*class="cell-wrap)', html)[1:]:
        url = _first(r'href="(https://m\.anjuke\.com/rz/sale/A\d+/\?[^"]+)"', b)
        if not url:
            continue
        pid = _first(r"/sale/A(\d+)/", url)
        title = _first(r'class="content-title"[^>]*>([^<]+)</span>', b)
        total_wan = _first(r'class="content-price"[^>]*>([\d.]+)</span>\s*<span[^>]*>万', b)
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

        # 小区名藏在图片 alt 里：「东海花园西区…3室2厅115㎡36.8万二手房图片」
        community = None
        alt = _first(r'alt="([^"]+)"', b) or ""
        m = re.match(r"(.+?)\d+室\d*厅?[\d.]+㎡[\d.]+万二手房图片$", alt)
        if m:
            community = m.group(1).strip()
        if not community and title:
            m = re.match(r"([\u4e00-\u9fa5A-Za-z0-9·\(\)（）]+)", title)
            community = m.group(1) if m else None
        if not (pid and community and total_wan):
            continue

        rows.append({
            "community_name": community,
            "title": (title or "").strip() or None,
            "layout": layout, "area": area,
            "total_wan": float(total_wan),
            "orientation": orientation,
            "district": plate or district,
            "source_url": url.split("?")[0] + "?from=anjuke_list",
            "source_ref": pid,
        })
    return rows


# --------------------------------------------------------------------------
# 58 同城列表页（Vue SSR，class 名稳定）
# --------------------------------------------------------------------------
# 只抓第一页：58 的风控对「短时间连续访问列表页」很敏感，翻页会触发
# antibot 验证码。低频抓一页既能每天拿到最新挂牌，也不去撞它的风控。
WUBA_INTERVAL = 8.0


def parse_wuba(html: str) -> list[dict]:
    """解析 58 日照二手房列表页（服务端抓到的 SSR HTML）。

    按卡片 class 精确取字段，避免「从整段文字里猜小区名」把「莒县城」这类
    地址词误当成小区。并做一次「总价 / 面积 ≈ 页面单价」交叉校验，
    对不上的行直接丢弃，宁可少抓也不写入错价。
    """
    import html as _html

    def clean(s: str | None) -> str:
        return _html.unescape(re.sub(r"\s+", " ", s or "")).strip()

    rows: list[dict] = []
    blocks = re.split(r'(?=<div[^>]*class="property"[>\s])', html)[1:]
    for b in blocks:
        # 只取本卡片范围，避免把下一张卡片的价格算进来
        url = _first(r'href="(https://rizhao\.58\.com/ershoufang/\d{8,}x?\.shtml)', b)
        if not url:
            continue
        community = clean(_first(r'class="property-content-info-comm-name"[^>]*>([^<]+)<', b))
        total = _first(r'class="property-price-total-num"[^>]*>\s*([\d.]+)\s*<', b)
        area = _first(r'class="property-content-info-text"[^>]*>\s*([\d.]+)\s*㎡', b)
        if not (community and total and area):
            continue

        total_wan = float(total)
        area_val = float(area)
        unit_hint = _first(r'class="property-price-average"[^>]*>\s*([\d,]+)\s*元', b)
        if unit_hint:
            shown = float(unit_hint.replace(",", ""))
            calc = total_wan * 10000 / area_val if area_val else 0
            # 页面展示的单价是自己算出来的，允许 3% 的四舍五入误差
            if shown > 0 and abs(calc - shown) / shown > 0.03:
                logger.debug("58 丢弃价格与单价不符的房源：%s %s万/%s㎡ 显示%s元/㎡",
                             community, total_wan, area_val, shown)
                continue

        attr = _first(r'class="property-content-info-text property-content-info-attribute"[^>]*>(.*?)</p>', b, "") or ""
        attr_txt = clean(re.sub(r"<[^>]+>", "", attr))
        layout = None
        m = re.search(r"(\d+)\s*室\s*(\d*)\s*厅?", attr_txt)
        if m:
            layout = f"{m.group(1)}室{m.group(2) or '1'}厅"

        infos = [clean(re.sub(r"<[^>]+>", "", x)) for x in
                 re.findall(r'class="property-content-info-text"[^>]*>(.*?)</p>', b, re.S)]
        orientation = next((x for x in infos if re.fullmatch(r"[南北东西]{1,4}", x)), None)
        floor_text = next((x for x in infos if re.search(r"(?:高|中|低|顶|底)层", x)), None)
        year = _first(r"((?:19|20)\d{2})\s*年建造", b)

        addr = _first(r'class="property-content-info-comm-address"[^>]*>(.*?)</p>', b, "") or ""
        parts = [clean(x) for x in re.findall(r"<span[^>]*>([^<]+)</span>", addr)]
        # 站点 district 用板块口径（新市区/高科园/老城区…），58 的第二段正是板块
        district = parts[1] if len(parts) > 1 else (parts[0] if parts else None)

        title = clean(_first(r'class="property-content-title-name"[^>]*title="([^"]+)"', b)) \
            or clean(_first(r'class="property-content-title-name"[^>]*>([^<]+)<', b))

        rows.append({
            "community_name": community,
            "title": title[:120] or None,
            "layout": layout, "area": area_val,
            "total_wan": total_wan,
            "orientation": orientation, "floor_text": floor_text,
            "build_year": int(year) if year else None,
            "district": district,
            "source_url": url,
            "source_ref": _first(r"/ershoufang/(\d{8,})", url),
        })
    return rows


# --------------------------------------------------------------------------
# 编排
# --------------------------------------------------------------------------
def fetch_source(src: dict, pages: int) -> tuple[list[dict], str]:
    """抓一个源的列表页。返回 (去重后的房源行, 提示信息)。

    被要求人机验证时抛 CaptchaBlocked，上层调用验证码模块自动通关后重试。
    """
    parser = _parser_for(src["key"])
    interval = {"anjuke": AJ_INTERVAL, "wuba": WUBA_INTERVAL}.get(src["key"], INTERVAL)
    cookie = get_session_cookie(src["key"])
    rows: list[dict] = []
    seen: set[str] = set()
    empty = 0
    for page in range(1, max(1, pages) + 1):
        url = src["list"] if page == 1 else src["page"].format(n=page)
        html = _get(url, src["ua"], cookie)
        if not html:
            empty += 1
            if empty >= 2:
                break
            time.sleep(interval)
            continue
        # 58 有时不跳转、直接返回「访问过于频繁」的验证壳页；
        # 这种情况要如实记为被拦，不能当成「抓到 0 条」糊弄过去。
        # 验证码自动通关（滑块本地识别 + 拟人轨迹，全程无人工参与）：
        # 通关成功拿到会话后立刻重试一次，仍被拦才抛 CaptchaBlocked。
        if src["key"] == "wuba" and 'class="property"' not in html:
            solved = gateway.auto_solve(src["key"], url)
            if solved.get("ok") and solved.get("cookies"):
                logger.info("[%s] 验证码自动通关成功，重试抓取", src["key"])
                cookie = solved["cookies"]
                html = _get(url, src["ua"], cookie)
                if not html or 'class="property"' not in html:
                    raise CaptchaBlocked(url)
            else:
                logger.warning("[%s] 验证码自动通关未成功：%s", src["key"], solved.get("note"))
                raise CaptchaBlocked(url)
        got = parser(html)
        fresh = 0
        for r in got:
            key = r.get("source_ref") or r["source_url"]
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
            fresh += 1
        logger.info("%s 第 %s 页：解析 %s 条，新增 %s 条", src["name"], page, len(got), fresh)
        if not got:
            empty += 1
            if empty >= 2:
                break
        time.sleep(interval)
    return rows, ("使用已保存的会话" if cookie else "免登录访问")


def refresh_source(key: str, pages: int | None = None) -> dict:
    """抓一个源并入库。返回摘要（新增/更新/调价/错误）。"""
    from .sources import import_listings

    src = SOURCES.get(key)
    if not src:
        raise ValueError(f"不支持在线刷新的来源：{key}")
    pages = pages or DEFAULT_PAGES.get(key) or int(settings.REFRESH_PAGES)
    try:
        rows, via = fetch_source(src, pages)
    except CaptchaBlocked as exc:
        # 触发人机验证 → 自动通关（无头浏览器本地识别，无人工参与）→ 成功则重抓
        logger.warning("%s 触发人机验证，启动自动通关：%s", src["name"], exc)
        solved = gateway.auto_solve(src["key"], src["list"])
        if solved.get("ok"):
            logger.info("[%s] 自动通关成功（第 %s 轮），重试抓取", src["key"], solved.get("rounds"))
            try:
                rows, via = fetch_source(src, pages)
                via = (via or "") + "（验证码自动通关后抓取）"
            except CaptchaBlocked:
                rows, via = [], "自动通关后仍被拦，本轮跳过"
        else:
            rows, via = [], f"自动通关失败：{solved.get('note')}"
        if not rows:
            return {
                "source": key, "fetched": 0, "captcha": not solved.get("ok"),
                "auto_solved": bool(solved.get("ok")),
                "note": solved.get("note") or "自动通关成功但未抓到数据",
            }
    if not rows:
        return {"source": key, "fetched": 0, "note": f"未抓到房源（{via}）"}
    res = import_listings(rows, src["key"], src["name"], "crawled")
    return {
        "source": key,
        "via": via,
        "fetched": len(rows),
        "saved": res["items_saved"],
        "updated": res.get("items_updated", 0),
        "price_changes": res.get("price_changes", 0),
        "skipped": res["items_skipped"],
    }


def refresh_all(pages: int | None = None) -> dict:
    """依次刷新所有已验证的在线源。单源失败不影响其它源。"""
    import json as _json
    from datetime import datetime as _dt

    out: dict = {"sources": {}, "errors": []}
    for key in ("ke", "anjuke", "wuba"):
        try:
            out["sources"][key] = refresh_source(key, pages)
        except Exception as exc:  # noqa: BLE001 单源异常不能拖垮整批
            logger.exception("刷新 %s 失败", key)
            out["errors"].append(f"{key}: {type(exc).__name__}: {exc}")
    out["at"] = _dt.now().strftime("%Y-%m-%d %H:%M:%S")

    # 每日调价报告：刷新完立刻统计当天涨跌明细并落库（网页端「每日调价报告」页可查）
    try:
        from . import price_report
        price_report.generate_today(push=True)
    except Exception as exc:  # noqa: BLE001  报告失败不影响抓取与重算
        logger.warning("每日调价报告生成失败：%s", exc, exc_info=True)

    # 市场脉搏：先用「今天确实抓到了哪些站」判定下架，再落全城日快照。
    # 放在抓取之后、保存结果之前，刷新摘要就能顺带报出大盘数字。
    try:
        from . import market_pulse
        out["delist"] = market_pulse.detect_delistings()
        out["market"] = market_pulse.record_market_daily(
            delist_n=out["delist"].get("marked", 0))
    except Exception as exc:  # noqa: BLE001 大盘失败不影响抓取本身
        logger.warning("市场脉搏刷新失败：%s", exc)
        out["errors"].append(f"market: {type(exc).__name__}: {exc}")

    try:
        from . import auth
        auth.set_setting("refresh_last", _json.dumps(out, ensure_ascii=False))
    except Exception:  # noqa: BLE001 记录失败不影响刷新本身
        logger.warning("保存刷新结果失败", exc_info=True)
    return out


# --------------------------------------------------------------------------
# 刷新完成后主动推送（用户要「验证后直接把数据推给我」）
# --------------------------------------------------------------------------
def push_refresh_summary(report: dict, pipeline: dict | None = None) -> dict | None:
    """把本次刷新结果主动推给企微 / 站内提醒，不用用户来网站看。

    含三类关键信息：新增与调价、值得看的线索数、以及需要人工点一次验证的通道。
    """
    from .alert_sender import AlertSender

    lines = ["【房源刷新完成】" + (report.get("at") or "")]
    if report.get("note"):
        lines.append(report["note"])
    blocked = []
    for key, v in (report.get("sources") or {}).items():
        label = {"ke": "贝壳", "anjuke": "安居客", "wuba": "58同城"}.get(key, key)
        if v.get("captcha"):
            blocked.append(label)
            lines.append(f"· {label}：验证码自动通关失败，本轮跳过（下一轮自动重试）")
        elif v.get("fetched"):
            lines.append(
                f"· {label}：抓到 {v['fetched']} 套，新增 {v.get('saved') or 0}，"
                f"更新 {v.get('updated') or 0}，调价 {v.get('price_changes') or 0}"
            )
        else:
            lines.append(f"· {label}：{v.get('note') or '未抓到数据'}")
    if pipeline:
        lines.append(f"当前在挂 {pipeline.get('listing_total') or 0} 套；"
                     f"黄级线索 {pipeline.get('yellow') or 0} 条，红级 {pipeline.get('red') or 0} 条")
    mkt = report.get("market") or {}
    if mkt and mkt.get("listing_n"):
        lines.append(
            f"大盘：在售 {mkt['listing_n']} 套，中位 {round(float(mkt.get('median_unit') or 0))} 元/㎡，"
            f"今日新增 {mkt.get('new_n') or 0}、下架 {mkt.get('delist_n') or 0}"
            "（下架≠成交，仅作活跃度代理）"
        )
    if blocked:
        lines.append("提示：验证码由系统自动识别处理（下一轮自动重试），无需人工操作。")
    lines.append("挂牌价≠成交价，捡漏分仅作观察参考。")

    text = "\n".join(lines)
    msg = {"title": "房源刷新完成", "text": text, "markdown": text, "score": 100}
    try:
        sender = AlertSender()
        return sender.send_message(msg, "refresh", int(time.time()), "yellow", 0, force=True)
    except Exception as exc:  # noqa: BLE001 推送失败不能影响刷新
        logger.warning("刷新摘要推送失败：%s", exc)
        return None


def check_session(key: str) -> dict:
    """会话自检：存进去的 Cookie 现在还能不能正常抓到页面。"""
    src = SOURCES.get(key)
    if not src:
        raise ValueError(f"未知来源：{key}")
    cookie = get_session_cookie(key)
    if not cookie:
        return {"key": key, "has_cookie": False, "ok": True,
                "note": "尚无保存的会话；触发验证码时系统会自动通关，无需人工操作"}
    try:
        html = _get(src["list"], src["ua"], cookie)
        return {"key": key, "has_cookie": True, "ok": bool(html),
                "bytes": len(html or ""),
                "note": "会话有效" if html else "会话可能已失效；失效后由系统自动重新通关"}
    except CaptchaBlocked:
        return {"key": key, "has_cookie": True, "ok": False,
                "note": "该会话仍被要求验证；系统将自动通关并刷新会话"}


# --------------------------------------------------------------------------
# 会话自愈：每天自检一次，失效就主动提醒（带恢复入口），不再盲目重试
# --------------------------------------------------------------------------
def session_health() -> list[dict]:
    """逐个来源自检会话，返回全部结果（供管理台/定时任务用）。"""
    out: list[dict] = []
    for key in ("ke", "anjuke", "wuba"):
        try:
            out.append(check_session(key))
        except Exception as exc:  # noqa: BLE001 单源自检异常不拖垮其它源
            out.append({"key": key, "ok": False, "has_cookie": False,
                        "note": f"自检失败：{type(exc).__name__}: {exc}"})
    return out


def push_session_alert(bad: list[dict] | None = None) -> dict | None:
    """保存过的会话失效时推送通知。

    验证码已由系统自动识别通关，此通知仅作信息同步，无需人工处理。
    """
    from ..config import settings

    bad = bad if bad is not None else session_health()
    need = [b for b in bad if b.get("has_cookie") and not b.get("ok")]
    if not need:
        return None
    labels = {"ke": "贝壳", "anjuke": "安居客", "wuba": "58同城"}
    names = "、".join(labels.get(b["key"], b["key"]) for b in need)
    lines = [
        "【会话状态通知】" + names,
        "以下来源的会话已失效；系统会自动通关验证码并刷新会话，下一轮照常抓取：",
    ]
    for b in need:
        lines.append(f"· {labels.get(b['key'], b['key'])}：{b.get('note') or '会话失效'}")
    lines.append("验证码自动识别处理中，无需人工操作。")
    text = "\n".join(lines)
    from .alert_sender import AlertSender
    try:
        return AlertSender().send_message(
            {"title": "会话状态通知", "text": text, "markdown": text, "score": 100},
            "session", int(time.time()), "yellow", 0, force=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话失效推送失败：%s", exc)
        return None


# --------------------------------------------------------------------------
# 官方公开接口：日照房管业务网（政府公示，免验证码、可全自动）
# --------------------------------------------------------------------------
GOV_API = {
    "sign": "https://www.rzfcfw.cn/second/phase/queyrHtqygs",      # 二手房签约公示
    "record": "https://www.rzfcfw.cn/second/phase/queyrEsfbaInfo",   # 二手房备案公示
}


def fetch_official_deals(kind: str = "sign", pages: int = 1) -> dict:
    """调用政府公开公示接口（无需登录/验证码，全自动）。

    如实说明：它返回的是**签约/备案公示**，价格字段为空、数据更新到 2023 年，
    不能用来更新挂牌价，但可以用于核验小区名称与历史成交时点。
    """
    import json as _json
    url = GOV_API.get(kind)
    if not url:
        raise ValueError(f"未知公示类型：{kind}")
    out: list[dict] = []
    total = None
    for page in range(1, max(1, pages) + 1):
        data = urllib.parse.urlencode({
            "htbh": "", "currentPage": page, "pageSize": 20,
        }).encode()
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": DESKTOP_UA,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.rzfcfw.cn/plugins/esfczfxx/htqygs.jsp",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = _json.loads(r.read().decode("utf-8", errors="ignore"))
            items = _json.loads(body.get("msg") or "[]")
        except Exception as exc:  # noqa: BLE001
            logger.warning("官方公示接口失败：%s", exc)
            break
        if items and total is None:
            total = items[0].get("totalCount")
        out.extend(items)
        time.sleep(INTERVAL)
    return {"ok": bool(out), "total": total, "count": len(out), "items": out[:20],
            "note": "政府公开签约/备案公示：免验证码、可全自动，但价格字段为空、数据停更于 2023 年，仅作核验参考"}
