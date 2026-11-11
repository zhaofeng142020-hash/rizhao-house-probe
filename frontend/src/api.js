/**
 * 后端 API 封装。
 * 开发模式走 vite 代理（/api → 127.0.0.1:8000），生产模式由 FastAPI 同源托管。
 *
 * 权限：读接口不需要口令；写接口（POST/PUT/PATCH/DELETE）要带 X-Admin-Token。
 * 口令存在 localStorage，401 时抛 AuthError，由页面引导去「管理台」登录。
 */
const BASE = import.meta.env.VITE_API_BASE || ''
const TOKEN_KEY = 'rz_admin_token'

export class AuthError extends Error {
  constructor(message) {
    super(message)
    this.name = 'AuthError'
  }
}

export const auth = {
  get() {
    try { return localStorage.getItem(TOKEN_KEY) || '' } catch (e) { return '' }
  },
  set(token) {
    try { localStorage.setItem(TOKEN_KEY, token) } catch (e) { /* 隐私模式下忽略 */ }
  },
  clear() {
    try { localStorage.removeItem(TOKEN_KEY) } catch (e) { /* 忽略 */ }
  },
}

/**
 * 全局「需要口令」事件。
 * 各页面不必逐个处理 401——外壳（App.vue）统一监听并引导去管理台登录。
 */
export const authEvents = new EventTarget()

async function request(path, options = {}) {
  const headers = { 'Content-Type': 'application/json' }
  const token = auth.get()
  if (token) headers['X-Admin-Token'] = token

  const resp = await fetch(`${BASE}${path}`, { headers, ...options })
  if (resp.status === 401) {
    let detail = '需要管理员口令'
    try {
      const body = await resp.json()
      detail = body.detail || detail
    } catch (e) { /* 忽略 */ }
    authEvents.dispatchEvent(new CustomEvent('required', { detail }))
    throw new AuthError(detail)
  }
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      detail = body.detail || detail
    } catch (e) { /* 忽略解析失败 */ }
    throw new Error(`${resp.status} ${detail}`)
  }
  return resp.json()
}

function qs(params = {}) {
  const usable = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  if (!usable.length) return ''
  return '?' + usable.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
}

