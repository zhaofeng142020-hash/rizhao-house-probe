<!-- 应用外壳：左侧导航（桌面）+ 底部标签栏（手机）+ 顶栏（含站内铃铛） -->
<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-name"><span class="brand-dot"></span>日照房价探针</div>
        <div class="brand-sub">捡漏雷达 · RIZHAO HOUSE PROBE</div>
      </div>

      <nav class="nav">
        <div
          v-for="item in navItems"
          :key="item.path"
          class="nav-item"
          :class="{ active: isActive(item) }"
          @click="go(item.path)"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
          <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
        </div>
      </nav>

      <div class="sidebar-foot">
        <div>先把低价当嫌疑，<br />再判断是否值得看。</div>
        <div style="margin-top:8px;opacity:.7">挂牌价≠成交价 · 仅供观察</div>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <span class="mobile-brand"><span class="dot"></span>捡漏雷达</span>
        <div class="topbar-title">{{ route.meta.title || '今日可留意' }}</div>
        <div class="topbar-sub">{{ subtitle }}</div>
        <div class="topbar-right">
          <el-button size="small" circle title="搜索小区" @click="go('/search')">
            <el-icon><Search /></el-icon>
          </el-button>
          <el-button size="small" :loading="running" @click="runPipeline">
            <el-icon><Refresh /></el-icon><span v-if="!isMobile">&nbsp;重算</span>
          </el-button>
          <el-badge :value="unread" :hidden="!unread" :max="99">
            <el-button size="small" circle @click="bellOpen = true">
              <el-icon><Bell /></el-icon>
            </el-button>
          </el-badge>
        </div>
      </header>

      <main class="content">
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>
    </div>

    <!-- 移动端底部标签栏：5 个主入口，其余进「更多」 -->
    <nav class="tabbar">
      <div
        v-for="item in tabItems"
        :key="'tab' + item.path"
        class="tab-item"
        :class="{ active: isActive(item) }"
        @click="go(item.path)"
      >
        <el-icon><component :is="item.icon" /></el-icon>
        <span>{{ item.short || item.label }}</span>
        <span v-if="item.badge" class="tab-badge">{{ item.badge > 99 ? '99+' : item.badge }}</span>
      </div>
      <div class="tab-item" :class="{ active: moreOpen }" @click="moreOpen = true">
        <el-icon><Menu /></el-icon>
        <span>更多</span>
      </div>
    </nav>

    <!-- 移动端「更多」抽屉 -->
    <el-drawer v-model="moreOpen" title="更多功能" size="78%" direction="btt">
      <div class="more-grid">
        <div
          v-for="item in moreItems"
          :key="'more' + item.path"
          class="more-item"
          @click="goMore(item.path)"
        >
          <el-icon :size="22"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
          <span v-if="item.badge" class="tag info">{{ item.badge }}</span>
        </div>
      </div>
      <div class="tiny muted" style="margin-top:14px;text-align:center">
        数据每天凌晨自动重算 · 挂牌价≠成交价
      </div>
    </el-drawer>

    <!-- 站内铃铛：未配置微信时的降级提醒通道 -->
    <el-drawer v-model="bellOpen" title="站内提醒" :size="isMobile ? '100%' : '420px'" @open="loadAlerts">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span class="small muted">未配置微信推送时，提醒会落在这里</span>
        <el-button link size="small" @click="readAll">全部已读</el-button>
      </div>
      <el-empty v-if="!alerts.length" description="暂无提醒" :image-size="70" />
      <div v-for="a in alerts" :key="a.id" class="alert-row" :class="{ unread: !a.is_read }">
        <div style="display:flex;align-items:center;gap:6px">
          <span class="grade-chip" :style="gradeStyle(a.grade)">{{ gradeText(a.grade) }}</span>
          <span class="tiny muted">{{ a.created_at }}</span>
          <span class="tag" style="margin-left:auto">{{ channelLabel(a.channel) }}</span>
        </div>
        <div class="alert-title">{{ a.title }}</div>
        <pre class="alert-body">{{ a.message }}</pre>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, authEvents, AuthError, fmt } from './api'
import { useIsMobile } from './composables/useIsMobile'

const route = useRoute()
const router = useRouter()
const { isMobile } = useIsMobile()

const bellOpen = ref(false)
const moreOpen = ref(false)
const alerts = ref([])
const unread = ref(0)
const running = ref(false)
const dashboard = ref(null)

