<!--
  搜索小区：小区名 / 别名 / 板块 / 对口学校 / 房源标题 都能搜。
  搜索结果里直接可以关注小区，关注后去「观察清单」看价格波动。
-->
<template>
  <div>
    <div class="card card-pad search-head">
      <div class="card-title">搜索小区 <span class="hint">支持小区名、别名、板块、对口学校</span></div>
      <el-input
        v-model="q"
        size="large"
        clearable
        placeholder="输入小区名，例如：海曲、实验、石臼"
        @input="onInput"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <div class="quick-row" v-if="!q.trim()">
        <span class="tiny muted" style="margin-right:4px">常看小区</span>
        <span v-for="c in hotCommunities" :key="c.id" class="quick-chip" @click="setQ(c.name)">{{ c.name }}</span>
      </div>
      <div class="quick-row" v-if="!q.trim()">
        <span class="tiny muted" style="margin-right:4px">按板块</span>
        <span v-for="d in districts" :key="d" class="quick-chip" @click="setQ(d)">{{ d }}</span>
      </div>
    </div>

    <div v-if="loading" class="card card-pad section-gap"><el-skeleton :rows="5" animated /></div>

    <template v-else-if="q.trim()">
      <!-- 小区结果 -->
      <div class="card card-pad section-gap">
        <div class="card-title">
          匹配小区（{{ communities.length }}）
          <span class="hint">点卡片进小区档案，点星标关注房价波动</span>
        </div>
        <el-empty v-if="!communities.length" description="没有匹配的小区" :image-size="62" />
        <div v-else class="comm-grid">
          <div v-for="c in communities" :key="c.id" class="comm-card" @click="openCommunity(c)">
            <div class="cc-top">
              <span class="strong">{{ c.name }}</span>
              <el-button
                link
                size="small"
                :type="c.watched ? 'warning' : 'info'"
                @click.stop="toggleWatch(c)"
                :title="c.watched ? '取消关注' : '关注房价波动'"
              >
                <el-icon :size="16"><StarFilled v-if="c.watched" /><Star v-else /></el-icon>
              </el-button>
            </div>
            <div class="tiny muted">
              {{ c.district }}<span v-if="c.school_district"> · {{ c.school_district }}</span>
              <span v-if="c.is_extended"> · 扩展区</span>
            </div>
            <div class="cc-metrics">
              <span>在售 <b class="mono">{{ c.listing_n }}</b> 套</span>
              <span>中位 <b class="mono">{{ fmt.money(c.median_unit) }}</b> 元/㎡</span>
            </div>
            <div class="cc-foot">
              <span v-if="c.red_n" class="tag danger">红级 {{ c.red_n }}</span>
              <span v-if="c.target_area_n" class="tag info">120-150㎡ {{ c.target_area_n }} 套</span>
              <span class="tag" :class="c.watched ? 'warn' : ''">{{ c.watched ? '已关注' : `去化 ${fmt.months(c.months_to_clear)}` }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 房源结果 -->
      <div class="card card-pad section-gap" v-if="listings.length">
        <div class="card-title">相关房源（{{ listingTotal }}） <span class="hint">只显示前 8 条</span></div>
        <div class="hit-list">
          <div v-for="l in listings" :key="l.id" class="hit-row" @click="$router.push(`/listing/${l.id}`)">
            <span class="strong">{{ l.community_name }}</span>
            <span class="tiny muted">{{ l.layout }} · {{ fmt.area(l.area) }}</span>
            <span v-if="l.source_info && l.source_info.kind !== 'demo'" class="tag info">真实录入</span>
            <span class="mono" style="margin-left:auto">{{ fmt.wanOnly(l.total_price) }}</span>
            <span class="tiny muted mono">{{ fmt.money(l.unit_price) }} 元/㎡</span>
          </div>
        </div>
      </div>

      <!-- 新盘结果 -->
      <div class="card card-pad section-gap" v-if="newHomes.length">
        <div class="card-title">相关新盘（{{ newHomes.length }}）</div>
        <div class="hit-list">
          <div v-for="n in newHomes" :key="n.id" class="hit-row" @click="$router.push('/newhomes')">
            <span class="strong">{{ n.name }}</span>
            <span class="tiny muted">{{ n.district }} · {{ n.sale_status }}</span>
            <span class="mono" style="margin-left:auto">{{ fmt.money(n.avg_unit_price) }} 元/㎡</span>
          </div>
        </div>
      </div>

      <div v-if="!communities.length && !listings.length && !newHomes.length" class="card card-pad section-gap">
        <el-empty description="没找到。试试更短的关键字、板块名或对口学校">
          <el-button size="small" @click="setQ('')">清空重来</el-button>
        </el-empty>
      </div>
    </template>

    <div v-else class="card card-pad section-gap empty-hint">
      <div class="strong">找小区，先看它在售几套、卖得多快</div>
      <div class="tiny muted" style="margin-top:6px;line-height:1.8">
        搜到小区后可以直接关注。系统会每天记录一次该小区的在售中位单价，
        在「观察清单」里看 7 / 30 / 90 天变化和价格曲线；波动超过 2% 会推送提醒。
      </div>
    </div>

    <div class="disclaimer section-gap">
      中位单价随在售房源结构变化，结构性变动不等于同一套房降价。挂牌价≠成交价。
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmt } from '../api'