export const api = {
  // ---- 基础 ----
  health: () => request('/api/meta/health'),
  dashboard: () => request('/api/meta/dashboard'),
  districts: () => request('/api/meta/districts'),
  communities: (district) => request(`/api/meta/communities${qs({ district })}`),
  publicConfig: () => request('/api/config'),
  disclaimer: () => request('/api/disclaimer'),
  pref: () => request('/api/meta/pref'),
  savePref: (data) => request('/api/meta/pref', { method: 'PUT', body: JSON.stringify(data) }),
  crawlLogs: () => request('/api/meta/crawl-logs'),

  // ---- 小区（含地图 / 关注 / 波动） ----
  communityList: (params) => request(`/api/communities${qs(params)}`),
  community: (id) => request(`/api/communities/${id}`),
  communityTrend: (id, days) => request(`/api/communities/${id}/trend${qs({ days })}`),
  watchCommunity: (id) => request(`/api/communities/${id}/watch`, { method: 'POST' }),
  unwatchCommunity: (id) => request(`/api/communities/${id}/watch`, { method: 'DELETE' }),
  followedCommunities: () => request('/api/communities/followed'),

  // ---- 市场大盘 ----
  marketOverview: (days) => request(`/api/market/overview${qs({ days })}`),
  marketRents: () => request('/api/market/rents'),
  rateTable: (params) => request(`/api/meta/rate-table${qs(params)}`),
  dropTiming: (days) => request(`/api/anomalies/drop-timing${qs({ days })}`),
  refreshRents: () => request('/api/admin/refresh-rents?pages=3', { method: 'POST', body: '{}' }),
  // 真实成交锚点（法拍成交价，人工/CSV 回填口径）
  dealSummary: (days) => request(`/api/market/deals${qs({ days })}`),
  auctionResults: (status = 'sold') => request(`/api/auctions${qs({ status })}`),

  // ---- 数据来源 ----
  // ---- 每日调价报告 ----
  dailyReport: (params) => request(`/api/reports/daily${qs(params)}`),
  reportDays: (limit = 30) => request(`/api/reports/days${qs({ limit })}`),
  reportRange: (days = 7) => request(`/api/reports/range${qs({ days })}`),
  sourceOverview: () => request('/api/sources'),
  sourceTemplate: () => request('/api/sources/template'),
  importListings: (data) => request('/api/admin/sources/import', { method: 'POST', body: JSON.stringify(data) }),
  toggleSource: (key, enabled) =>
    request(`/api/admin/sources/${key}/enable`, { method: 'PUT', body: JSON.stringify({ enabled }) }),
  crawlSources: () => request('/api/admin/sources/crawl', { method: 'POST' }),
  sourceBatches: () => request('/api/admin/sources/batches'),
  purgeDemo: () => request('/api/admin/purge-demo', { method: 'POST', body: '{}' }),
  // force=1：管理台按钮是用户主动点的，跳过 10 分钟防抖冷却
  refreshSources: () => request('/api/admin/refresh-sources?force=1', { method: 'POST', body: '{}' }),
  refreshStatus: () => request('/api/admin/refresh-status'),
  sessionCookies: () => request('/api/admin/sources/cookies'),
  sessionCheck: (key) => request(`/api/admin/sources/session-check${qs({ key })}`),
  officialDeals: () => request('/api/admin/official-deals'),
  saveSessionCookie: (key, cookie) =>
    request(`/api/admin/sources/${key}/cookies`, { method: 'PUT', body: JSON.stringify({ cookie }) }),
  upsertCommunities: (rows) =>
    request('/api/admin/communities/upsert', { method: 'POST', body: JSON.stringify({ rows }) }),

  // ---- 新房 ----
  newHomes: (params) => request(`/api/new-homes${qs(params)}`),
  newHome: (id) => request(`/api/new-homes/${id}`),

  // ---- 重点关注面积段 ----
  target: (params) => request(`/api/target${qs(params)}`),

  // ---- 房源 ----
  listings: (params) => request(`/api/listings${qs(params)}`),
  listing: (id) => request(`/api/listings/${id}`),

  // ---- 异动 ----
  today: (params) => request(`/api/anomalies/today${qs(params)}`),
  anomalies: (params) => request(`/api/anomalies${qs(params)}`),
  anomalySummary: (days) => request(`/api/anomalies/summary${qs({ days })}`),
  ranking: (params) => request(`/api/anomalies/ranking${qs(params)}`),

  // ---- 法拍 ----
  auctions: (params) => request(`/api/auctions${qs(params)}`),
  auction: (id) => request(`/api/auctions/${id}`),
  saveAuctionResult: (id, data) =>
    request(`/api/admin/auctions/${id}/result`, { method: 'POST', body: JSON.stringify(data) }),
  importAuctionResults: (payload) =>
    request('/api/admin/auctions/results/import', { method: 'POST', body: JSON.stringify(payload) }),

  // ---- 观察清单 ----
  watchlist: () => request('/api/watchlist'),
  addWatch: (data) => request('/api/watchlist', { method: 'POST', body: JSON.stringify(data) }),
  updateWatch: (id, data) => request(`/api/watchlist/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  removeWatch: (id) => request(`/api/watchlist/${id}`, { method: 'DELETE' }),
  weeklyReport: () => request('/api/watchlist/weekly-report'),

  // ---- 推送 ----
  alertConfig: () => request('/api/alerts/config'),
  saveAlertConfig: (data) => request('/api/alerts/config', { method: 'PUT', body: JSON.stringify(data) }),
  testAlert: () => request('/api/alerts/test', { method: 'POST' }),
  alertLogs: (params) => request(`/api/alerts/logs${qs(params)}`),
  readAllAlerts: () => request('/api/alerts/logs/read-all', { method: 'POST' }),
  pushOne: (type, id) => request(`/api/alerts/push/${type}/${id}`, { method: 'POST' }),
  runPipeline: () => request('/api/alerts/run-pipeline', { method: 'POST' }),

  // ---- 管理 ----
  adminLogin: (password) => request('/api/admin/login', { method: 'POST', body: JSON.stringify({ password }) }),
  adminCheck: () => request('/api/admin/auth-check'),
  adminStatus: () => request('/api/admin/status'),
  integration: () => request('/api/admin/integration'),
  saveIntegration: (data) => request('/api/admin/integration', { method: 'PUT', body: JSON.stringify(data) }),
  testIntegration: () => request('/api/admin/integration/test', { method: 'POST' }),
  runNow: (push = true) => request(`/api/admin/run-now${qs({ push })}`, { method: 'POST' }),

  // ---- 数据备份 ----
  backup: () => request('/api/admin/backup'),
  backupSnapshot: () => request('/api/admin/backup/snapshot', { method: 'POST' }),
  backupRestore: (data, replace = true) =>
    request(`/api/admin/backup/import${qs({ replace })}`, { method: 'POST', body: JSON.stringify(data) }),

  /** 下载资产备份。GET 但含口令密钥，所以照样要带头；走 blob 存成文件。 */
  async backupDownload() {
    const token = auth.get()
    const resp = await fetch(`${BASE}/api/admin/backup/export`, {
      headers: token ? { 'X-Admin-Token': token } : {},
    })
    if (resp.status === 401) {
      authEvents.dispatchEvent(new CustomEvent('required', { detail: '需要管理员口令' }))
      throw new AuthError('需要管理口令')
    }
    if (!resp.ok) throw new Error(`${resp.status} 导出失败`)
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const d = new Date()
    const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}`
    a.href = url
    a.download = `rizhao-assets-${stamp}.json`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    return true
  },
}

