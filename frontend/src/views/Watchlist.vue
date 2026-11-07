<!-- 观察清单：收藏 → 持续跟踪 → 每周复盘（哪些更值得看、哪些已涨价剔除） -->
<template>
  <div v-loading="loading">
    <!-- 周报 -->
    <div class="card card-pad section-gap">
      <div class="card-title">
        观察周报
        <span class="hint">{{ report.week_start || '' }} 起 · 共观察 {{ report.total || 0 }} 项</span>
        <el-button size="small" style="margin-left:auto" @click="load">刷新</el-button>
      </div>
      <div class="grid-2">
        <div>
          <div class="tiny strong" style="color:var(--good);margin-bottom:6px">✅ 仍值得看（{{ (report.still_worth || []).length }}）</div>
          <div v-if="!(report.still_worth || []).length" class="tiny muted">暂无</div>
          <div v-for="w in (report.still_worth || []).slice(0, 5)" :key="w.id" class="report-row" @click="open(w)">
            <span class="strong small">{{ w.community_name }}</span>
            <span class="tiny muted">{{ w.district }}</span>
            <span class="tag" :class="w.change_pct < 0 ? 'info' : ''">{{ changeLabel(w) }}</span>
            <span class="mono tiny" style="margin-left:auto">{{ reportMetric(w) }}</span>
          </div>
        </div>
        <div>
          <div class="tiny strong" style="color:var(--red);margin-bottom:6px">🗑 建议剔除（{{ (report.drop_out || []).length }}）</div>
          <div v-if="!(report.drop_out || []).length" class="tiny muted">暂无</div>
          <div v-for="w in (report.drop_out || []).slice(0, 5)" :key="w.id" class="report-row" @click="open(w)">
            <span class="strong small">{{ w.community_name }}</span>
            <span class="tag danger">{{ changeLabel(w) }}</span>
            <span class="mono tiny" style="margin-left:auto">{{ reportMetric(w) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 关注小区横向对比：把曲线叠在一张图上看谁在跌、谁横盘 -->
    <div v-if="commItems.length >= 2" class="card card-pad section-gap">
      <div class="card-title">
        关注小区横向对比
        <span class="hint">中位挂牌单价（元/㎡）· 曲线各自有观测点才画，缺口表示当天无快照</span>
      </div>
      <Chart :option="compareChart" height="280px" />
      <div class="compare-row compare-head">
        <span>小区</span><span>板块</span><span>当前中位</span><span>加入时</span><span>变化</span><span>观测点</span>
      </div>
      <div v-for="c in commItems" :key="c.id" class="compare-row" style="cursor:pointer" @click="open(c)">
        <span class="strong small">{{ c.community_name }}</span>
        <span class="tiny muted">{{ c.district }}</span>
        <span class="mono small">{{ fmt.money(c.last_price) }}</span>
        <span class="mono tiny muted">{{ fmt.money(c.base_price) }}</span>
        <span class="mono tiny" :style="{ color: c.change_pct < 0 ? 'var(--good)' : c.change_pct > 0 ? 'var(--red)' : 'var(--muted)' }">
          {{ c.change_pct ? (c.change_pct > 0 ? '+' : '') + fmt.pct(c.change_pct) : '—' }}
        </span>
        <span class="tiny muted">{{ (c.curve || []).length }}</span>
      </div>
    </div>

    <!-- 清单 -->
    <div class="card card-pad">
      <div class="card-title">
        跟踪中的房源与小区
        <span class="hint">小区项跟踪中位单价（元/㎡）；状态可改，备注只存在本地库里</span>
      </div>
      <el-empty v-if="!items.length" description="还没有收藏。去首页或异动专区点开一套房源，加入观察清单。" :image-size="80" />
      <div v-for="it in items" :key="it.id" class="watch-row">
        <div style="flex:1;min-width:220px">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="strong" style="cursor:pointer" @click="open(it)">{{ it.community_name }}</span>
            <span v-if="it.target_type === 'auction'" class="tag danger">法拍</span>
            <span v-else-if="it.target_type === 'community'" class="tag info">小区 · 房价波动</span>
            <span v-if="it.target_type !== 'community'" class="grade-chip" :style="{ color: fmt.gradeMeta(it.grade).color, background: fmt.gradeMeta(it.grade).bg }">
              {{ fmt.gradeMeta(it.grade).icon }} {{ Math.round(it.score || 0) }} 分
            </span>
          </div>
          <div class="tiny muted" style="margin-top:2px">
            <template v-if="it.target_type === 'community'">
              {{ it.district }} · 跟踪全小区在售中位单价 · {{ (it.curve || []).length }} 个观测点
            </template>
            <template v-else>
              {{ it.district }} · {{ it.layout || '—' }} · {{ fmt.area(it.area) }}
              <span v-if="it.price_changes"> · 调价 {{ it.price_changes }} 次</span>
              <span v-if="it.list_days"> · 挂牌 {{ it.list_days }} 天</span>
            </template>
          </div>
        </div>

        <div style="width:130px">
          <div class="mono strong">{{ priceText(it) }}</div>
          <div class="tiny" :style="{ color: it.change_pct < 0 ? 'var(--good)' : it.change_pct > 0 ? 'var(--red)' : 'var(--muted)' }">
            加入时 {{ baseText(it) }}
            <span v-if="it.change_pct">（{{ it.change_pct > 0 ? '+' : '' }}{{ fmt.pct(it.change_pct) }}）</span>
          </div>
        </div>

        <div style="width:110px">
          <Chart :option="curveOpt(it.curve)" height="40px" empty-text=" " />
        </div>

        <div style="width:120px">
          <el-select :model-value="it.follow_status" size="small" @change="(v) => changeStatus(it, v)">
            <el-option v-for="s in statusOptions" :key="s" :value="s" :label="statusLabel(s)" />
          </el-select>
        </div>

        <div style="width:250px">
          <el-input
            :model-value="it.note" size="small" placeholder="备注（如：业主置换急售）"
            @change="(v) => saveNote(it, v)"
          />
        </div>

        <el-button link type="danger" size="small" @click="remove(it)">移除</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import Chart from '../components/Chart.vue'
import { api, fmt } from '../api'
import { curveOption } from '../charts/options'

const router = useRouter()
const loading = ref(true)
const items = ref([])
const report = ref({})
const statusOptions = ref(['watching', 'contacted', 'viewed', 'rejected', 'done'])

// ---- 关注小区横向对比 ----
const commItems = computed(() => items.value.filter((x) => x.target_type === 'community'))
const compareChart = computed(() => {
  const list = commItems.value.filter((c) => (c.curve || []).length > 1)
  if (list.length < 2) return null
  const dateSet = new Set()
  for (const c of list) for (const p of c.curve) dateSet.add(p.date)
  const dates = [...dateSet].sort()
  const COLORS = ['#0e7490', '#d97706', '#e11d48', '#059669', '#7c3aed', '#64748b']
  return {
    grid: { left: 10, right: 14, top: 34, bottom: 12, containLabel: true },
    legend: { top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : Number(v).toLocaleString() + ' 元/㎡') },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10, formatter: (v) => (v / 1000).toFixed(1) + 'k' } },
    series: list.map((c, i) => {
      const byDate = Object.fromEntries((c.curve || []).map((p) => [p.date, p.price]))
      return {
        name: c.community_name, type: 'line', smooth: true, symbolSize: 4,
        connectNulls: true,
        lineStyle: { color: COLORS[i % COLORS.length], width: 2 },
        itemStyle: { color: COLORS[i % COLORS.length] },
        data: dates.map((d) => byDate[d] ?? null),
      }
    }),
  }
})

