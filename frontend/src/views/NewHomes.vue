<!--
  新房倒挂：把「新房均价 vs 同板块二手」放在一起，并强制做两项调整。
  为什么必须调整：
    · 毛坯新房要另花 1000~1500 元/㎡ 装修；
    · 新房得房率常 75%~80%，二手 80%~85%。
  不调整的话，「新房 1.1 万、旁边二手 1.2 万」这种名义倒挂会把人骗进去。
-->
<template>
  <div>
    <!-- 判读说明 -->
    <div class="explain card card-pad">
      <div class="explain-title">为什么「新房比二手便宜」不能直接信</div>
      <div class="explain-grid">
        <div class="ex-item">
          <div class="ex-icon">🧱</div>
          <div class="ex-name">毛坯要另算装修</div>
          <div class="ex-desc">毛坯新盘每平还要投 1000~1500 元，这笔钱不体现在均价里</div>
        </div>
        <div class="ex-item">
          <div class="ex-icon">📐</div>
          <div class="ex-name">得房率不一样</div>
          <div class="ex-desc">新房公摊更大，同样 120㎡ 可能实际少 6~8㎡ 可用面积</div>
        </div>
        <div class="ex-item">
          <div class="ex-icon">⏳</div>
          <div class="ex-name">交付不确定</div>
          <div class="ex-desc">延期、减配、精装降标都是新房独有的风险，二手是现房</div>
        </div>
        <div class="ex-item">
          <div class="ex-icon">🎓</div>
          <div class="ex-name">学区可能没落地</div>
          <div class="ex-desc">新盘学区未划定前，「规划中」等于没有，二手学区是确定的</div>
        </div>
      </div>
    </div>

    <!-- 筛选 -->
    <div class="toolbar section-gap">
      <el-select v-model="district" placeholder="全部板块" clearable size="small" style="width:130px">
        <el-option v-for="d in districtOptions" :key="d" :value="d" :label="d" />
      </el-select>
      <el-checkbox v-model="onlyGap" size="small">只看真正倒挂（调整后仍便宜）</el-checkbox>
      <span class="tiny muted">共 {{ filtered.length }} 个在售楼盘</span>
    </div>

    <!-- 楼盘卡片 -->
    <div class="nh-grid section-gap">
      <div v-for="n in filtered" :key="n.id" class="nh-card card" :class="`v-${n.verdict}`">
        <div class="nh-head">
          <div style="min-width:0">
            <div class="nh-name">
              {{ n.name }}
              <span class="tag" :class="statusTag(n.sale_status)">{{ n.sale_status }}</span>
            </div>
            <div class="tiny muted">{{ n.district }} · {{ n.layout_text }}</div>
          </div>
          <div class="verdict" :class="`vd-${n.verdict}`">{{ verdictLabel(n.verdict) }}</div>
        </div>

        <!-- 价格对照 -->
        <div class="price-compare">
          <div class="pc-side">
            <div class="pc-label">新盘均价</div>
            <div class="pc-value mono">{{ fmt.money(n.avg_unit_price) }}</div>
            <div class="pc-sub">{{ n.decoration || '—' }}</div>
          </div>
          <div class="pc-mid">
            <div class="pc-gap" :class="n.adjusted_gap > 0 ? 'good' : 'bad'">
              {{ n.adjusted_gap === null ? '—' : fmt.signed(n.adjusted_gap) }}
            </div>
            <div class="pc-gap-label">调整后倒挂</div>
          </div>
          <div class="pc-side right">
            <div class="pc-label">同板块二手</div>
            <div class="pc-value mono">{{ fmt.money(n.used_median_unit) }}</div>
            <div class="pc-sub">中位单价</div>
          </div>
        </div>

        <!-- 两根进度条：名义 vs 调整后 -->
        <div class="bars">
          <div class="bar-row">
            <span class="bar-name">名义倒挂</span>
            <span class="bar-track"><i class="bar-nominal" :style="{ width: barW(n.nominal_gap) }"></i></span>
            <span class="bar-val mono">{{ n.nominal_gap === null ? '—' : fmt.signed(n.nominal_gap) }}</span>
          </div>
          <div class="bar-row">
            <span class="bar-name">调整后</span>
            <span class="bar-track"><i class="bar-adjusted" :style="{ width: barW(n.adjusted_gap) }"></i></span>
            <span class="bar-val mono">{{ n.adjusted_gap === null ? '—' : fmt.signed(n.adjusted_gap) }}</span>
          </div>
          <div v-if="n.is_bare" class="tiny muted" style="margin-top:4px">
            已为毛坯加计 {{ fmt.money(n.decor_added) }} 元/㎡ 装修成本；套内可比价
            {{ fmt.money(n.new_eff_unit) }} vs {{ fmt.money(n.used_eff_unit) }}
          </div>
        </div>

        <div class="nh-verdict-text">{{ n.verdict_text }}</div>

        <!-- 入手门槛 -->
        <div class="entry">
          <div class="entry-item">
            <span class="entry-key">{{ fmt.area(n.area_min) }} 起总价</span>
            <span class="entry-val mono">{{ fmt.wanOnly(n.entry_total) }}</span>
          </div>
          <div class="entry-item">
            <span class="entry-key">月供约</span>
            <span class="entry-val mono">{{ fmt.money(n.entry_monthly) }}</span>
          </div>
          <div class="entry-item">
            <span class="entry-key">首付+税费现金</span>
            <span class="entry-val mono">{{ fmt.wanOnly(n.entry_cash) }}</span>
          </div>
        </div>

        <div class="nh-foot">
          <span v-if="n.delivery_date" class="tag">交付 {{ n.delivery_date }}</span>
          <span v-if="n.property_fee" class="tag">物业 {{ n.property_fee }} 元/㎡</span>
          <span v-if="n.plot_ratio" class="tag">容积率 {{ n.plot_ratio }}</span>
          <span v-for="t in n.risk_tags" :key="t" class="tag danger">{{ t }}</span>
          <el-button link type="primary" size="small" style="margin-left:auto" @click="openDetail(n)">
            核验清单
          </el-button>
        </div>
      </div>
    </div>

    <el-empty v-if="!filtered.length" description="没有符合条件的楼盘" />

    <!-- 核验清单抽屉 -->
    <el-drawer v-model="drawer" :title="detail?.item?.name || '楼盘核验清单'" :size="isMobile ? '100%' : '460px'">
      <div v-if="detail">
        <div class="dr-verdict" :class="`vd-${detail.verdict}`">{{ detail.verdict_text }}</div>

        <div class="dr-section">价格口径</div>
        <div class="dr-rows">
          <div class="dro"><span>新盘均价</span><b class="mono">{{ fmt.money(detail.item.avg_unit_price) }} 元/㎡</b></div>
          <div class="dro"><span>同板块二手中位</span><b class="mono">{{ fmt.money(detail.used_median_unit) }} 元/㎡</b></div>
          <div class="dro"><span>名义倒挂</span><b class="mono">{{ fmt.signed(detail.nominal_gap) }}</b></div>
          <div class="dro"><span>套内可比（新房）</span><b class="mono">{{ fmt.money(detail.new_eff_unit) }} 元/㎡</b></div>
          <div class="dro"><span>套内可比（二手）</span><b class="mono">{{ fmt.money(detail.used_eff_unit) }} 元/㎡</b></div>
          <div class="dro"><span>调整后倒挂</span><b class="mono">{{ fmt.signed(detail.adjusted_gap) }}</b></div>
        </div>

        <div class="dr-section">面积段与到手成本</div>
        <div v-for="s in detail.scenarios" :key="s.area" class="dr-scenario">
          <div class="sc-head">
            <span class="strong">{{ fmt.area(s.area) }}</span>
            <span class="mono">{{ fmt.wanOnly(s.total) }}</span>
          </div>
          <div class="tiny muted">
            月供约 {{ fmt.money(s.monthly) }} · 首付+税费+装修现金约 {{ fmt.wanOnly(s.cash_needed) }}
            <span v-if="s.decoration"> （含装修 {{ fmt.wanOnly(s.decoration) }}）</span>
          </div>
        </div>

        <div class="dr-section">Buy 前必须核验</div>
        <div class="dr-checklist">
          <div v-for="(c, i) in detail.checklist" :key="c.label" class="ck-item">
            <span class="ck-no">{{ i + 1 }}</span>
            <div>
              <div class="ck-label">{{ c.label }}</div>
              <div class="tiny muted">{{ c.desc }}</div>
            </div>
          </div>
        </div>

        <div v-if="detail.item.note" class="dr-note">{{ detail.item.note }}</div>

        <el-button style="width:100%;margin-top:14px" @click="$router.push('/map')">
          去「小区地图」看周边二手
        </el-button>
      </div>
      <div class="disclaimer" style="margin-top:14px">{{ detail?.disclaimer }}</div>
    </el-drawer>

    <div class="disclaimer section-gap">{{ note }}</div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmt } from '../api'
