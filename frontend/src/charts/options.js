/**
 * ECharts 配置构造器（统一放这里，页面只负责传数据）。
 * 配色遵循「探针」冷色底 + 红黄分级，避免用绿色表达"跌"造成误读。
 */
const C = {
  text: '#1b2430',
  muted: '#6b7786',
  line: '#e6e9ef',
  brand: '#0e7490',
  brandSoft: 'rgba(14,116,144,.14)',
  red: '#e11d48',
  yellow: '#d97706',
  gray: '#94a3b8',
  good: '#059669',
}

const baseGrid = { left: 12, right: 18, top: 30, bottom: 12, containLabel: true }

const tooltipStyle = {
  backgroundColor: 'rgba(255,255,255,.97)',
  borderColor: C.line,
  borderWidth: 1,
  textStyle: { color: C.text, fontSize: 12 },
  extraCssText: 'box-shadow:0 6px 20px rgba(15,23,42,.10);border-radius:8px;',
}

/** 捡漏评分雷达图：5 个维度，权重写在同名字段里 */
export function radarOption(radar = []) {
  const indicators = radar.map((r) => ({
    name: `${r.name}\n${Math.round(r.weight * 100)}%`,
    max: 100,
  }))
  return {
    tooltip: { ...tooltipStyle, trigger: 'item' },
    radar: {
      indicator: indicators,
      radius: '64%',
      center: ['50%', '54%'],
      splitNumber: 4,
      axisName: { color: C.muted, fontSize: 11, lineHeight: 15 },
      splitLine: { lineStyle: { color: [C.line] } },
      splitArea: { areaStyle: { color: ['rgba(248,250,252,.9)', 'rgba(255,255,255,0)'] } },
      axisLine: { lineStyle: { color: C.line } },
    },
    series: [{
      type: 'radar',
      symbolSize: 5,
      data: [{
        value: radar.map((r) => r.value),
        name: '维度得分',
        areaStyle: { color: C.brandSoft },
        lineStyle: { color: C.brand, width: 2 },
        itemStyle: { color: C.brand },
      }],
    }],
  }
}

/**
 * 同小区同户型单价分布箱线图。
 * 只画一个分组，但叠加全部散点，避免"箱线图掩盖真实分布"的问题。
 */
export function boxplotOption(comp = {}, currentUnit = 0) {
  const values = comp.values || []
  if (!values.length) return null
  const jitter = values.map((v, i) => [0 + ((i % 7) - 3) * 0.045, v])
  const box = [[comp.min, comp.q1, comp.median, comp.q3, comp.max]]
  const markLine = currentUnit > 0 ? {
    silent: true,
    symbol: 'none',
    label: { formatter: `本套 ${Math.round(currentUnit).toLocaleString()} 元/㎡`, color: C.red, fontSize: 11, position: 'insideEndTop' },
    lineStyle: { color: C.red, type: 'dashed', width: 1.4 },
    data: [{ yAxis: currentUnit }],
  } : undefined

  return {
    grid: { ...baseGrid, top: 36, bottom: 24 },
    tooltip: {
      ...tooltipStyle,
      trigger: 'item',
      formatter: (p) => {
        if (p.seriesType === 'boxplot') {
          const [min, q1, med, q3, max] = p.data
          return `同户型 ${comp.n} 套<br/>最低 ${Math.round(min).toLocaleString()}<br/>Q1 ${Math.round(q1).toLocaleString()}<br/>中位 ${Math.round(med).toLocaleString()}<br/>Q3 ${Math.round(q3).toLocaleString()}<br/>最高 ${Math.round(max).toLocaleString()}`
        }
        return `${Math.round(p.value[1]).toLocaleString()} 元/㎡`
      },
    },
    xAxis: {
      type: 'category',
      data: [`${comp.layout || ''}（${comp.n} 套可比）`],
      axisLine: { lineStyle: { color: C.line } },
      axisTick: { show: false },
      axisLabel: { color: C.muted, fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      scale: true,                    // 关键：不强制从 0 开始，否则箱体被压扁看不出分布
      name: '元/㎡',
      nameTextStyle: { color: C.muted, fontSize: 11, align: 'left' },
      splitLine: { lineStyle: { color: C.line, type: 'dashed' } },
      axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => (v / 1000).toFixed(1) + 'k' },
    },
    series: [
      {
        name: '分布',
        type: 'boxplot',
        data: box,
        boxWidth: [14, 46],
        itemStyle: { color: 'rgba(14,116,144,.10)', borderColor: C.brand, borderWidth: 1.4 },
        markLine,
      },
      {
        name: '可比房源',
        type: 'scatter',
        data: jitter,
        symbolSize: 7,
        itemStyle: { color: 'rgba(14,116,144,.55)' },
      },
    ],
  }
}