// tab: true 的项进手机底部标签栏（只放最高频的 4 个），其余进「更多」
const navItems = computed(() => [
  { path: '/', label: '今日可留意', short: '今日', icon: 'Aim', tab: true },
  { path: '/anomalies', label: '异动专区', short: '异动', icon: 'TrendCharts', tab: true, badge: dashboard.value?.counts?.events_7d || 0 },
  { path: '/map', label: '小区地图', short: '地图', icon: 'MapLocation', tab: true },
  { path: '/area', label: '改善 120-150㎡', short: '改善', icon: 'House', tab: true },
  { path: '/newhomes', label: '新房倒挂', short: '新房', icon: 'OfficeBuilding', tab: false },
  { path: '/deals', label: '成交锚点', short: '成交', icon: 'Tickets', tab: false },
  { path: '/search', label: '搜索小区', short: '搜索', icon: 'Search', tab: false },
  { path: '/sources', label: '数据来源', short: '来源', icon: 'Document', tab: false },
  { path: '/watchlist', label: '观察清单', short: '观察', icon: 'Star', tab: false, badge: dashboard.value?.counts?.watched || 0 },
  { path: '/settings', label: '推送设置', short: '设置', icon: 'ChatDotRound', tab: false },
  { path: '/admin', label: '管理台', short: '管理', icon: 'Setting', tab: false },
])

const tabItems = computed(() => navItems.value.filter((i) => i.tab))
const moreItems = computed(() => navItems.value.filter((i) => !i.tab))

const subtitle = computed(() => {
  if (!dashboard.value) return ''
  const c = dashboard.value.counts || {}
  return `${dashboard.value.today} · 在挂 ${c.listings} 套 · 红 ${c.red} / 黄 ${c.yellow}`
})

function isActive(item) {
  if (item.path === '/') return route.path === '/'
  return route.path.startsWith(item.path)
}
function go(path) {
  moreOpen.value = false
  router.push(path)
}
function goMore(path) {
  moreOpen.value = false
  router.push(path)
}

function gradeText(grade) {
  return fmt.gradeMeta(grade).icon + fmt.gradeMeta(grade).label
}
function gradeStyle(grade) {
  const m = fmt.gradeMeta(grade)
  return { color: m.color, background: m.bg }
}
function channelLabel(ch) {
  return {
    wecom_webhook: '企微群机器人', wecom_app: '企微应用', mp_template: '公众号',
    telegram: 'Telegram', email: '邮件', log: '站内',
  }[ch] || ch
}

async function loadDashboard() {
  try {
    dashboard.value = await api.dashboard()
  } catch (e) {
    ElMessage.error('后端未连接：' + e.message)
  }
}
async function loadAlerts() {
  try {
    const data = await api.alertLogs({ limit: 30 })
    alerts.value = data.items
    unread.value = data.unread
  } catch (e) { /* 静默：铃铛不阻塞主流程 */ }
}
async function readAll() {
  try {
    await api.readAllAlerts()
    await loadAlerts()
  } catch (e) {
    if (e instanceof AuthError) {
      ElMessage.warning('需要管理员口令，正在跳转管理台')
      router.push('/admin')
    }
  }
}
async function runPipeline() {
  running.value = true
  try {
    const s = await api.runPipeline()
    ElMessage.success(`重算完成：${s.scored} 条评分，${s.events} 条异动，推送 ${s.pushed} 条`)
    await loadDashboard()
    await loadAlerts()
  } catch (e) {
    if (e instanceof AuthError) {
      ElMessage.warning('重算需要管理员口令，请在管理台登录后操作')
      router.push('/admin')
    } else {
      ElMessage.error('重算失败：' + e.message)
    }
  } finally {
    running.value = false
  }
}

let timer = null
// 统一处理「需要口令」：任何页面触发写操作被拦，都引导去管理台登录，
// 而不是让用户在各个页面里各看一遍 401 报错。
let lastAuthTip = 0
function onAuthRequired() {
  const now = Date.now()
  if (now - lastAuthTip < 3000) return   // 一次操作可能并发多个请求，只提示一次
  lastAuthTip = now
  ElMessage.warning('这个操作需要管理员口令，请先在管理台登录')
  if (route.path !== '/admin') router.push('/admin')
}

onMounted(() => {
  loadDashboard()
  loadAlerts()
  timer = setInterval(() => { loadAlerts() }, 60000)
  authEvents.addEventListener('required', onAuthRequired)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  authEvents.removeEventListener('required', onAuthRequired)
})
</script>

<style scoped>
.alert-row {
  border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin-bottom: 10px;
  background: var(--panel);
}
.alert-row.unread { border-color: rgba(14, 116, 144, .35); background: #f7fcfd; }
.alert-title { font-size: 13px; font-weight: 650; margin-top: 6px; }
.alert-body {
  margin: 6px 0 0; font-size: 11.5px; color: var(--muted); white-space: pre-wrap;
  font-family: var(--mono); max-height: 160px; overflow: auto; line-height: 1.6;
}

.more-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.more-item {
  display: flex; align-items: center; gap: 9px; padding: 14px 15px;
  border: 1px solid var(--line); border-radius: 11px; font-size: 13.5px; font-weight: 600;
  color: var(--ink);
}
.more-item:active { background: #f2fafc; border-color: var(--brand); }
.more-item .tag { margin-left: auto; }
</style>
