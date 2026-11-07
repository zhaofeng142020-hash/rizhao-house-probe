<!-- 通用 ECharts 容器：负责初始化、resize、以及 option 为空时的占位提示 -->
<template>
  <div class="chart-wrap" :style="{ height }">
    <div v-if="!option" class="chart-empty">{{ emptyText }}</div>
    <div v-else ref="el" class="chart-canvas"></div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, default: null },
  height: { type: String, default: '260px' },
  emptyText: { type: String, default: '暂无数据' },
})

const el = ref(null)
let chart = null
let observer = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value, null, { renderer: 'canvas' })
  chart.setOption(props.option, true)
}

watch(() => props.option, async (val) => {
  if (!val) {
    chart?.dispose()
    chart = null
    return
  }
  await nextTick()
  render()
}, { deep: true })

onMounted(() => {
  if (props.option) render()
  // 容器宽度变化（侧边栏折叠 / 响应式）时自动重绘
  observer = new ResizeObserver(() => chart?.resize())
  if (el.value) observer.observe(el.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.chart-wrap { width: 100%; position: relative; }
.chart-canvas { width: 100%; height: 100%; }
.chart-empty {
  height: 100%; display: flex; align-items: center; justify-content: center;
  color: var(--muted); font-size: 13px; background: var(--panel-soft);
  border: 1px dashed var(--line); border-radius: 10px;
}
</style>