import { useIsMobile } from '../composables/useIsMobile'

const router = useRouter()
const { isMobile } = useIsMobile()

const items = ref([])
const note = ref('')
const district = ref('')
const onlyGap = ref(false)
const drawer = ref(false)
const detail = ref(null)

const districtOptions = computed(() => [...new Set(items.value.map((i) => i.district))])

const filtered = computed(() =>
  items.value.filter((i) => {
    if (district.value && i.district !== district.value) return false
    if (onlyGap.value && !(i.adjusted_gap > 0)) return false
    return true
  })
)

const VERDICT = {
  opportunity: { label: '真倒挂', cls: 'vd-opportunity' },
  caution: { label: '倒挂有风险', cls: 'vd-caution' },
  slight: { label: '小幅倒挂', cls: 'vd-slight' },
  neutral: { label: '与二手持平', cls: 'vd-neutral' },
  premium: { label: '比二手更贵', cls: 'vd-premium' },
  unknown: { label: '缺可比', cls: 'vd-neutral' },
}
function verdictLabel(v) {
  return (VERDICT[v] || VERDICT.unknown).label
}
function statusTag(s) {
  if (s === '尾盘') return 'warn'
  if (s === '待售') return ''
  return 'info'
}
/** 倒挂幅度映射成条宽：12% 封顶，负值显示为 0（用颜色区分方向） */
function barW(gap) {
  if (gap === null || gap === undefined) return '0%'
  return Math.min(100, Math.max(0, (Number(gap) / 12) * 100)) + '%'
}

