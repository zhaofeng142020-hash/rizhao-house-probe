"""滑块验证码自动通关器：DrissionPage 实现验证码页 → 自动拖动 → 会话留存。

全程无人工参与：
  1. 无头浏览器打开目标站触发验证码页
  2. 截取滑块/背景图 → captcha.solver 本地识别缺口
  3. 生成拟人轨迹拖动 → 判定通过 → 保存会话 Cookie
  4. 失败自动刷新重试（默认 3 次），仍失败记日志并走源降级

依赖：DrissionPage（pip install DrissionPage）。未安装时 solve_captcha 返回 False。
"""
from __future__ import annotations

import logging
import random
import time

from ..config import settings
from . import solver

logger = logging.getLogger("rzprobe.captcha.auto")

SESSION_KEYS = {"ke": "session_cookies.ke", "anjuke": "session_cookies.anjuke",
                "wuba": "session_cookies.wuba"}


def _browser():
    """创建无头浏览器。失败返回 None（环境不支持时自动降级为直连抓取）。"""
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
    except ImportError:
        logger.info("DrissionPage 未安装，验证码自动通关不可用")
        return None
    co = ChromiumOptions()
    co.headless()
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-gpu")
    co.set_argument("--window-size=1280,900")
    if "Chrome" in settings.CRAWL_UA:
        co.set_user_agent(settings.CRAWL_UA)
    try:
        page = ChromiumPage(co)
        return page
    except Exception as exc:  # noqa: BLE001
        logger.warning("无头浏览器启动失败：%s", exc)
        return None


def _find_slider_elements(page):
    """在当前页面定位滑块验证码组件（兼容极验/顶象/阿里云盾常见 DOM）。"""
    bg = page.ele("css:img.geetest_item_bg", timeout=2) \
        or page.ele("css:img.captcha-verify-image", timeout=2) \
        or page.ele("css:.nc_iconfont img", timeout=2) \
        or page.ele("css:canvas.captcha-verify-image", timeout=2)
    if not bg:
        return None
    slider = page.ele("css:img.geetest_canvas_slice", timeout=2) \
        or page.ele("css:img.captcha_slide_img", timeout=2) \
        or page.ele("css:.btn_slide", timeout=2)
    handle = page.ele("css:span.geetest_slider_button", timeout=1) \
        or page.ele("css:.btn_slide", timeout=2) \
        or page.ele("css:.nc-lang-cnt", timeout=1) \
        or slider
    return {"bg": bg, "slider": slider, "handle": handle}


def _shoot(page, ele, rect_only=True) -> bytes | None:
    """元素截图 → PNG bytes。"""
    try:
        data = ele.get_screenshot(as_bytes="png")
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("截图失败：%s", exc)
        return None


def solve_captcha(source_key: str, target_url: str, max_rounds: int = 3) -> dict:
    """打开 target_url，若出现滑块验证码则自动通关，返回结果与会话。

    返回 {"ok": bool, "cookies": str, "rounds": int, "note": str}
    """
    page = _browser()
    if page is None:
        return {"ok": False, "cookies": "", "note": "DrissionPage 不可用，无法自动通关验证码"}

    ok = False
    note = ""
    rounds = 0
    try:
        page.get(target_url, timeout=20)
        time.sleep(random.uniform(1.5, 2.5))

        for rnd in range(1, max_rounds + 1):
            rounds = rnd
            eles = _find_slider_elements(page)
            if not eles:
                # 没出现验证码 → 页面本身就是列表页，直接视为通过
                if "ershoufang" in (page.url or "") and page.html and len(page.html) > 5000:
                    ok = True
                    note = "未触发验证码，直接访问成功"
                else:
                    note = "页面未加载出有效内容"
                break

            bg_bytes = _shoot(page, eles["bg"])
            sl_bytes = _shoot(page, eles["slider"]) if eles.get("slider") else None
            if not bg_bytes:
                note = "验证码截图失败"
                break
            try:
                dist = solver.solve_slider(bg_bytes, sl_bytes)
            except RuntimeError as exc:
                note = f"缺口识别失败：{exc}"
                break

            track = solver.human_track(dist)
            try:
                from DrissionPage.common import Actions  # noqa: PLC0415
                act = Actions(page)
                handle = eles.get("handle") or eles["bg"]
                act.move_to(handle)
                act.hold()
                for dx, dy, delay in track:
                    act.move(offset_x=dx, offset_y=dy, duration=delay / 1000.0)
                    time.sleep(delay / 1000.0 * random.uniform(0.7, 1.3))
                act.release()
            except Exception as exc:  # noqa: BLE001
                note = f"拖动执行失败：{exc}"
                break

            # 等待校验结果：通过后验证码浮层消失
            time.sleep(random.uniform(1.8, 3.0))
            if not _find_slider_elements(page):
                ok = True
                note = f"第 {rnd} 轮滑块通过"
                break
            # 没过 → 刷新验证码再试
            try:
                refresh = page.ele("css:.geetest_panel_error_content a") \
                    or page.ele("text:刷新", timeout=1)
                if refresh:
                    refresh.click()
                    time.sleep(1)
            except Exception:  # noqa: BLE001
                pass
        cookies = ""
        if ok:
            try:
                pairs = page.cookies(as_dict=True)
                cookies = "; ".join(f"{k}={v}" for k, v in pairs.items())
            except Exception:  # noqa: BLE001
                cookies = ""
    finally:
        try:
            page.quit()
        except Exception:  # noqa: BLE001
            pass

    result = {"ok": ok, "cookies": cookies, "rounds": rounds, "note": note}
    if ok and cookies:
        _save_session(source_key, cookies)
    logger.info("[%s] 验证码自动通关：%s（%d 轮）%s", source_key, ok, rounds, note)
    return result


def _save_session(source_key: str, cookie: str) -> None:
    try:
        from ..services import auth
        auth.set_setting(SESSION_KEYS.get(source_key, f"session_cookies.{source_key}"),
                         cookie, is_secret=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话保存失败：%s", exc)
