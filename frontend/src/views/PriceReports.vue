<template>
  <div class="page reports">
    <h2>每日调价报告</h2>
    <p class="muted">
      每次房源刷新后自动统计当天所有涨价 / 降价的房源明细。价格来自公开挂牌数据，
      来源与原始链接在明细中逐一标明，挂牌价≠成交价。
    </p>

    <!-- 日期选择 -->
    <div class="report-bar">
      <select v-model="selectedDay" @change="load">
        <option v-for="d in days" :key="d.day" :value="d.day">
          {{ d.day }}（涨 {{ d.up_count }} / 降 {{ d.down_count }}）
        </option>
      </select>
      <input type="date" v-model="pickedDate" @change="onPickDate" />
      <button class="ghost" @click="load" :disabled="loading">刷新</button>
    </div>

    <div v-if="error" class="err">{{ error }}</div>

    <!-- 汇总卡 -->
    <div class="summary-cards" v-if="report">
      <div class="card up">
        <div class="num">{{ summary.up_count }}</div>
        <div class="lab">涨价套数</div>
        <div class="sub">平均 +{{ summary.up_avg_pct }}%</div>
      </div>
      <div class="card down">
        <div class="num">{{ summary.down_count }}</div>
        <div class="lab">降价套数</div>
        <div class="sub">平均 {{ summary.down_avg_pct }}%</div>
      </div>
      <div class="card">
        <div class="num">{{ summary.total_tracked }}</div>
        <div class="lab">当日观测</div>
        <div class="sub">变化 {{ summary.changed_total }} 套</div>
      </div>
    </div>

    <!-- 近 7 天趋势（纯 CSS 条形） -->
    <div class="trend" v-if="trend.length">
      <h3>近 7 天涨跌趋势</h3>
      <div class="trend-row" v-for="t in trend" :key="t.day">
        <span class="t-day">{{ t.day.slice(5) }}</span>
        <div class="t-bar up" :style="{ width: barW(t.up_count) + '%' }">
          <template v-if="t.up_count">{{ t.up_count }}</template>
        </div>
        <div class="t-bar down" :style="{ width: barW(t.down_count) + '%' }">
          <template v-if="t.down_count">{{ t.down_count }}</template>
        </div>
      </div>
      <div class="legend"><span class="dot up"></span>涨价 <span class="dot down"></span>降价</div>
    </div>

    <!-- 涨价明细 -->
    <section v-if="report && report.up.length">
      <h3 class="up-title">↑ 涨价明细（{{ report.up.length }} 套）</h3>
      <table class="report-table">
        <thead>
          <tr><th>小区</th><th>户型</th><th>面积</th><th>原价</th><th>现价</th><th>变化</th><th>幅度</th><th>来源</th><th>链接</th></tr>
        </thead>
        <tbody>
          <tr v-for="i in report.up" :key="'u' + i.listing_id">
            <td>{{ i.community_name }}</td>
            <td>{{ i.layout || '—' }}</td>
            <td>{{ i.area ? i.area + '㎡' : '—' }}</td>
            <td>{{ fmtWan(i.old_price) }}</td>
            <td>{{ fmtWan(i.new_price) }}</td>
            <td class="up-text">+{{ fmtWan(i.delta) }}</td>
            <td class="up-text">+{{ i.delta_pct }}%</td>
            <td>{{ i.source }}</td>
            <td><a v-if="i.source_url" :href="i.source_url" target="_blank" rel="noopener">查看</a></td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 降价明细 -->
    <section v-if="report && report.down.length">
      <h3 class="down-title">↓ 降价明细（{{ report.down.length }} 套）</h3>
      <table class="report-table">
        <thead>
          <tr><th>小区</th><th>户型</th><th>面积</th><th>原价</th><th>现价</th><th>变化</th><th>幅度</th><th>来源</th><th>链接</th></tr>
        </thead>
        <tbody>
          <tr v-for="i in report.down" :key="'d' + i.listing_id">
            <td>{{ i.community_name }}</td>
            <td>{{ i.layout || '—' }}</td>
            <td>{{ i.area ? i.area + '㎡' : '—' }}</td>
            <td>{{ fmtWan(i.old_price) }}</td>
            <td>{{ fmtWan(i.new_price) }}</td>
            <td class="down-text">{{ fmtWan(i.delta) }}</td>
            <td class="down-text">{{ i.delta_pct }}%</td>
            <td>{{ i.source }}</td>
            <td><a v-if="i.source_url" :href="i.source_url" target="_blank" rel="noopener">查看</a></td>
          </tr>
        </tbody>
      </table>
    </section>

    <p v-if="report && !report.up.length && !report.down.length" class="muted">
      该日没有观测到价格变化。
    </p>
    <p v-if="loading" class="muted">加载中…</p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api.js'

