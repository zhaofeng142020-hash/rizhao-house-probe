<!--
  重点关注 120-150㎡：把二手、法拍、新房三类放在同一个面积段里横向看。
  这个面积段最容易踩的坑是「单价便宜就以为捡到漏」——
  大面积在同一个小区里本来就比小面积便宜 3%~8%，那是市场定价，不是漏。
-->
<template>
  <div>
    <!-- 面积段选择 -->
    <div class="area-bar card">
      <div class="ab-left">
        <span class="ab-label">关注面积段</span>
        <div class="seg">
          <button
            v-for="p in presets"
            :key="p.label"
            :class="['seg-btn', { on: range.min === p.min && range.max === p.max }]"
            @click="pick(p)"
          >{{ p.label }}</button>
        </div>
      </div>
      <div class="ab-right">
        <el-input-number v-model="range.min" :min="50" :max="300" :step="5" size="small" controls-position="right" style="width:100px" />
        <span class="muted">—</span>
        <el-input-number v-model="range.max" :min="60" :max="400" :step="5" size="small" controls-position="right" style="width:100px" />
        <span class="tiny muted">㎡</span>
        <el-button size="small" type="primary" plain @click="load">应用</el-button>
      </div>
    </div>

    <!-- 汇总 -->
    <div class="stat-grid section-gap">
      <div class="card stat-card">
        <div class="sc-val">{{ s.listing_n }}</div>
        <div class="sc-key">二手在售</div>
      </div>
      <div class="card stat-card">
        <div class="sc-val">{{ s.auction_n }}</div>
        <div class="sc-key">法拍在拍</div>
      </div>
      <div class="card stat-card">
        <div class="sc-val">{{ s.new_home_n }}</div>
        <div class="sc-key">在售新盘</div>
      </div>
      <div class="card stat-card">
        <div class="sc-val mono">{{ fmt.money(s.median_unit) }}</div>
        <div class="sc-key">中位单价 元/㎡</div>
      </div>
      <div class="card stat-card">
        <div class="sc-val mono">{{ fmt.wanOnly(s.median_total) }}</div>
        <div class="sc-key">中位总价</div>
      </div>
      <div class="card stat-card">
        <div class="sc-val">
          <span style="color:#e11d48">{{ s.red_n }}</span>
          <span class="muted" style="font-size:14px"> / </span>
          <span style="color:#d97706">{{ s.yellow_n }}</span>
        </div>
        <div class="sc-key">红 / 黄</div>
      </div>
    </div>

    <!-- 这个面积段的三个反直觉 -->
    <div class="insight card card-pad section-gap">
      <div class="in-title">买这个面积段之前，先知道三件事</div>
      <div class="in-grid">
        <div class="in-item">
          <div class="in-no">01</div>
          <div class="in-name">大面积单价本来就低</div>
          <div class="in-desc">
            <template v-if="s.size_premium">
              本库实测：{{ s.size_premium.note }}。所以「大户型单价便宜」多半是正常定价，不是漏。
            </template>
            <template v-else>样本不足，暂无法给出同小区大面积折价率。</template>
          </div>
        </div>
        <div class="in-item">
          <div class="in-no">02</div>
          <div class="in-name">总价高 → 客群窄 → 卖得慢</div>
          <div class="in-desc">
            120-150㎡ 的接盘客群明显比 90㎡ 小。自住十年以上可以忽略，但若三五年内可能换房，流动性要提前算进去。
          </div>
        </div>
        <div class="in-item">
          <div class="in-no">03</div>
          <div class="in-name">正因难卖，议价空间更大</div>
          <div class="in-desc">
            这个面积段的挂牌天数普遍更长、调价次数更多，是真正能谈下价格的地方。进详情页看「议价锚点」。
          </div>
        </div>
      </div>
      <div class="in-loan tiny muted">
        月供按首付 {{ (s.loan?.down_ratio * 100 || 30).toFixed(0) }}%、
        {{ s.loan?.years || 30 }} 年、年利率 {{ ((s.loan?.rate || 0.031) * 100).toFixed(2) }}% 估算（可在管理台调整）。
      </div>
    </div>

    <!-- 三类房源 -->
    <el-tabs v-model="tab" class="section-gap">
      <el-tab-pane :label="`二手在售 ${s.listing_n}`" name="listing">
        <div class="sort-row">
          <el-radio-group v-model="sort" size="small" @change="load">
            <el-radio-button value="score">捡漏分↓</el-radio-button>
            <el-radio-button value="unit">单价↑</el-radio-button>
            <el-radio-button value="total">总价↑</el-radio-button>
            <el-radio-button value="area">面积↓</el-radio-button>
          </el-radio-group>
          <span class="tiny muted">点卡片进入详情看「为什么便宜 / 为什么可能是坑」</span>
        </div>

        <div v-if="!listings.length" class="card card-pad"><el-empty description="该面积段暂无二手在售" /></div>
        <div v-else class="ta-grid">
          <div
            v-for="it in listings"
            :key="it.id"
            class="ta-card card"
            :class="`g-${it.grade || 'white'}`"
            @click="$router.push(`/listing/${it.id}`)"
          >
            <div class="ta-head">
              <div style="min-width:0">
                <div class="ta-name ellipsis">{{ it.community_name }}</div>
                <div class="tiny muted">
                  {{ it.district }} · {{ it.layout }} · {{ fmt.area(it.area) }}
                  <span v-if="it.floor_text"> · {{ it.floor_text }}</span>
                </div>
              </div>
              <span class="grade-chip" :style="gradeStyle(it.grade)">{{ Math.round(it.score || 0) }}</span>
            </div>

            <div class="ta-price">
              <span class="ta-total mono">{{ fmt.wanOnly(it.total_price) }}</span>
              <span class="tiny muted mono">{{ fmt.money(it.unit_price) }} 元/㎡</span>
              <span v-if="it.deviation_pct > 0" class="tag info">低于同行 {{ fmt.pct(it.deviation_pct) }}</span>
            </div>

            <div class="ta-afford">
              <span>月供 <b class="mono">{{ fmt.money(it.monthly) }}</b></span>
              <span>现金 <b class="mono">{{ fmt.wanOnly(it.cash_needed) }}</b></span>
            </div>

            <div class="ta-foot">
              <span v-if="it.list_days" class="tag">挂 {{ it.list_days }} 天</span>
              <span v-if="it.price_changes" class="tag warn">降 {{ it.price_changes }} 次</span>
              <span v-for="t in (it.defect_tags || []).slice(0, 2)" :key="t" class="tag danger">{{ t }}</span>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="`司法拍卖 ${s.auction_n}`" name="auction">
        <div v-if="!auctions.length" class="card card-pad"><el-empty description="该面积段暂无在拍法拍" /></div>
        <div v-else class="ta-grid">
          <div
            v-for="a in auctions"
            :key="a.id"
            class="ta-card card"
            :class="`g-${a.grade || 'white'}`"
            @click="$router.push(`/auction/${a.id}`)"
          >
            <div class="ta-head">
              <div style="min-width:0">
                <div class="ta-name ellipsis">{{ a.community_name || a.title }}</div>
                <div class="tiny muted">
                  {{ a.district }} · {{ a.layout }} · {{ fmt.area(a.area) }} · {{ a.stage }}
                </div>
              </div>
              <span class="grade-chip" :style="gradeStyle(a.grade)">{{ Math.round(a.score || 0) }}</span>
            </div>
            <div class="ta-price">
              <span class="ta-total mono">{{ fmt.wanOnly(a.landing_price) }}</span>
              <span class="tiny muted">到手价（含税费欠费维修）</span>
            </div>
            <div class="ta-afford">
              <span>起拍 <b class="mono">{{ fmt.wanOnly(a.start_price) }}</b></span>
              <span>净折扣 <b class="mono">{{ fmt.pct(a.net_discount) }}</b></span>
            </div>
            <div class="ta-foot">
              <span class="tag" :class="a.risk_level === 'high' ? 'danger' : a.risk_level === 'mid' ? 'warn' : 'info'">
                风险 {{ fmt.riskLabel(a.risk_level) }}
              </span>
              <span v-for="t in (a.risk_tags || []).slice(0, 2)" :key="t" class="tag danger">{{ t }}</span>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="`在售新盘 ${s.new_home_n}`" name="new_home">
        <div v-if="!newHomes.length" class="card card-pad"><el-empty description="该面积段暂无在售新盘" /></div>
        <div v-else class="ta-grid">
          <div v-for="n in newHomes" :key="n.id" class="ta-card card" @click="$router.push('/newhomes')">
            <div class="ta-head">
              <div>
                <div class="ta-name">{{ n.name }}</div>
                <div class="tiny muted">
                  {{ n.district }} · {{ fmt.area(n.area_min) }}–{{ fmt.area(n.area_max) }} · {{ n.decoration }}
                </div>
              </div>
              <span class="tag" :class="n.sale_status === '尾盘' ? 'warn' : 'info'">{{ n.sale_status }}</span>
            </div>
            <div class="ta-price">
              <span class="ta-total mono">{{ fmt.money(n.avg_unit_price) }}</span>
              <span class="tiny muted">元/㎡</span>
              <span v-if="n.delivery_date" class="tag">交付 {{ n.delivery_date }}</span>
            </div>
            <div class="ta-afford">
              <span>{{ fmt.area(n.entry_area) }} 总价 <b class="mono">{{ fmt.wanOnly(n.entry_total) }}</b></span>
              <span>月供 <b class="mono">{{ fmt.money(n.entry_monthly) }}</b></span>
            </div>
            <div v-if="n.risk_tags && n.risk_tags.length" class="ta-foot">
              <span v-for="t in n.risk_tags" :key="t" class="tag danger">{{ t }}</span>
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <div class="disclaimer section-gap">{{ note }}</div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmt } from '../api'

