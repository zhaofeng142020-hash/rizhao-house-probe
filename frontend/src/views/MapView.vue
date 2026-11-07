<!--
  小区地图：一眼看到每个小区在哪、贵不贵、卖得动不动。
  · 未配置 AMAP_KEY 时：用经纬度投影自绘「示意底图」，点位大小=在售套数，颜色深浅=中位单价。
    这是纯前端 SVG，不依赖任何外部地图服务，也不绘制任何行政边界（合规）。
  · 配置了 AMAP_KEY 时：自动切换为高德地图真实底图，同样显示点位并支持点击进小区。
  两种模式交互一致：点小区 → 右侧档案卡 → 进入小区详情看全部在售房源。
-->
<template>
  <div>
    <!-- 工具条 -->
    <div class="map-toolbar card">
      <div class="seg">
        <button :class="['seg-btn', { on: scope === 'city' }]" @click="scope = 'city'">主城区</button>
        <button :class="['seg-btn', { on: scope === 'all' }]" @click="scope = 'all'">全域（含莒县/五莲/岚山）</button>
      </div>
      <div class="toolbar-right">
        <span class="legend">
          <span class="legend-label">中位单价</span>
          <span class="legend-bar"></span>
          <span class="tiny muted">{{ fmt.money(legendMin) }}</span>
          <span class="tiny muted">→ {{ fmt.money(legendMax) }} 元/㎡</span>
        </span>
        <span class="legend">
          <span class="legend-label">点位大小=在售套数</span>
        </span>
        <span v-if="amapError" class="tag warn">底图加载失败 · 已用示意底图</span>
        <span v-else-if="!amapKey" class="tag warn">未配地图 Key · 示意底图</span>
        <span v-else class="tag info">高德地图</span>
      </div>
    </div>

    <div class="map-wrap section-gap">
      <div class="map-canvas card" :class="{ 'is-live': amapReady }">
        <!-- 自绘 SVG 示意底图（可点击）。
             真实底图加载成功前一直显示它——否则 Key 错/网络慢时用户只看到一块白板 -->
        <svg
          v-if="!amapReady"
          class="svg-map"
          :viewBox="`0 0 ${MAP_W} ${MAP_H}`"
          preserveAspectRatio="xMidYMid meet"
          @mouseleave="hoverId = null"
        >
          <defs>
            <linearGradient id="mapbg" x1="0" y1="0" x2="0.7" y2="1">
              <stop offset="0%" stop-color="#f4f9fb" />
              <stop offset="100%" stop-color="#e6f0f4" />
            </linearGradient>
            <radialGradient id="glow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stop-color="rgba(14,116,144,.28)" />
              <stop offset="100%" stop-color="rgba(14,116,144,0)" />
            </radialGradient>
          </defs>

          <rect :width="MAP_W" :height="MAP_H" fill="url(#mapbg)" rx="12" />

          <!-- 经纬网 -->
          <g stroke="#cfdfe6" stroke-width=".7" stroke-dasharray="3 5" opacity=".85">
            <line v-for="g in gridLines.v" :key="'v' + g.k" :x1="g.x" :y1="pad" :x2="g.x" :y2="MAP_H - pad" />
            <line v-for="g in gridLines.h" :key="'h' + g.k" :x1="pad" :y1="g.y" :x2="MAP_W - pad" :y2="g.y" />
          </g>

          <!-- 板块名（淡淡的，只做方位参照） -->
          <g>
            <text
              v-for="d in districtLabels"
              :key="'d' + d.name"
              :x="d.x" :y="d.y"
              text-anchor="middle"
              class="district-label"
            >{{ d.name }}</text>
          </g>

          <!-- 小区点位 -->
          <g>
            <g
              v-for="c in visibleCommunities"
              :key="c.id"
              class="map-pin"
              :class="{ active: selected && selected.id === c.id, dim: hoverId && hoverId !== c.id }"
              @mouseenter="hoverId = c.id"
              @click="select(c)"
            >
              <!-- 红色环：该小区有 🔴 重点留意 -->
              <circle
                v-if="c.red_n > 0"
                :cx="pos(c).x" :cy="pos(c).y" :r="radius(c) + 7"
                fill="none" stroke="#e11d48" stroke-width="1.6" opacity=".55"
              />
              <circle :cx="pos(c).x" :cy="pos(c).y" :r="radius(c) + 9" fill="url(#glow)" />
              <!-- 手机端点击命中区放大，避免手指点不中 -->
              <circle
                v-if="isMobile"
                :cx="pos(c).x" :cy="pos(c).y" :r="radius(c) + 12"
                fill="transparent"
              />
              <circle
                :cx="pos(c).x" :cy="pos(c).y" :r="radius(c)"
                :fill="colorOf(c)" stroke="#fff" stroke-width="1.6"
              />
              <!-- 手机屏只有 375px，SVG 缩放后 10px 标签实际不到 5px，读不出来。
                   所以窄屏只留点位，点一下在下方卡片里看详情。 -->
              <text
                v-if="!isMobile"
                :x="labelOf(c).txt.x" :y="labelOf(c).txt.y"
                :text-anchor="labelOf(c).anchor" class="pin-label"
              >{{ c.name }}</text>
            </g>
          </g>

          <text :x="MAP_W - 12" :y="MAP_H - 10" text-anchor="end" class="watermark">
            经纬度示意 · 非真实底图 · 不表示任何行政边界
          </text>
        </svg>

        <!-- 真实底图：覆盖在示意底图之上，只有加载成功（amapReady）才显形 -->
        <div v-if="amapKey" ref="mapEl" class="amap-box" :class="{ 'is-ready': amapReady }"></div>

        <!-- 悬浮提示 -->
        <div v-if="hoverItem && !amapReady" class="map-tip" :style="tipStyle">
          <div class="tip-title">{{ hoverItem.name }}</div>
          <div class="tip-row">{{ hoverItem.district }} · {{ hoverItem.build_year }}年建成</div>
          <div class="tip-row">中位单价 <b>{{ fmt.money(hoverItem.median_unit) }}</b> 元/㎡</div>
          <div class="tip-row">在售 {{ hoverItem.listing_n }} 套 · 去化 {{ fmt.months(hoverItem.months_to_clear) }}</div>
          <div v-if="hoverItem.red_n" class="tip-row" style="color:#e11d48">🔴 {{ hoverItem.red_n }} 套重点留意</div>
        </div>
      </div>

      <!-- 选中小区档案 -->
      <aside class="map-panel card">
        <template v-if="selected">
          <div class="panel-head">
            <div>
              <div class="panel-name">{{ selected.name }}</div>
              <div class="tiny muted">{{ selected.district }} · {{ selected.build_year }}年建成</div>
            </div>
            <span v-if="selected.is_extended" class="tag warn">扩展区</span>
          </div>

          <div class="panel-metrics">
            <div class="pm">
              <div class="pm-val mono">{{ fmt.money(selected.median_unit) }}</div>
              <div class="pm-key">中位单价</div>
            </div>
            <div class="pm">
              <div class="pm-val mono">{{ selected.listing_n }}</div>
              <div class="pm-key">在售套数</div>
            </div>
            <div class="pm">
              <div class="pm-val mono">{{ fmt.months(selected.months_to_clear) }}</div>
              <div class="pm-key">去化周期</div>
            </div>
            <div class="pm">
              <div class="pm-val mono">{{ selected.target_area_n }}</div>
              <div class="pm-key">120-150㎡</div>
            </div>
          </div>

          <div class="panel-rows">
            <div class="prow"><span>电梯</span><b>{{ selected.has_elevator ? '有' : '无' }}</b></div>
            <div class="prow"><span>对口学校</span><b class="ellipsis">{{ selected.school_district || '—' }}</b></div>
            <div class="prow"><span>近90天成交</span><b>{{ selected.deal_active_90d }} 套</b></div>
            <div class="prow"><span>流动性分</span><b>{{ Math.round(selected.liquidity_score || 0) }}</b></div>
            <div class="prow"><span>最高捡漏分</span><b>{{ selected.top_score ? selected.top_score.toFixed(1) : '—' }}</b></div>
          </div>

          <el-button type="primary" style="width:100%" @click="$router.push(`/community/${selected.id}`)">
            查看该小区全部在售房源
          </el-button>
          <div class="tiny muted" style="margin-top:8px">
            去化周期 = 在售套数 ÷ 月均成交套数。超过 24 个月说明卖得很慢，买入后想脱手会很难。
          </div>
        </template>
        <div v-else class="panel-empty">
          <div class="empty-icon">🗺️</div>
          <div class="strong">点击地图上的小区点位</div>
          <div class="tiny muted" style="margin-top:6px">右侧会显示该小区的价格、去化周期与在售房源入口</div>
        </div>
      </aside>
    </div>

    <!-- 小区清单 -->
    <div class="card card-pad">
      <div class="card-title">
        小区清单
        <span class="hint">点任意一行进入小区详情</span>
      </div>
      <div class="comm-grid">
        <div
          v-for="c in sortedList"
          :key="'l' + c.id"
          class="comm-item"
          :class="{ active: selected && selected.id === c.id }"
          @click="select(c, true)"
        >
          <div class="ci-head">
            <span class="strong">{{ c.name }}</span>
            <span v-if="c.is_extended" class="tag warn">扩展区</span>
            <span v-if="c.red_n" class="tag danger">🔴{{ c.red_n }}</span>
            <span class="mono strong ci-price">{{ fmt.money(c.median_unit) }}</span>
          </div>
          <div class="tiny muted">
            {{ c.district }} · 建成 {{ c.build_year }} · {{ c.has_elevator ? '有电梯' : '无电梯' }} · 在售 {{ c.listing_n }} 套
          </div>
          <div class="ci-foot">
            <span class="tag" :class="turnoverTag(c.months_to_clear)">去化 {{ fmt.months(c.months_to_clear) }}</span>
            <span class="tag" :class="c.target_area_n ? 'info' : ''">120-150㎡ {{ c.target_area_n }} 套</span>
          </div>
        </div>
      </div>
    </div>

    <div class="disclaimer section-gap">
      地图仅用于定位参照：{{ amapReady ? '底图来自高德地图（GCJ-02 坐标）' : '示意底图为经纬度线性投影' }}，
      不绘制任何行政边界，点位为小区大致位置，精确位置请以实地为准。
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmt } from '../api'
import { useIsMobile } from '../composables/useIsMobile'

