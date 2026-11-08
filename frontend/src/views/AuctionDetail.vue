<!-- 法拍详情：到手价 = 起拍 + 税费 + 欠费 + 维修预留。
      折扣一律按到手价算，避免"起拍价看起来很美，到手并不便宜"。
      拍卖结束后可回填真实成交价（全站唯一的真实价格锚点）。 -->
<template>
  <div v-loading="loading">
    <template v-if="data">
      <div class="detail-hero section-gap">
        <div style="flex:1;min-width:300px">
          <div class="h-title">
            <span class="grade-chip" :style="{ color: '#fff', background: meta.color }">{{ meta.icon }} {{ meta.label }}</span>
            <span v-if="item.deal_price" class="deal-chip">✅ 已成交</span>
            <span style="margin-left:8px">{{ item.community_name }}</span>
          </div>
          <div class="h-meta">
            {{ item.district }} · {{ item.layout }} · {{ fmt.area(item.area) }} ·
            {{ item.stage }} · 开拍 {{ item.auction_date || '待定' }}
            <span v-if="src.kind !== 'demo' && src.platform"> · {{ src.platform }}</span>
          </div>
          <div class="hero-kpi">
            <div><div class="k">评估价</div><div class="v">{{ fmt.wan(item.appraisal_price) }}</div></div>
            <div><div class="k">起拍价</div><div class="v">{{ fmt.wan(item.start_price) }}</div></div>
            <div><div class="k">到手价预估</div><div class="v" style="color:#fbbf24">{{ fmt.wan(score.landing_price || item.landing_price) }}</div></div>
            <div><div class="k">起拍折扣</div><div class="v">{{ fmt.pct(score.start_discount ?? item.start_discount) }}</div></div>
            <div><div class="k">净折扣（对二手）</div><div class="v" style="color:#34d399">{{ fmt.pct(score.net_discount ?? item.net_discount) }}</div></div>
          </div>
        </div>
        <div style="text-align:right">
          <div class="hero-price">{{ Math.round(score.score || 0) }}<small>分</small></div>
          <div class="tiny" style="color:#9fb8c4;margin-top:2px">
            风险 {{ fmt.riskLabel(score.risk_level) }} · 置信度 {{ fmt.confidenceLabel(score.confidence) }}
          </div>
          <div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap">
            <el-button size="small" :type="data.is_watched ? 'success' : 'primary'" @click="addWatch">
              <el-icon><Star /></el-icon>&nbsp;{{ data.is_watched ? '已在观察清单' : '加入观察清单' }}
            </el-button>
            <el-button size="small" @click="pushOne">手动推送</el-button>
            <el-button size="small" @click="openResult">{{ item.deal_price ? '修改成交价' : '回填成交价' }}</el-button>
          </div>
        </div>
      </div>

      <!-- 来源核验：演示样本不得冒充真实拍卖平台 -->
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
        <a v-if="src.url" :href="src.url" target="_blank" rel="noopener noreferrer" class="sc-link">打开原始公告页 ↗</a>
        <span v-else class="tiny muted" style="white-space:nowrap">无可核验链接</span>
      </div>

      <!-- 成交结果：回填后仍在详情页可见，列表按 status=sold 另查 -->
      <div v-if="item.deal_price" class="deal-card section-gap">
        <div class="dc-head">
          <span class="dc-badge">✅ 已成交 · 真实价格锚点</span>
          <span class="tiny muted">成交价来自人工 / CSV 回填 · 法拍成交价 ≠ 二手成交价</span>
        </div>
        <div class="dc-grid">
          <div><div class="k">成交总价</div><div class="v">{{ fmt.wanOnly(deal.deal_price) }}</div></div>
          <div><div class="k">成交日期</div><div class="v">{{ fmt.date(deal.deal_date) }}</div></div>
          <div><div class="k">成交单价</div><div class="v">{{ deal.deal_unit ? fmt.money(deal.deal_unit) + ' 元/㎡' : '—' }}</div></div>
          <div><div class="k">相对评估价折价</div><div class="v">{{ deal.appraisal_discount_pct != null ? fmt.signed(deal.appraisal_discount_pct) : '—' }}</div></div>
          <div><div class="k">高出起拍价</div><div class="v">{{ deal.over_start_pct != null ? fmt.signed(deal.over_start_pct) : '—' }}</div></div>
          <div><div class="k">相对在挂挂牌</div><div class="v">{{ deal.deal_vs_listing_pct != null ? fmt.signed(deal.deal_vs_listing_pct) : '—' }}</div></div>
        </div>
        <div class="tiny" style="margin-top:8px;line-height:1.7;color:var(--muted)">
          这是本项目唯一的真实成交价锚点，用来校准「到手价估算」和折价判断，不是全城成交均价。
          逐条汇总与历史折扣曲线见
          <a class="cell-link" @click="$router.push('/deals')">成交锚点页 →</a>
        </div>
      </div>

      <div class="row" style="align-items:flex-start">
        <div style="flex:1 1 62%;display:flex;flex-direction:column;gap:16px">
          <div class="card card-pad">
            <div class="card-title">
              到手价拆解
              <span class="hint">税费 {{ fmt.money(item.tax_est) }} · 欠费 {{ fmt.money(item.arrears_est) }} · 维修预留 {{ fmt.money(item.repair_reserve) }}</span>
            </div>
            <Chart :option="waterfallOpt" height="270px" />
            <div class="tiny muted" style="margin-top:8px">
              税费/欠费/维修为兜底估算（税率 4.5%、欠费 15 元/㎡、维修 400 元/㎡），真实金额需在竞拍前人工核对：
              契税、增值税差额、个税、物业供暖欠费都可能由买方承担。
            </div>
          </div>

          <div class="card card-pad">
            <div class="card-title">先问三个问题</div>
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

          <div class="card card-pad">
            <div class="card-title">同小区二手可比 <span class="hint">{{ compare.n }} 套在挂</span></div>
            <div class="table-scroll">
            <el-table :data="compare.items" size="small" style="width:100%">
              <el-table-column prop="layout" label="户型" width="110" />
              <el-table-column label="面积" width="80">
                <template #default="{ row }">{{ fmt.area(row.area) }}</template>
              </el-table-column>
              <el-table-column label="总价" width="100">
                <template #default="{ row }">{{ fmt.wan(row.total_price) }}</template>
              </el-table-column>
              <el-table-column label="单价" width="100">
                <template #default="{ row }">{{ fmt.money(row.unit_price) }}</template>
              </el-table-column>
              <el-table-column prop="floor_text" label="楼层" />
            </el-table>
            </div>
          </div>

          <div class="grid-2">
            <div class="card card-pad">
              <div class="card-title">风险清单 <span class="hint">法拍必看</span></div>
              <RiskChecklist :checklist="data.risk_checklist" />
            </div>
            <div class="card card-pad">
              <div class="card-title">人工核验 3 步</div>
              <VerifySteps :steps="data.verify_steps" />
            </div>
          </div>
        </div>

        <div style="flex:1 1 34%;display:flex;flex-direction:column;gap:16px">
          <div class="card card-pad">
            <div class="card-title">评分拆解 <span class="hint">总分 {{ Math.round(score.score || 0) }}</span></div>
            <Chart :option="radarOpt" height="250px" />
            <ScoreBreakdown :radar="score.radar || []" />
          </div>

          <div class="card card-pad">
            <div class="card-title">标的字段</div>
            <div class="tiny" style="display:grid;grid-template-columns:auto 1fr;gap:5px 12px;line-height:1.7">
              <span class="muted">评估价</span><span class="mono">{{ fmt.money(item.appraisal_price) }}</span>
              <span class="muted">起拍价</span><span class="mono">{{ fmt.money(item.start_price) }}</span>
              <span class="muted">保证金</span><span class="mono">{{ item.deposit ? fmt.money(item.deposit) : '公告未列' }}</span>
              <span class="muted">阶段</span><span>{{ item.stage }}</span>
              <span class="muted">风险标签</span><span>{{ (item.risk_tags || []).join('、') || '无' }}</span>
              <span class="muted">风险等级</span><span>{{ fmt.riskLabel(item.risk_level) }}</span>
              <span class="muted">标的标题</span><span>{{ item.title }}</span>
            </div>
          </div>

          <div class="disclaimer">
            法拍不是"便宜买房"，是"买一份带风险的债权处置结果"。竞拍前必须确认：是否有人居住、租约是否有效、税费谁承担、能否顺利过户。
          </div>
        </div>
      </div>
    </template>

    <!-- 回填成交价：写操作需口令，401 由 api.js 的全局事件统一引导去管理台登录 -->
    <el-dialog v-model="resultOpen" :title="item.deal_price ? '修改成交价' : '回填法拍成交价'" width="440px">
      <div class="tiny muted" style="margin-bottom:10px;line-height:1.8">
        只填拍卖结束后的<strong>真实落槌价</strong>。系统会把它标为「人工回填」，写入真实成交锚点，
        并把标的从「在拍机会」里移出。没有成交公告可核对的，请先人工核实再录入。
        <br /><strong>法拍成交价 ≠ 二手成交价</strong>，不要拿它当全城成交均价。
      </div>
      <el-form label-width="92px">
        <el-form-item label="成交总价">
          <el-input-number v-model="resultForm.deal_price" :min="1" :max="1000000000" :step="10000" style="width:100%" />
          <div class="tiny muted">单位：元（例如 72 万填 720000）</div>
        </el-form-item>
        <el-form-item label="成交日期">
          <el-date-picker v-model="resultForm.deal_date" type="date" value-format="YYYY-MM-DD"
                          placeholder="留空则记为今天" style="width:100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resultOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveResult">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import Chart from '../components/Chart.vue'
