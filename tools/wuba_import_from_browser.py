"""把专用浏览器里已经正常显示的 58 二手房列表，精确提取并导入站点。

只读用户已经看到、页面已经渲染出来的公开字段；不点击、不请求额外接口、
不识别或绕过任何验证码、不采集经纪人姓名/评分/联系方式等隐私字段。

用法：
  .venv/Scripts/python.exe tools/wuba_import_from_browser.py            # 导入线上
  .venv/Scripts/python.exe tools/wuba_import_from_browser.py --dry-run  # 只提取不导入
  .venv/Scripts/python.exe tools/wuba_import_from_browser.py --base http://127.0.0.1:3000
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.request
import urllib.error

import websocket

ROOT = pathlib.Path(__file__).resolve().parents[1]
CDP = "http://127.0.0.1:9224"

# 58 的列表卡片 class 是稳定的（Vue SSR 输出），按类名字段取比「向上找父节点猜文本」可靠得多。
# 这样不会出现把「莒县城」当成小区名、把标题整段当成 title 之类的错数据。
EXTRACT_JS = r"""
(() => {
  const txt = el => el ? (el.innerText || '').replace(/\s+/g, ' ').trim() : '';
  const cards = [...document.querySelectorAll('div.property')];
  const rows = [];
  const rejected = [];
  for (const card of cards) {
    const a = card.querySelector('a.property-ex[href]');
    if (!a) { rejected.push('无详情链接'); continue; }
    const url = a.href.split('#')[0].split('?')[0];
    if (!/\/ershoufang\/\d{8,}x?\.shtml$/.test(url)) { rejected.push('链接形态不符'); continue; }
    const ref = (url.match(/\/(\d{8,})/) || [])[1] || null;

    const community = txt(card.querySelector('p.property-content-info-comm-name'));
    const totalNum = txt(card.querySelector('span.property-price-total-num'));
    const infos = [...card.querySelectorAll('p.property-content-info-text')].map(txt);
    const areaText = infos.find(t => /^[\d.]+\s*㎡$/.test(t)) || '';
    const area = (areaText.match(/[\d.]+/) || [])[0];
    const total = (totalNum.match(/[\d.]+/) || [])[0];

    if (!community || !total || !area) {
      rejected.push(`字段缺失 小区=${community} 总价=${total} 面积=${area}`);
      continue;
    }

    const addr = [...card.querySelectorAll('p.property-content-info-comm-address span')].map(txt);
    // 站点里的 district 用板块口径（新市区/高科园/老城区…），58 的第二段正是板块
    const district = addr[1] || addr[0] || '';
    const layoutText = infos.find(t => /\d+\s*室/.test(t)) || '';
    const lm = layoutText.match(/(\d+)\s*室\s*(\d*)\s*厅?/);
    const layout = lm ? `${lm[1]}室${lm[2] || '1'}厅` : '';
    const orient = infos.find(t => /^[南北东西]{1,4}$/.test(t)) || '';
    const floor = infos.find(t => /层\)?$/.test(t) && /(?:高|中|低|顶|底)层/.test(t)) || '';
    const yearText = infos.find(t => /(?:19|20)\d{2}年建造/.test(t)) || '';
    const year = (yearText.match(/((?:19|20)\d{2})/) || [])[1] || null;
    const h3 = card.querySelector('h3.property-content-title-name');
    const title = (h3 && (h3.getAttribute('title') || txt(h3))) || '';

    rows.push({
      community_name: community,
      district: district,
      title: title.slice(0, 120),
      layout: layout,
      area: parseFloat(area),
      total_wan: parseFloat(total),
      orientation: orient,
      floor_text: floor,
      build_year: year,
      source_url: url,
      source_ref: ref,
    });
  }
  return {rows, rejected};
})()
"""


def cdp_targets() -> list[dict]:
    with urllib.request.urlopen(CDP + "/json/list", timeout=6) as r:
        return json.load(r)


def evaluate(ws_url: str, expr: str):
    ws = websocket.create_connection(ws_url, timeout=25)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True,
                                       "awaitPromise": True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                res = m.get("result", {}).get("result", {})
                if res.get("exceptionDetails"):
                    raise RuntimeError(res["exceptionDetails"].get("text", "JS 执行失败"))
                return res.get("value")
    finally:
        ws.close()


def api(base: str, path: str, token: str, sharecode: str,
        method: str = "GET", payload: dict | None = None) -> dict:
    sep = "&" if "?" in path else "?"
    url = base.rstrip("/") + path + (f"{sep}sharecode={sharecode}" if sharecode else "")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "X-Admin-Token": token,
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc


def read_env() -> dict:
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://rizhao-house-radar.app.workbuddy.host")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-refresh", action="store_true", help="不触发重算推送")
    args = ap.parse_args()

    env = read_env()
    token = env.get("ADMIN_TOKEN", "")
    sharecode = env.get("SITE_SHARECODE", "")
    if not token:
        raise SystemExit(".env 里没有 ADMIN_TOKEN")

    page = next((t for t in cdp_targets() if t["type"] == "page"
                 and "rizhao.58.com/ershoufang" in t["url"]), None)
    if not page:
        raise SystemExit("专用浏览器里没找到 58 日照二手房页面。请双击桌面「日照房价探针」打开后再运行。")

    got = evaluate(page["webSocketDebuggerUrl"], EXTRACT_JS)
    rows = got["rows"]
    print(f"页面解析：有效 {len(rows)} 条，跳过 {len(got['rejected'])} 条")
    if got["rejected"]:
        print("  跳过原因样例：", got["rejected"][:3])
    if not rows:
        raise SystemExit("没提取到房源，页面可能尚未渲染完成或结构改版")
    print("  样例：", json.dumps(rows[:2], ensure_ascii=False)[:500])

    out_file = ROOT / "data" / "wuba_ershoufang.json"
    out_file.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已保存：", out_file)

    if args.dry_run:
        print("--dry-run：不导入")
        return

    # 单批最多 500 条，这里一次一页足够；服务端会按 source_ref 去重并记调价
    res = api(args.base, "/api/admin/sources/import", token, sharecode, "POST", {
        "rows": rows, "source_key": "wuba",
        "source_name": "58同城 · 日照二手房", "source_kind": "crawled",
    })
    print("导入结果：", json.dumps({k: v for k, v in res.items() if k != "errors"},
                                  ensure_ascii=False)[:600])
    if res.get("errors"):
        print("  被拒样例：", res["errors"][:3])

    if not args.no_refresh:
        try:
            api(args.base, "/api/admin/refresh-sources", token, sharecode, "POST", {})
            print("已触发在线重算与推送（异步）")
            for _ in range(12):
                time.sleep(5)
                st = api(args.base, "/api/admin/refresh-status", token, sharecode)
                if not st.get("running"):
                    print("刷新完成：", json.dumps(st.get("result"), ensure_ascii=False)[:700])
                    break
            else:
                print("刷新仍在后台运行，可稍后看 /api/admin/refresh-status")
        except Exception as exc:  # noqa: BLE001
            print("触发重算失败（不影响已入库数据）：", exc)


if __name__ == "__main__":
    main()