const { isMobile } = useIsMobile()

const router = useRouter()

const MAP_W = 820
const MAP_H = 700
const pad = 34

// 只区分「主城」与「全域」两种视野；实际取景框由数据自动算出（见 projection），
// 写死窗口会导致点位挤在角落——日照市区的小区经差不到 0.1 度，必须自适应。
const SCOPE_FILTER = {
  city: (c) => !c.is_extended,
  all: () => true,
}

const scope = ref('city')
const items = ref([])
const selected = ref(null)
const hoverId = ref(null)
const amapKey = ref('')
const amapSecurity = ref('')
const amapReady = ref(false)      // 底图真的起来了才切过去
const amapError = ref('')         // 失败原因（持久提示，不用一闪而过的 toast）
const mapEl = ref(null)
let amap = null
let amapMap = null
let dotMarkers = []     // 只画点的 marker（永远显示）
let labelMarkers = []   // 名字气泡（按避让结果决定显示/隐藏）

async function load() {
  try {
    const [c, cfg] = await Promise.all([api.communityList(), api.publicConfig()])
    items.value = c.items
    amapKey.value = cfg.amap_key || ''
    amapSecurity.value = cfg.amap_security_code || ''
    if (amapKey.value) initAmap()
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  }
}

// --------------------------------------------------------------------------
// 投影：把经纬度映射到 SVG 画布
// 经度方向按 cos(纬度) 压缩，否则在日照纬度上东西会被拉长近 1.2 倍。
// 取景框由当前视野内的实际点位算出并留 10% 边距，否则点位会挤成一团。
// --------------------------------------------------------------------------
const visibleCommunities = computed(() => {
  const keep = SCOPE_FILTER[scope.value]
  return items.value.filter((c) => c.lat && c.lng && keep(c))
})

