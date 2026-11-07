<!-- 房源详情：所有结论都必须能追溯到数据，所以每块都标注口径 -->
<template>
  <div v-loading="loading">
    <template v-if="data">
      <!-- 顶部概览 -->
      <div class="detail-hero section-gap">
        <div style="flex:1;min-width:280px">
          <div class="h-title">
            <span class="grade-chip" :style="{ color: '#fff', background: meta.color }">{{ meta.icon }} {{ meta.label }}</span>
            <span style="margin-left:8px">{{ item.community_name }}</span>
          </div>
          <div class="h-meta">
            {{ item.district }} · {{ item.layout }} · {{ fmt.area(item.area) }} ·
            {{ item.floor_text || '楼层未知' }} · {{ item.orientation || '朝向未知' }} ·
            {{ item.decoration || '装修未知' }} · 房龄 {{ item.age || '—' }} 年
          </div>
          <div class="hero-kpi">
            <div><div class="k">挂牌总价</div><div class="v">{{ fmt.wan(item.total_price) }}</div></div>
            <div><div class="k">单价</div><div class="v">{{ fmt.money(item.unit_price) }}</div></div>
            <div><div class="k">同户型中位</div><div class="v">{{ fmt.money(score.median_unit) }}</div></div>
            <div><div class="k">低于中枢</div><div class="v" :style="{ color: dev > 0 ? '#34d399' : '#f87171' }">{{ dev > 0 ? fmt.pct(dev) : '—' }}</div></div>
            <div><div class="k">挂牌天数</div><div class="v">{{ item.list_days || 0 }}</div></div>
          </div>
        </div>
        <div style="text-align:right">
          <div class="hero-price">{{ Math.round(score.score || 0) }}<small>分</small></div>
          <div class="tiny" style="color:#9fb8c4;margin-top:2px">
            置信度 {{ fmt.confidenceLabel(score.confidence) }} · 风险 {{ fmt.riskLabel(score.risk_level || riskOf) }}
            · 样本 {{ score.sample_n || 0 }} 套
          </div>
          <div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end">
            <el-button size="small" :type="data.is_watched ? 'success' : 'primary'" @click="addWatch">
              <el-icon><Star /></el-icon>&nbsp;{{ data.is_watched ? '已在观察清单' : '加入观察清单' }}
            </el-button>
            <el-button size="small" @click="pushOne">手动推送</el-button>
          </div>
        </div>
      </div>

      <!-- 来源核验：先看这条数据是不是真的，再谈它为什么便宜 -->
      <div class="source-card section-gap" :class="src.kind === 'demo' ? 'is-demo' : 'is-real'">
        <div class="sc-icon">{{ src.kind === 'demo' ? '◌' : '✓' }}</div>
        <div style="flex:1;min-width:200px">
          <div class="strong small">
            {{ src.label || '来源未标注' }}
            <span v-if="src.platform" class="tag info" style="margin-left:6px">{{ src.platform }}</span>
            <span v-if="src.ref" class="tiny muted mono" style="margin-left:6px">编号 {{ src.ref }}</span>
          </div>
          <div class="tiny" style="margin-top:3px;line-height:1.65">{{ src.note }}</div>
          <div v-if="src.collected_at" class="tiny muted" style="margin-top:2px">记录于 {{ src.collected_at }}</div>
        </div>
        <a v-if="src.url" :href="src.url" target="_blank" rel="noopener noreferrer" class="sc-link">打开原始挂牌页 ↗</a>
        <span v-else class="tiny muted" style="white-space:nowrap">无可核验链接</span>
      </div>

      <div class="row" style="align-items:flex-start">
        <div style="flex:1 1 62%;display:flex;flex-direction:column;gap:16px">
          <!-- 三问 -->
          <div class="card card-pad">
            <div class="card-title">先问三个问题 <span class="hint">这是本系统的核心逻辑</span></div>
            <div class="explain-block">
              <div class="explain-item cheap">
                <div class="lbl" style="color:var(--yellow)">① 它为什么便宜？</div>
                <div class="txt">{{ score.why_cheap || '暂无结论' }}</div>
              </div>
              <div class="explain-item good">
                <div class="lbl" style="color:var(--good)">② 为什么可能是真漏？</div>
                <div class="txt">{{ score.why_good || '暂无结论' }}</div>
              </div>
              <div class="explain-item trap">
                <div class="lbl" style="color:var(--red)">③ 为什么可能是坑？</div>
                <div class="txt">{{ score.why_trap || '暂无结论' }}</div>
              </div>
            </div>
          </div>

          <!-- 箱线图 -->
          <div class="card card-pad">
            <div class="card-title">
              同小区同户型价格分布
              <span class="hint">{{ comp.layout }} · {{ comp.n }} 套可比 · 箱体为四分位，点为每套房</span>
            </div>
            <Chart :option="boxOption" height="280px" empty-text="该户型可比样本不足，无法画分布" />
            <div class="tiny muted" style="margin-top:8px">
              口径：同小区、同室数、在挂房源的单价；样本 &lt; 3 套时置信度自动降为「低」，分数会向中性收缩。
            </div>
          </div>

          <!-- 调价时间轴 -->
          <div class="card card-pad">
            <div class="card-title">
              调价时间轴
              <span class="hint">首次价 {{ fmt.wan(item.first_price) }} → 当前 {{ fmt.wan(item.total_price) }} · 调价 {{ item.price_changes }} 次</span>
            </div>
            <Chart :option="timelineOpt" height="260px" empty-text="暂无调价记录（可能是新上架房源）" />
            <div v-if="history.length > 1" class="tiny muted" style="margin-top:8px">
              <span v-for="(h, i) in history.slice(1)" :key="i" class="tag" style="margin-right:6px">
                {{ fmt.date(h.observed_at) }} {{ fmt.wan(h.price) }}
                <b v-if="h.drop_pct" :style="{ color: h.drop_pct < 0 ? 'var(--good)' : 'var(--red)' }">
                  {{ (h.drop_pct * 100).toFixed(1) }}%
                </b>
              </span>
            </div>
          </div>

          <!-- 购房成本与议价锚点：评分说「值不值得看」，这里说「买不买得起、能谈到多少」 -->
          <AffordPanel
            v-if="data.afford"
            class="section-gap"
            :cost="data.afford.cost"
            :rent="data.afford.rent"
            :bargain="data.afford.bargain"
            :item="data.item"
          />

          <!-- 风险 + 核验 -->
          <div class="grid-2">
            <div class="card card-pad">
              <div class="card-title">风险清单</div>
              <RiskChecklist :checklist="data.risk_checklist" />
            </div>
            <div class="card card-pad">
              <div class="card-title">人工核验 3 步</div>
              <VerifySteps :steps="data.verify_steps" />
              <div class="disclaimer" style="margin-top:10px">
                系统只负责把候选筛出来；这 3 步不做，任何分数都不算数。
              </div>
            </div>
          </div>
        </div>

        <div style="flex:1 1 34%;display:flex;flex-direction:column;gap:16px">
          <div class="card card-pad">
            <div class="card-title">捡漏评分拆解 <span class="hint">总分 {{ Math.round(score.score || 0) }}</span></div>
            <Chart :option="radarOpt" height="250px" />
            <ScoreBreakdown :radar="score.radar || []" />
          </div>

          <div class="card card-pad">
            <div class="card-title">命中异动 <span class="hint">{{ (data.events || []).length }} 条</span></div>
            <el-empty v-if="!(data.events || []).length" description="今日无新异动" :image-size="54" />
            <div v-for="e in data.events" :key="e.id" class="tiny" style="padding:7px 0;border-bottom:1px dashed var(--line)">
              <span class="tag" :class="e.severity === 'red' ? 'danger' : e.severity === 'yellow' ? 'warn' : ''">
                {{ eventLabel(e.event_type) }}
              </span>
              <span style="margin-left:6px">{{ e.event_date }}</span>
              <span v-if="e.drop_pct" class="mono" style="margin-left:6px;color:var(--good)">
                {{ (e.drop_pct * 100).toFixed(1) }}%
              </span>
            </div>
          </div>

          <div class="card card-pad">
            <div class="card-title">房源原始字段</div>
            <div class="tiny" style="display:grid;grid-template-columns:auto 1fr;gap:5px 12px;line-height:1.7">
              <span class="muted">来源</span><span>{{ item.source }} · {{ fmt.date(item.first_seen) }} 首见</span>
              <span class="muted">硬伤标签</span><span>{{ (item.defect_tags || []).join('、') || '无' }}</span>
              <span class="muted">租金参考</span><span>{{ item.rent_month ? fmt.money(item.rent_month) + ' 元/月' : '—' }}</span>
              <span class="muted">年租金回报</span><span>{{ item.rent_month ? ((item.rent_month * 12) / item.total_price * 100).toFixed(2) + '%' : '—' }}</span>
              <span class="muted">对口学校</span><span>{{ item.school_district || '—' }}</span>
              <span class="muted">小区流动性</span><span>{{ item.liquidity_score || '—' }} / 100</span>
              <span class="muted">指纹</span><span class="mono" style="word-break:break-all">{{ item.fingerprint }}</span>
            </div>
          </div>

          <div class="disclaimer">
            挂牌价 ≠ 成交价；中介口头价 ≠ 网签价。请以近 3 个月同户型真实成交价作为唯一锚点。
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import Chart from '../components/Chart.vue'
import AffordPanel from '../components/AffordPanel.vue'
import RiskChecklist from '../components/RiskChecklist.vue'
import ScoreBreakdown from '../components/ScoreBreakdown.vue'
import VerifySteps from '../components/VerifySteps.vue'
import { api, fmt } from '../api'
import { boxplotOption, radarOption, timelineOption } from '../charts/options'