const route = useRoute()
const router = useRouter()
const q = ref(String(route.query.q || ''))
const loading = ref(false)
const communities = ref([])
const listings = ref([])
const listingTotal = ref(0)
const newHomes = ref([])
const hotCommunities = ref([])
const districts = ref([])
let timer = null

const trimmed = computed(() => q.value.trim())

function setQ(v) {
  q.value = v
  router.replace({ path: '/search', query: v ? { q: v } : {} })
  run()
}
function onInput() {
  clearTimeout(timer)
  timer = setTimeout(() => {
    router.replace({ path: '/search', query: trimmed.value ? { q: trimmed.value } : {} })
    run()
  }, 260)
}
function openCommunity(c) {
  router.push(`/community/${c.id}`)
}

async function toggleWatch(c) {
  try {
    if (c.watched) await api.unwatchCommunity(c.id)
    else await api.watchCommunity(c.id)
    c.watched = !c.watched
    ElMessage.success(c.watched ? '已关注，可在观察清单里看波动' : '已取消关注')
  } catch (e) {
    ElMessage.error('操作失败：' + e.message)
  }
}

async function run() {
  clearTimeout(timer)
  if (!trimmed.value) {
    communities.value = []
    listings.value = []
    newHomes.value = []
    return
  }
  loading.value = true
  const kw = trimmed.value
  try {
    const [c, l, n] = await Promise.all([
      api.communityList({ keyword: kw }),
      api.listings({ keyword: kw, limit: 8 }),
      api.newHomes({ keyword: kw }),
    ])
    // 三个列表接口统一返回 { total, items }
    communities.value = c.items || []
    listings.value = l.items || []
    listingTotal.value = l.total || 0
    newHomes.value = n.items || []
  } catch (e) {
    ElMessage.error('搜索失败：' + e.message)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const [all, ds] = await Promise.all([api.communityList(), api.districts()])
    // /api/meta/districts 返回 { items: [{ district, n, avg_unit, ... }] }
    hotCommunities.value = (all.items || []).slice(0, 8)
    districts.value = (ds.items || []).map((x) => x.district).filter(Boolean).slice(0, 6)
  } catch {
    hotCommunities.value = []
    districts.value = []
  }
  if (trimmed.value) run()
})
</script>

<style scoped>
.search-head .el-input { max-width: 640px; }
.quick-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.quick-chip {
  font-size: 12px; padding: 3px 10px; border: 1px solid var(--border, #dcdfe6);
  border-radius: 16px; cursor: pointer; color: var(--muted);
}
.quick-chip:hover { color: var(--brand, #1a7f96); border-color: var(--brand, #1a7f96); }
.comm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; margin-top: 12px; }
.comm-card {
  border: 1px solid var(--border, #e8eaed); border-radius: 10px; padding: 11px 12px;
  cursor: pointer; transition: border-color .15s;
}
.comm-card:hover { border-color: var(--brand, #1a7f96); }
.cc-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.cc-metrics { display: flex; gap: 12px; font-size: 12px; margin-top: 9px; color: var(--muted); flex-wrap: wrap; }
.cc-metrics b { color: var(--text, #20242a); }
.cc-foot { display: flex; gap: 5px; margin-top: 9px; flex-wrap: wrap; }
.hit-list { display: flex; flex-direction: column; margin-top: 10px; }
.hit-row {
  display: flex; align-items: center; gap: 10px; padding: 9px 4px;
  border-bottom: 1px solid var(--border, #eef0f2); cursor: pointer; font-size: 13px;
}
.hit-row:hover { background: var(--bg-soft, #f7fafb); }
.empty-hint { font-size: 13px; }
@media (max-width: 640px) {
  .hit-row { flex-wrap: wrap; }
  .comm-grid { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); }
}
</style>
