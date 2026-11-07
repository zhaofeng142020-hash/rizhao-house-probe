"""桌面快捷方式的启动器：保证「会话桥」扩展每次都在启用状态。

Chrome 重启时会把通过调试协议安装的未打包扩展标为禁用，所以每次从这个
快捷方式启动时都做一次自检：没运行就启动专用 Chrome，扩展被禁用就重新激活，
并把配置写回去。用户不需要做任何操作。

由 pythonw.exe 调用，无黑窗口：
  .venv\\Scripts\\pythonw.exe tools\\start_bridge_chrome.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "tools"))

from install_extension import (  # noqa: E402
    CDP, PORT, PROFILE, CHROME, EXT_DIR, SITE, browser_ws, http_json,
)

WUBA_URL = "https://rizhao.58.com/ershoufang/"


def env_value(key: str) -> str:
    p = BASE_DIR / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return ""


def launch() -> None:
    if browser_ws():
        return
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        str(CHROME),
        f"--user-data-dir={PROFILE}",
        f"--remote-debugging-port={PORT}",
        "--no-first-run", "--no-default-browser-check",
        WUBA_URL,
    ], close_fds=True)
    for _ in range(30):
        time.sleep(1)
        if browser_ws():
            return
    raise SystemExit("Chrome 启动超时")


def extension_alive(cdp: CDP) -> str | None:
    try:
        targets = http_json(f"http://127.0.0.1:{PORT}/json/list")
    except Exception:
        return None
    for t in targets:
        u = t.get("url", "")
        if u.startswith("chrome-extension://") and "/background.js" in u:
            return u.split("/")[2]
    return None


def ensure_extension(cdp: CDP) -> str:
    if extension_alive(cdp):
        return extension_alive(cdp)

    # 开启开发者模式（WebUI 内部 API），再卸载重装以清除 disable_reasons
    r = cdp.call("Target.createTarget", {"url": "chrome://extensions/"})
    if "result" in r:
        a = cdp.call("Target.attachToTarget",
                     {"targetId": r["result"]["targetId"], "flatten": True})
        sid = a.get("result", {}).get("sessionId")
        if sid:
            cdp.call("Runtime.enable", {}, sid)
            time.sleep(1)
            cdp.call("Runtime.evaluate", {
                "expression": "chrome.developerPrivate && "
                              "chrome.developerPrivate.updateProfileConfiguration({inDeveloperMode:true}); 'on'",
            }, sid)
    time.sleep(1)

    existing = None
    for fn in ("Secure Preferences", "Preferences"):
        p = PROFILE / "Default" / fn
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for k, v in (d.get("extensions", {}).get("settings", {}) or {}).items():
            if "bridge-extension" in (v.get("path") or "").replace("\\", "/"):
                existing = k
    if existing:
        cdp.call("Extensions.uninstall", {"id": existing})
        time.sleep(1)
    r = cdp.call("Extensions.loadUnpacked", {"path": str(EXT_DIR)})
    if "result" not in r:
        raise SystemExit("扩展激活失败：" + str(r.get("error", ""))[:160])
    eid = r["result"]["id"]
    for _ in range(10):
        time.sleep(1)
        if extension_alive(cdp):
            break
    return eid


def write_config(cdp: CDP, eid: str) -> None:
    r = cdp.call("Target.createTarget", {"url": f"chrome-extension://{eid}/options.html"})
    a = cdp.call("Target.attachToTarget", {"targetId": r["result"]["targetId"], "flatten": True})
    sid = a["result"]["sessionId"]
    cdp.call("Runtime.enable", {}, sid)
    for _ in range(12):
        chk = cdp.call("Runtime.evaluate", {
            "expression": "typeof chrome !== 'undefined' && !!(chrome.storage && chrome.storage.local)",
        }, sid)
        if (chk.get("result", {}).get("result", {}) or {}).get("value") is True:
            break
        time.sleep(1)
    payload = {
        "base": SITE,
        "sharecode": env_value("SITE_SHARECODE"),
        "token": env_value("ADMIN_TOKEN"),
        "auto": True,
    }
    cdp.call("Runtime.evaluate", {
        "expression": "chrome.storage.local.set(%s); 'ok'" % json.dumps(payload, ensure_ascii=False),
    }, sid)


def main() -> None:
    launch()
    cdp = CDP(browser_ws())
    try:
        eid = ensure_extension(cdp)
        write_config(cdp, eid)
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