const bounds = computed(() => {
  const pts = visibleCommunities.value
  if (!pts.length) {
    return { minLng: 119.48, maxLng: 119.6, minLat: 35.36, maxLat: 35.47 }
  }
  const lngs = pts.map((c) => Number(c.lng))
  const lats = pts.map((c) => Number(c.lat))
  let minLng = Math.min(...lngs)
  let maxLng = Math.max(...lngs)
  let minLat = Math.min(...lats)
  let maxLat = Math.max(...lats)
  // 单点或所有点几乎共线时给一个最小跨度，避免除零
  const spanLng = Math.max(maxLng - minLng, 0.012)
  const spanLat = Math.max(maxLat - minLat, 0.012)
  const padLng = spanLng * 0.14
  const padLat = spanLat * 0.14
  minLng -= padLng; maxLng += padLng
  minLat -= padLat; maxLat += padLat
  return { minLng, maxLng, minLat, maxLat }
})

const projection = computed(() => {
  const b = bounds.value
  const latMid = (b.minLat + b.maxLat) / 2
  const kx = Math.cos((latMid * Math.PI) / 180)
  const geoW = (b.maxLng - b.minLng) * kx
  const geoH = b.maxLat - b.minLat
  const availW = MAP_W - pad * 2
  const availH = MAP_H - pad * 2
  const ratio = geoW / geoH || 1
  let drawW = availW
  let drawH = availW / ratio
  if (drawH > availH) {
    drawH = availH
    drawW = availH * ratio
  }
  const offX = pad + (availW - drawW) / 2
  const offY = pad + (availH - drawH) / 2
  return { b, drawW, drawH, offX, offY }
})