/** 调价时间轴：阶梯线 + 降价点标注 */
export function timelineOption(history = []) {
  if (!history.length) return null
  const dates = history.map((h) => String(h.observed_at).slice(0, 10))
  const prices = history.map((h) => Number(h.price))
  const drops = history
    .map((h, i) => ({ value: [i, Number(h.price)], drop: Number(h.drop_pct || 0) }))
    .filter((d) => d.drop < 0)
    .map((d) => ({ ...d, label: { formatter: `${(d.drop * 100).toFixed(1)}%`, color: '#fff', fontSize: 10 } }))

  return {
    grid: { ...baseGrid, top: 34, bottom: 26 },
    tooltip: {
      ...tooltipStyle,
      trigger: 'axis',
      formatter: (ps) => {
        const p = ps[0]
        const item = history[p.dataIndex]
        const drop = Number(item.drop_pct || 0)
        return `${dates[p.dataIndex]}<br/>总价 ${Number(p.value).toLocaleString()} 元` +
          (drop < 0 ? `<br/>较上次 ${(drop * 100).toFixed(1)}%` : '')
      },
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: C.line } },
      axisTick: { show: false },
      axisLabel: { color: C.muted, fontSize: 10, rotate: dates.length > 6 ? 30 : 0 },
    },
    yAxis: {
      type: 'value',
      name: '总价(万)',
      nameTextStyle: { color: C.muted, fontSize: 11 },
      scale: true,
      splitLine: { lineStyle: { color: C.line, type: 'dashed' } },
      axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => (v / 10000).toFixed(0) },
    },
    series: [{
      type: 'line',
      step: 'end',
      data: prices,
      symbolSize: 6,
      lineStyle: { color: C.brand, width: 2 },
      itemStyle: { color: C.brand },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: 'rgba(14,116,144,.18)' }, { offset: 1, color: 'rgba(14,116,144,0)' }],
        },
      },
      markPoint: {
        symbol: 'pin',
        symbolSize: 34,
        itemStyle: { color: C.red },
        data: drops,
      },
    }],
  }
}

/** 法拍到价瀑布图 */
export function waterfallOption(waterfall = []) {
  const names = waterfall.map((w) => w.name)
  // 横轴只放短标签：360~390px 的手机上 5 个全称会挤在一起互相压字。
  // 完整含义仍走 tooltip，卡片标题与下方说明里也有完整口径。
  const shortNames = names.map((n) => n.replace(/(预估|预留)$/, ''))
  const helps = waterfall.map((w, i) => {
    if (i === 0 || i === waterfall.length - 1) return 0
    return waterfall.slice(0, i).reduce((s, x) => s + x.value, 0)
  })
  const values = waterfall.map((w, i) => (i === 0 || i === waterfall.length - 1 ? w.value : w.value))
  return {
    grid: { ...baseGrid, top: 30 },
    tooltip: {
      ...tooltipStyle,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const idx = ps[ps.length - 1].dataIndex
        return `${names[idx]}<br/>${Math.round(waterfall[idx].value).toLocaleString()} 元`
      },
    },
    xAxis: {
      type: 'category',
      data: shortNames,
      axisLine: { lineStyle: { color: C.line } },
      axisTick: { show: false },
      axisLabel: { color: C.muted, fontSize: 11, interval: 0 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: C.line, type: 'dashed' } },
      axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => (v / 10000).toFixed(0) + '万' },
    },
    series: [
      { type: 'bar', stack: 'total', silent: true, itemStyle: { color: 'transparent' }, data: helps },
      {
        type: 'bar',
        stack: 'total',
        barWidth: 34,
        label: {
          show: true, position: 'top', color: C.text, fontSize: 10,
          formatter: (p) => (p.value / 10000).toFixed(1) + '万',
        },
        data: values.map((v, i) => ({
          value: v,
          itemStyle: {
            color: i === 0 ? C.brand : i === values.length - 1 ? C.red : C.gray,
            borderRadius: i === values.length - 1 ? [4, 4, 0, 0] : 0,
          },
        })),
      },
    ],
  }
}

