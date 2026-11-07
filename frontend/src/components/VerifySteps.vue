<!-- 人工核验 3 步：这一块是产品底线，不允许被"自动评分"替代 -->
<template>
  <div>
    <div v-for="s in steps" :key="s.step" class="step-item">
      <span class="step-num">{{ s.step }}</span>
      <div>
        <div style="font-weight:650;font-size:13px">{{ s.title }}</div>
        <div class="tiny muted" style="line-height:1.7">{{ s.desc }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ steps: { type: Array, default: () => [] } })
</script>
