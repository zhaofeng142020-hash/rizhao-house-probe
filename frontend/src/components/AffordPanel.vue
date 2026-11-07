<!--
  购房成本与议价锚点面板（房源详情页复用）。
  捡漏分回答「值不值得看」，这里回答「买不买得起、能谈到多少」。
  刻意把「首付之外还要花多少钱」摆在最前面——很多人只算首付，签合同时才发现差十几万。
-->
<template>
  <div class="card card-pad">
    <div class="card-title">
      买下来要花多少
      <span class="hint">含税费与中介费，不只是首付</span>
    </div>

    <!-- 核心三个数 -->
    <div class="big-three">
      <div class="bg-item">
        <div class="bg-val mono">{{ fmt.money(calc.cash) }}</div>
        <div class="bg-key">一次性现金门槛</div>
      </div>
      <div class="bg-item">
        <div class="bg-val mono">{{ fmt.money(calc.monthly) }}</div>
        <div class="bg-key">月供（等额本息）</div>
      </div>
      <div class="bg-item">
        <div class="bg-val mono">{{ fmt.wanOnly(calc.interest) }}</div>
        <div class="bg-key">总利息</div>
      </div>
    </div>

    <!-- 参数调节 -->
    <div class="param-row">
      <span class="pr-key">首付</span>
      <el-slider v-model="downPct" :min="20" :max="100" :step="5" :show-tooltip="false" style="flex:1" />
      <span class="pr-val mono">{{ downPct }}%</span>
    </div>
    <div class="param-row">
      <span class="pr-key">年限</span>
      <el-slider v-model="years" :min="5" :max="30" :step="1" :show-tooltip="false" style="flex:1" />
      <span class="pr-val mono">{{ years }} 年</span>
    </div>
    <div class="param-row">
      <span class="pr-key">利率</span>
      <el-slider v-model="ratePct" :min="2" :max="6" :step="0.05" :show-tooltip="false" style="flex:1" />
      <span class="pr-val mono">{{ ratePct.toFixed(2) }}%</span>
    </div>

    <!-- 明细 -->
    <div class="detail-rows">
      <div class="drow"><span>首付</span><b class="mono">{{ fmt.money(calc.down) }}</b></div>
      <div class="drow"><span>契税</span><b class="mono">{{ fmt.money(cost.tax) }}</b></div>
      <div v-if="cost.agent_fee" class="drow"><span>中介费</span><b class="mono">{{ fmt.money(cost.agent_fee) }}</b></div>
      <div class="drow"><span>维修基金</span><b class="mono">{{ fmt.money(cost.maintenance) }}</b></div>
      <div v-if="cost.decoration_cost" class="drow">
        <span>{{ cost.is_new ? '装修预留（毛坯必花）' : '软装家电预留' }}</span>
        <b class="mono">{{ fmt.money(cost.decoration_cost) }}</b>
      </div>
      <div class="drow total"><span>贷款本金</span><b class="mono">{{ fmt.money(calc.loan) }}</b></div>
    </div>

    <div class="tip-line">
      月供不超家庭月收入 <b>40%</b> 才比较从容，按当前月供推算需要家庭月收入约
      <b>{{ fmt.money(calc.income) }}</b>。
    </div>

    <!-- 租金视角 -->
    <div v-if="rent.yield_pct" class="rent-box">
      <div class="rb-title">租金视角</div>
      <div class="rb-grid">
        <div><span class="rb-key">年租金</span><b class="mono">{{ fmt.money(rent.annual_rent) }}</b></div>
        <div><span class="rb-key">租金回报率</span><b class="mono">{{ rent.yield_pct }}%</b></div>
        <div><span class="rb-key">静态回本</span><b class="mono">{{ rent.payback_years }} 年</b></div>
      </div>
      <div class="tiny muted" style="margin-top:6px">
        日照这类城市租金回报率普遍在 1.5%~2.5%，低于此区间说明房价相对租金偏高，自住可以、纯投资要谨慎。
      </div>
    </div>

    <!-- 议价锚点 -->
    <div v-if="bargain && bargain.suggest_offer" class="bargain-box">
      <div class="bb-head">
        <span class="bb-title">议价参考</span>
        <span class="bb-room">可谈空间约 {{ bargain.room_pct }}%</span>
      </div>
      <div class="bb-prices">
        <div class="bp">
          <div class="bp-key">挂牌价</div>
          <div class="bp-val mono">{{ fmt.wanOnly(item.total_price) }}</div>
        </div>
        <div class="bp arrow">→</div>
        <div class="bp">
          <div class="bp-key">建议首轮出价</div>
          <div class="bp-val mono accent">{{ fmt.wanOnly(bargain.suggest_offer) }}</div>
        </div>
        <div class="bp">
          <div class="bp-key">合理区间</div>
          <div class="bp-val mono">{{ fmt.wanOnly(bargain.fair_low) }}–{{ fmt.wanOnly(bargain.fair_high) }}</div>
        </div>
      </div>
      <div v-if="bargain.market_anchor" class="bb-anchor">
        同户型中位价参照：<b class="mono">{{ fmt.wanOnly(bargain.market_anchor) }}</b>
        （{{ fmt.money(bargain.market_anchor_unit) }} 元/㎡ × {{ fmt.area(item.area) }}）
      </div>
      <ul class="bb-reasons">
        <li v-for="(r, i) in bargain.reasons" :key="i">{{ r }}</li>
      </ul>
      <div class="tiny muted" style="margin-top:6px">{{ bargain.disclaimer }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { fmt } from '../api'

const props = defineProps({
  cost: { type: Object, required: true },
  rent: { type: Object, default: () => ({}) },
  bargain: { type: Object, default: null },
  item: { type: Object, required: true },
})

// 用后端算好的默认参数初始化，用户可现场调
const downPct = ref(Math.round(Number(props.cost.down_ratio || 0.3) * 100))
const years = ref(Number(props.cost.years || 30))
const ratePct = ref(Number((props.cost.annual_rate || 0.031) * 100))

/** 等额本息月供（与后端 services/afford.py 同一公式） */
function monthlyOf(principal, annualRatePct, yrs) {
  const n = Math.max(1, yrs * 12)
  if (principal <= 0) return 0
  const r = annualRatePct / 100 / 12
  if (r <= 0) return principal / n
  const f = Math.pow(1 + r, n)
  return (principal * r * f) / (f - 1)
}

const calc = computed(() => {
  const total = Number(props.item.total_price || 0)
  // 税费/中介/维修/装修不随首付比例变化，直接用后端算好的值
  const extras =
    Number(props.cost.tax || 0) +
    Number(props.cost.agent_fee || 0) +
    Number(props.cost.maintenance || 0) +
    Number(props.cost.decoration_cost || 0)
  const down = (total * downPct.value) / 100
  const loan = Math.max(0, total - down)
  const monthly = monthlyOf(loan, ratePct.value, years.value)
  const interest = monthly * years.value * 12 - loan
  return {
    down,
    loan,
    monthly,
    interest,
    cash: down + extras,
    income: monthly / 0.4,
  }
})
</script>

<style scoped>
.big-three { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 14px; }
.bg-item { background: linear-gradient(135deg, #f2fafc, #f8fbfc); border-radius: 10px; padding: 11px 13px; }
.bg-val { font-size: 18px; font-weight: 750; color: var(--brand-dark, #0b5468); }
.bg-key { font-size: 10.5px; color: var(--muted); margin-top: 2px; }

.param-row { display: flex; align-items: center; gap: 12px; margin-bottom: 2px; }
.pr-key { font-size: 12px; color: var(--muted); flex: 0 0 32px; }
.pr-val { font-size: 12px; font-weight: 650; flex: 0 0 56px; text-align: right; }

.detail-rows { display: flex; flex-direction: column; gap: 5px; margin-top: 10px; }
.drow { display: flex; justify-content: space-between; font-size: 12.5px; }
.drow span { color: var(--muted); }
.drow.total { border-top: 1px dashed var(--line); padding-top: 6px; margin-top: 2px; }
.drow.total span { color: var(--ink); font-weight: 650; }

.tip-line {
  margin-top: 10px; font-size: 11.5px; color: #4a5768; line-height: 1.7;
  background: #f6f9fb; border-radius: 8px; padding: 8px 10px;
}
.tip-line b { color: var(--ink); }

.rent-box { margin-top: 14px; border-top: 1px solid var(--line); padding-top: 12px; }
.rb-title { font-size: 12.5px; font-weight: 650; margin-bottom: 7px; }
.rb-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.rb-key { display: block; font-size: 10.5px; color: var(--muted); }
.rb-grid b { font-size: 13px; }

.bargain-box {
  margin-top: 14px; border-top: 1px solid var(--line); padding-top: 12px;
}
.bb-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 9px; }
.bb-title { font-size: 12.5px; font-weight: 650; }
.bb-room { font-size: 11.5px; font-weight: 650; color: #b45309; background: rgba(217, 119, 6, .12); padding: 2px 9px; border-radius: 20px; }
.bb-prices { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.bp { flex: 1; min-width: 84px; background: #f7fafb; border-radius: 9px; padding: 8px 10px; }
.bp.arrow { flex: 0 0 auto; background: none; padding: 0; color: var(--muted); }
.bp-key { font-size: 10px; color: var(--muted); }
.bp-val { font-size: 14px; font-weight: 720; }
.bp-val.accent { color: var(--brand); }
.bb-anchor { font-size: 11.5px; color: var(--muted); margin-top: 8px; }
.bb-anchor b { color: var(--ink); }
.bb-reasons { margin: 8px 0 0; padding-left: 16px; font-size: 11.5px; color: #4a5768; line-height: 1.8; }

@media (max-width: 860px) {
  .big-three { grid-template-columns: 1fr; }
  .bg-item { padding: 9px 12px; }
  .bb-prices { flex-direction: column; align-items: stretch; }
  .bp.arrow { display: none; }
  .rb-grid { grid-template-columns: 1fr 1fr; }
}
</style>
