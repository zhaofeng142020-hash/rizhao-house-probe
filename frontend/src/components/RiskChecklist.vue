<!-- 风险清单：命中的硬伤按等级高亮，未命中的通用项一律提示"必须人工核验" -->
<template>
  <div>
    <div v-for="c in checklist" :key="c.key" class="check-item">
      <span class="check-dot" :class="c.hit ? c.level : 'ok'"></span>
      <div style="flex:1">
        <div style="font-size:13px;font-weight:600">
          {{ c.label }}
          <span v-if="!c.hit" class="tag" style="margin-left:6px">待核验</span>
          <span v-else class="tag danger" style="margin-left:6px">已发现</span>
        </div>
        <div class="tiny muted" style="line-height:1.7">{{ c.desc }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ checklist: { type: Array, default: () => [] } })
</script>