// ---- 展示用格式化 ----
export const fmt = {
  wan(value) {
    const v = Number(value || 0)
    if (!v) return '—'
    return v >= 10000 ? `${(v / 10000).toFixed(1)}万` : `${Math.round(v)}元`
  },
  /** 强制按「万元」显示，房源总价用这个更符合中国人的读法 */
  wanOnly(value, digits = 1) {
    const v = Number(value || 0)
    return v ? `${(v / 10000).toFixed(digits)}万` : '—'
  },
  money(value) {
    const v = Number(value || 0)
    return v ? v.toLocaleString('zh-CN', { maximumFractionDigits: 0 }) : '—'
  },
  pct(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
    return `${(Number(value) * 100).toFixed(digits)}%`
  },
  /** 已经是百分数（如 12.3 表示 12.3%）时用这个 */
  signed(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
    const v = Number(value)
    return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
  },
  area(value) {
    return value ? `${Number(value).toFixed(0)}㎡` : '—'
  },
  gradeMeta(grade) {
    return {
      red: { icon: '🔴', label: '重点留意', color: '#e11d48', bg: 'rgba(225,29,72,.08)' },
      yellow: { icon: '🟡', label: '建议观察', color: '#d97706', bg: 'rgba(217,119,6,.10)' },
      white: { icon: '⚪', label: '仅记录', color: '#64748b', bg: 'rgba(100,116,139,.10)' },
    }[grade] || { icon: '⚪', label: '未评级', color: '#64748b', bg: 'rgba(100,116,139,.10)' }
  },
  confidenceLabel(c) {
    return { high: '高', mid: '中', low: '低' }[c] || '中'
  },
  riskLabel(r) {
    return { low: '低', mid: '中', high: '高' }[r] || '中'
  },
  date(value) {
    return value ? String(value).slice(0, 10) : '—'
  },
  /** 去化月数：50 个月直接说「4 年以上」更好理解 */
  months(m) {
    if (m === null || m === undefined) return '无成交'
    const v = Number(m)
    if (v >= 24) return `${(v / 12).toFixed(1)} 年`
    return `${v.toFixed(1)} 个月`
  },
}

