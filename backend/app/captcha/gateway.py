"""验证码自动通关统一入口。

优先级：DrissionPage → Playwright → 失败。
调用方（live_refresh / sources 采集）只管这一个函数，失败时拿到 ok=False
自动把该源降级跳过，不影响其它源——全程无人工参与。
"""
from __future__ import annotations

import logging

from . import auto, pw_auto

logger = logging.getLogger("rzprobe.captcha")


def auto_solve(source_key: str, target_url: str) -> dict:
    """依次尝试两套无头浏览器实现。返回 {"ok": bool, "cookies": str, "note": str}。"""
    result = auto.solve_captcha(source_key, target_url)
    if result.get("ok"):
        return result
    logger.info("[%s] DrissionPage 通关未成功（%s），尝试 Playwright",
                source_key, result.get("note"))
    result2 = pw_auto.solve_captcha(source_key, target_url)
    if result2.get("ok"):
        return result2
    return {
        "ok": False,
        "cookies": "",
        "note": f"自动通关失败：{result.get('note')} / {result2.get('note')}",
    }


def cookies_available(source_key: str) -> bool:
    """该源是否已有可用会话（供调度器决定是否先直连）。"""
    from ..services import auth
    return bool((auth.get_setting(f"session_cookies.{source_key}") or "").strip())
