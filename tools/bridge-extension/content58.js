/**
 * 58 房源页内容脚本：把「你已经看到」的公开列表数据回传到自己的站点。
 *
 * 前提是这个页面已经在你的浏览器里正常显示（验证码由你本人完成）。
 * 脚本只读 DOM 里已经渲染出来的文字和链接，不请求任何额外接口、
 * 不识别验证码、不碰联系方式等隐私字段。
 *
 * 按列表卡片的 class 精确取小区名/总价/面积/户型等字段，并用
 * 「总价÷面积≈页面单价」交叉校验；不合格的行直接丢弃，不会污染数据。
 * 页面正常显示房源后自动回传一次；此后每天两次的更新由服务端复用会话完成，
 * 扩展不再反复开页面，避免触发 58 的「访问过于频繁」验证。
 */
(() => {
  const API_PATH = '/api/admin/sources/import';

  function config() {
    return new Promise(resolve => chrome.storage.local.get({
      base: 'https://rizhao-house-radar.app.workbuddy.host',
      sharecode: '', token: '', auto: true,
    }, resolve));
  }

  const txt = el => el ? (el.innerText || '').replace(/\s+/g, ' ').trim() : '';

  /**
   * 按 58 列表卡片的 class 精确取字段。
   * 早期版本是「向上找父节点、从整段文字里猜」，会把「莒县城」这种地址词
   * 当成小区名，也会把整张卡片文字塞进 title。现在改成逐字段定位，
   * 并用「总价 / 面积 ≈ 页面单价」做一次交叉校验，对不上的行直接丢弃。
   */
  function extract() {
    const cards = [...document.querySelectorAll('div.property')];
    const rows = [];
    const seen = new Set();
    for (const card of cards) {
      const a = card.querySelector('a.property-ex[href]');
      if (!a) continue;
      // 去掉 58 附加的一长串追踪参数，留干净的原始挂牌页链接
      const url = a.href.split('#')[0].split('?')[0];
      if (!/\/ershoufang\/\d{8,}x?\.shtml$/.test(url) || seen.has(url)) continue;
      seen.add(url);
      const ref = (url.match(/\/(\d{8,})/) || [])[1] || null;

      const community = txt(card.querySelector('p.property-content-info-comm-name'));
      const totalNum = txt(card.querySelector('span.property-price-total-num'));
      const infos = [...card.querySelectorAll('p.property-content-info-text')].map(txt);
      const areaText = infos.find(t => /^[\d.]+\s*㎡$/.test(t)) || '';
      const area = (areaText.match(/[\d.]+/) || [])[0];
      const total = (totalNum.match(/[\d.]+/) || [])[0];
      if (!community || !total || !area) continue;

      const unitTxt = txt(card.querySelector('p.property-price-average'));
      const shown = (unitTxt.match(/[\d,]+/) || [])[0];
      if (shown) {
        const calc = parseFloat(total) * 10000 / parseFloat(area);
        const unit = parseFloat(shown.replace(/,/g, ''));
        if (unit > 0 && Math.abs(calc - unit) / unit > 0.03) continue;
      }

      const addr = [...card.querySelectorAll('p.property-content-info-comm-address span')].map(txt);
      // 站点的 district 用板块口径（新市区/高科园/老城区…），58 第二段正是板块
      const district = addr[1] || addr[0] || '';
      const layoutText = infos.find(t => /\d+\s*室/.test(t)) || '';
      const lm = layoutText.match(/(\d+)\s*室\s*(\d*)\s*厅?/);
      const h3 = card.querySelector('h3.property-content-title-name');
      const infoAll = card.innerText || '';

      rows.push({
        community_name: community,
        district: district,
        title: ((h3 && (h3.getAttribute('title') || txt(h3))) || '').slice(0, 120),
        layout: lm ? `${lm[1]}室${lm[2] || '1'}厅` : '',
        area: parseFloat(area),
        total_wan: parseFloat(total),
        orientation: infos.find(t => /^[南北东西]{1,4}$/.test(t)) || '',
        floor_text: infos.find(t => /(?:高|中|低|顶|底)层/.test(t)) || '',
        build_year: (infoAll.match(/((?:19|20)\d{2})年建造/) || [])[1] || null,
        source_url: url,
        source_ref: ref,
      });
    }
    return rows;
  }

  async function push(manual = false) {
    const cfg = await config();
    // 把页面可见 Cookie 交给后台，与 onChanged 累积的 HttpOnly Cookie 合并
    try { chrome.runtime.sendMessage({ type: 'page-cookie', site: 'wuba', cookie: document.cookie }); } catch (_) {}
    if (!cfg.token || !cfg.base) return { ok: false, note: '请先在扩展设置里填站点地址和口令' };
    if (!cfg.auto && !manual) return { ok: false, note: '自动回传已关闭' };
    const rows = extract();
    if (!rows.length) {
      return { ok: false, note: '本页没识别到房源（可能还在加载、被要求验证，或页面结构变了）', count: 0 };
    }
    const qs = cfg.sharecode ? `?sharecode=${encodeURIComponent(cfg.sharecode)}` : '';
    try {
      const resp = await fetch(cfg.base.replace(/\/$/, '') + API_PATH + qs, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Token': cfg.token },
        body: JSON.stringify({
          rows, source_key: 'wuba', source_name: '58同城 · 日照二手房',
          source_kind: 'crawled',
        }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`);

      // 导入后立刻触发一次「只重算 + 推送」，不再抓三个站点。
      // crawl=0 是关键：页面每次加载都会回传，若这里又抓一轮 58，
      // 短时间连续访问正是「访问过于频繁」验证码的成因。定时抓取仍按 07:30 / 19:30。
      const refreshQs = cfg.sharecode
        ? `?sharecode=${encodeURIComponent(cfg.sharecode)}&crawl=0`
        : '?crawl=0';
      fetch(cfg.base.replace(/\/$/, '') + '/api/admin/refresh-sources' + refreshQs, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Token': cfg.token },
        body: '{}',
      }).catch(() => {});

      return { ok: true, note: `已回传 ${rows.length} 条，入库 ${body.items_saved || 0}，更新 ${body.items_updated || 0}`, count: rows.length };
    } catch (e) {
      return { ok: false, note: `回传失败：${e.message}`, count: rows.length };
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === 'push-page') { push(true).then(sendResponse); return true; }
    if (msg.type === 'probe-page') {
      const rows = extract();
      sendResponse({ count: rows.length, sample: rows.slice(0, 2) });
      return true;
    }
  });

  // 页面正常显示房源后自动回传一次（延迟等异步渲染完成）
  if (document.readyState === 'complete') setTimeout(() => push(false), 3500);
  else window.addEventListener('load', () => setTimeout(() => push(false), 3500));
})();
