"""部署包本体的端到端自检（对着真实跑起来的 HTTP 服务，不是 TestClient）。

用法：
    PORT=3199 ENABLE_SCHEDULER=0 python run.py          # 另一个终端，在部署包目录里跑
    python tools/verify_deploy.py --base http://127.0.0.1:3199

检查点（对应「关机后云端还能不能自动跑」这件事）：
  1. 服务能起来、健康检查通过；
  2. /api/sources 如实返回 schedule（云端调度）与 push（提醒通道）；
  3. /api/admin/status 如实返回 live_refresh；
  4. crawl=0 只重算推送、不抓外部站点；
  5. 10 分钟冷却生效，force=1 可跳过。

刻意不抓真实站点，所以可以反复跑，不会触发 58 的「访问过于频繁」验证。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.error
import urllib.request

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent


def read_env() -> dict:
    env = {}
    for candidate in (pathlib.Path.cwd() / ".env", BASE_DIR / ".env"):
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:3199")
    ap.add_argument("--env-file", default="")
    args = ap.parse_args()

    env = read_env()
    if args.env_file:
        p = pathlib.Path(args.env_file)
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")

    token = env.get("ADMIN_TOKEN", "")
    share = env.get("SITE_SHARECODE", "")
    qs = f"?sharecode={share}" if share else ""

    def call(method: str, path: str, body: dict | None = None, admin: bool = False) -> dict:
        sep = "&" if "?" in path else "?"
        url = args.base + path + (sep + f"sharecode={share}" if share else "")
        data = json.dumps(body or {}).encode() if method == "POST" else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if admin:
            req.add_header("X-Admin-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return {"HTTP_ERROR": exc.code, "body": exc.read().decode()[:300]}
        except Exception as exc:  # noqa: BLE001
            return {"ERROR": f"{type(exc).__name__}: {exc}"}

    # 等服务就绪
    health = {}
    for _ in range(40):
        health = call("GET", "/api/meta/health")
        if health.get("ok") or "scheduler" in health:
            break
        time.sleep(0.5)
    print("1) 健康检查:", json.dumps(health, ensure_ascii=False)[:300])

    sources = call("GET", "/api/sources")
    sch = sources.get("schedule", {})
    push = sources.get("push", {})
    print("2) /api/sources.schedule:", json.dumps(sch, ensure_ascii=False))
    print("   /api/sources.push:", json.dumps(push, ensure_ascii=False))

    status = call("GET", "/api/admin/status", admin=True)
    live = status.get("live_refresh", {})
    print("3) /api/admin/status.live_refresh:", json.dumps(live, ensure_ascii=False))

    # 用 wait=1 同步执行，方便立刻拿到结果；crawl=0 明确不抓外部站点
    first = call("POST", "/api/admin/refresh-sources?crawl=0&wait=1", admin=True)
    print("4) crawl=0 首次刷新:", json.dumps({
        k: first.get(k) for k in ("ok", "running", "skipped", "error")
    }, ensure_ascii=False))
    result = (first.get("result") or {})
    print("   report.note:", (result.get("refresh") or {}).get("note"))
    print("   report.sources(应为空):", (result.get("refresh") or {}).get("sources"))

    second = call("POST", "/api/admin/refresh-sources?crawl=0", admin=True)
    print("5) 冷却内再次触发:", json.dumps({
        k: second.get(k) for k in ("skipped", "cooldown_seconds", "note")
    }, ensure_ascii=False))

    forced = call("POST", "/api/admin/refresh-sources?crawl=0&force=1", admin=True)
    print("6) force=1 跳过冷却:", json.dumps({
        k: forced.get(k) for k in ("started", "crawl", "skipped", "note")
    }, ensure_ascii=False))

    # 汇总判定
    checks = {
        "健康检查通过": bool(health.get("ok") or "scheduler" in health),
        "来源页标注云端运行": bool(sch.get("runs_in_cloud") and sch.get("needs_local_pc") is False),
        "来源页展示推送通道": bool(push.get("key")),
        "状态页有 live_refresh": bool(live),
        "crawl=0 未抓外部站点": (result.get("refresh") or {}).get("sources") == {},
        "冷却生效": bool(second.get("skipped")),
        "force=1 不被冷却拦下": bool(forced.get("started")) and not forced.get("skipped"),
    }
    print("\n=== 判定 ===")
    for name, ok in checks.items():
        print(("  PASS  " if ok else "  FAIL  ") + name)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