function pos(c) {
  const p = projection.value
  const lng = Number(c.lng)
  const lat = Number(c.lat)
  const x = p.offX + ((lng - p.b.minLng) / (p.b.maxLng - p.b.minLng)) * p.drawW
  const y = p.offY + ((p.b.maxLat - lat) / (p.b.maxLat - p.b.minLat)) * p.drawH
  return { x, y }
}

const gridLines = computed(() => {
  const p = projection.value
  const v = []
  const h = []
  const stepLng = scope.value === 'city' ? 0.02 : 0.1
  const stepLat = scope.value === 'city' ? 0.02 : 0.1
  let k = 0
  for (let lng = Math.ceil(p.b.minLng / stepLng) * stepLng; lng <= p.b.maxLng; lng += stepLng) {
    v.push({ k: k++, x: p.offX + ((lng - p.b.minLng) / (p.b.maxLng - p.b.minLng)) * p.drawW })
  }
  k = 0
  for (let lat = Math.ceil(p.b.minLat / stepLat) * stepLat; lat <= p.b.maxLat; lat += stepLat) {
    h.push({ k: k++, y: p.offY + ((p.b.maxLat - lat) / (p.b.maxLat - p.b.minLat)) * p.drawH })
  }
  return { v, h }
})

// 板块名称标注：放在该板块点群的质心上方，作为方位参照
const districtLabels = computed(() => {
  const groups = {}
  visibleCommunities.value.forEach((c) => {
    const p = pos(c)
    groups[c.district] = groups[c.district] || { xs: [], ys: [] }
    groups[c.district].xs.push(p.x)
    groups[c.district].ys.push(p.y)
  })
  return Object.entries(groups).map(([name, g]) => {
    const cx = g.xs.reduce((a, b) => a + b, 0) / g.xs.length
    const cy = g.ys.reduce((a, b) => a + b, 0) / g.ys.length
    // 太靠顶时改放点群下方，避免被裁掉
    const y = cy - 46 < 24 ? cy + 52 : cy - 46
    return { name, x: cx, y }
  })
})

// --------------------------------------------------------------------------
// 视觉编码
// --------------------------------------------------------------------------
const priceRange = computed(() => {
  const vals = visibleCommunities.value.map((c) => Number(c.median_unit || 0)).filter(Boolean)
  if (!vals.length) return [0, 1]
  return [Math.min(...vals), Math.max(...vals)]
})
const legendMin = computed(() => priceRange.value[0])
const legendMax = computed(() => priceRange.value[1])

/** 单价 → 青色系深浅（不用红绿，避免和涨跌语义混淆） */
function colorOf(c) {
  const [lo, hi] = priceRange.value
  const v = Number(c.median_unit || 0)
  const t = hi > lo ? (v - lo) / (hi - lo) : 0.5
  const from = [125, 211, 252]   // #7dd3fc
  const to = [12, 74, 110]       // #0c4a6e
  const mix = from.map((f, i) => Math.round(f + (to[i] - f) * t))
  return `rgb(${mix.join(',')})`
}

function radius(c) {
  // 在售套数映射到 6~15px，用开方压缩，避免大社区把图撑满
  const base = Math.max(6, Math.min(15, 5 + Math.sqrt(Number(c.listing_n || 1)) * 2.4))
  // 手机上没有文字标签了，点位要更大才好点、好看
  return isMobile.value ? base * 1.35 : base
}

/**
 * 标签避让。
 * 日照市区十几个小区的经纬度非常接近（经差不到 0.1 度），
 * 如果一律把名字画在点的正上方，必然糊成一团。这里做一次贪心布局：
 * 按纵向排序，逐个尝试「上/下/右/左」四个位置，选第一个不撞已有标签、
 * 也不压住其它点位的方向。
 */
