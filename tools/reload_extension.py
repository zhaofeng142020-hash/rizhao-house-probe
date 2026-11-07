"""重载本机专用 Chrome 里的「会话桥」扩展（保留配置与会话）。

改了 background.js / content58.js 后跑一次即可，不用卸载重装。
用法：.venv/Scripts/python.exe tools/reload_extension.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_extension import CDP, browser_ws  # noqa: E402

EXT_ID = "mngccpgbjgjepadljclhojkonjonknfm"


def main() -> None:
    cdp = CDP(browser_ws())
    r = cdp.call("Target.createTarget", {"url": "chrome://extensions/"})
    tid = r.get("result", {}).get("targetId")
    if not tid:
        raise SystemExit("无法打开 chrome://extensions 页面")
    a = cdp.call("Target.attachToTarget", {"targetId": tid, "flatten": True})
    sid = a["result"]["sessionId"]
    cdp.call("Runtime.enable", {}, sid)
    time.sleep(1.5)
    expr = """
        chrome.developerPrivate.reload('%s', {failQuietly:true})
          .then(() => 'reloaded', e => 'ERR:' + String(e))
    """ % EXT_ID
    res = cdp.call("Runtime.evaluate", {
        "expression": expr, "awaitPromise": True, "returnByValue": True,
    }, sid)
    value = res.get("result", {}).get("result", {}).get("value")
    print("扩展重载结果：", value)
    cdp.close()
    time.sleep(2)

    import urllib.request
    ts = json.load(urllib.request.urlopen("http://127.0.0.1:9224/json/list", timeout=5))
    sw = [t["url"] for t in ts if t["type"] == "service_worker"]
    print("后台 Service Worker：", sw or "（未立即出现，稍后会自动唤醒）")


if __name__ == "__main__":
    main()