const report = ref(null)
const days = ref([])
const trend = ref([])
const selectedDay = ref('')
const pickedDate = ref('')
const loading = ref(false)
const error = ref('')

const summary = computed(() => (report.value && report.value.summary) || {
  up_count: 0, down_count: 0, up_avg_pct: 0, down_avg_pct: 0,
  total_tracked: 0, changed_total: 0,
})

function fmtWan(v) {
  const n = Number(v || 0)
  if (!n) return '—'
  return n >= 10000 ? (n / 10000).toFixed(1) + '万' : Math.round(n) + '元'
}

function barW(n) {
  const max = Math.max(1, ...trend.value.map(t => Math.max(t.up_count, t.down_count)))
  return Math.round((n / max) * 70)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    report.value = await api.dailyReport({ day: selectedDay.value })
    trend.value = (await api.reportRange({ days: 7 })).items || []
  } catch (e) {
    error.value = '报告加载失败：' + (e.message || e)
  } finally {
    loading.value = false
  }
}

function onPickDate() {
  if (pickedDate.value) {
    selectedDay.value = pickedDate.value
    load()
  }
}

onMounted(async () => {
  try {
    const r = await api.reportDays()
    days.value = r.items || []
    if (days.value.length) selectedDay.value = days.value[0].day
  } catch (e) { /* 日期列表失败不阻塞主报告 */ }
  await load()
})
</script>

<style scoped>
.reports { max-width: 1080px; margin: 0 auto; padding: 16px; }
.muted { color: #888; font-size: 13px; }
.report-bar { display: flex; gap: 8px; margin: 12px 0; flex-wrap: wrap; }
.report-bar select, .report-bar input, .report-bar button { padding: 6px 10px; border: 1px solid #ddd; border-radius: 6px; }
.summary-cards { display: flex; gap: 12px; margin: 12px 0; flex-wrap: wrap; }
.card { flex: 1; min-width: 140px; background: #f7f8fa; border-radius: 10px; padding: 14px; text-align: center; }
.card.up { background: #fdf0ef; }
.card.down { background: #eef7f0; }
.card .num { font-size: 26px; font-weight: 700; }
.card.up .num { color: #d03050; }
.card.down .num { color: #18a058; }
.card .lab { font-size: 13px; color: #555; margin-top: 4px; }
.card .sub { font-size: 12px; color: #999; margin-top: 2px; }
.report-table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 8px 0 20px; }
.report-table th, .report-table td { border-bottom: 1px solid #eee; padding: 7px 8px; text-align: left; white-space: nowrap; }
.report-table th { color: #666; font-weight: 600; background: #fafafa; }
.up-text { color: #d03050; }
.down-text { color: #18a058; }
.up-title { color: #d03050; }
.down-title { color: #18a058; }
.trend { margin: 16px 0; }
.trend-row { display: flex; align-items: center; gap: 6px; margin: 4px 0; }
.t-day { width: 44px; font-size: 12px; color: #666; }
.t-bar { height: 14px; border-radius: 3px; min-width: 2px; font-size: 11px; color: #fff; line-height: 14px; padding-left: 4px; }
.t-bar.up { background: #d03050; }
.t-bar.down { background: #18a058; }
.legend { font-size: 12px; color: #888; margin-top: 6px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin: 0 4px 0 10px; }
.dot.up { background: #d03050; }
.dot.down { background: #18a058; }
.err { color: #d03050; padding: 8px 0; }
a { color: #4098fc; text-decoration: none; }
</style>
