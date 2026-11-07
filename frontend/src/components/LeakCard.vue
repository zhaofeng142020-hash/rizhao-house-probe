<!-- 房源卡片：首页「今日可留意」与异动列表共用。
     刻意把「为什么便宜」放在最显眼位置——先给嫌疑，再给判断。 -->
<template>
  <div class="leak-card" :class="`grade-${item.grade || 'white'}`" @click="$emit('open', item)">
    <div class="leak-card-head">
      <div style="flex:1;min-width:0">
        <div class="leak-card-title" :title="title">
          <span v-if="item.kind === 'auction'" class="tag danger" style="margin-right:6px">法拍</span>
          {{ item.community_name || item.title }}
        </div>
        <div class="leak-card-meta">
          {{ item.district }} · {{ item.layout || '—' }} · {{ fmt.area(item.area) }}
          <span v-if="item.floor_text"> · {{ item.floor_text }}</span>
        </div>
      </div>
      <div class="score-pill" :style="scoreStyle">
        {{ Math.round(item.score || 0) }}<small>分</small>
      </div>
    </div>

    <div class="price-line">
      <span class="price-total">{{ fmt.wan(displayPrice) }}</span>
      <span class="price-unit">单价 {{ fmt.money(displayUnit) }} 元/㎡</span>
      <span v-if="deviation > 0" class="dev-badge">↓{{ fmt.pct(deviation) }}</span>
    </div>

    <div v-if="item.why_cheap" class="tiny" style="color:#4a5768;line-height:1.7">
      <span class="strong" style="color:var(--yellow)">为什么便宜：</span>
      {{ truncate(item.why_cheap, 62) }}
    </div>

    <div class="leak-card-foot">
      <span class="grade-chip" :style="{ color: meta.color, background: meta.bg }">
        {{ meta.icon }} {{ meta.label }}
      </span>
      <span class="tag">置信度 {{ fmt.confidenceLabel(item.confidence) }}</span>
      <span v-if="item.price_changes" class="tag warn">调价 {{ item.price_changes }} 次</span>
      <span v-if="item.list_days" class="tag">挂牌 {{ item.list_days }} 天</span>
      <span v-for="t in defectTags" :key="t" class="tag danger">{{ t }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmt } from '../api'

const props = defineProps({ item: { type: Object, required: true } })
defineEmits(['open'])

const meta = computed(() => fmt.gradeMeta(props.item.grade))

const scoreStyle = computed(() => ({
  color: meta.value.color,
  background: meta.value.bg,
}))

const title = computed(() => props.item.title || props.item.community_name || '')

// 法拍用「到手价」，二手房用挂牌总价
const displayPrice = computed(() =>
  props.item.kind === 'auction' ? props.item.landing_price : props.item.total_price
)
const displayUnit = computed(() => {
  if (props.item.kind === 'auction') {
    const area = Number(props.item.area || 0)
    return area ? Number(props.item.landing_price || 0) / area : 0
  }
  return props.item.unit_price
})
const deviation = computed(() => Number(props.item.deviation_pct || 0))

const defectTags = computed(() => {
  const raw = props.item.kind === 'auction' ? props.item.risk_tags : props.item.defect_tags
  if (Array.isArray(raw)) return raw.slice(0, 3)
  if (typeof raw === 'string') {
    try { return JSON.parse(raw).slice(0, 3) } catch (e) { return [] }
  }
  return []
})

function truncate(text, n) {
  const s = String(text || '')
  return s.length > n ? s.slice(0, n) + '…' : s
}
</script>
