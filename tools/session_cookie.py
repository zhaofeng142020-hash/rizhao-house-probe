"""会话 Cookie 工具：你自己过一次验证码，把会话交给程序复用。

为什么这样设计：
58 同城弹人工滑块、安居客翻页多了弹极验。**识别/破解验证码不做**（既违反
站点技术保护，也违反本项目采集器的合规红线）。合法且稳定的做法是：
你自己在浏览器里完成人机验证，程序复用你的会话 Cookie 去抓公开列表页。

用法：
    # 1) 打开浏览器，你手动过验证码；过完回到终端按回车
    python tools/session_cookie.py --site anjuke
    python tools/session_cookie.py --site wuba --dump

    # 默认保存到本地 127.0.0.1:8765；要存到线上就加 --base/--sharecode
    python tools/session_cookie.py --site wuba --base https://rizhao-house-radar.app.workbuddy.host --sharecode XXX

--dump 会把抓到的列表页存到 crawl_probe/<site>_list.html，方便校准解析器。
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROBE_DIR = BASE_DIR.parent / "crawl_probe"

SITES = {
    "anjuke": {
        "name": "安居客 · 日照二手房",
        "url": "https://m.anjuke.com/rz/sale/",
        "domains": (".anjuke.com", "anjuke.com"),
        "ua": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
    },
    "wuba": {
        "name": "58同城 · 日照二手房",
        "url": "https://rizhao.58.com/ershoufang/",
        "domains": (".58.com", "58.com"),
        "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    },
    "ke": {
        "name": "贝壳找房 · 日照二手房",
        "url": "https://rz.ke.com/ershoufang/",
        "domains": (".ke.com", "ke.com"),
        "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    },
}

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("没找到 Chrome / Edge，请先安装其中一个浏览器")


# ---------------------------------------------------------------------------
# Windows Chrome Cookie 解密（DPAPI + AES-GCM）
# ---------------------------------------------------------------------------
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise OSError("CryptUnprotectData 失败")
    out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return out


def aes_key(profile: Path) -> bytes:
    state = json.loads((profile / "Local State").read_text(encoding="utf-8"))
    enc = base64.b64decode(state["os_crypt"]["encrypted_key"])
    if enc[:5] == b"DPAPI":
        enc = enc[5:]
    return dpapi_unprotect(enc)


def decrypt_value(val: bytes, key: bytes, host_key: str = "") -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise SystemExit("缺少 cryptography：请先运行 "
                         "`.venv/Scripts/python.exe -m pip install cryptography`")
    if val[:3] in (b"v10", b"v11"):
        import hashlib
        nonce, payload = val[3:15], val[15:]
        pt = AESGCM(key).decrypt(nonce, payload, None)
        # Chrome 库版本 >= 24：明文前 32 字节是 host_key 的 SHA256 摘要，要剥掉
        if host_key and len(pt) > 32:
            digest = hashlib.sha256(host_key.encode("utf-8")).digest()
            if pt[:32] == digest:
                pt = pt[32:]
        return pt.decode("utf-8", "ignore")
    if val[:3] == b"v20":
        # Chrome 127+ 的应用绑定加密，密钥绑定到浏览器本体，无法在此解密
        raise ValueError("v20")
    return val.decode("utf-8", "ignore")


def read_cookies(profile: Path, domains: tuple[str, ...]) -> tuple[str, list[str]]:
    """从 Chrome 配置目录读 Cookie，返回 (Cookie 头字符串, 说明)。"""
    candidates = [profile / "Default" / "Network" / "Cookies", profile / "Default" / "Cookies",
                  profile / "Network" / "Cookies", profile / "Cookies"]
    db = next((p for p in candidates if p.exists()), None)
    if not db:
        raise SystemExit(f"没找到 Cookie 数据库（{profile}）。请确认浏览器确实打开过站点。")

    tmp = Path(tempfile.gettempdir()) / f"cookies_{int(time.time())}.db"
    shutil.copy2(db, tmp)
    key = aes_key(profile)

    pairs: list[str] = []
    notes: list[str] = []
    v20 = 0
    conn = sqlite3.connect(tmp)
    try:
        rows = conn.execute(
            "SELECT host_key, name, encrypted_value, value FROM cookies"
        ).fetchall()
    finally:
        conn.close()          # 必须先关连接，否则 Windows 上删不掉临时库
    for host, name, enc, plain in rows:
        if not any(host.endswith(d) for d in domains):
            continue
        value = plain or ""
        if not value and enc:
            try:
                value = decrypt_value(enc, key, host)
            except ValueError:
                v20 += 1
                continue
            except Exception:  # noqa: BLE001
                continue
        if value:
            pairs.append(f"{name}={value}")
    try:
        tmp.unlink()
    except OSError:
        pass                  # 删不掉也不影响结果

    if v20:
        notes.append(f"{v20} 个 Cookie 是 Chrome 新版应用绑定加密（v20），需要手工粘贴")
    pairs.sort()
    return "; ".join(pairs), notes


def post_cookie(base: str, token: str, site: str, cookie: str, sharecode: str | None) -> dict:
    sep = "&" if "?" in f"/api/admin/sources/{site}/cookies" else "?"
    url = f"{base}/api/admin/sources/{site}/cookies" + (f"{sep}sharecode={sharecode}" if sharecode else "")
    req = urllib.request.Request(
        url, method="PUT", data=json.dumps({"cookie": cookie}).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Token": token,
                 "User-Agent": "rz-session/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def dump_list(site: str, cookie: str) -> None:
    """带会话抓一次列表页，存下来供解析器校准。"""
    cfg = SITES[site]
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(cfg["url"], headers={
        "User-Agent": cfg["ua"], "Cookie": cookie, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=25) as r:
        html = r.read().decode("utf-8", errors="ignore")
        final = r.geturl()
    out = PROBE_DIR / f"{site}_list.html"
    out.write_text(html, encoding="utf-8")
    print(f"  已保存 {len(html)} 字节 -> {out}")
    print(f"  最终地址: {final[:120]}")
    if re.search(r"captcha|verifycode|antibot|验证", final, re.I):
        print("  ⚠ 仍被要求验证：说明这次会话没生效，请在浏览器里确认已经能正常看到房源列表")
    else:
        print("  ✅ 会话已生效（未被要求验证）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, choices=sorted(SITES))
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--token", default=None, help="管理员口令；不填则从 .env 读")
    ap.add_argument("--sharecode", default=None)
    ap.add_argument("--dump", action="store_true", help="抓一次列表页存下来便于校准解析器")
    ap.add_argument("--paste", action="store_true", help="不用浏览器，直接粘贴 Cookie 字符串")
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()

    cfg = SITES[args.site]
    token = args.token
    if not token:
        env = BASE_DIR / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("ADMIN_TOKEN="):
                    token = line.split("=", 1)[1].strip()
    if not token:
        raise SystemExit("需要管理员口令：请用 --token 传入，或确保 .env 里有 ADMIN_TOKEN")

    if args.paste:
        print(f"请在浏览器打开 {cfg['url']}，过完验证后按 F12 → Network → 任意请求 →\n"
              f"把请求头里的 Cookie 整行粘贴到下面（回车结束）：")
        cookie = input().strip()
    else:
        browser = find_browser()
        profile = Path(args.profile) if args.profile else (BASE_DIR / "data" / f"chrome-profile-{args.site}")
        profile.mkdir(parents=True, exist_ok=True)
        print(f"即将打开浏览器（独立配置目录：{profile}）")
        print(f"请在弹出的窗口里打开 {cfg['url']}")
        print("如果出现验证码/滑块，请手动完成；能看到房源列表后，回到这里按回车。\n")
        proc = subprocess.Popen([
            browser, f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-features=Translate",
            cfg["url"],
        ])
        input("完成验证、能看到房源列表后按回车…")
        time.sleep(1)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        time.sleep(2)          # 等 Chrome 把 Cookie 落盘
        cookie, notes = read_cookies(profile, cfg["domains"])
        for n in notes:
            print("  提示:", n)
        if not cookie:
            raise SystemExit("没读到 Cookie。可改用 --paste 手工粘贴。")

    print(f"读到 Cookie {len(cookie)} 字符，前 60：{cookie[:60]}…")
    res = post_cookie(args.base, token, args.site, cookie, args.sharecode)
    print("已保存到服务器：", res)

    if args.dump:
        print("带会话抓列表页…")
        dump_list(args.site, cookie)


if __name__ == "__main__":
    main()
