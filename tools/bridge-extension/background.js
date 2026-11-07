/**
 * 会话桥后台：只做一件事 —— 你在浏览器里正常通过人机验证后，
 * 把该站点的 Cookie 自动同步到自己的房价探针服务。
 *
 * 不识别、不破解、不绕过验证码；验证码始终由人手动完成。
 * 之后服务端每天 07:30 / 19:30 自动抓取、自动重算、自动推送结果。
 */
const SITES = {
  wuba:   { label: '58同城', domain: '.58.com',   url: 'https://rizhao.58.com/ershoufang/' },
  anjuke: { label: '安居客', domain: '.anjuke.com', url: 'https://m.anjuke.com/rz/sale/' },
  ke:     { label: '贝壳',   domain: '.ke.com',   url: 'https://rz.ke.com/ershoufang/' },
};

const DEFAULT_CONFIG = {
  base: 'https://rizhao-house-radar.app.workbuddy.host',
  sharecode: '',
  token: '',
  auto: true,
};

async function getConfig() {
  const cfg = await chrome.storage.local.get(DEFAULT_CONFIG);
  return cfg;
}

async function setStatus(site, status) {
  const key = `status.${site}`;
  await chrome.storage.local.set({ [key]: { ...status, at: new Date().toISOString() } });
}

// 某些 Chrome 版本里 chrome.cookies.getAll() 对扩展返回空数组（get 按名却正常）。
// 因此以 onChanged 事件累积的 Cookie 为准，再叠加 getAll 结果兜底。
const KNOWN_NAMES = {
  wuba: ['f', 'n', '58', 'city', 'wmda_uuid', 'xxzl_deviceid', 'spid', 'ppinf', 'PPU',
         'sessionid', 'idsLoginUserIdLastTime', '58tj_uuid', 'new_uv', 'init_refer',
         'antibot', 'fingerprint', 'aqq_jsessionid'],
  anjuke: ['ajk_member_id', 'ajk_auth', 'sessid', 'ctid', 'ajkApp', 'is_ajax',
           'lianjia_ssid', 'lianjia_uuid', 'select_city'],
  ke: ['lianjia_ssid', 'lianjia_uuid', 'select_city', 'srcid', 'HMACCOUNT'],
};

async function getJar(site) {
  const key = `cookieJar.${site}`;
  const got = await chrome.storage.local.get(key);
  return got[key] || {};
}

async function saveJar(site, jar) {
  await chrome.storage.local.set({ [`cookieJar.${site}`]: jar });
}

async function collectCookie(domain, site) {
  const host = domain.replace(/^\./, '');
  const jar = { ...(await getJar(site)) };

  // 1) getAll 在部分版本可用时叠加
  try {
    const cookies = await chrome.cookies.getAll({ domain });
    for (const c of cookies) if (c.domain.endsWith(host)) jar[c.name] = c.value;
  } catch (_) { /* 忽略，用 jar */ }

  // 2) 按常见名精确取（getAll 返回空但 get 可用）
  const url = SITES[site].url;
  for (const name of (KNOWN_NAMES[site] || [])) {
    if (jar[name]) continue;
    try {
      const c = await chrome.cookies.get({ url, name });
      if (c && c.domain.endsWith(host)) jar[name] = c.value;
    } catch (_) { /* 忽略 */ }
  }

  // 3) 内容脚本上报的页面可见 Cookie
  const pageKey = `pageCookie.${site}`;
  const page = await chrome.storage.local.get(pageKey);
  if (page[pageKey]) {
    for (const pair of String(page[pageKey]).split(';')) {
      const i = pair.indexOf('=');
      if (i > 0) jar[pair.slice(0, i).trim()] = pair.slice(i + 1).trim();
    }
  }

  await saveJar(site, jar);
  return Object.keys(jar).sort().map(k => `${k}=${jar[k]}`).join('; ');
}

