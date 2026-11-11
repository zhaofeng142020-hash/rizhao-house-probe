"""滑块验证码自动通关器：Playwright 兜底实现（DrissionPage 不可用时启用）。

与 captcha.auto.py 同一套流程，用 Playwright API 实现：
  截图 → captcha.solver 识别 → Actions 拟人拖动 → 校验通过存会话。

依赖：playwright + chromium（pip install playwright && playwright install chromium）。
未安装时返回 ok=False，上层源自动降级，不影响其它源。
"""
from __future__ import annotations

import logging
import random
import time

from ..config import settings
from . import solver

logger = logging.getLogger("rzprobe.captcha.pw")

SESSION_KEYS = {"ke": "session_cookies.ke", "anjuke": "session_cookies.anjuke",
                "wuba": "session_cookies.wuba"}

BG_SELECTORS = [
    "img.geetest_item_bg",
    "img.captcha-verify-image",
    "canvas.captcha-verify-image",
    ".nc_iconfont img",
]
SLICE_SELECTORS = [
    "img.geetest_canvas_slice",
    "img.captcha_slide_img",
]
HANDLE_SELECTORS = [
    "span.geetest_slider_button",
    ".btn_slide",
    ".nc_iconfont.btn_slide",
]


def _first_visible(page, selectors, timeout_ms=2000):
    for sel in selectors:
        try:
            ele = page.locator(sel).first
            ele.wait_for(state="visible", timeout=timeout_ms)
            return ele
        except Exception:  # noqa: BLE001
            continue
    return None


def solve_captcha(source_key: str, target_url: str, max_rounds: int = 3) -> dict:
    """Playwright 版滑块自动通关。返回结构与 captcha.auto.solve_captcha 一致。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "cookies": "", "note": "playwright 未安装"}

    ok = False
    note = ""
    rounds = 0
    cookies = ""
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True,
                                            args=["--no-sandbox", "--disable-gpu"])
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "cookies": "", "note": f"chromium 启动失败：{exc}"}
            ctx = browser.new_context(
                user_agent=settings.CRAWL_UA, viewport={"width": 1280, "height": 900},
                locale="zh-CN",
            )
            page = ctx.new_page()
            try:
                page.goto(target_url, timeout=25000, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001
                browser.close()
                return {"ok": False, "cookies": "", "note": f"页面打开失败：{exc}"}
            time.sleep(random.uniform(1.5, 2.5))

            for rnd in range(1, max_rounds + 1):
                rounds = rnd
                bg = _first_visible(page, BG_SELECTORS)
                if not bg:
                    content = page.content()
                    if "ershoufang" in page.url and len(content) > 5000:
                        ok, note = True, "未触发验证码，直接访问成功"
                    else:
                        note = "页面无验证码也无有效内容"
                    break

                sl = _first_visible(page, SLICE_SELECTORS, timeout_ms=1000)
                try:
                    bg_bytes = bg.screenshot()
                    sl_bytes = sl.screenshot() if sl else None
                except Exception as exc:  # noqa: BLE001
                    note = f"截图失败：{exc}"
                    break
                try:
                    dist = solver.solve_slider(bg_bytes, sl_bytes)
                except RuntimeError as exc:
                    note = f"缺口识别失败：{exc}"
                    break

                handle = _first_visible(page, HANDLE_SELECTORS, timeout_ms=1500)
                if not handle:
                    note = "未找到滑块手柄"
                    break
                box = handle.bounding_box()
                if not box:
                    note = "手柄无坐标"
                    break
                track = solver.human_track(dist)
                sx = box["x"] + box["width"] / 2
                sy = box["y"] + box["height"] / 2
                try:
                    page.mouse.move(sx, sy)
                    page.mouse.down()
                    cur_x = sx
                    for dx, dy, delay in track:
                        cur_x += dx
                        page.mouse.move(cur_x, sy + dy, steps=1)
                        time.sleep(delay / 1000.0 * random.uniform(0.7, 1.3))
                    page.mouse.up()
                except Exception as exc:  # noqa: BLE001
                    note = f"拖动失败：{exc}"
                    break

                time.sleep(random.uniform(1.8, 3.0))
                if not _first_visible(page, BG_SELECTORS, timeout_ms=1500):
                    ok, note = True, f"第 {rnd} 轮滑块通过"
                    break
                try:
                    page.locator(".geetest_panel_error_content a").first.click(timeout=1500)
                    time.sleep(1)
                except Exception:  # noqa: BLE001
                    pass

            if ok:
                try:
                    pairs = ctx.cookies()
                    cookies = "; ".join(f"{c['name']}={c['value']}" for c in pairs)
                except Exception:  # noqa: BLE001
                    cookies = ""
            browser.close()
    except Exception as exc:  # noqa: BLE001  整体兜底：绝不让验证码模块拖垮抓取主流程
        logger.warning("playwright 通关异常：%s", exc, exc_info=True)
        note = note or f"异常中断：{type(exc).__name__}: {exc}"

    result = {"ok": ok, "cookies": cookies, "rounds": rounds, "note": note}
    if ok and cookies:
        _save_session(source_key, cookies)
    logger.info("[%s] playwright 通关：%s（%d 轮）%s", source_key, ok, rounds, note)
    return result


def _save_session(source_key: str, cookie: str) -> None:
    try:
        from ..services import auth
        auth.set_setting(SESSION_KEYS.get(source_key, f"session_cookies.{source_key}"),
                         cookie, is_secret=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话保存失败：%s", exc)