const labelLayout = computed(() => {
  const placed = []
  const out = {}

  // 板块名先占位：小区标签要绕开它，否则会出现「日照港家属院」压住「石臼」这种情况
  for (const d of districtLabels.value) {
    const w = String(d.name).length * 11 + 10
    const h = 15
    placed.push({ x: d.x - w / 2, y: d.y - h + 3, w, h })
  }

  const pts = visibleCommunities.value
    .map((c) => ({ c, p: pos(c), r: radius(c) }))
    .sort((a, b) => a.p.y - b.p.y || a.p.x - b.p.x)

  // 8 个方向 × 3 档距离 = 24 个候选位置，密集点位也能排开
  const DIRS = [
    { dx: 0, dy: -1, anchor: 'middle' },
    { dx: 0, dy: 1, anchor: 'middle' },
    { dx: 1, dy: 0, anchor: 'start' },
    { dx: -1, dy: 0, anchor: 'end' },
    { dx: 0.9, dy: -0.9, anchor: 'start' },
    { dx: -0.9, dy: -0.9, anchor: 'end' },
    { dx: 0.9, dy: 0.9, anchor: 'start' },
    { dx: -0.9, dy: 0.9, anchor: 'end' },
  ]
  const GAPS = [4, 13, 23]
  const H = 13

  const overlaps = (a, b) =>
    !(a.x + a.w < b.x || a.x > b.x + b.w || a.y + a.h < b.y || a.y > b.y + b.h)

  for (const it of pts) {
    const text = String(it.c.name || '')
    const w = text.length * 10.5 + 8      // 中文字宽按 10.5px 估（.pin-label 字号 10.5）
    const cands = []
    for (const gap of GAPS) {
      for (const dir of DIRS) {
        const gx = it.p.x + dir.dx * (it.r + gap)
        let gy = it.p.y + dir.dy * (it.r + gap)
        if (dir.anchor === 'middle') {
          // 垂直方向：向下时基线要再下移一个字高，否则文字会压住点
          gy = dir.dy < 0 ? gy : gy + 10
        }
        const bx = dir.anchor === 'middle' ? gx - w / 2 : dir.anchor === 'start' ? gx : gx - w
        cands.push({
          anchor: dir.anchor,
          txt: { x: gx, y: gy },
          box: { x: bx, y: gy - 11, w, h: H },
        })
      }
    }

    const inCanvas = (b) => b.x > 2 && b.y > 2 && b.x + b.w < MAP_W - 2 && b.y + b.h < MAP_H - 2
    const freeOfLabels = (b) => !placed.some((o) => overlaps(b, o))
    const freeOfDots = (b) =>
      !pts.some((o) => o !== it && o.p.x > b.x - 2 && o.p.x < b.x + b.w + 2
        && o.p.y > b.y - 2 && o.p.y < b.y + b.h + 2)

    const chosen =
      cands.find((c) => inCanvas(c.box) && freeOfLabels(c.box) && freeOfDots(c.box)) ||
      cands.find((c) => inCanvas(c.box) && freeOfLabels(c.box)) ||
      cands.find((c) => freeOfLabels(c.box)) ||
      cands[0]
    placed.push(chosen.box)
    out[it.c.id] = chosen
  }
  return out
})

function labelOf(c) {
  return labelLayout.value[c.id] || {
    anchor: 'middle',
    txt: { x: pos(c).x, y: pos(c).y - radius(c) - 6 },
  }
}

function turnoverTag(m) {
  if (m === null || m === undefined) return 'danger'
  if (m <= 12) return 'info'
  if (m <= 24) return 'warn'
  return 'danger'
}

const sortedList = computed(() =>
  [...items.value].sort((a, b) => Number(b.median_unit || 0) - Number(a.median_unit || 0))
)

const hoverItem = computed(() => items.value.find((c) => c.id === hoverId.value) || null)
const tipStyle = computed(() => {
  const it = hoverItem.value
  if (!it) return {}
  const p = pos(it)
  const left = Math.min(Math.max((p.x / MAP_W) * 100, 6), 62)
  const top = Math.min(Math.max((p.y / MAP_H) * 100 + 3, 4), 70)
  return { left: left + '%', top: top + '%' }
})

function select(c, jump = false) {
  selected.value = c
  if (jump) router.push(`/community/${c.id}`)
}

