<!--
  小区详情：以小区为单位看全部在售房源。
  回答三个买家真正关心的问题：
    这个小区现在有几套在卖 / 卖得多快 / 同户型之间差在哪
-->
<template>
  <div v-if="loading" class="card card-pad"><el-skeleton :rows="6" animated /></div>

  <div v-else-if="!data">
    <el-empty description="小区不存在" />
  </div>

  <div v-else>
    <!-- 小区档案 -->
    <div class="hero card">
      <div class="hero-main">
        <div class="hero-title">
          {{ c.name }}
          <span v-if="c.is_extended" class="tag warn">扩展区</span>
          <el-button
            size="small"
            :type="data.watched ? 'warning' : 'default'"
            :loading="watchBusy"
            style="margin-left:4px"
            @click="toggleWatch"
          >
            <el-icon><StarFilled v-if="data.watched" /><Star v-else /></el-icon>
            &nbsp;{{ data.watched ? '已关注波动' : '关注房价波动' }}
          </el-button>
        </div>
        <div class="hero-sub">
          {{ c.district }} · 建成 {{ c.build_year }} 年 · {{ c.has_elevator ? '有电梯' : '无电梯' }}
          <span v-if="c.school_district"> · 对口 {{ c.school_district }}</span>
        </div>
        <div class="hero-tags">
          <span class="tag" :class="turnoverTag(s.months_to_clear)">去化 {{ fmt.months(s.months_to_clear) }}</span>
          <span class="tag">流动性 {{ Math.round(c.liquidity_score || 0) }}</span>
          <span class="tag">近90天成交 {{ c.deal_active_90d }} 套</span>
          <span v-if="s.red_n" class="tag danger">🔴 {{ s.red_n }}</span>
          <span v-if="s.yellow_n" class="tag warn">🟡 {{ s.yellow_n }}</span>
        </div>
      </div>
      <div class="hero-metrics">
        <div class="hm">
          <div class="hm-val mono">{{ fmt.money(s.median_unit) }}</div>
          <div class="hm-key">中位单价 元/㎡</div>
        </div>
        <div class="hm">
          <div class="hm-val mono">{{ fmt.wanOnly(s.median_total) }}</div>
          <div class="hm-key">中位总价</div>
        </div>
        <div class="hm">
          <div class="hm-val mono">{{ s.listing_n }}</div>
          <div class="hm-key">在售套数</div>
        </div>
        <div class="hm">
          <div class="hm-val mono">{{ s.target_area_n }}</div>
          <div class="hm-key">120-150㎡</div>
        </div>
      </div>
    </div>

    <div class="liquidity-note section-gap">
      <b>能不能卖得掉：</b>{{ s.liquidity_note }}
    </div>

    <!-- 真实租售比（最近一批租房样本口径） -->
    <div v-if="rent && rent.available" class="liquidity-note section-gap">
      <b>租售比视角：</b>
      最近 {{ rent.collected_date }} 抓到 {{ rent.sample_n }} 套在租，
      租金中位 <b class="mono">{{ rent.median_rent }}</b> 元/月
      （{{ rent.median_rent_pm }} 元/㎡·月），
      年化租金回报约 <b class="mono">{{ rent.rent_yield_pct != null ? rent.rent_yield_pct + '%' : '—' }}</b>。
      <span class="tiny muted">{{ rent.note }}</span>
    </div>
    <div v-else-if="rent && !rent.available" class="liquidity-note section-gap tiny muted">
      租售比：{{ rent.note }}
    </div>

    <!-- 关注小区 + 挂牌中位价波动 -->
    <div class="card card-pad section-gap">
      <div class="card-title">
        挂牌中位价波动
        <span class="hint">跟踪在售房源的中位单价，不是成交价</span>
        <el-button link size="small" style="margin-left:auto" :loading="watchBusy" @click="toggleWatch">
          {{ data.watched ? '取消关注' : '关注这个小区' }}
        </el-button>
      </div>
      <div v-if="t.points > 0">
        <div class="trend-kpi">
          <div class="tk">
            <div class="tk-val mono">{{ fmt.money(t.current) }}</div>
            <div class="tk-key">当前中位单价 元/㎡</div>
          </div>
          <div v-for="d in trendDeltas" :key="d.label" class="tk">
            <div class="tk-val mono" :style="{ color: deltaColor(d.value) }">{{ fmt.signed(d.value) }}</div>
            <div class="tk-key">{{ d.label }}</div>
          </div>
          <div class="tk">
            <div class="tk-val mono">{{ t.points }}</div>
            <div class="tk-key">观测点（{{ t.backfill_points || 0 }} 个回推）</div>
          </div>
        </div>
        <Chart :option="trendChart" height="200px" empty-text="观测点不足，暂无法绘制曲线" />
        <div class="tiny muted" style="margin-top:8px;line-height:1.75">
          {{ t.quality_note }}<br />{{ t.disclaimer }}
        </div>
        <div class="tiny" style="margin-top:8px;color:var(--brand-dark)">
          {{ data.watched
            ? '已关注：可在「观察清单」查看曲线；近 7 天波动超过 2% 会推送提醒。'
            : '点「关注这个小区」后，系统每天记录一次中位单价，从今天开始累积。' }}
        </div>
      </div>
      <el-empty v-else description="该小区暂无价格观测点，关注后从今天开始累积" :image-size="62" />
    </div>

    <!-- 在售房源 -->
    <div class="card card-pad section-gap">
      <div class="card-title">
        在售房源（{{ listings.length }} 套）
        <span class="hint">点任意一行查看「为什么便宜 / 为什么可能是坑」</span>
      </div>

      <div class="filters">
        <el-radio-group v-model="sortKey" size="small">
          <el-radio-button value="unit">单价↑</el-radio-button>
          <el-radio-button value="total">总价↑</el-radio-button>
          <el-radio-button value="area">面积↓</el-radio-button>
          <el-radio-button value="days">挂牌↓</el-radio-button>
          <el-radio-button value="score">捡漏分↓</el-radio-button>
        </el-radio-group>
        <span class="tiny muted">按当前排序找「最该先看」的那一套</span>
      </div>

      <!-- 桌面：表格便于横向比较同小区房源 -->
      <div v-if="!isMobile" class="table-scroll">
        <el-table :data="sortedListings" size="small" style="width:100%" @row-click="openListing" row-class-name="clickable-row">
          <el-table-column label="户型" min-width="108">
            <template #default="{ row }">
              <span class="strong">{{ row.layout || `${row.rooms}室` }}</span>
            </template>
          </el-table-column>
          <el-table-column label="面积" width="82" sortable :sort-by="(r) => Number(r.area || 0)">
            <template #default="{ row }">{{ fmt.area(row.area) }}</template>
          </el-table-column>
          <el-table-column prop="floor_text" label="楼层" width="90" />
          <el-table-column label="单价" width="96" sortable :sort-by="(r) => Number(r.unit_price || 0)">
            <template #default="{ row }">
              <span class="mono" :class="{ 'below-median': Number(row.unit_price) < Number(s.median_unit || 0) }">
                {{ fmt.money(row.unit_price) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="总价" width="90" sortable :sort-by="(r) => Number(r.total_price || 0)">
            <template #default="{ row }"><span class="mono">{{ fmt.wanOnly(row.total_price) }}</span></template>
          </el-table-column>
          <el-table-column label="挂牌" width="80">
            <template #default="{ row }">{{ row.list_days }} 天</template>
          </el-table-column>
          <el-table-column label="调价" width="70">
            <template #default="{ row }">
              <span v-if="row.price_changes" class="tag warn">{{ row.price_changes }} 次</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="硬伤" min-width="130">
            <template #default="{ row }">
              <span v-for="t in row.defect_tags" :key="t" class="tag danger" style="margin-right:3px">{{ t }}</span>
              <span v-if="!row.defect_tags || !row.defect_tags.length" class="muted tiny">无标注</span>
            </template>
          </el-table-column>
          <el-table-column label="评分" width="86" sortable :sort-by="(r) => Number(r.score || 0)">
            <template #default="{ row }">
              <span class="grade-chip" :style="gradeStyle(row.grade)">{{ Math.round(row.score || 0) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="" width="70" align="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click.stop="openListing(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 手机：卡片列表 -->
      <div v-else class="m-list">
        <div v-for="row in sortedListings" :key="row.id" class="m-card" @click="openListing(row)">
          <div class="m-head">
            <span class="strong">{{ row.layout || `${row.rooms}室` }} · {{ fmt.area(row.area) }}</span>
            <span class="grade-chip" :style="gradeStyle(row.grade)">{{ Math.round(row.score || 0) }}</span>
          </div>
          <div class="m-price">
            <span class="strong mono">{{ fmt.wanOnly(row.total_price) }}</span>
            <span class="tiny muted mono">{{ fmt.money(row.unit_price) }} 元/㎡</span>
            <span v-if="Number(row.unit_price) < Number(s.median_unit || 0)" class="tag info">
              低于小区中位
            </span>
          </div>
          <div class="tiny muted">
            {{ row.floor_text || '—' }} · 挂牌 {{ row.list_days }} 天
            <span v-if="row.price_changes"> · 降价 {{ row.price_changes }} 次</span>
          </div>
          <div v-if="row.defect_tags && row.defect_tags.length" class="m-tags">
            <span v-for="t in row.defect_tags" :key="t" class="tag danger">{{ t }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 同户型竞争格局 -->
    <div class="card card-pad section-gap">
      <div class="card-title">
        同户型竞争格局
        <span class="hint">同户型在挂越多、价差越大，你能谈的空间越大</span>
      </div>
      <div class="layout-grid">
        <div v-for="g in data.layout_groups" :key="g.layout" class="layout-card">
          <div class="lc-head">
            <span class="strong">{{ g.layout }}</span>
            <span class="tag">{{ g.n }} 套在挂</span>
          </div>
          <div class="lc-metrics">
            <span>中位 <b class="mono">{{ fmt.money(g.median_unit) }}</b></span>
            <span v-if="g.spread_pct !== null">价差 <b>{{ g.spread_pct }}%</b></span>
          </div>
          <div class="lc-bar">
            <span class="lc-min mono">{{ fmt.money(g.min_unit) }}</span>
            <span class="lc-track"><i :style="{ width: barWidth(g) }"></i></span>
            <span class="lc-max mono">{{ fmt.money(g.max_unit) }}</span>
          </div>
          <div class="lc-note tiny muted">
            {{ spreadNote(g) }}
          </div>
        </div>
      </div>
    </div>

    <!-- 该小区异动：按房源聚合，同一套房命中多个信号只占一行 -->
    <div v-if="eventGroups.length" class="card card-pad section-gap">
      <div class="card-title">
        该小区近期异动
        <span class="hint">{{ eventGroups.length }} 套房命中 {{ data.events.length }} 个信号</span>
      </div>
      <div class="event-list">
        <div v-for="g in eventGroups" :key="g.key" class="event-row" @click="openEvent(g)">
          <span class="grade-chip" :style="gradeStyle(g.severity)">{{ gradeIcon(g.severity) }}</span>
          <span class="event-labels">
            <span v-for="l in g.labels" :key="l" class="tag info">{{ l }}</span>
          </span>
          <span class="tiny muted" style="margin-left:auto;white-space:nowrap">{{ fmt.date(g.event_date) }}</span>
          <span class="mono tiny" style="width:64px;text-align:right">
            {{ g.total_price ? fmt.wanOnly(g.total_price) : '—' }}
          </span>
        </div>
      </div>
    </div>

    <!-- 周边参照 -->
    <div class="grid-2 section-gap">
      <div class="card card-pad">
        <div class="card-title">同板块其他小区 <span class="hint">横向比价用</span></div>
        <div class="nearby-list">
          <div v-for="n in data.nearby" :key="n.id" class="nearby-row" @click="$router.push(`/community/${n.id}`)">
            <span class="strong">{{ n.name }}</span>
            <span class="tiny muted">{{ n.listing_n }} 套在售</span>
            <span class="mono" style="margin-left:auto">{{ fmt.money(n.median_unit) }}</span>
          </div>
        </div>
      </div>

      <div class="card card-pad">
        <div class="card-title">同板块新盘 <span class="hint">新房倒挂对照</span></div>
        <el-empty v-if="!data.new_homes.length" description="该板块暂无在售新盘" :image-size="60" />
        <div v-else class="nearby-list">
          <div v-for="nh in data.new_homes" :key="nh.id" class="nearby-row" @click="$router.push('/newhomes')">
            <span class="strong">{{ nh.name }}</span>
            <span class="tag" :class="nh.gap_pct > 0 ? 'info' : 'warn'">
              {{ nh.gap_pct === null ? '—' : (nh.gap_pct > 0 ? '倒挂 ' : '贵 ') + Math.abs(nh.gap_pct) + '%' }}
            </span>
            <span class="mono" style="margin-left:auto">{{ fmt.money(nh.avg_unit_price) }}</span>
          </div>
          <div class="tiny muted" style="margin-top:8px">
            名义倒挂未计装修与得房率差异，完整对比见「新房倒挂」页。
          </div>
        </div>
      </div>
    </div>

    <div class="disclaimer section-gap">{{ data.disclaimer }}</div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import Chart from '../components/Chart.vue'
import { api, fmt } from '../api'
import { curveOption } from '../charts/options'
import { useIsMobile } from '../composables/useIsMobile'

const route = useRoute()
const router = useRouter()
const { isMobile } = useIsMobile()

const loading = ref(true)
const data = ref(null)
const rent = computed(() => data.value?.rent || null)
const sortKey = ref('unit')

const c = computed(() => data.value?.community || {})
const s = computed(() => data.value?.stats || {})
const listings = computed(() => data.value?.listings || [])
const t = computed(() => data.value?.trend || {})
const watchBusy = ref(false)

// change_7d/30d/90d 是百分数数值（如 -3.2 表示 -3.2%），直接用 fmt.signed
const trendDeltas = computed(() => [
  { label: '近 7 天', value: t.value.change_7d },
  { label: '近 30 天', value: t.value.change_30d },
  { label: '近 90 天', value: t.value.change_90d },
])
function deltaColor(v) {
  if (v === null || v === undefined) return 'var(--muted)'
  // 站在买方视角：中位价下探是好事（绿），抬头是坏事（红）
  return v < 0 ? 'var(--good)' : v > 0 ? 'var(--red)' : 'var(--muted)'
}
const trendChart = computed(() => {
  const series = t.value.series || []
  if (series.length < 2) return null
  return curveOption(series.map((p) => ({ date: p.date, price: p.median_unit })))
})

async function toggleWatch() {
  watchBusy.value = true
  try {
    const id = route.params.id
    if (data.value.watched) await api.unwatchCommunity(id)
    else await api.watchCommunity(id)
    data.value.watched = !data.value.watched
    ElMessage.success(data.value.watched ? '已关注，可在观察清单里看波动' : '已取消关注')
  } catch (e) {
    ElMessage.error('操作失败：' + e.message)
  } finally {
    watchBusy.value = false
  }
}

const sortedListings = computed(() => {
  const arr = [...listings.value]
  const key = sortKey.value
  return arr.sort((a, b) => {
    if (key === 'unit') return Number(a.unit_price || 0) - Number(b.unit_price || 0)
    if (key === 'total') return Number(a.total_price || 0) - Number(b.total_price || 0)
    if (key === 'area') return Number(b.area || 0) - Number(a.area || 0)
    if (key === 'days') return Number(b.list_days || 0) - Number(a.list_days || 0)
    return Number(b.score || 0) - Number(a.score || 0)
  })
})

function gradeStyle(grade) {
  const m = fmt.gradeMeta(grade)
  return { color: m.color, background: m.bg }
}
function gradeIcon(grade) {
  return fmt.gradeMeta(grade).icon
}
function turnoverTag(m) {
  if (m === null || m === undefined) return 'danger'
  if (m <= 12) return 'info'
  if (m <= 24) return 'warn'
  return 'danger'
}
function barWidth(g) {
  const lo = Number(g.min_unit || 0)
  const hi = Number(g.max_unit || 0)
  const mid = Number(g.median_unit || 0)
  if (!hi || hi === lo) return '50%'
  return Math.max(6, Math.min(100, ((mid - lo) / (hi - lo)) * 100)) + '%'
}
function spreadNote(g) {
  if (g.n === 1) return '该户型仅此一套在挂，没有同户型竞争，议价参考较弱'
  if (g.spread_pct === null) return ''
  if (g.spread_pct >= 12) return '同户型价差很大，说明业主预期分化，是压价的好机会'
  if (g.spread_pct >= 6) return '同户型存在一定价差，可拿低价那套去谈高价那套'
  return '同户型报价接近，说明该户型价格共识较强，谈价空间有限'
}

function openListing(row) {
  router.push(`/listing/${row.id}`)
}
function openEvent(ev) {
  if (ev.target_type === 'auction') router.push(`/auction/${ev.target_id}`)
  else router.push(`/listing/${ev.target_id}`)
}

/** 同一套房常同时命中「深跌 + 连降 + 久挂」，聚合成一行读起来更清楚 */
const SEVERITY_RANK = { red: 0, yellow: 1, white: 2 }
const eventGroups = computed(() => {
  const map = new Map()
  for (const ev of data.value?.events || []) {
    const key = `${ev.target_type}-${ev.target_id}`
    const cur = map.get(key)
    if (!cur) {
      map.set(key, {
        key,
        target_type: ev.target_type,
        target_id: ev.target_id,
        severity: ev.severity,
        event_date: ev.event_date,
        total_price: ev.total_price,
        labels: [ev.event_label],
      })
    } else {
      if (!cur.labels.includes(ev.event_label)) cur.labels.push(ev.event_label)
      if ((SEVERITY_RANK[ev.severity] ?? 9) < (SEVERITY_RANK[cur.severity] ?? 9)) {
        cur.severity = ev.severity
      }
      if (ev.event_date > cur.event_date) cur.event_date = ev.event_date
    }
  }
  return [...map.values()].sort(
    (a, b) => (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9)
  )
})

async function load() {
  loading.value = true
  try {
    data.value = await api.community(route.params.id)
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(() => route.params.id, load, { immediate: true })
</script>

<style scoped>
.hero {
  display: flex; gap: 20px; padding: 18px 20px; align-items: flex-start;
  flex-wrap: wrap;
  background: linear-gradient(135deg, #ffffff 0%, #f5fbfd 100%);
  border-left: 3px solid var(--brand);
}
.hero-main { flex: 1; min-width: 240px; }
.hero-title { font-size: 20px; font-weight: 750; display: flex; align-items: center; gap: 8px; }
.hero-sub { font-size: 12.5px; color: var(--muted); margin-top: 4px; }
.hero-tags { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }
.hero-metrics { display: grid; grid-template-columns: repeat(4, auto); gap: 18px; }
.hm { text-align: right; }
.hm-val { font-size: 17px; font-weight: 720; }
.hm-key { font-size: 10.5px; color: var(--muted); margin-top: 2px; }

.liquidity-note {
  background: #fff8ec; border: 1px solid #f2dfc0; border-radius: 10px;
  padding: 10px 14px; font-size: 12.5px; color: #7a5a24; line-height: 1.7;
}
.liquidity-note b { color: #8a6520; }

.trend-kpi { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 12px; }
.tk { background: var(--panel-soft, #f6f9fb); border-radius: 9px; padding: 9px 11px; }
.tk-val { font-size: 16px; font-weight: 720; color: var(--ink); }
.tk-key { font-size: 10.5px; color: var(--muted); margin-top: 2px; }

.filters { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.table-scroll { overflow-x: auto; }
:deep(.clickable-row) { cursor: pointer; }
.below-median { color: #0e7490; font-weight: 650; }

.m-list { display: flex; flex-direction: column; gap: 10px; }
.m-card { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; }
.m-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.m-price { display: flex; align-items: baseline; gap: 8px; margin: 5px 0 3px; flex-wrap: wrap; }
.m-price .strong { font-size: 16px; }
.m-tags { display: flex; gap: 4px; margin-top: 6px; flex-wrap: wrap; }

.layout-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.layout-card { border: 1px solid var(--line); border-radius: 10px; padding: 11px 13px; }
.lc-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 13px; }
.lc-metrics { display: flex; gap: 14px; font-size: 12px; color: var(--muted); margin: 7px 0 5px; }
.lc-metrics b { color: var(--ink); }
.lc-bar { display: flex; align-items: center; gap: 7px; }
.lc-min, .lc-max { font-size: 10.5px; color: var(--muted); }
.lc-track { flex: 1; height: 6px; background: #eef2f6; border-radius: 4px; overflow: hidden; }
.lc-track i { display: block; height: 100%; background: linear-gradient(90deg, #7dd3fc, #0e7490); border-radius: 4px; }
.lc-note { margin-top: 6px; line-height: 1.65; }

.event-list { display: flex; flex-direction: column; }
.event-row {
  display: flex; align-items: center; gap: 8px; padding: 7px 0;
  border-bottom: 1px dashed var(--line); cursor: pointer; font-size: 12.5px;
}
.event-row:last-child { border-bottom: none; }
.event-row:hover { background: #f8fcfd; }
.event-labels { display: flex; gap: 4px; flex-wrap: wrap; min-width: 0; }

.nearby-list { display: flex; flex-direction: column; gap: 2px; }
.nearby-row {
  display: flex; align-items: center; gap: 8px; padding: 7px 0;
  border-bottom: 1px dashed var(--line); cursor: pointer; font-size: 12.5px;
}
.nearby-row:last-child { border-bottom: none; }
.nearby-row:hover { color: var(--brand); }

@media (max-width: 860px) {
  .hero { padding: 14px; gap: 14px; }
  .hero-title { font-size: 17px; flex-wrap: wrap; }
  .trend-kpi { grid-template-columns: repeat(3, 1fr); }
  .hero-metrics { grid-template-columns: repeat(2, 1fr); gap: 10px; width: 100%; }
  .hm { text-align: left; background: #f6f9fb; border-radius: 8px; padding: 7px 10px; }
  .layout-grid { grid-template-columns: 1fr; }
}
</style>
