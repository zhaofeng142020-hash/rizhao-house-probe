<!-- 异动专区：7 类信号分标签页。默认只看红黄，可打开「含仅记录」 -->
<template>
  <div>
    <div class="card card-pad section-gap">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <div>
          <div style="font-weight:650">异动识别结果</div>
          <div class="tiny muted">每日增量计算产出；同一房源同类事件每天只记一条，避免刷屏</div>
        </div>
        <div style="margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <el-select v-model="district" placeholder="全部板块" clearable size="small" style="width:120px">
            <el-option v-for="d in districts" :key="d.district" :label="d.district" :value="d.district" />
          </el-select>
          <el-select v-model="days" size="small" style="width:110px">
            <el-option :value="7" label="近 7 天" />
            <el-option :value="30" label="近 30 天" />
            <el-option :value="90" label="近 90 天" />
          </el-select>
          <el-switch v-model="includeWhite" size="small" active-text="含仅记录" />
        </div>
      </div>
    </div>

    <!-- 降价时机规律：历史降价集中在几月/星期几（观察口径，不是预测） -->
    <div v-if="timing && timing.total_drops" class="card card-pad section-gap">
      <div class="card-title">
        降价时机规律
        <span class="hint">近 {{ timing.days }} 天共 {{ timing.total_drops }} 次降价 · 峰值 {{ timing.peak_month }}</span>
      </div>
      <div class="timing-grid">
        <Chart :option="monthChart" height="150px" />
        <Chart :option="weekdayChart" height="150px" />
      </div>
      <div class="tiny muted" style="margin-top:6px">{{ timing.note }} {{ timing.disclaimer }}</div>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane v-for="t in tabs" :key="t.key" :name="t.key">
        <template #label>
          <span>{{ t.label }}</span>
          <span v-if="countOf(t.key)" class="tab-count">{{ countOf(t.key) }}</span>
        </template>
      </el-tab-pane>
    </el-tabs>

    <div v-loading="loading">
      <div v-if="!visible.length" class="card card-pad empty-state">
        该标签下暂无{{ includeWhite ? '' : '红/黄级' }}异动。
        可以打开「含仅记录」查看被判定为高风险而压分的条目——它们不是漏，是被否掉的嫌疑。
      </div>

      <div v-else class="card" style="overflow:hidden">
        <div v-for="e in visible" :key="e.id" class="event-row" @click="open(e)">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="grade-chip" :style="{ color: meta(e.severity).color, background: meta(e.severity).bg }">
              {{ meta(e.severity).icon }} {{ meta(e.severity).label }}
            </span>
            <span class="strong">{{ e.community_name }}</span>
            <span class="tiny muted">{{ e.district }} · {{ e.layout || '—' }} · {{ fmt.area(e.area) }}</span>
            <span v-if="e.event_type === 'auction_deal'" class="tag danger">法拍</span>
            <span style="margin-left:auto" class="mono strong">{{ Math.round(e.score || 0) }} 分</span>
          </div>

          <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:6px" class="tiny">
            <span>当前价 <b class="mono">{{ fmt.wan(e.price_now) }}</b></span>
            <span v-if="e.price_now && e.area">单价 <b class="mono">{{ fmt.money(e.price_now / e.area) }}</b></span>
            <span v-if="e.ref_unit_price">可比中位 <b class="mono">{{ fmt.money(e.ref_unit_price) }}</b></span>
            <span v-if="e.drop_pct" style="color:var(--good)">
              {{ dropLabel(e.event_type) }} <b class="mono">{{ (e.drop_pct * 100).toFixed(1) }}%</b>
            </span>
            <span v-if="e.price_changes">调价 {{ e.price_changes }} 次</span>
            <span v-if="e.list_days">挂牌 {{ e.list_days }} 天</span>
            <span class="muted">{{ e.event_date }}</span>
          </div>

          <div v-if="detailText(e)" class="tiny muted" style="margin-top:4px;line-height:1.7">
            {{ detailText(e) }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmt } from '../api'
import Chart from '../components/Chart.vue'