// --------------------------------------------------------------------------
// 高德地图（仅配置 Key 时启用；坐标已是 GCJ-02，不做二次偏移）
// --------------------------------------------------------------------------
async function initAmap() {
  await nextTick()
  // 底图加载可能因为 Key 错、被墙、超时而挂住。给 9 秒硬上限：
  // 到点没起来就认定失败，保持示意底图可用（宁可少个底图，不能给一块白板）。
  const timeout = new Promise((_, reject) => {
    setTimeout(() => reject(new Error('TIMEOUT')), 9000)
  })
  try {
    // JS API 2.0 自 2021-12 起强制校验安全密钥，缺了会白屏报 INVALID_USER_SCODE
    if (amapSecurity.value) {
      window._AMapSecurityConfig = { securityJsCode: amapSecurity.value }
    }
    const AMapLoader = (await import('@amap/amap-jsapi-loader')).default
    await Promise.race([
      (async () => {
        amap = await AMapLoader.load({
          key: amapKey.value,
          version: '2.0',
          plugins: ['AMap.Scale'],
        })
      })(),
      timeout,
    ])
    if (amapMap) { amapMap.destroy(); amapMap = null }
    amapMap = new amap.Map(mapEl.value, {
      zoom: 11,
      center: [119.53, 35.42],
      viewMode: '2D',
    })
    // 地图实例化成功 ≠ 瓦片能出来（Key 白名单不符时也是这个状态）。
    // 等一次 complete 再切过去，看起来才是「真的加载成功」。
    const goLive = () => {
      if (amapReady.value) return
      amapReady.value = true
      // 容器高度是切过去之后才撑开的，地图得跟着重算尺寸，并重新取一次景
      nextTick(() => {
        try { amapMap && amapMap.resize() } catch (_) {}
        fitAll()
        layoutLabels()
      })
    }
    amapMap.on('complete', goLive)
    // 缩放/拖动后文字的相对位置全变了，得重新避让一次
    amapMap.on('zoomend', scheduleLayout)
    amapMap.on('moveend', scheduleLayout)
    drawMarkers()
    // 兜底：部分环境不触发 complete，2.5 秒后仍未置位就按成功处理
    setTimeout(() => { if (amapMap) goLive() }, 2500)
  } catch (e) {
    const msg = String((e && e.message) || e)
    amapError.value = /SCODE|INVALID_USER|USERKEY/i.test(msg)
      ? 'Key 或安全密钥不匹配（管理台 → 地图底图配置）'
      : (msg === 'TIMEOUT' ? '加载超时（网络或 Key 白名单）' : msg)
    ElMessage.warning('高德底图没起来，已用示意底图：' + amapError.value)
  }
}

// 视野按实际点位自适应。写死 zoom 的话，12 个小区在 11 级下间距只有几十像素，
// 标签全叠在一起——等于没标。
function fitAll() {
  if (!amapMap || !amap) return
  const pts = visibleCommunities.value.filter((c) => c.lng && c.lat)
  if (!pts.length) return
  try {
    amapMap.setFitView(null, false, [70, 70, 70, 70])
    // 只有一个小区（或全部重合）时 setFitView 会放到最大级别，压到 13 级更合适
    if (amapMap.getZoom() > 13) amapMap.setZoom(13)
  } catch (_) { /* 底图未就绪时忽略 */ }
}

function drawMarkers() {
  if (!amapMap || !amap) return
  amapMap.clearMap()
  dotMarkers = []
  labelMarkers = []
  visibleCommunities.value.forEach((c) => {
    const pos = [Number(c.lng), Number(c.lat)]
    const dot = new amap.Marker({
      position: pos,
      title: `${c.name} 中位 ${Math.round(c.median_unit || 0)} 元/㎡`,
      content: `<div class="amap-dot${c.red_n > 0 ? ' has-red' : ''}" style="--pin:${colorOf(c)}"></div>`,
      offset: new amap.Pixel(-6.5, -6.5),
      zIndex: 120,
    })
    dot.on('click', () => select(c, true))
    amapMap.add(dot)
    dotMarkers.push({ c, marker: dot })

    // 文字单独一个 marker：高德的 HTML Marker 不会自动避让，
    // 城区里几个小区只隔几百米，气泡会叠成一坨，所以位置由 layoutLabels() 逐个算。
    const label = new amap.Marker({
      position: pos,
      content: `<div class="amap-pin" style="--pin:${colorOf(c)}">${c.name}<b>${Math.round((c.median_unit || 0) / 1000)}k</b></div>`,
      offset: new amap.Pixel(10, -10),
      zIndex: 130,
    })
    label.on('click', () => select(c, true))
    amapMap.add(label)
    labelMarkers.push({ c, marker: label, w: 26 + `${c.name}000k`.length * 6.6, h: 20 })
  })
  fitAll()
  layoutLabels()
}