async function syncSite(site, manual = false) {
  const cfg = await getConfig();
  if (!cfg.base || !cfg.token) {
    await setStatus(site, { ok: false, note: '还没填站点地址和口令，请打开扩展设置' });
    return { ok: false, note: '配置不完整' };
  }
  if (!cfg.auto && !manual) return { ok: false, note: '自动同步已关闭' };

  const meta = SITES[site];
  let cookie = '';
  try {
    cookie = await collectCookie(meta.domain, site);
  } catch (e) {
    await setStatus(site, { ok: false, note: `读取 Cookie 失败：${e.message}` });
    return { ok: false, note: e.message };
  }
  if (!cookie) {
    await setStatus(site, { ok: false, note: '还没有该站点的 Cookie，请先打开站点并完成验证' });
    return { ok: false, note: '无 Cookie' };
  }

  const qs = cfg.sharecode ? `?sharecode=${encodeURIComponent(cfg.sharecode)}` : '';
  const url = `${cfg.base.replace(/\/$/, '')}/api/admin/sources/${site}/cookies${qs}`;
  try {
    const resp = await fetch(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Token': cfg.token },
      body: JSON.stringify({ cookie }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`);
    const status = { ok: true, note: '会话已同步', length: cookie.length };
    await setStatus(site, status);
    return status;
  } catch (e) {
    const status = { ok: false, note: `同步失败：${e.message}` };
    await setStatus(site, status);
    return status;
  }
}

async function syncAll(manual = false) {
  const out = {};
  for (const site of Object.keys(SITES)) out[site] = await syncSite(site, manual);
  return out;
}

// 你在网页上完成验证 → 站点写下通行 Cookie → 自动同步
let debounce = null;
chrome.cookies.onChanged.addListener(async (info) => {
  const c = info.cookie;
  if (!c) return;
  const domain = c.domain || '';
  const site = Object.keys(SITES).find(k => domain.endsWith(SITES[k].domain.replace(/^\./, '')));
  if (!site) return;
  // 累积每一个变化（含 HttpOnly）——用户过验证时写入的通行 Cookie 会在这里被捕获
  const jar = await getJar(site);
  if (info.removed) delete jar[c.name];
  else jar[c.name] = c.value;
  await saveJar(site, jar);
  clearTimeout(debounce);
  debounce = setTimeout(() => syncSite(site, false), 2500);
});

// ---- 会话保鲜：只同步 Cookie，不再后台反复打开 58 页面 ----
// 曾经每 2 小时后台开一次列表页，结果 58 判定「访问过于频繁」，
// 在后台标签里弹出 antibot 验证码（用户根本看不见，会话反而失效）。
// 现在改由服务端复用这份会话，每天 07:30 / 19:30 低频抓一次首屏，
// 既拿到最新挂牌，也不去撞风控。扩展只负责把会话保鲜。
const WUBA_LIST = 'https://rizhao.58.com/ershoufang/';

async function pullWuba() {
  const cfg = await getConfig();
  if (!cfg.token) return;
  // 手动点按钮时才打开，且让用户看得见：不再偷偷开后台标签
  await chrome.tabs.create({ url: WUBA_LIST, active: true });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'session-keepalive') syncAll(false);
});

function schedule() {
  chrome.alarms.create('session-keepalive', { periodInMinutes: 30 });
  // 旧的 wuba-pull 闹钟如果还在（升级前装的），显式清掉
  chrome.alarms.clear('wuba-pull');
}
chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'page-cookie') {
    chrome.storage.local.set({ [`pageCookie.${msg.site}`]: msg.cookie || '' });
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === 'sync') {
    syncAll(true).then(sendResponse);
    return true;
  }
  if (msg.type === 'sync-one') {
    syncSite(msg.site, true).then(sendResponse);
    return true;
  }
  if (msg.type === 'pull-wuba') {
    pullWuba().then(() => sendResponse({ ok: true, note: '已打开 58 页面，正常显示房源后会自动回传' }));
    return true;
  }
  if (msg.type === 'status') {
    chrome.storage.local.get(null).then(sendResponse);
    return true;
  }
});