const route = useRoute()
const loading = ref(true)
const data = ref(null)

const item = computed(() => data.value?.item || {})
const score = computed(() => data.value?.score || {})
const comp = computed(() => data.value?.comparables || {})
const history = computed(() => data.value?.history || [])
const meta = computed(() => fmt.gradeMeta(score.value.grade))
const dev = computed(() => Number(score.value.deviation_pct || 0))
const src = computed(() => item.value.source_info || {})
const riskOf = computed(() => {
  const tags = item.value.defect_tags || []
  if (tags.some((t) => ['产权瑕疵', '占用', '腾退难'].includes(t))) return 'high'
  return (tags.length >= 2 ? 'mid' : 'low')
})

const radarOpt = computed(() => (score.value.radar ? radarOption(score.value.radar) : null))
const boxOption = computed(() => (comp.value.values?.length ? boxplotOption(comp.value, Number(item.value.unit_price || 0)) : null))
const timelineOpt = computed(() => (history.value.length ? timelineOption(history.value) : null))

const EVENT_LABEL = {
  sudden_drop: '突发降价', deep_dip: '深跌', long_listed: '久挂让利',
  miskill: '错杀候选', board_divergence: '板块背离', new_home: '新房倒挂', auction_deal: '法拍机会',
}
function eventLabel(t) { return EVENT_LABEL[t] || t }