// 标签避让：优先红级、其次在售多的；右/左/上/下四个候选位都撞就只留圆点。
// 和示意底图用的是同一套思路，保证两种模式下看到的名字顺序一致。
function layoutLabels() {
  if (!amapMap) return
  const items = labelMarkers.map((it) => {
    const p = amapMap.lngLatToContainer([Number(it.c.lng), Number(it.c.lat)])
    return { it, x: typeof p.getX === 'function' ? p.getX() : p.x, y: typeof p.getY === 'function' ? p.getY() : p.y }
  })
  items.sort((a, b) => ((b.it.c.red_n || 0) - (a.it.c.red_n || 0)) || ((b.it.c.listing_n || 0) - (a.it.c.listing_n || 0)))

  const dots = items.map(({ x, y }) => ({ x1: x - 7, y1: y - 7, x2: x + 7, y2: y + 7 }))
  const placed = []
  const hits = (r, list) => list.some((o) => r.x1 < o.x2 && r.x2 > o.x1 && r.y1 < o.y2 && r.y2 > o.y1)
  // 贴边的气泡会被容器裁掉半截，所以候选位还必须落在地图可视范围内
  let vw = 9999
  let vh = 9999
  try {
    const sz = amapMap.getSize()
    vw = sz.getWidth()
    vh = sz.getHeight()
  } catch (_) { /* 取不到尺寸就不做边界限制 */ }
  const inside = (r) => r.x1 >= 4 && r.y1 >= 4 && r.x2 <= vw - 4 && r.y2 <= vh - 4

  items.forEach(({ it, x, y }) => {
    const { w, h } = it
    const cands = [
      { dx: 10, dy: -h / 2 },          // 右
      { dx: -10 - w, dy: -h / 2 },     // 左
      { dx: -w / 2, dy: -h - 12 },     // 上
      { dx: -w / 2, dy: 12 },          // 下
    ]
    const hit = cands.find((cd) => {
      const r = { x1: x + cd.dx, y1: y + cd.dy, x2: x + cd.dx + w, y2: y + cd.dy + h }
      return inside(r) && !hits(r, dots) && !hits(r, placed)
    })
    if (hit) {
      it.marker.setOffset(new amap.Pixel(hit.dx, hit.dy))
      it.marker.show()
      placed.push({ x1: x + hit.dx, y1: y + hit.dy, x2: x + hit.dx + w, y2: y + hit.dy + h })
    } else {
      it.marker.hide()   // 放不下就只留圆点，点一下右侧卡片里有名字
    }
  })
}

let layoutTimer = null
function scheduleLayout() {
  clearTimeout(layoutTimer)
  layoutTimer = setTimeout(layoutLabels, 120)
}

onMounted(load)
watch(scope, () => {
  selected.value = null
  if (amapKey.value) drawMarkers()
})
</script>

