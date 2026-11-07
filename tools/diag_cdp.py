"""诊断专用浏览器：看看 58 页面到底渲染了什么、验证码在哪、扩展状态如何。

只读，不点击、不通过验证、不改页面。用于定位「打开了却没弹验证码」这类问题。
用法：.venv/Scripts/python.exe tools/diag_cdp.py
"""
from __future__ import annotations

import json
import urllib.request

import websocket

CDP = "http://127.0.0.1:9224"


def targets() -> list[dict]:
    with urllib.request.urlopen(CDP + "/json/list", timeout=5) as r:
        return json.load(r)


def evaluate(ws_url: str, expr: str, timeout: int = 15):
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True,
                       "awaitPromise": True},
        }))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                return msg.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()


LIST_PROBE = r"""
(() => {
  const links = [...document.querySelectorAll('a[href]')];
  const house = links.filter(a =>
    /\/(?:ershoufang|fangyuan|esf)\/\d+[^/]*\.shtml|\/(?:ershoufang|fangyuan)\/\d+\.html/.test(a.href));
  const text = document.body ? document.body.innerText : '';
  return {
    title: document.title,
    url: location.href,
    bodyChars: text.length,
    head: text.slice(0, 260).replace(/\s+/g, ' '),
    linksTotal: links.length,
    houseLinks: house.length,
    houseSample: house.slice(0, 3).map(a => ({href: a.href.slice(0, 120), title: (a.innerText||'').slice(0, 40)})),
    hasCaptchaWord: /验证|滑块|安全|拖动/.test(text),
    hasBridge: typeof chrome !== 'undefined' && !!(chrome && chrome.runtime && chrome.runtime.id),
  };
})()
"""

VERIFY_PROBE = r"""
(() => {
  const text = document.body ? document.body.innerText : '';
  return {
    title: document.title,
    url: location.href,
    bodyChars: text.length,
    head: text.slice(0, 400).replace(/\s+/g, ' '),
    imgs: document.images.length,
    hasSlider: /滑动|拖动|滑块|向右/.test(text),
  };
})()
"""

STORAGE_PROBE = r"""
(async () => {
  const d = await chrome.storage.local.get(null);
  const out = {};
  for (const k of Object.keys(d)) {
    if (k === 'token') { out[k] = d[k] ? '已填(' + String(d[k]).length + '位)' : '空'; continue; }
    if (k.startsWith('cookieJar.')) { out[k] = Object.keys(d[k] || {}).length + ' 个 Cookie'; continue; }
    if (k.startsWith('pageCookie.')) { out[k] = String(d[k] || '').length + ' 字符'; continue; }
    if (k.startsWith('status.')) { out[k] = d[k]; continue; }
    out[k] = d[k];
  }
  return out;
})()
"""

# 完整复刻 content58.js 的提取逻辑，看看当前页面究竟能提出几行
WUBA_EXTRACT = r"""
(() => {
  function num(s){ if(!s) return null; const m=String(s).replace(/,/g,'').match(/-?\d+(\.\d+)?/); return m?parseFloat(m[0]):null; }
  function parseText(text){
    const t=(text||'').replace(/\s+/g,' ').trim();
    const priceWan=(t.match(/(\d+(?:\.\d+)?)\s*万/)||[])[1];
    const area=(t.match(/(\d+(?:\.\d+)?)\s*(?:㎡|平米|平)/)||[])[1];
    const layout=(t.match(/(\d+)\s*室\s*(\d*)\s*厅?/)||[]);
    const unit=(t.match(/(\d[\d,]*)\s*元\s*\/\s*[㎡平]/)||[])[1];
    return {total_wan:priceWan?parseFloat(priceWan):null, area:area?parseFloat(area):null,
      layout:layout[1]?`${layout[1]}室${layout[2]||'1'}厅`:null, unit_hint:unit?parseInt(unit.replace(/,/g,''),10):null};
  }
  const anchors=[...document.querySelectorAll('a[href]')].filter(a =>
    /\/(?:ershoufang|fangyuan|esf)\/\d+[^/]*\.shtml|\/(?:ershoufang|fangyuan)\/\d+\.html/.test(a.href));
  const seen=new Set(); const rows=[]; const rejected=[];
  for(const a of anchors){
    const url=a.href.split('#')[0]; if(seen.has(url)) continue; seen.add(url);
    let box=a;
    for(let i=0;i<5&&box.parentElement;i++){ box=box.parentElement;
      if(/\d+(?:\.\d+)?\s*(?:㎡|平米)/.test(box.innerText||'')) break; }
    const text=(box.innerText||a.innerText||'').replace(/\s+/g,' ').trim();
    const f=parseText(text);
    if(!f.total_wan||!f.area){ rejected.push({reason:'无总价或面积', text:text.slice(0,110)}); continue; }
    let community=null;
    const mc=text.match(/[\u4e00-\u9fa5A-Za-z0-9·]{2,20}(?:小区|花园|家园|公馆|府邸|华庭|公寓|雅苑|名邸|学府|城|湾|苑)/g);
    if(mc&&mc.length) community=mc[0];
    if(!community){ const t=(a.getAttribute('title')||a.innerText||'').trim();
      const m2=t.match(/^([\u4e00-\u9fa5A-Za-z0-9·]{2,18})/); community=m2?m2[1]:null; }
    if(!community){ rejected.push({reason:'无小区名', text:text.slice(0,110)}); continue; }
    const ref=(url.match(/(\d{6,})/)||[])[1]||null;
    rows.push({community_name:community, title:(a.getAttribute('title')||a.innerText||'').trim().slice(0,120)||null,
      layout:f.layout, area:f.area, total_wan:f.total_wan, source_url:url, source_ref:ref});
  }
  return {rows:rows.length, rejected:rejected.length,
    sample:rows.slice(0,4), rejectSample:rejected.slice(0,3),
    firstCardHtml: anchors.length ? (()=>{ let b=anchors[0];
      for(let i=0;i<5&&b.parentElement;i++) b=b.parentElement;
      return b.outerHTML.slice(0,1600); })() : ''};
})()
"""


def main() -> None:
    ts = targets()
    print("=== 目标概览 ===")
    for t in ts:
        print(f"  {t['type']:16} | {t.get('title','')[:36]:36} | {t['url'][:88]}")

    for t in ts:
        if t["type"] != "page":
            continue
        url = t["url"]
        if "58.com/ershoufang" in url or "antibot/verifycode" in url or "ke.com/ershoufang" in url:
            expr = VERIFY_PROBE if "antibot" in url else LIST_PROBE
            print(f"\n=== 页面诊断：{t.get('title','')[:50]} ===")
            try:
                print(json.dumps(evaluate(t["webSocketDebuggerUrl"], expr),
                                 ensure_ascii=False, indent=2))
            except Exception as exc:  # noqa: BLE001
                print("  读取失败：", exc)
            if "58.com/ershoufang" in url:
                print("\n=== 内容脚本提取逻辑实测 ===")
                try:
                    print(json.dumps(evaluate(t["webSocketDebuggerUrl"], WUBA_EXTRACT),
                                     ensure_ascii=False, indent=2))
                except Exception as exc:  # noqa: BLE001
                    print("  读取失败：", exc)

    for t in ts:
        if t["type"] == "service_worker":
            print("\n=== 扩展后台存储 ===")
            try:
                print(json.dumps(evaluate(t["webSocketDebuggerUrl"], STORAGE_PROBE),
                                 ensure_ascii=False, indent=2))
            except Exception as exc:  # noqa: BLE001
                print("  读取失败：", exc)


if __name__ == "__main__":
    main()