async function openDetail(n) {
  drawer.value = true
  detail.value = null
  try {
    detail.value = await api.newHome(n.id)
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  }
}

async function load() {
  try {
    const d = await api.newHomes()
    items.value = d.items
    note.value = d.disclaimer + ' ' + (d.note || '')
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  }
}
onMounted(load)
</script>

<style scoped>
.explain { background: linear-gradient(135deg, #fbfdfe, #f2f8fa); }
.explain-title { font-size: 14px; font-weight: 700; margin-bottom: 12px; }
.explain-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.ex-item { font-size: 12px; }
.ex-icon { font-size: 18px; }
.ex-name { font-weight: 650; margin: 4px 0 3px; }
.ex-desc { color: var(--muted); line-height: 1.65; }

.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

.nh-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; }
.nh-card { padding: 14px 15px; display: flex; flex-direction: column; gap: 11px; }
.nh-card.v-opportunity { border-top: 3px solid #0e7490; }
.nh-card.v-caution { border-top: 3px solid #e11d48; }
.nh-card.v-slight { border-top: 3px solid #d97706; }
.nh-card.v-neutral, .nh-card.v-premium { border-top: 3px solid #cbd5e1; }

.nh-head { display: flex; align-items: flex-start; gap: 8px; }
.nh-name { font-size: 15px; font-weight: 720; display: flex; align-items: center; gap: 6px; }
.verdict {
  font-size: 11.5px; font-weight: 650; padding: 3px 9px; border-radius: 20px;
  white-space: nowrap; margin-left: auto;
}
.vd-opportunity { color: #0b6c8a; background: rgba(14, 116, 144, .1); }
.vd-caution { color: #be123c; background: rgba(225, 29, 72, .1); }
.vd-slight { color: #b45309; background: rgba(217, 119, 6, .12); }
.vd-neutral, .vd-premium { color: #64748b; background: rgba(100, 116, 139, .1); }

.price-compare { display: flex; align-items: center; gap: 10px; }
.pc-side { flex: 1; }
.pc-side.right { text-align: right; }
.pc-label { font-size: 10.5px; color: var(--muted); }
.pc-value { font-size: 15px; font-weight: 700; }
.pc-sub { font-size: 10.5px; color: var(--muted); }
.pc-mid { text-align: center; min-width: 82px; }
.pc-gap { font-size: 17px; font-weight: 760; }
.pc-gap.good { color: #0e7490; }
.pc-gap.bad { color: #94a3b8; }
.pc-gap-label { font-size: 10px; color: var(--muted); }

.bars { display: flex; flex-direction: column; gap: 5px; }
.bar-row { display: flex; align-items: center; gap: 8px; }
.bar-name { font-size: 10.5px; color: var(--muted); width: 52px; flex: 0 0 52px; }
.bar-track { flex: 1; height: 7px; background: #eef2f6; border-radius: 4px; overflow: hidden; }
.bar-track i { display: block; height: 100%; border-radius: 4px; transition: width .4s; }
.bar-nominal { background: linear-gradient(90deg, #bae6fd, #38bdf8); }
.bar-adjusted { background: linear-gradient(90deg, #5eead4, #0e7490); }
.bar-val { width: 56px; text-align: right; font-size: 11px; color: var(--ink); }

.nh-verdict-text {
  font-size: 12px; line-height: 1.7; color: #4a5768;
  background: #f7fafb; border-radius: 8px; padding: 8px 10px;
}

.entry { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.entry-item { background: #f6f9fb; border-radius: 8px; padding: 7px 9px; }
.entry-key { display: block; font-size: 10px; color: var(--muted); }
.entry-val { font-size: 13px; font-weight: 700; }

.nh-foot { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }

.dr-verdict {
  font-size: 12.5px; line-height: 1.7; border-radius: 9px; padding: 10px 12px; margin-bottom: 14px;
}
.dr-section { font-size: 12.5px; font-weight: 700; margin: 14px 0 8px; }
.dr-rows { display: flex; flex-direction: column; gap: 6px; }
.dro { display: flex; justify-content: space-between; font-size: 12.5px; }
.dro span { color: var(--muted); }
.dr-scenario { border: 1px solid var(--line); border-radius: 9px; padding: 9px 11px; margin-bottom: 8px; }
.sc-head { display: flex; justify-content: space-between; margin-bottom: 3px; font-size: 13px; }
.dr-checklist { display: flex; flex-direction: column; gap: 10px; }
.ck-item { display: flex; gap: 9px; font-size: 12.5px; }
.ck-no {
  flex: 0 0 20px; height: 20px; border-radius: 50%; background: var(--brand); color: #fff;
  display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700;
}
.ck-label { font-weight: 650; margin-bottom: 1px; }
.dr-note {
  margin-top: 14px; font-size: 12px; line-height: 1.7; color: #7a5a24;
  background: #fff8ec; border: 1px solid #f2dfc0; border-radius: 9px; padding: 9px 11px;
}

@media (max-width: 860px) {
  .explain-grid { grid-template-columns: 1fr 1fr; gap: 10px; }
  .nh-grid { grid-template-columns: 1fr; }
  .entry { grid-template-columns: 1fr 1fr; }
}
</style>