<style scoped>
/* ---------- 工具条 ---------- */
.map-toolbar {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 10px 14px; margin-bottom: 12px;
}
.seg { display: inline-flex; background: #eef2f6; border-radius: 8px; padding: 2px; }
.seg-btn {
  border: 0; background: transparent; cursor: pointer; font-size: 12.5px;
  padding: 5px 12px; border-radius: 6px; color: var(--muted); font-family: inherit;
}
.seg-btn.on { background: #fff; color: var(--ink); font-weight: 650; box-shadow: 0 1px 3px rgba(15, 30, 50, .1); }
.toolbar-right { display: flex; align-items: center; gap: 14px; margin-left: auto; flex-wrap: wrap; }
.legend { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; }
.legend-label { color: var(--muted); }
.legend-bar {
  width: 70px; height: 7px; border-radius: 4px;
  background: linear-gradient(90deg, #7dd3fc, #0c4a6e);
}

/* ---------- 地图 ---------- */
.map-wrap { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 14px; align-items: start; }
.map-canvas { position: relative; padding: 6px; overflow: hidden; }
/* 真实底图是绝对定位的（要压在示意地图上），会让容器失去高度。
   所以底图起来后由容器自己撑高，否则只剩顶部一条瓦片。 */
.map-canvas.is-live { height: 572px; }
.svg-map { width: 100%; height: auto; display: block; border-radius: 12px; }
.amap-box {
  position: absolute; inset: 6px; border-radius: 12px; overflow: hidden;
  opacity: 0; pointer-events: none; transition: opacity .2s;
}
.amap-box.is-ready { opacity: 1; pointer-events: auto; }

.district-label {
  font-size: 11px; fill: #8aa4b1; letter-spacing: 2px;
  font-family: var(--sans); pointer-events: none;
}
.watermark { font-size: 10px; fill: #a8bfc9; pointer-events: none; }

.map-pin { cursor: pointer; transition: opacity .15s; }
.map-pin.dim { opacity: .45; }
.map-pin circle { transition: r .15s; }
.map-pin:hover circle:nth-of-type(2) { stroke-width: 2.6; }
.map-pin.active circle:nth-of-type(2) { stroke: #0f172a; stroke-width: 2.6; }
.pin-label {
  font-size: 10.5px; fill: #24424f; font-family: var(--sans); font-weight: 600;
  paint-order: stroke; stroke: rgba(255, 255, 255, .92); stroke-width: 3px;
  pointer-events: none;
}

.map-tip {
  position: absolute; z-index: 5; pointer-events: none;
  background: rgba(255, 255, 255, .98); border: 1px solid var(--line);
  border-radius: 9px; padding: 8px 10px; min-width: 168px;
  box-shadow: 0 6px 22px rgba(15, 30, 50, .13);
}
.tip-title { font-size: 13px; font-weight: 700; margin-bottom: 3px; }
.tip-row { font-size: 11.5px; color: var(--muted); line-height: 1.65; }
.tip-row b { color: var(--ink); }

/* ---------- 右侧档案卡 ---------- */
.map-panel { padding: 14px; display: flex; flex-direction: column; gap: 12px; }
.panel-head { display: flex; align-items: flex-start; gap: 8px; }
.panel-name { font-size: 16px; font-weight: 700; }
.panel-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.pm { background: #f6f9fb; border-radius: 9px; padding: 8px 10px; }
.pm-val { font-size: 15px; font-weight: 700; }
.pm-key { font-size: 10.5px; color: var(--muted); margin-top: 1px; }
.panel-rows { display: flex; flex-direction: column; gap: 6px; }
.prow { display: flex; justify-content: space-between; gap: 10px; font-size: 12.5px; }
.prow span { color: var(--muted); }
.ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.panel-empty { text-align: center; padding: 30px 10px; color: var(--muted); }
.empty-icon { font-size: 30px; margin-bottom: 8px; }

/* ---------- 小区清单 ---------- */
.comm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(272px, 1fr)); gap: 10px; }
.comm-item {
  border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
  cursor: pointer; transition: border-color .15s, background .15s;
}
.comm-item:hover { border-color: rgba(14, 116, 144, .4); background: #f8fcfd; }
.comm-item.active { border-color: var(--brand); background: #f2fafc; }
.ci-head { display: flex; align-items: center; gap: 6px; font-size: 13.5px; margin-bottom: 2px; }
.ci-price { margin-left: auto; }
.ci-foot { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }

/* 高德 marker 的样式（在 scoped 里需要 :deep 才作用于动态插入的 DOM） */
:deep(.amap-dot) {
  width: 13px; height: 13px; border-radius: 50%;
  background: var(--pin, #0e7490); border: 2px solid #fff;
  box-shadow: 0 1px 4px rgba(15, 30, 50, .3); cursor: pointer;
}
/* 有红级房源的小区加一圈红环，和示意底图保持一致 */
:deep(.amap-dot.has-red) { box-shadow: 0 0 0 3.5px rgba(225, 29, 72, .38), 0 1px 4px rgba(15, 30, 50, .3); }
:deep(.amap-pin) {
  background: var(--pin, #0e7490); color: #fff; font-size: 11px; font-weight: 600;
  padding: 2px 7px; border-radius: 6px; white-space: nowrap; cursor: pointer;
  box-shadow: 0 2px 8px rgba(15, 30, 50, .22);
}
:deep(.amap-pin b) { margin-left: 5px; font-weight: 700; }

@media (max-width: 860px) {
  .map-wrap { grid-template-columns: 1fr; }
  .map-panel { order: 2; }
  .map-canvas.is-live { height: 352px; }
  .pin-label { font-size: 13px; }
  .comm-grid { grid-template-columns: 1fr; }
  .toolbar-right { width: 100%; margin-left: 0; }
}
</style>