const STATUS_LABEL = {
  watching: '观察中', contacted: '已联系', viewed: '已看房', rejected: '已排除', done: '已结束',
}
function statusLabel(s) { return STATUS_LABEL[s] || s }
function curveOpt(curve) { return curve?.length > 1 ? curveOption(curve) : null }
// 周报里的变动标签：涨 / 跌 / 没动但分数不够
function changeLabel(w) {
  const c = Number(w.change_pct || 0)
  if (c > 0.001) return `涨价 ${fmt.pct(c)}`
  if (c < -0.001) return `继续下探 ${fmt.pct(Math.abs(c))}`
  return '价格未变'
}

// 小区关注跟踪的是「元/㎡」的中位单价，不能按总价套「万」，也不能显示房源专属字段
function priceText(it) {
  return it.target_type === 'community' ? `${fmt.money(it.last_price)} 元/㎡` : fmt.wan(it.last_price)
}
function baseText(it) {
  return it.target_type === 'community' ? `${fmt.money(it.base_price)} 元/㎡` : fmt.wan(it.base_price)
}
function reportMetric(w) {
  return w.target_type === 'community' ? '小区中位价' : `${Math.round(w.score || 0)} 分`
}

function open(it) {
  const PATHS = {
    auction: (id) => `/auction/${id}`,
    community: (id) => `/community/${id}`,
    listing: (id) => `/listing/${id}`,
  }
  router.push((PATHS[it.target_type] || PATHS.listing)(it.target_id))
}

