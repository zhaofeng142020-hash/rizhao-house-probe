<!-- 评分拆解：5 个维度得分条 + 权重标注，让分数可解释、可质疑 -->
<template>
  <div>
    <div v-for="r in radar" :key="r.name" class="break-line">
      <span class="break-name">{{ r.name }}</span>
      <span style="width:34px;text-align:right" class="tiny muted">{{ Math.round(r.weight * 100) }}%</span>
      <div class="break-bar"><div class="break-fill" :style="{ width: `${r.value}%` }"></div></div>
      <span class="break-val">{{ Math.round(r.value) }}<span class="tiny">/100</span></span>
    </div>
    <div v-if="notes.length" class="tiny muted" style="margin-top:8px;line-height:1.8">
      <div v-for="(n, i) in notes" :key="i">· {{ n }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  radar: { type: Array, default: () => [] },
  notes: { type: Array, default: () => [] },
})
</script>
