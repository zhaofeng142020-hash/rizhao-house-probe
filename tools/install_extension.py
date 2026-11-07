"""在本机 Chrome 上安装并配置「日照房价探针 · 会话桥」扩展。

为什么用独立的用户配置目录：
- 用户日常 Chrome 正在运行，且新版 Chrome（136+）禁止对默认配置目录开启远程调试；
- 关闭用户浏览器会打断他正在进行的页面（当前是一个登录回调）；
- 独立配置不影响日常浏览、完全可逆，扩展仍真实安装在 chrome://extensions 里。

流程：启动带调试端口的专用 Chrome → CDP Extensions.loadUnpacked 安装 →
      CDP 打开 options 页写入配置 → 验证 → 创建桌面快捷方式。

用法：
  python tools/install_extension.py --sharecode XXX [--token YYY]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

BASE_DIR = Path(__file__).resolve().parent.parent
EXT_DIR = BASE_DIR / "tools" / "bridge-extension"
PROFILE = Path(os.environ["LOCALAPPDATA"]) / "RizhaoProbe" / "ChromeProfile"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PORT = 9224
SITE = "https://rizhao-house-radar.app.workbuddy.host"


def http_json(url: str, timeout: int = 10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=20,
                                              origin=f"http://127.0.0.1:{PORT}")
        self.i = 0

    def call(self, method: str, params: dict | None = None, session: str | None = None):
        self.i += 1
        msg = {"id": self.i, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.i:
                return r

    def close(self):
        self.ws.close()


def browser_ws() -> str | None:
    try:
        return http_json(f"http://127.0.0.1:{PORT}/json/version")["webSocketDebuggerUrl"]
    except Exception:
        return None


def start_chrome() -> None:
    if browser_ws():
        print("[1/5] 专用 Chrome 已在运行，复用调试端口")
        return
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [
        str(CHROME),
        f"--user-data-dir={PROFILE}",
        f"--remote-debugging-port={PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=ChromeWhatsNewUI",
        "chrome://extensions/",
    ]
    print("[1/5] 启动专用 Chrome…")
    subprocess.Popen(args, close_fds=True)
    for _ in range(25):
        time.sleep(1)
        if browser_ws():
            print("      调试端口就绪")
            return
    raise SystemExit("Chrome 调试端口未就绪，安装中止")


def find_installed_id() -> str | None:
    """已安装时从配置里按扩展名找 ID（不要猜 targets——会拿到 Chrome 内置扩展）。"""
    for fn in ("Secure Preferences", "Preferences"):
        p = PROFILE / "Default" / fn
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for k, v in (d.get("extensions", {}).get("settings", {}) or {}).items():
            name = (v.get("manifest") or {}).get("name", "")
            path = v.get("path", "") or ""
            # Secure Preferences 里可能不存 manifest.name，按安装路径匹配最稳
            if "会话桥" in name or "bridge-extension" in path.replace("\\", "/"):
                return k
    return None


def install(cdp: CDP) -> str:
    existing = find_installed_id()
    if existing:
        print("[2/5] 扩展已安装，ID =", existing)
        return existing
    r = cdp.call("Extensions.loadUnpacked", {"path": str(EXT_DIR)})
    if "result" in r:
        eid = r["result"]["id"]
        print("[2/5] 扩展已安装，ID =", eid)
        return eid
    print("[2/5] loadUnpacked 返回：", r.get("error", {}).get("message", "")[:120])
    time.sleep(2)
    existing = find_installed_id()
    if existing:
        print("      从配置确认 ID =", existing)
        return existing
    raise SystemExit("无法确认扩展 ID")


def open_target(cdp: CDP, url: str) -> str:
    r = cdp.call("Target.createTarget", {"url": url})
    if "error" in r:
        raise SystemExit(f"打开页面失败：{r['error']}")
    tid = r["result"]["targetId"]
    # 找到该 target 的 page session；用 attach + flatten 更稳
    a = cdp.call("Target.attachToTarget", {"targetId": tid, "flatten": True})
    if "error" in a:
        raise SystemExit(f"附加页面失败：{a['error']}")
    return a["result"]["sessionId"]


def configure(cdp: CDP, ext_id: str, sharecode: str, token: str) -> None:
    url = f"chrome-extension://{ext_id}/options.html"
    session = open_target(cdp, url)
    # 等扩展页真正就绪（chrome.storage 可用），否则会在错误页上执行脚本
    for _ in range(15):
        chk = cdp.call("Runtime.evaluate", {
            "expression": "typeof chrome !== 'undefined' && !!chrome.storage && !!chrome.storage.local",
        }, session)
        if (chk.get("result", {}).get("result", {}) or {}).get("value") is True:
            break
        time.sleep(1)
    else:
        raise SystemExit("扩展页面未就绪：chrome.storage 不可用（扩展 ID 可能不对）")
    payload = {
        "base": SITE, "sharecode": sharecode, "token": token, "auto": True,
    }
    expr = "chrome.storage.local.set(%s); 'written'" % json.dumps(payload, ensure_ascii=False)
    cdp.call("Runtime.enable", {}, session)
    r = cdp.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True}, session)
    if "error" in r or (r.get("result", {}).get("result", {}).get("value") != "written"):
        print("[3/5] 写配置返回：", json.dumps(r, ensure_ascii=False)[:300])
    else:
        print("[3/5] 配置已写入：站点 / 分享码 / 管理口令 / 自动同步")

    # 读回校验
    check = cdp.call("Runtime.evaluate", {
        "expression": "new Promise(r => chrome.storage.local.get(['base','sharecode','token','auto'], d => "
                      "r(JSON.stringify({base:d.base, hasShare:!!d.sharecode, hasToken:!!d.token, auto:d.auto}))))",
        "awaitPromise": True,
    }, session)
    val = (check.get("result", {}).get("result", {}) or {}).get("value")
    print("[4/5] 配置校验：", val)

    # 触发一次同步，确认服务端接口通
    sync = cdp.call("Runtime.evaluate", {
        "expression": "new Promise(r => chrome.runtime.sendMessage({type:'sync'}, x => r(JSON.stringify(x))))",
        "awaitPromise": True,
    }, session)
    sval = (sync.get("result", {}).get("result", {}) or {}).get("value")
    print("      同步试探：", (sval or "")[:260])


def make_shortcut() -> Path:
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
    if not desktop.exists():
        desktop = Path(os.environ["PUBLIC"]) / "Desktop"
    lnk = desktop / "日照房价探针.lnk"
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(str(lnk))
        # 指向启动器：每次启动自动确保扩展启用、配置完好（Chrome 会禁用未打包扩展）
        pyw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
        launcher = BASE_DIR / "tools" / "start_bridge_chrome.py"
        sc.TargetPath = str(pyw if pyw.exists() else sys.executable)
        sc.Arguments = f'"{launcher}"'
        sc.WorkingDirectory = str(BASE_DIR)
        sc.IconLocation = str(CHROME) + ",0"
        sc.Description = "日照房价探针：启动浏览器并自动启用会话桥扩展"
        sc.Save()
        print("[5/5] 桌面快捷方式：", lnk)
    except ImportError:
        # 没有 pywin32 时生成一个 .cmd 启动器（英文文件名，避免编码问题）
        cmd = desktop / "RizhaoHouseProbe.cmd"
        cmd.write_text(
            '@echo off\r\nstart "" "%s" --user-data-dir="%s" --no-first-run\r\n'
            % (CHROME, PROFILE), encoding="ascii")
        print("[5/5] 桌面启动器（未装 pywin32）：", cmd)
        return cmd
    return lnk


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sharecode", required=True)
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    token = args.token
    if not token:
        env = BASE_DIR / ".env"
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ADMIN_TOKEN="):
                token = line.split("=", 1)[1].strip()
    if not token:
        raise SystemExit("需要管理口令：请用 --token 传入，或确保 .env 里有 ADMIN_TOKEN")

    if not CHROME.exists():
        raise SystemExit(f"没找到 Chrome：{CHROME}")
    if not (EXT_DIR / "manifest.json").exists():
        raise SystemExit(f"扩展目录不完整：{EXT_DIR}")

    start_chrome()
    cdp = CDP(browser_ws())
    try:
        ext_id = install(cdp)
        configure(cdp, ext_id, args.sharecode, token)
    finally:
        cdp.close()
    make_shortcut()
    print("\n完成。现在可以打开 58 日照二手房，手动过一次验证。")


if __name__ == "__main__":
    main()