import RiskChecklist from '../components/RiskChecklist.vue'
import ScoreBreakdown from '../components/ScoreBreakdown.vue'
import VerifySteps from '../components/VerifySteps.vue'
import { api, fmt } from '../api'
import { radarOption, waterfallOption } from '../charts/options'

const route = useRoute()
const loading = ref(true)
const data = ref(null)
const resultOpen = ref(false)
const saving = ref(false)
const resultForm = ref({ deal_price: null, deal_date: null })

const item = computed(() => data.value?.item || {})
const score = computed(() => data.value?.score || {})
const compare = computed(() => data.value?.compare || { items: [] })
const deal = computed(() => data.value?.deal || {})
const meta = computed(() => fmt.gradeMeta(score.value.grade))
const src = computed(() => item.value.source_info || {})
const waterfallOpt = computed(() => (data.value?.waterfall ? waterfallOption(data.value.waterfall) : null))
const radarOpt = computed(() => (score.value.radar ? radarOption(score.value.radar) : null))

async function load() {
  loading.value = true
  try {
    data.value = await api.auction(route.params.id)
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

function openResult() {
  resultForm.value = {
    deal_price: item.value.deal_price || null,
    deal_date: item.value.deal_date || null,
  }
  resultOpen.value = true
}

async function saveResult() {
  const price = Number(resultForm.value.deal_price || 0)
  if (!price || price <= 0) {
    ElMessage.warning('请填写大于 0 的成交总价（单位：元）')
    return
  }
  saving.value = true
  try {
    await api.saveAuctionResult(Number(route.params.id), {
      deal_price: price,
      deal_date: resultForm.value.deal_date || undefined,
    })
    ElMessage.success('成交价已回填，真实成交锚点已更新')
    resultOpen.value = false
    await load()
  } catch (e) {
    ElMessage.error('回填失败：' + e.message)
  } finally {
    saving.value = false
  }
}

async function addWatch() {
  try {
    await api.addWatch({ target_type: 'auction', target_id: Number(route.params.id), note: '' })
    ElMessage.success('已加入观察清单')
    await load()
  } catch (e) {
    ElMessage.error('加入失败：' + e.message)
  }
}
async function pushOne() {
  try {
    const r = await api.pushOne('auction', Number(route.params.id))
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

.deal-chip {
  margin-left: 8px; padding: 2px 8px; border-radius: 10px; font-size: 11px;
  background: rgba(5, 150, 105, .14); color: #059669; border: 1px solid rgba(5, 150, 105, .3);
}
.deal-card { border-radius: 10px; padding: 12px 14px; border: 1px solid #bfe9d7; background: #f0fbf6; }
.dc-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.dc-badge { font-size: 12px; font-weight: 700; color: #14603f; }
.dc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.dc-grid .k { font-size: 11px; color: #4b7a68; }
.dc-grid .v { font-size: 16px; font-weight: 700; margin-top: 2px; color: #14603f; }
.cell-link { color: var(--brand); cursor: pointer; text-decoration: none; }
.cell-link:hover { text-decoration: underline; }
</style>