async function load() {
  loading.value = true
  try {
    const [w, r] = await Promise.all([api.watchlist(), api.weeklyReport()])
    items.value = w.items
    statusOptions.value = w.status_options || statusOptions.value
    report.value = r
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

async function changeStatus(it, status) {
  try {
    await api.updateWatch(it.id, { follow_status: status })
    it.follow_status = status
    ElMessage.success(`已标记为「${statusLabel(status)}」`)
  } catch (e) {
    ElMessage.error('更新失败：' + e.message)
  }
}
async function saveNote(it, note) {
  try {
    await api.updateWatch(it.id, { note })
    it.note = note
    ElMessage.success('备注已保存')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  }
}
async function remove(it) {
  try {
    await api.removeWatch(it.id)
    items.value = items.value.filter((x) => x.id !== it.id)
    ElMessage.success('已移除')
  } catch (e) {
    ElMessage.error('移除失败：' + e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.compare-row {
  display: grid; grid-template-columns: 2fr 1fr 1.2fr 1.2fr 0.9fr 0.7fr;
  gap: 8px; align-items: center; padding: 7px 0; border-bottom: 1px dashed var(--line);
}
.compare-row:last-child { border-bottom: none; }
.compare-head { color: var(--muted); font-size: 11px; border-bottom: 1px solid var(--line); }
@media (max-width: 860px) { .compare-row { grid-template-columns: 2fr 1fr 1fr 1fr; } 
  .compare-row > :nth-child(2), .compare-row > :nth-child(6) { display: none; } }
.report-row {
  display: flex; align-items: center; gap: 8px; padding: 6px 0;
  border-bottom: 1px dashed var(--line); cursor: pointer; font-size: 12.5px;
}
.report-row:last-child { border-bottom: none; }
.report-row:hover { color: var(--brand); }
.watch-row {
  display: flex; align-items: center; gap: 14px; padding: 12px 0;
  border-bottom: 1px solid var(--line); flex-wrap: wrap;
}
.watch-row:last-child { border-bottom: none; }

/* 窄屏：信息独占一行，价格与迷你曲线并排，状态下拉与备注各占一行。
   注意必须同时重置 flex——行内写的是 flex:1（flex-basis:0），
   只改 width 会被 flex-basis 忽略，文字会被压成一字一列。 */
@media (max-width: 860px) {
  .watch-row { gap: 8px; padding: 12px 0 14px; align-items: flex-start; }
  .watch-row > div {
    flex: 1 1 100% !important; width: 100% !important; min-width: 0 !important;
  }
  .watch-row > div:nth-child(2),
  .watch-row > div:nth-child(3) {
    flex: 0 0 calc(50% - 5px) !important; width: calc(50% - 5px) !important;
  }
  .watch-row > .el-button { flex: 0 0 auto !important; width: auto !important; margin-left: auto; }
}
</style>