const presets = [
  { label: '120-150㎡', min: 120, max: 150 },
  { label: '110-130㎡', min: 110, max: 130 },
  { label: '130-160㎡', min: 130, max: 160 },
  { label: '90-120㎡', min: 90, max: 120 },
]

const range = reactive({ min: 120, max: 150 })
const tab = ref('listing')
const sort = ref('score')
const listings = ref([])
const auctions = ref([])
const newHomes = ref([])
const summary = ref({})
const note = ref('')

const s = computed(() => summary.value || {})

function pick(p) {
  range.min = p.min
  range.max = p.max
  load()
}
function gradeStyle(grade) {
  const m = fmt.gradeMeta(grade)
  return { color: m.color, background: m.bg }
}

async function load() {
  if (range.min >= range.max) {
    ElMessage.warning('面积下限要小于上限')
    return
  }
  try {
    const d = await api.target({ min_area: range.min, max_area: range.max, sort: sort.value })
    listings.value = d.listings
    auctions.value = d.auctions
    newHomes.value = d.new_homes
    summary.value = d.summary
    note.value = d.disclaimer
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.area-bar {
  display: flex; align-items: center; gap: 16px; padding: 11px 15px; flex-wrap: wrap;
}
.ab-left, .ab-right { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.ab-right { margin-left: auto; }
.ab-label { font-size: 12.5px; font-weight: 650; }
.seg { display: inline-flex; background: #eef2f6; border-radius: 8px; padding: 2px; }
.seg-btn {
  border: 0; background: transparent; cursor: pointer; font-size: 12px;
  padding: 5px 11px; border-radius: 6px; color: var(--muted); font-family: inherit; white-space: nowrap;
}
.seg-btn.on { background: #fff; color: var(--ink); font-weight: 650; box-shadow: 0 1px 3px rgba(15, 30, 50, .1); }

.insight { background: linear-gradient(135deg, #fbfdfe, #f3f9fb); }
.in-title { font-size: 14px; font-weight: 700; margin-bottom: 12px; }
.in-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.in-no { font-size: 11px; font-weight: 800; color: var(--brand); letter-spacing: 1px; }
.in-name { font-size: 13px; font-weight: 650; margin: 3px 0 4px; }
.in-desc { font-size: 12px; color: var(--muted); line-height: 1.7; }
.in-loan { margin-top: 12px; }

.sort-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }

.ta-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.ta-card { padding: 12px 14px; cursor: pointer; transition: border-color .15s, transform .15s; }
.ta-card:hover { border-color: rgba(14, 116, 144, .45); }
.ta-card.g-red { border-left: 3px solid #e11d48; }
.ta-card.g-yellow { border-left: 3px solid #d97706; }
.ta-card.g-white { border-left: 3px solid #e2e8ef; }

.ta-head { display: flex; align-items: flex-start; gap: 8px; justify-content: space-between; }
.ta-name { font-size: 14.5px; font-weight: 700; }
.ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ta-price { display: flex; align-items: baseline; gap: 8px; margin: 8px 0 6px; flex-wrap: wrap; }
.ta-total { font-size: 19px; font-weight: 760; }
.ta-afford {
  display: flex; gap: 14px; font-size: 11.5px; color: var(--muted);
  background: #f6f9fb; border-radius: 8px; padding: 6px 9px; flex-wrap: wrap;
}
.ta-afford b { color: var(--ink); }
.ta-foot { display: flex; gap: 5px; margin-top: 8px; flex-wrap: wrap; }

.stat-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; }
.stat-card { padding: 11px 13px; }
.sc-val { font-size: 18px; font-weight: 720; }
.sc-key { font-size: 10.5px; color: var(--muted); margin-top: 2px; }

@media (max-width: 1080px) {
  .stat-grid { grid-template-columns: repeat(3, 1fr); }
  .in-grid { grid-template-columns: 1fr; gap: 12px; }
}
@media (max-width: 860px) {
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .ta-grid { grid-template-columns: 1fr; }
  .ab-right { margin-left: 0; width: 100%; }
  .area-bar { gap: 10px; }
}
</style>