/** 板块对比：中位单价条形 + 流动性折线（双轴） */
export function districtOption(districts = []) {
  const names = districts.map((d) => d.district)
  return {
    grid: { left: 8, right: 12, top: 44, bottom: 54, containLabel: true },
    legend: { top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: C.muted, fontSize: 11 } },
    tooltip: { ...tooltipStyle, trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'category',
      data: names,
      axisLine: { lineStyle: { color: C.line } },
      axisTick: { show: false },
      axisLabel: {
        color: C.muted, fontSize: 10, interval: 0,
        rotate: names.length > 6 ? 34 : 0,
        margin: 8,
      },
    },
    yAxis: [
      {
        type: 'value',
        splitLine: { lineStyle: { color: C.line, type: 'dashed' } },
        axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => (v / 1000).toFixed(0) + 'k/㎡' },
      },
      {
        type: 'value',
        max: 100,
        splitLine: { show: false },
        axisLabel: { color: C.muted, fontSize: 11 },
      },
    ],
    series: [
      {
        name: '中位单价',
        type: 'bar',
        barWidth: 22,
        data: districts.map((d) => ({
          value: d.median_unit,
          itemStyle: {
            color: d.is_extended ? 'rgba(100,116,139,.55)' : C.brand,
            borderRadius: [4, 4, 0, 0],
          },
        })),
      },
      {
        name: '流动性',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        symbolSize: 6,
        lineStyle: { color: C.yellow, width: 2 },
        itemStyle: { color: C.yellow },
        data: districts.map((d) => d.liquidity),
      },
    ],
  }
}

/** 观察清单价格曲线（迷你折线） */
export function curveOption(curve = []) {
  if (!curve.length) return null
  return {
    grid: { left: 0, right: 0, top: 6, bottom: 0 },
    tooltip: { ...tooltipStyle, trigger: 'axis', formatter: (ps) => `${ps[0].name}<br/>${Number(ps[0].value).toLocaleString()} 元` },
    xAxis: { type: 'category', data: curve.map((c) => c.date), show: true, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { show: false } },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{
      type: 'line',
      data: curve.map((c) => c.price),
      smooth: true,
      symbol: 'none',
      lineStyle: { color: C.brand, width: 2 },
      areaStyle: { color: C.brandSoft },
    }],
  }
}

/** 市场大盘：在售套数（柱，右轴）+ 中位挂牌单价（线，左轴） */
export function marketOption(series = []) {
  if (!series.length) return null
  const dates = series.map((p) => String(p.date).slice(5))   // MM-DD 省空间
  return {
    grid: { left: 10, right: 14, top: 34, bottom: 24, containLabel: true },
    legend: { top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: C.muted, fontSize: 11 } },
    tooltip: {
      ...tooltipStyle,
      trigger: 'axis',
      formatter: (ps) => {
        const idx = ps[0].dataIndex
        const p = series[idx]
        return `${p.date}<br/>中位单价 ${p.median_unit ? Math.round(p.median_unit).toLocaleString() : '—'} 元/㎡` +
          `<br/>在售 ${p.listing_n} 套 · 新增 ${p.new_n} · 下架 ${p.delist_n}`
      },
    },
    xAxis: {
      type: 'category', data: dates, boundaryGap: true,
      axisLine: { lineStyle: { color: C.line } }, axisTick: { show: false },
      axisLabel: { color: C.muted, fontSize: 10, interval: Math.max(0, Math.floor(dates.length / 8) - 1) },
    },
    yAxis: [
      {
        type: 'value', name: '元/㎡', scale: true,
        nameTextStyle: { color: C.muted, fontSize: 11 },
        splitLine: { lineStyle: { color: C.line, type: 'dashed' } },
        axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => (v / 1000).toFixed(1) + 'k' },
      },
      {
        type: 'value', name: '套',
        nameTextStyle: { color: C.muted, fontSize: 11 },
        splitLine: { show: false },
        axisLabel: { color: C.muted, fontSize: 11 },
      },
    ],
    series: [
      {
        name: '在售套数', type: 'bar', yAxisIndex: 1, barWidth: 10,
        itemStyle: { color: 'rgba(100,116,139,.35)', borderRadius: [3, 3, 0, 0] },
        data: series.map((p) => p.listing_n),
      },
      {
        name: '中位单价', type: 'line', yAxisIndex: 0, smooth: true, symbol: 'none',
        lineStyle: { color: C.brand, width: 2 },
        areaStyle: { color: C.brandSoft },
        data: series.map((p) => p.median_unit),
      },
    ],
  }
}

/** 异动类型分布（横向条形） */
export function eventTypeOption(byType = []) {
  const sorted = [...byType].sort((a, b) => a.n - b.n)
  return {
    grid: { left: 8, right: 30, top: 10, bottom: 8, containLabel: true },
    tooltip: { ...tooltipStyle, trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', splitLine: { lineStyle: { color: C.line, type: 'dashed' } }, axisLabel: { color: C.muted, fontSize: 11 } },
    yAxis: {
      type: 'category',
      data: sorted.map((t) => t.label || t.event_type),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: C.text, fontSize: 11 },
    },
    series: [{
      type: 'bar',
      barWidth: 12,
      label: { show: true, position: 'right', color: C.muted, fontSize: 11 },
      data: sorted.map((t) => ({
        value: t.n,
        itemStyle: { color: t.red > 0 ? C.red : t.yellow > 0 ? C.yellow : C.gray, borderRadius: [0, 6, 6, 0] },
      })),
    }],
  }
}

export const CHART_COLORS = C
