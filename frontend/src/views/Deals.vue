<!--
  成交锚点：全站唯一的「真实成交价」视图（法拍成交，人工/CSV 回填）。

  刻意摆在明面上的三件事：
    1. 法拍成交价 ≠ 二手成交价，不能推算全城成交均价；
    2. 成交价是人工回填的，不是自动采集，演示样本不参与；
    3. 样本少就说样本少，曲线不当统计结论。
-->
<template>
  <div v-loading="loading">
    <div class="disclaimer section-gap">
      <strong>这是全站唯一的真实成交价。</strong>
      日照没有公开的二手成交页，所以这里只收<strong>法拍成交价</strong>，且全部来自人工 / CSV 回填。
      挂牌价 ≠ 成交价，下架 ≠ 成交，<strong>法拍成交价 ≠ 二手成交价</strong>；
      它的作用是给「到手价估算」和「折价判断」一个真实锚点，不是全城成交均价。
    </div>

    <div class="stat-grid section-gap">
      <div class="stat accent-brand">
        <div class="stat-label">已登记成交</div>
        <div class="stat-value">{{ data?.total || 0 }}</div>
        <div class="stat-sub">法拍成交锚点（演示样本不计）</div>
      </div>
      <div class="stat">
        <div class="stat-label">中位折价（对评估价）</div>
        <div class="stat-value">{{ data?.summary?.median_discount_pct != null ? fmt.signed(data.summary.median_discount_pct) : '—' }}</div>
        <div class="stat-sub">正数=比评估价便宜</div>
      </div>
      <div class="stat">
        <div class="stat-label">中位成交单价</div>
        <div class="stat-value">{{ data?.summary?.median_deal_unit != null ? Math.round(data.summary.median_deal_unit).toLocaleString() : '—' }}</div>
        <div class="stat-sub">元/㎡，成交总价 ÷ 建筑面积</div>
      </div>
      <div class="stat">
        <div class="stat-label">最近成交日</div>
        <div class="stat-value" style="font-size:20px">{{ fmt.date(data?.summary?.latest) }}</div>
        <div class="stat-sub">最早 {{ fmt.date(data?.summary?.earliest) }}</div>
      </div>
    </div>

    <div v-if="data && data.total" class="card card-pad section-gap">
      <div class="card-title">
        历史成交折扣曲线
        <span class="hint">按成交月份聚合 · 柱=成交套数，线=相对评估价的中位折价率</span>
      </div>
      <Chart v-if="curveOpt" :option="curveOpt" height="250px" />
      <div v-else class="empty-state" style="padding:14px 0">
        成交记录还没填日期，暂时画不出月度曲线；逐条结果见下方明细。
      </div>
      <div class="tiny muted" style="margin-top:8px;line-height:1.7">{{ data.quality_note }}</div>
      <div class="quality-note">{{ data.disclaimer }}</div>
    </div>

    <div v-else-if="!loading" class="card card-pad section-gap empty-state" style="padding:26px 16px">
      <div style="font-size:15px;margin-bottom:8px">暂无成交锚点</div>
      <div class="tiny muted" style="line-height:1.9">
        法拍结束后，在<strong>法拍详情页</strong>点「回填成交价」录入真实成交价；<br />
        或到<strong>管理台 → 法拍成交批量回填</strong>贴一段 CSV 一次录入多条。<br />
        回填后这里会出现折扣曲线与明细，成交标的也会从「在拍机会」里移出。
      </div>
    </div>

    <div v-if="data && data.total" class="card card-pad section-gap">
      <div class="card-title">
        成交明细
        <span class="hint">共 {{ data.total }} 条 · 每条都可点原始公告页复核</span>
      </div>
      <div class="table-scroll">
        <el-table :data="data.items" size="small" style="width:100%">
          <el-table-column label="成交日期" width="110">
            <template #default="{ row }">{{ fmt.date(row.deal_date) }}</template>
          </el-table-column>
          <el-table-column label="小区 / 板块" min-width="170">
            <template #default="{ row }">
              <a class="cell-link" @click="$router.push(`/auction/${row.auction_id}`)">{{ row.community_name || row.title || '—' }}</a>
              <div class="tiny muted">{{ row.district || '—' }} · {{ row.stage || '阶段未填' }}</div>
            </template>
          </el-table-column>
          <el-table-column label="成交总价" width="110">
            <template #default="{ row }">{{ fmt.wanOnly(row.deal_price) }}</template>
          </el-table-column>
          <el-table-column label="成交单价" width="110">
            <template #default="{ row }">{{ row.deal_unit ? fmt.money(row.deal_unit) + ' 元/㎡' : '—' }}</template>
          </el-table-column>
          <el-table-column label="评估折价" width="100">
            <template #default="{ row }">
              <span :style="{ color: row.appraisal_discount_pct > 0 ? '#059669' : '#b45309' }">
                {{ row.appraisal_discount_pct != null ? fmt.signed(row.appraisal_discount_pct) : '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="高出起拍" width="100">
            <template #default="{ row }">{{ row.over_start_pct != null ? fmt.signed(row.over_start_pct) : '—' }}</template>
          </el-table-column>
          <el-table-column label="相对在挂挂牌" width="120">
            <template #default="{ row }">{{ row.deal_vs_listing_pct != null ? fmt.signed(row.deal_vs_listing_pct) : '—' }}</template>
          </el-table-column>
          <el-table-column label="原始公告" width="100">
            <template #default="{ row }">
              <a v-if="row.source_url" :href="row.source_url" target="_blank" rel="noopener noreferrer" class="cell-link">打开 ↗</a>
              <span v-else class="tiny muted">无链接</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div class="tiny muted" style="margin-top:8px;line-height:1.7">
        「相对在挂挂牌」= 成交单价与同小区当前在售挂牌单价中位的差，正数表示成交比在挂便宜。
        挂牌是报价口径，成交是真实落槌口径，两者不可直接等同。
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import Chart from '../components/Chart.vue'
import { api, fmt } from '../api'
import { dealCurveOption } from '../charts/options'

const loading = ref(true)
const data = ref(null)
const curveOpt = computed(() =>
  data.value?.by_period?.length ? dealCurveOption(data.value.by_period) : null
)

async function load() {
  loading.value = true
  try {
    data.value = await api.dealSummary(730)
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.quality-note {
  margin-top: 10px; padding: 9px 12px; border-radius: 8px;
  background: rgba(14, 116, 144, .06); border: 1px solid var(--line);
  font-size: 12px; line-height: 1.75; color: var(--muted);
}
.cell-link { color: var(--brand); cursor: pointer; text-decoration: none; }
.cell-link:hover { text-decoration: underline; }
@media (max-width: 860px) {
  .stat-grid { grid-template-columns: 1fr 1fr; }
}
</style>
