<!-- 首页「今日可留意」：先给红黄线索，再给板块与异动概览 -->
<template>
  <div>
    <div class="stat-grid section-gap">
      <div class="stat accent-red">
        <div class="stat-label">🔴 重点留意</div>
        <div class="stat-value">{{ counts.red || 0 }}</div>
        <div class="stat-sub">分数≥75 且风险低，建议 48h 内核实</div>
      </div>
      <div class="stat accent-yellow">
        <div class="stat-label">🟡 建议观察</div>
        <div class="stat-value">{{ counts.yellow || 0 }}</div>
        <div class="stat-sub">进观察清单，先电话后跑腿</div>
      </div>
      <div class="stat accent-brand">
        <div class="stat-label">近 7 天异动</div>
        <div class="stat-value">{{ counts.events_7d || 0 }}</div>
        <div class="stat-sub">突发事件 + 法拍折扣合计</div>
      </div>
      <div class="stat">
        <div class="stat-label">在挂房源 / 观察中</div>
        <div class="stat-value">{{ counts.listings || 0 }} <span style="font-size:14px;color:var(--muted)">/ {{ counts.watched || 0 }}</span></div>
        <div class="stat-sub">共 {{ counts.communities || 0 }} 个小区 · {{ counts.auctions || 0 }} 条法拍</div>
      </div>
    </div>

    <div class="disclaimer section-gap">
      <strong>先质疑，再判断：</strong>下面每一条都只是「值得看一下」的候选，不是推荐。挂牌价 ≠ 成交价，捡漏分仅作观察参考，最终以实地尽调为准。
    </div>

    <!-- 市场大盘：全城口径的库存 / 中位价 / 新增 / 下架，判断该不该着急买 -->
    <div v-if="market" class="card card-pad section-gap">
      <div class="card-title">
        市场大盘
        <span class="hint">全城真实采集口径 · 下架≠成交，仅作活跃度代理</span>
      </div>
      <template v-if="market.points && market.current && market.current.listing_n">
        <div class="market-stats">
          <div class="ms-item">
            <div class="ms-label">在售总量</div>
            <div class="ms-value">{{ market.current?.listing_n || 0 }} <span class="ms-unit">套</span></div>
          </div>
          <div class="ms-item">
            <div class="ms-label">中位挂牌单价</div>
            <div class="ms-value">{{ Math.round(market.current?.median_unit || 0).toLocaleString() }} <span class="ms-unit">元/㎡</span></div>
            <div class="ms-sub" :class="signClass(market.change_7d)">近7天 {{ fmt.signed(market.change_7d) }}</div>
          </div>
          <div class="ms-item">
            <div class="ms-label">近 7 天新增</div>
            <div class="ms-value">{{ market.new_7d || 0 }} <span class="ms-unit">套</span></div>
          </div>
          <div class="ms-item">
            <div class="ms-label">近 7 天下架</div>
            <div class="ms-value">{{ market.delist_7d || 0 }} <span class="ms-unit">套</span></div>
          </div>
          <div class="ms-item">
            <div class="ms-label">去化近似</div>
            <div class="ms-value">{{ market.clearance_months != null ? market.clearance_months : '—' }} <span class="ms-unit">个月</span></div>
          </div>
          <div v-if="rents && rents.city && rents.city.yield_median != null" class="ms-item">
            <div class="ms-label">全城租售比</div>
            <div class="ms-value">{{ rents.city.yield_median }} <span class="ms-unit">%/年</span></div>
            <div class="ms-sub">{{ rents.city.sample_n }} 套在租样本</div>
          </div>
          <div v-if="deals && deals.total > 0" class="ms-item">
            <div class="ms-label">真实成交锚点</div>
            <div class="ms-value">{{ deals.total }} <span class="ms-unit">条</span></div>
            <div class="ms-sub">法拍中位折价 {{ fmt.signed(deals.summary?.median_discount_pct) }} · 非二手成交</div>
          </div>
        </div>
        <Chart :option="marketChart" height="230px" />
        <div class="tiny" style="color:var(--muted);margin-top:6px">{{ market.clearance_note }}</div>
      </template>
      <div v-else class="empty-state" style="padding:14px 0">
        暂无真实采集房源，大盘为空（演示样本不计入）。接入抓取并跑过一天后这里就会出现趋势。
      </div>
    </div>

    <!-- 快捷入口：把「按小区 / 按面积段 / 按新房」三条找房路径直接摆出来 -->
    <div class="quick-grid section-gap">
      <div class="quick-card" @click="$router.push('/map')">
        <div class="q-icon">🗺️</div>
        <div class="q-body">
          <div class="q-title">小区地图</div>
          <div class="q-desc">一眼看到小区在哪、贵不贵、卖得动不动</div>
        </div>
        <div class="q-arrow">→</div>
      </div>
      <div class="quick-card" @click="$router.push('/area')">
        <div class="q-icon">📐</div>
        <div class="q-body">
          <div class="q-title">改善 120-150㎡</div>
          <div class="q-desc">你重点关注的面积段，二手 + 法拍 + 新房一起看</div>
        </div>
        <div class="q-arrow">→</div>
      </div>
      <div class="quick-card" @click="$router.push('/newhomes')">
        <div class="q-icon">🏗️</div>
        <div class="q-body">
          <div class="q-title">新房倒挂</div>
          <div class="q-desc">扣掉装修与得房率之后，新房到底便不便宜</div>
        </div>
        <div class="q-arrow">→</div>
      </div>
      <div class="quick-card" @click="$router.push('/admin')">
        <div class="q-icon">⚙️</div>
        <div class="q-body">
          <div class="q-title">管理台</div>
          <div class="q-desc">企业微信推送配置 · 数据更新状态 · 权限</div>
        </div>
        <div class="q-arrow">→</div>
      </div>
    </div>

    <div class="row" style="align-items:flex-start">
      <div style="flex:1 1 68%">
        <div class="card card-pad">
          <div class="card-title">
            今日可留意
            <span class="hint">{{ today }}</span>
            <el-radio-group v-model="gradeFilter" size="small" style="margin-left:auto">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="red">🔴 重点</el-radio-button>
              <el-radio-button value="yellow">🟡 观察</el-radio-button>
            </el-radio-group>
          </div>

          <el-skeleton v-if="loading" :rows="6" animated />
          <div v-else>
            <div v-if="filteredListings.length" class="grid-cards">
              <LeakCard
                v-for="item in filteredListings"
                :key="'l' + item.id"
                :item="item"
                @open="open"
              />
            </div>
            <div v-else class="empty-state">当前筛选条件下没有挂牌房源；试试切到「全部」或放宽阈值</div>

            <template v-if="auctions.length">
              <div class="divider"></div>
              <div class="card-title" style="margin-bottom:10px">
                法拍机会
                <span class="hint">折扣按到手价计算，不是起拍价</span>
              </div>
              <div class="grid-cards">
                <LeakCard
                  v-for="item in auctions"
                  :key="'a' + item.id"
                  :item="{ ...item, kind: 'auction' }"
                  @open="open"
                />
              </div>
            </template>
          </div>
        </div>
      </div>

      <div style="flex: 0 0 340px;display:flex;flex-direction:column;gap:16px">
        <div class="card card-pad">
          <div class="card-title">板块中位单价 / 流动性 <span class="hint">灰色=扩展区</span></div>
          <Chart :option="districtChart" height="240px" />
        </div>

        <div class="card card-pad">
          <div class="card-title">近 7 天异动类型 <span class="hint">红/黄/灰=最高级别</span></div>
          <Chart :option="eventChart" height="220px" />
        </div>

        <div class="card card-pad">
          <div class="card-title">最新异动</div>
          <div v-for="e in recentEvents" :key="e.id" class="recent-row" @click="openEvent(e)">
            <span class="tag" :class="e.severity === 'red' ? 'danger' : e.severity === 'yellow' ? 'warn' : ''">
              {{ e.event_label }}
            </span>
            <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              {{ e.community_name }}
            </span>
            <span class="mono tiny" style="color:var(--muted)">{{ e.drop_pct ? (e.drop_pct * 100).toFixed(1) + '%' : '' }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import Chart from '../components/Chart.vue'
import LeakCard from '../components/LeakCard.vue'
import { api, fmt } from '../api'
import { districtOption, eventTypeOption, marketOption } from '../charts/options'

const router = useRouter()
const loading = ref(true)
const gradeFilter = ref('')
const dashboard = ref(null)
const today = ref('')
const listings = ref([])
const auctions = ref([])
const summary = ref({ by_type: [] })
const market = ref(null)
const rents = ref(null)
const deals = ref(null)

const counts = computed(() => dashboard.value?.counts || {})
const recentEvents = computed(() => (dashboard.value?.recent_events || []).slice(0, 6))
const filteredListings = computed(() =>
  gradeFilter.value ? listings.value.filter((x) => x.grade === gradeFilter.value) : listings.value
)
const districtChart = computed(() =>
  dashboard.value?.districts?.length ? districtOption(dashboard.value.districts) : null
)
const eventChart = computed(() => (summary.value.by_type?.length ? eventTypeOption(summary.value.by_type) : null))
const marketChart = computed(() => (market.value?.series?.length ? marketOption(market.value.series) : null))
function signClass(v) {
  if (v === null || v === undefined) return ''
  return Number(v) > 0 ? 'up' : Number(v) < 0 ? 'down' : ''
}

function open(item) {
  if (item.kind === 'auction') router.push(`/auction/${item.id}`)
  else router.push(`/listing/${item.id}`)
}
function openEvent(e) {
  if (e.target_type === 'auction') router.push(`/auction/${e.target_id}`)
  else router.push(`/listing/${e.target_id}`)
}

async function load() {
  loading.value = true
  try {
    const [dash, todayData, sum, mkt] = await Promise.all([
      api.dashboard(), api.today({ limit: 12 }), api.anomalySummary(7), api.marketOverview(180),
    ])
    dashboard.value = dash
    today.value = todayData.date
    listings.value = todayData.listings
    auctions.value = todayData.auctions
    summary.value = sum
    market.value = mkt
    try {
      rents.value = await api.marketRents()
    } catch (e) { /* 租售比失败不阻塞首页 */ }
    try {
      deals.value = await api.dealSummary(730)
    } catch (e) { /* 成交锚点失败不阻塞首页 */ }
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.market-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 8px; }
.ms-item { background: var(--panel-soft, rgba(148,163,184,.08)); border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; }
.ms-label { font-size: 11.5px; color: var(--muted); }
.ms-value { font-size: 18px; font-weight: 700; margin-top: 2px; }
.ms-unit { font-size: 11px; font-weight: 400; color: var(--muted); }
.ms-sub { font-size: 11px; margin-top: 2px; color: var(--muted); }
.ms-sub.up { color: var(--red, #e11d48); }
.ms-sub.down { color: var(--good, #059669); }
@media (max-width: 860px) { .market-stats { grid-template-columns: 1fr 1fr; } }
.quick-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.quick-card {
  display: flex; align-items: center; gap: 10px; padding: 11px 13px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 11px;
  cursor: pointer; transition: border-color .15s, transform .15s;
}
.quick-card:hover { border-color: rgba(14, 116, 144, .45); transform: translateY(-1px); }
.q-icon { font-size: 20px; flex: 0 0 auto; }
.q-body { min-width: 0; }
.q-title { font-size: 13px; font-weight: 700; }
.q-desc { font-size: 11px; color: var(--muted); line-height: 1.5; margin-top: 1px; }
.q-arrow { margin-left: auto; color: var(--muted); font-size: 13px; }

.recent-row {
  display: flex; align-items: center; gap: 8px; padding: 7px 0;
  border-bottom: 1px dashed var(--line); font-size: 12.5px; cursor: pointer;
}
.recent-row:last-child { border-bottom: none; }
.recent-row:hover { color: var(--brand); }

@media (max-width: 1080px) {
  .quick-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 860px) {
  .quick-grid { grid-template-columns: 1fr; gap: 8px; }
  .q-desc { display: none; }
}
</style>
