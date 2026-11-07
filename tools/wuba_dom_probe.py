"""把用户浏览器里已经正常显示的 58 列表页，按真实 DOM 结构抽样。

只读取已渲染的公开文字与链接，不点击、不请求额外接口、不碰隐私字段。
输出：data/wuba_card_sample.html（卡片结构）+ 控制台打印字段顺序。
"""
from __future__ import annotations

import json
import pathlib
import urllib.request

import websocket

CDP = "http://127.0.0.1:9224"


def evaluate(ws_url: str, expr: str):
    ws = websocket.create_connection(ws_url, timeout=20)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True,
                                       "awaitPromise": True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                return m.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()


CARD_PROBE = r"""
(() => {
  const anchors = [...document.querySelectorAll('a[href*="/ershoufang/"]')]
    .filter(a => /\/\d{8,}x?\.shtml/.test(a.href));
  const out = [];
  for (const a of anchors.slice(0, 3)) {
    let b = a;
    for (let i = 0; i < 6 && b.parentElement; i++) {
      b = b.parentElement;
      const t = b.innerText || '';
      if (/\d+(\.\d+)?\s*万/.test(t) && /\d+(\.\d+)?\s*㎡/.test(t) && t.length < 900) break;
    }
    out.push({
      cls: b.className,
      tag: b.tagName,
      text: (b.innerText || '').replace(/\n{2,}/g, '\n').trim().slice(0, 700),
      html: b.outerHTML,
      href: a.href.split('?')[0],
      allClasses: [...b.querySelectorAll('[class]')].slice(0, 25)
        .map(e => e.tagName + '.' + String(e.className).split(' ').join('.')).slice(0, 25),
    });
  }
  return {count: anchors.length, cards: out};
})()
"""


def main() -> None:
    with urllib.request.urlopen(CDP + "/json/list", timeout=5) as r:
        ts = json.load(r)
    page = next((t for t in ts if t["type"] == "page"
                 and "rizhao.58.com/ershoufang" in t["url"]), None)
    if not page:
        raise SystemExit("没找到 58 日照二手房页面，请先在专用浏览器里打开它")
    data = evaluate(page["webSocketDebuggerUrl"], CARD_PROBE)
    print("匹配到的房源链接数：", data["count"])
    for i, c in enumerate(data["cards"]):
        print(f"\n--- 卡片 {i+1} | {c['tag']}.{c['cls']} | {c['href']} ---")
        print("文本：\n" + c["text"])
        print("内部类名：", c["allClasses"][:18])
    if data["cards"]:
        p = pathlib.Path(__file__).resolve().parents[1] / "data" / "wuba_card_sample.html"
        p.write_text("\n\n<!-- CARD SPLIT -->\n\n".join(
            c["html"] for c in data["cards"]), encoding="utf-8")
        print("\n卡片 HTML 已存：", p)


if __name__ == "__main__":
    main()