async function load() {
  loading.value = true
  try {
    data.value = await api.listing(route.params.id)
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

async function addWatch() {
  try {
    await api.addWatch({ target_type: 'listing', target_id: Number(route.params.id), note: '' })
    ElMessage.success('已加入观察清单')
    await load()
  } catch (e) {
    ElMessage.error('加入失败：' + e.message)
  }
}
async function pushOne() {
  try {
    const r = await api.pushOne('listing', Number(route.params.id))
    ElMessage[r.status === 'sent' ? 'success' : 'warning'](
      r.status === 'sent' ? `已通过 ${r.channel} 推送` : `未实际外发（${r.reason || r.status}），已写入站内提醒`
    )
  } catch (e) {
    ElMessage.error('推送失败：' + e.message)
  }
}

onMounted(load)
watch(() => route.params.id, load)
</script>

<style scoped>
.source-card {
  display: flex; align-items: center; gap: 11px; flex-wrap: wrap;
  border-radius: 10px; padding: 10px 14px; border: 1px solid var(--line);
  background: var(--panel-soft, #f6f9fb);
}
.source-card.is-demo { background: #fff8ec; border-color: #f2dfc0; color: #7a5a24; }
.source-card.is-real { background: #edfaf5; border-color: #bfe9d7; color: #14603f; }
.sc-icon { font-size: 19px; line-height: 1; opacity: .75; }
.sc-link { color: var(--brand); font-size: 12px; text-decoration: none; white-space: nowrap; }
.sc-link:hover { text-decoration: underline; }
</style>