const router = useRouter()
const tabs = [
  { key: 'sudden_drop', label: '突发降价' },
  { key: 'deep_dip', label: '深跌' },
  { key: 'long_listed', label: '久挂让利' },
  { key: 'auction_deal', label: '法拍机会' },
  { key: 'miskill', label: '错杀候选' },
  { key: 'board_divergence', label: '板块背离' },
  { key: 'new_home', label: '新房倒挂' },
]
// 不同事件的「降幅」含义不同，标签必须区分，否则会误导
const DROP_LABEL = {
  sudden_drop: '本次降幅',
  deep_dip: '低于中枢',
  long_listed: '累计降幅',
  miskill: '低于中枢',
  board_divergence: '低于板块',
  new_home: '低于周边二手',
  auction_deal: '到手价净折扣',
}

const activeTab = ref('sudden_drop')
const allItems = ref([])
const loading = ref(false)
const district = ref('')
const days = ref(30)
const includeWhite = ref(false)
const districts = ref([])
const timing = ref(null)

const monthChart = computed(() => {
  if (!timing.value) return null
  return {
    grid: { left: 8, right: 8, top: 26, bottom: 6, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: timing.value.months.map((m) => m.label),
      axisLabel: { fontSize: 9, interval: 0 }, axisTick: { show: false } },
    yAxis: { type: 'value', show: false },
    series: [{ type: 'bar', barWidth: '55%', data: timing.value.months.map((m) => m.n),
      itemStyle: { color: 'rgba(14,116,144,.55)', borderRadius: [3, 3, 0, 0] } }],
  }
})
const weekdayChart = computed(() => {
  if (!timing.value) return null
  return {
    grid: { left: 8, right: 8, top: 26, bottom: 6, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: timing.value.weekdays.map((m) => m.label),
      axisLabel: { fontSize: 9, interval: 0 }, axisTick: { show: false } },
    yAxis: { type: 'value', show: false },
    series: [{ type: 'bar', barWidth: '55%', data: timing.value.weekdays.map((m) => m.n),
      itemStyle: { color: 'rgba(217,119,6,.55)', borderRadius: [3, 3, 0, 0] } }],
  }
})

function meta(sev) { return fmt.gradeMeta(sev) }
function dropLabel(type) { return DROP_LABEL[type] || '幅度' }

// 先按「红黄 / 含白」过滤，再按标签分组；这样标签角标与可见条目永远一致
const pool = computed(() => (includeWhite.value
  ? allItems.value
  : allItems.value.filter((x) => x.severity !== 'white')))
const visible = computed(() => pool.value.filter((x) => x.event_type === activeTab.value))
function countOf(key) { return pool.value.filter((x) => x.event_type === key).length }

function open(e) {
  if (e.target_type === 'auction') router.push(`/auction/${e.target_id}`)
  else router.push(`/listing/${e.target_id}`)
}
function detailText(e) {
  const d = e.detail || {}
  return d.note || d.trigger || (d.list_days ? `挂 ${d.list_days} 天` : '')
}

async function load() {
  loading.value = true
  try {
    const res = await api.anomalies({ days: days.value, district: district.value, limit: 300 })
    allItems.value = res.items || []
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

watch([district, days], () => load())

onMounted(async () => {
  try {
    districts.value = (await api.districts()).items
  } catch (e) { /* 板块列表失败不阻塞 */ }
  try {
    timing.value = await api.dropTiming(365)
  } catch (e) { /* 降价规律失败不阻塞 */ }
  load()
})
</script>

<style scoped>
.timing-grid { display: grid; grid-template-columns: 3fr 2fr; gap: 12px; }
@media (max-width: 860px) { .timing-grid { grid-template-columns: 1fr; } }
.tab-count {
  display: inline-block; margin-left: 5px; padding: 0 6px; border-radius: 999px;
  background: var(--panel-soft); border: 1px solid var(--line);
  font-size: 10px; line-height: 16px; color: var(--muted);
}
.event-row {
  padding: 12px 18px; border-bottom: 1px solid var(--line); cursor: pointer; transition: background .14s;
}
.event-row:last-child { border-bottom: none; }
.event-row:hover { background: #f8fbfc; }
</style>
