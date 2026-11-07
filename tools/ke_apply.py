"""把真实数据落到数据库（tools/ke_apply.py）。

对目标站点（本地或线上）依次执行：
  1. 清除全部演示样本（purge-demo）
  2. 导入贝壳真实挂牌（带原始链接，source_kind=crawled）
  3. 补全小区档案（坐标 / 板块 / 建成年，来自贝壳小区页）
  4. 重算评分（run-pipeline）
  5. 打印核验摘要

用法：
    python tools/ke_apply.py --base http://127.0.0.1:8765 --token <口令>
    # 导入其它来源（如安居客）：
    python tools/ke_apply.py --base ... --token ... --source-key anjuke \
        --source-name "安居客 · 日照二手房" --rows-file data/aj_ershoufang.json --no-comm --skip-purge
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ROWS_FILE = BASE_DIR / "data" / "ke_ershoufang.json"
COMM_FILE = BASE_DIR / "data" / "ke_communities.json"


def call(base: str, path: str, token: str, payload=None, method: str = "POST",
         sharecode: str | None = None, timeout: int = 120) -> dict:
    sep = "&" if "?" in path else "?"
    url = base + path + (f"{sep}sharecode={sharecode}" if sharecode else "")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        "X-Admin-Token": token,
        "User-Agent": "rz-probe-apply/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:300]
        raise SystemExit(f"[失败] {method} {path} -> HTTP {e.code}: {body}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--sharecode", default=None, help="线上网关需要 sharecode 参数")
    ap.add_argument("--skip-purge", action="store_true")
    ap.add_argument("--no-comm", action="store_true", help="跳过小区档案补全（无坐标数据源用）")
    ap.add_argument("--source-key", default="ke")
    ap.add_argument("--source-name", default="贝壳找房 · 日照二手房")
    ap.add_argument("--rows-file", default=None, help="默认 data/ke_ershoufang.json")
    ap.add_argument("--comm-file", default=None, help="默认 data/ke_communities.json")
    args = ap.parse_args()

    kw = {"base": args.base, "token": args.token, "sharecode": args.sharecode}

    # 1) 清演示样本
    if not args.skip_purge:
        r = call(args.base, "/api/admin/purge-demo", args.token, {}, sharecode=args.sharecode)
        print("[1/4] 清除演示样本:", r["purged"], "剩余:", r["left"])
        time.sleep(1)

    # 2) 补小区档案（先建小区并带上坐标/板块，房源导入时才能挂对）
    comm_path = Path(args.comm_file) if args.comm_file else COMM_FILE
    comms = json.loads(comm_path.read_text(encoding="utf-8")) if (not args.no_comm and comm_path.exists()) else []
    name2plate = {c["name"]: (c.get("plate") or c.get("region")) for c in comms}
    if comms:
        payload = [{
            "name": c["name"],
            "district": c.get("plate") or None,
            "lat": c.get("lat"), "lng": c.get("lng"),
            "build_year": c.get("build_year"), "xiaoqu_id": c.get("xiaoqu_id"),
        } for c in comms]
        r = call(args.base, "/api/admin/communities/upsert", args.token,
                 {"rows": payload}, sharecode=args.sharecode)
        print("[2/4] 小区档案: 更新", r["updated"], "/ 新建", r["created"],
              "/ 跳过", r["skipped"], "/ 有坐标小区", r["communities_with_geo"])
    else:
        print("[2/4] 跳过小区档案补全（无档案文件）")

    # 3) 导入真实挂牌（板块优先用数据自带 plate，其次小区档案，最后为空）
    rows_path = Path(args.rows_file) if args.rows_file else ROWS_FILE
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    for r_ in rows:
        r_["district"] = r_.get("plate") or name2plate.get(r_["community_name"]) or r_.get("district") or ""
        for k in ("xiaoqu_id", "unit_price_hint", "house_raw", "plate"):
            r_.pop(k, None)
    r = call(args.base, "/api/admin/sources/import", args.token, {
        "rows": rows, "source_key": args.source_key,
        "source_name": args.source_name, "source_kind": "crawled",
    }, sharecode=args.sharecode)
    print(f"[3/4] 导入真实挂牌: 收到 {r['items_in']} / 入库 {r['items_saved']} / 跳过 {r['items_skipped']}")
    if r.get("errors"):
        for e in r["errors"][:5]:
            print("   第", e["row"], "行:", e["reason"])

    # 4) 重算评分
    r = call(args.base, "/api/alerts/run-pipeline", args.token, {}, sharecode=args.sharecode)
    s = r.get("summary") or r
    print("[4/4] 重算完成:", {k: s.get(k) for k in
          ("listing_total", "scored", "events", "red", "yellow", "community_snapshots")})

    # 核验
    def get(path: str) -> dict:
        sep = "&" if "?" in path else "?"
        url = args.base + path + (f"{sep}sharecode={args.sharecode}" if args.sharecode else "")
        with urllib.request.urlopen(urllib.request.Request(url, headers={
                "User-Agent": "rz-probe-apply/1.0"}), timeout=60) as resp:
            return json.loads(resp.read())

    ov = get("/api/sources")
    print("\n=== 核验 ===")
    print("来源总览: total=%s demo=%s real=%s crawled=%s" %
          (ov["total"], ov["demo"], ov["real"], ov["crawled"]))
    print("口径:", ov["honesty_note"])
    cfg = get("/api/config")
    print("demo_only =", cfg.get("demo_only"))
    ke = next((x for x in ov["sources"] if x["key"] == "ke"), None)
    print("贝壳通道: verified=%s real_count=%s" % (ke and ke["verified"], ke and ke["real_count"]))


if __name__ == "__main__":
    main()
