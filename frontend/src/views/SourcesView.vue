<!--
  数据来源：把「房源到底是不是真的」摆在明面上。

  这个页面刻意不粉饰：库里绝大部分是演示样本，就直接写演示样本；
  采集器只是代码存在、选择器未验证，就写未验证；
  想让房源变全，只有两条路 —— 逐条核验的真实导入，或验证过的采集通道。
-->
<template>
  <div v-loading="loading">
    <!-- 真实性总览 -->
    <div class="card card-pad truth-card">
      <div class="card-title">
        现在这套数据，有多少是真的？
        <span class="hint">更新时间 {{ ov.updated_at || '—' }}</span>
        <el-button size="small" style="margin-left:auto" @click="load">刷新</el-button>
      </div>
      <div class="truth-grid">
        <div class="truth-cell">
          <div class="tc-val mono">{{ ov.total || 0 }}</div><div class="tc-key">在挂房源</div>
        </div>
        <div class="truth-cell demo">
          <div class="tc-val mono">{{ ov.demo || 0 }}</div><div class="tc-key">演示样本</div>
        </div>
        <div class="truth-cell real">
          <div class="tc-val mono">{{ ov.real || 0 }}</div><div class="tc-key">真实录入 / 采集</div>
        </div>
        <div class="truth-cell">
          <div class="tc-val mono">{{ ov.crawled || 0 }}</div><div class="tc-key">自动采集</div>
        </div>
        <div class="truth-cell">
          <div class="tc-val mono">{{ ov.manual || 0 }}</div><div class="tc-key">人工录入</div>
        </div>
      </div>
      <div class="honesty-note">
        <b>数据口径：</b>{{ ov.honesty_note || '正在读取…' }}
      </div>

      <!-- 云端调度：明确「本机关机也照跑」，以及异动要怎么看 -->
      <div v-if="ov.schedule" class="refresh-note" style="margin-top:8px">
        <div class="strong tiny">
          云端自动刷新：每天 {{ (ov.schedule.times || []).join(' / ') }}（{{ ov.schedule.timezone || 'Asia/Shanghai' }}）
          <span :style="{ color: ov.schedule.running ? 'var(--good)' : '#b45309' }">
            · {{ ov.schedule.running ? '调度运行中' : '调度未运行' }}
          </span>
        </div>
        <div class="tiny" style="margin-top:3px;line-height:1.8">
          下次执行：{{ ov.schedule.next_run ? ov.schedule.next_run.replace('T', ' ').slice(0, 16) : '待调度启动' }}。
          抓取、入库、重算、提醒都在<b>云端服务进程</b>里完成，本机电脑关机不影响；
          若托管容器被回收，任何人打开一次网站会自动补跑一轮。
          本机只在 58 会话失效时需要打开一次页面重新同步。
        </div>
        <div class="tiny" style="margin-top:3px;line-height:1.8">
          异动提醒：{{ (ov.push && ov.push.label) || '站内提醒' }} —
          {{ ov.push && ov.push.external ? '会主动推送到外部通道' : '目前只写站内，用手机或其它设备打开本站即可查看' }}。
        </div>
      </div>

      <!-- 自动刷新状态：每天两次的抓取结果，如实展示（含被验证码拦住的情况） -->
      <div v-if="ov.last_refresh" class="refresh-note">
        <div class="strong tiny">上次自动刷新：{{ ov.last_refresh.at }}</div>
        <div class="tiny" style="margin-top:3px;line-height:1.8">
          <span v-for="(v, k) in (ov.last_refresh.sources || {})" :key="k" style="margin-right:12px">
            {{ sourceLabel(k) }}：
            <template v-if="v.captcha">
              <b style="color:#b45309">被要求人机验证，本次跳过</b>
            </template>
            <template v-else-if="v.fetched">
              抓到 {{ v.fetched }} / 新增 {{ v.saved || 0 }} / 更新 {{ v.updated || 0 }} / 调价 {{ v.price_changes || 0 }}
            </template>
            <template v-else>{{ v.note || '未抓到数据' }}</template>
          </span>
        </div>
        <div v-if="captchaBlocked" class="tiny" style="margin-top:4px;color:#b45309;line-height:1.7">
          站点弹了验证码。程序不识别验证码：请在本机运行
          <code>python tools/session_cookie.py --site {{ captchaKeys.join(' --site ') || 'anjuke' }}</code>
          自己过一次验证，把会话交给程序，之后每天按该会话抓取。
        </div>
      </div>
      <div class="tiny muted" style="margin-top:8px;line-height:1.75">
        演示样本是固定随机种子生成的示意数据，用于展示「为什么便宜 / 为什么可能是坑」的研判逻辑，
        <b>不是任何平台的真实挂牌，不能拿去议价、不能当作市场行情</b>。
        每条房源详情页都带来源卡片，真实录入的才会给出原始页面链接。
        同一套房可能同时挂在贝壳、安居客和 58 同城，系统按平台分别记录（便于横向比价），不做跨平台合并。
        每天 07:30 / 19:30 自动抓取两次，抓到的调价会写进房源的价格历史。
      </div>
    </div>

    <!-- 来源目录 -->
    <div class="card card-pad section-gap">
      <div class="card-title">
        来源目录（{{ (ov.sources || []).length }} 个通道）
        <span class="hint">「真实条数」为 0 就代表还没真正接上数据</span>
      </div>

      <div class="src-head tiny muted">
        <span>通道 / 类型</span><span>真实条数</span><span>接入状态</span><span>最近采集</span><span>开关</span>
      </div>
      <div v-for="s in (ov.sources || [])" :key="s.key" class="src-row">
        <div class="src-name">
          <a :href="s.home" target="_blank" rel="noopener noreferrer" class="strong" @click.stop>{{ s.name }}</a>
          <div class="tiny muted">
            {{ s.kind_label }} · {{ s.parser }}
            <span v-if="!s.verified" class="tag warn" style="margin-left:4px">选择器未验证</span>
          </div>
          <div class="tiny muted" style="margin-top:2px">{{ s.note }}</div>
        </div>
        <div class="mono strong" :class="s.real_count ? 'has-real' : 'no-real'">{{ s.real_count }}</div>
        <div>
          <span class="tag" :class="s.enabled && s.crawl_ready ? 'info' : ''">
            {{ s.enabled && s.crawl_ready ? '已开启' : (s.enabled ? '已勾选·采集未启用' : '未开启') }}
          </span>
          <div class="tiny muted" style="margin-top:3px">
            {{ s.crawl_ready ? '服务端 CRAWL_ENABLED=1' : '服务端 CRAWL_ENABLED=0' }}
          </div>
        </div>
        <div class="tiny muted mono">{{ s.last_crawl_at || '从未采集' }}</div>
        <div>
          <el-switch
            :model-value="s.enabled"
            size="small"
            :loading="toggling === s.key"
            @change="(v) => toggle(s, v)"
          />
        </div>
      </div>

      <div class="precond">
        <div class="strong tiny" style="margin-bottom:6px">开启采集前必须知道的四件事</div>
        <ol>
          <li v-for="(p, i) in (ov.preconditions || [])" :key="i">{{ p }}</li>
        </ol>
        <div class="tiny muted" style="margin-top:8px;line-height:1.7">
          勾选开关<b>不会立刻变出房源</b>。它只决定每日管线是否去抓；抓不抓得到、抓到的是否准确，
          必须在首批数据里人工核对 3~5 条（价格、面积、原始链接能否打开）之后才算数。
          在这之前，本系统不会把采集结果当成可信行情。
        </div>
      </div>

      <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
        <el-button size="small" :loading="crawling" @click="crawl">手动采集一次</el-button>
        <el-button size="small" :loading="recalc" @click="recalcNow">重算评分</el-button>
        <el-button size="small" type="primary" plain :loading="refreshBusy" @click="refreshNow">
          {{ refreshBusy ? '刷新中…' : '立即刷新房源' }}
        </el-button>
        <el-button size="small" type="danger" plain :loading="purging" @click="purgeDemo">清除演示样本</el-button>
        <span class="tiny muted" style="align-self:center">以上操作需要管理口令</span>
      </div>
      <div v-if="crawlReport" class="tiny" :class="crawlReport.status === 'done' ? 'crawl-ok' : 'crawl-skip'" style="margin-top:10px">
        {{ crawlText }}
      </div>
    </div>

    <!-- 全自动接入：免验证码通道 + 网页端验证后自动推送 -->
    <div class="card card-pad section-gap">
      <div class="card-title">
        全自动接入
        <span class="hint">验证码只由你本人在网页上完成，系统不识别、不破解</span>
      </div>

      <div class="auto-grid">
        <div class="auto-card">
          <div class="strong small">① 完全不用验证码：政府公示接口</div>
          <div class="tiny muted" style="margin-top:5px;line-height:1.8">
            日照房管业务网的二手房签约/备案公示是公开 JSON 接口，无需登录与验证码，服务端可全自动定时调用。
            <b>但它没有价格字段、数据停更于 2023 年</b>，只适合做核验参考，不能用来更新挂牌价。
          </div>
          <el-button size="small" style="margin-top:8px" :loading="govLoading" @click="checkGov">实测取一次</el-button>
          <div v-if="govInfo" class="tiny" style="margin-top:6px;color:var(--good)">{{ govInfo }}</div>
        </div>

        <div class="auto-card">
          <div class="strong small">② 58 同城：会话复用，服务端全自动</div>
          <div class="tiny muted" style="margin-top:5px;line-height:1.8">
            58 在短时间连续翻页时会弹人工滑块。装一次「会话桥」扩展后：你在网页上正常访问 →
            扩展把该站会话同步过来，此后由<b>服务端</b>每天 07:30 / 19:30 低频抓首屏列表、
            精确解析入库、自动重算并<b>把结果推送给你</b>，不需要你反复打开页面，也不去撞它的风控。
            会话失效时推送里会提示再打开一次页面。程序不识别、不绕过任何验证码。
          </div>
          <div class="tiny" style="margin-top:6px">
            安装：<code>chrome://extensions</code> → 开发者模式 → 加载已解压的扩展程序 →
            选择 <code>tools/bridge-extension</code> → 在设置里填站点地址、分享码、管理口令。
          </div>
          <div class="tiny muted" style="margin-top:5px">
            另外两条商业路径：58 官方开放平台（需企业资质 + 签名）、第三方数据 API（按量付费），
            都能免验证码全自动，但需要账号与费用。
          </div>
        </div>
      </div>

      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <el-button size="small" :loading="sessionLoading" @click="loadSessions">检查会话状态</el-button>
        <span v-for="(v, k) in sessions" :key="k" class="tag" :class="v.has_cookie ? 'info' : ''">
          {{ k }}：{{ v.has_cookie ? `已存会话 ${v.length} 字符` : '无会话' }}
        </span>
      </div>
      <div v-if="sessionNote" class="tiny" style="margin-top:6px;color:var(--brand-dark)">{{ sessionNote }}</div>
    </div>

    <!-- 导入真实房源 -->
    <div class="card card-pad section-gap">
      <div class="card-title">
        导入真实挂牌
        <span class="hint">唯一能保证「准确和真实」的做法：每条都要有原始页面链接</span>
      </div>
      <div class="import-rule">
        <b>规则：</b>必填 <code>community_name</code>、<code>area</code>、<code>total_wan</code>（万元）、
        <code>source_url</code>。没有 <code>source_url</code> 的行会被拒绝 —— 没有链接就无法回溯核验。
        同小区 + 同户型 + 同面积 + 同总价会自动去重。单批最多 500 条。
      </div>

      <div class="field-table" v-if="tpl.fields">
        <div v-for="f in tpl.fields" :key="f.key" class="field-row">
          <code>{{ f.key }}</code>
          <span class="tiny muted">{{ f.desc }}</span>
          <span class="tiny mono example">{{ JSON.stringify(f.example) }}</span>
        </div>
      </div>

      <div style="display:flex;gap:8px;margin:10px 0 8px;flex-wrap:wrap">
        <el-button size="small" @click="fillSample">填入示例</el-button>
        <el-button size="small" @click="downloadTpl">下载字段模板</el-button>
        <el-button size="small" @click="jsonText = ''">清空</el-button>
        <span class="tiny muted" style="align-self:center">格式：JSON 数组</span>
      </div>

      <el-input
        v-model="jsonText"
        type="textarea"
        :rows="9"
        spellcheck="false"
        placeholder='[{"community_name":"示例小区","area":128.5,"total_wan":112,"source_url":"https://..."}]'
        class="json-input"
      />

      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;align-items:center">
        <el-select v-model="sourceKey" size="small" style="width:210px">
          <el-option label="人工录入 / 表格整理" value="manual" />
          <el-option v-for="s in (ov.sources || [])" :key="s.key" :label="s.name" :value="s.key" />
        </el-select>
        <el-button type="primary" size="small" :loading="importing" @click="doImport">校验并导入</el-button>
        <span class="tiny muted">导入后需点一次「重算评分」才会参与评分与异动检测</span>
      </div>

      <div v-if="importError" class="import-err">{{ importError }}</div>
      <div v-if="importResult" class="import-result">
        <div class="strong tiny">
          结果：收到 {{ importResult.items_in }} 条，入库
          <b style="color:var(--good)">{{ importResult.items_saved }}</b> 条，跳过
          <b style="color:var(--red)">{{ importResult.items_in - importResult.items_saved }}</b> 条
        </div>
        <div class="tiny muted" style="margin-top:4px">{{ importResult.note }}</div>
        <div v-if="(importResult.errors || []).length" class="err-list">
          <div v-for="(e, i) in importResult.errors.slice(0, 20)" :key="i" class="tiny">
            第 {{ e.row }} 行：{{ e.reason }}
          </div>
          <div v-if="importResult.errors.length > 20" class="tiny muted">…另有 {{ importResult.errors.length - 20 }} 条</div>
        </div>
      </div>
    </div>

    <!-- 导入批次 -->
    <div class="card card-pad section-gap">
      <div class="card-title">导入批次 <span class="hint">最近 30 批，可逐批回溯</span></div>
      <el-empty v-if="!batches.length" description="还没有导入过真实房源" :image-size="62" />
      <div v-else class="batch-list">
        <div v-for="b in batches" :key="b.id" class="batch-row">
          <span class="tiny muted mono">#{{ b.id }} · {{ b.created_at }}</span>
          <span class="strong small">{{ b.source_name || b.source_key }}</span>
          <span class="tiny">收到 {{ b.items_in }} / 入库 <b>{{ b.items_saved }}</b> / 跳过 {{ b.items_skipped }}</span>
          <span v-if="b.note" class="tiny muted">{{ b.note }}</span>
        </div>
      </div>
    </div>

    <div class="disclaimer section-gap">
      本页如实标注数据来源与核验状态。挂牌价≠成交价；人工录入仅记录原始页面链接，不代表系统已核实其内容。
      任何房源在看房、查产权、核实挂牌真实性之前，都不构成购买建议。
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const loading = ref(true)
const ov = ref({})
const tpl = ref({})
const batches = ref([])
const jsonText = ref('')
const sourceKey = ref('manual')
const toggling = ref('')
const crawling = ref(false)
const refreshBusy = ref(false)
const govLoading = ref(false)
const govInfo = ref('')
const sessionLoading = ref(false)
const sessions = ref({})
const sessionNote = ref('')
let refreshTimer = null
const purging = ref(false)
const recalc = ref(false)
const importing = ref(false)
const importError = ref('')
const importResult = ref(null)
const crawlReport = ref(null)
// 被验证码拦住的通道（用于提示用户去过一次验证）
const captchaKeys = computed(() =>
  Object.entries(ov.value.last_refresh?.sources || {})
    .filter(([, v]) => v && v.captcha)
    .map(([k]) => k)
)
const captchaBlocked = computed(() => captchaKeys.value.length > 0)

const crawlText = computed(() => {
  const r = crawlReport.value
  if (!r) return ''
  if (r.status === 'skipped') {
    return `未执行采集：${r.reason}。这是预期行为——采集默认关闭，且首批数据必须先人工核验。`
  }
  return `采集已执行，启用通道：${(r.enabled || []).join('、') || '无'}。请到库里逐条核对价格、面积与原始链接。`
})
async function load() {
  loading.value = true
  try {
    const [o, t, b] = await Promise.all([
      api.sourceOverview(),
      api.sourceTemplate(),
      api.sourceBatches().catch(() => ({ items: [] })),
    ])
    ov.value = o
    tpl.value = t
    batches.value = b.items || []
    try { sessions.value = (await api.sessionCookies()).sources || {} } catch { /* 未登录时忽略 */ }
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

async function toggle(s, enabled) {
  toggling.value = s.key
  try {
    await api.toggleSource(s.key, enabled)
    s.enabled = enabled
    ElMessage.success(enabled ? `已勾选「${s.name}」；仍需服务端启用采集并核验首批数据` : `已关闭「${s.name}」`)
  } catch (e) {
    ElMessage.error('操作失败：' + e.message)
  } finally {
    toggling.value = ''
  }
}

async function crawl() {
  crawling.value = true
  try {
    crawlReport.value = await api.crawlSources()
    if (crawlReport.value.status === 'done') await load()
  } catch (e) {
    ElMessage.error('采集失败：' + e.message)
  } finally {
    crawling.value = false
  }
}

async function recalcNow() {
  recalc.value = true
  try {
    await api.runNow(false)
    ElMessage.success('重算完成')
    await load()
  } catch (e) {
    ElMessage.error('重算失败：' + e.message)
  } finally {
    recalc.value = false
  }
}

async function checkGov() {
  govLoading.value = true
  try {
    const r = await api.officialDeals()
    govInfo.value = r.ok
      ? `接口可用：公示总数 ${r.total} 条，本次取回 ${r.count} 条；最新一条 ${(r.items[0] || {}).date || '—'}`
      : '接口暂不可用'
  } catch (e) {
    govInfo.value = '调用失败：' + e.message
  } finally {
    govLoading.value = false
  }
}

async function loadSessions() {
  sessionLoading.value = true
  try {
    const r = await api.sessionCookies()
    sessions.value = r.sources || {}
    const checks = await Promise.all(
      Object.keys(sessions.value).filter(k => sessions.value[k].has_cookie)
        .map(k => api.sessionCheck(k).catch(() => null))
    )
    const bad = checks.filter(Boolean).filter(c => !c.ok)
    sessionNote.value = bad.length
      ? `${bad.map(c => c.key).join('、')} 的会话已失效，请在浏览器里点一次验证后重新同步`
      : (checks.length ? '会话全部有效，每天 07:30 / 19:30 会自动抓取并推送结果' : '还没有会话，按上面 ② 安装扩展并完成一次验证')
  } catch (e) {
    sessionNote.value = '检查失败：' + e.message
  } finally {
    sessionLoading.value = false
  }
}

async function refreshNow() {
  refreshBusy.value = true
  try {
    await api.refreshSources()
    ElMessage.info('已开始刷新，抓完会自动重算')
    pollRefresh(0)
  } catch (e) {
    refreshBusy.value = false
    ElMessage.error('启动刷新失败：' + e.message)
  }
}

const SOURCE_LABELS = { ke: '贝壳', anjuke: '安居客', wuba: '58同城', gov: '政府公示' }
function sourceLabel(k) { return SOURCE_LABELS[k] || k }

function pollRefresh(times) {
  clearTimeout(refreshTimer)
  if (times > 60) { refreshBusy.value = false; return }
  refreshTimer = setTimeout(async () => {
    try {
      const st = await api.refreshStatus()
      if (st.running) { pollRefresh(times + 1); return }
      refreshBusy.value = false
      const srcs = st.result?.refresh?.sources || {}
      const bits = Object.entries(srcs).map(([k, v]) => {
        const label = sourceLabel(k)
        if (v.captcha) return `${label}被验证码拦住`
        return `${label} 新增${v.saved || 0}/更新${v.updated || 0}/调价${v.price_changes || 0}`
      })
      if (st.error) ElMessage.error('刷新失败：' + st.error)
      else ElMessage.success(bits.join('；') || '刷新完成')
      await load()
    } catch (e) {
      refreshBusy.value = false
    }
  }, 4000)
}

async function purgeDemo() {
  try {
    await ElMessageBox.confirm(
      '将删除全部演示样本（含其评分 / 异动 / 价格历史），只保留真实来源的数据，且不可恢复。确定继续？',
      '清除演示样本',
      { type: 'warning', confirmButtonText: '确认清除', cancelButtonText: '先不清' },
    )
  } catch {
    return
  }
  purging.value = true
  try {
    const r = await api.purgeDemo()
    ElMessage.success(
      `已清除：房源 ${r.purged.listing} / 法拍 ${r.purged.auction} / 新盘 ${r.purged.new_home} / 小区 ${r.purged.community}`,
    )
    await load()
  } catch (e) {
    ElMessage.error('清除失败：' + e.message)
  } finally {
    purging.value = false
  }
}

function fillSample() {
  jsonText.value = JSON.stringify(tpl.value.sample || [], null, 2)
  ElMessage.info('示例里的链接是占位符，导入前请换成真实挂牌页')
}

function downloadTpl() {
  const blob = new Blob([JSON.stringify(tpl.value, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'rizhao-import-template.json'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

async function doImport() {
  importError.value = ''
  importResult.value = null
  let rows
  try {
    rows = JSON.parse(jsonText.value)
  } catch (e) {
    importError.value = 'JSON 解析失败：' + e.message
    return
  }
  if (!Array.isArray(rows) || !rows.length) {
    importError.value = '请填入非空的 JSON 数组'
    return
  }
  importing.value = true
  try {
    importResult.value = await api.importListings({ rows, source_key: sourceKey.value })
    ElMessage.success(`导入完成，入库 ${importResult.value.items_saved} 条`)
    await load()
  } catch (e) {
    importError.value = '导入失败：' + e.message
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.truth-card { border-left: 3px solid var(--brand); }
.truth-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 12px 0 14px; }
.truth-cell { background: var(--panel-soft, #f6f9fb); border-radius: 9px; padding: 10px 12px; }
.truth-cell.demo { background: #fff8ec; }
.truth-cell.real { background: #edfaf5; }
.tc-val { font-size: 20px; font-weight: 750; color: var(--ink); }
.tc-key { font-size: 10.5px; color: var(--muted); margin-top: 2px; }
.honesty-note {
  background: #fff8ec; border: 1px solid #f2dfc0; border-radius: 9px;
  padding: 9px 12px; font-size: 12.5px; color: #7a5a24; line-height: 1.7;
}
.auto-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.auto-card { border: 1px solid var(--line); border-radius: 9px; padding: 11px 13px; background: var(--panel-soft, #f6f9fb); }
.src-head, .src-row {
  display: grid; grid-template-columns: minmax(220px, 2.4fr) 80px minmax(130px, 1.2fr) 130px 60px;
  gap: 10px; align-items: center;
}
.src-head { padding: 0 4px 6px; border-bottom: 1px solid var(--line); }
.src-row { padding: 11px 4px; border-bottom: 1px dashed var(--line); font-size: 12.5px; }
.src-row:last-child { border-bottom: none; }
.src-name a { color: var(--ink); text-decoration: none; }
.src-name a:hover { color: var(--brand); text-decoration: underline; }
.has-real { color: var(--good); }
.no-real { color: var(--muted); }
.precond {
  margin-top: 14px; background: var(--panel-soft, #f6f9fb);
  border-radius: 9px; padding: 11px 14px 11px 28px; font-size: 12px; color: var(--ink);
}
.precond ol { margin: 0; padding-left: 16px; line-height: 1.9; color: var(--muted); }
.crawl-ok { color: var(--good); }
.crawl-skip { color: #a16207; }
.import-rule {
  background: var(--panel-soft, #f6f9fb); border-radius: 9px;
  padding: 9px 12px; font-size: 12.5px; line-height: 1.8; color: var(--ink);
}
code { background: rgba(14,116,144,.09); color: var(--brand-dark); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }
.field-table { margin-top: 10px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 14px; }
.field-row { display: flex; align-items: baseline; gap: 8px; padding: 4px 0; border-bottom: 1px dashed var(--line); min-width: 0; }
.field-row .example { margin-left: auto; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 40%; }
:deep(.json-input textarea) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.6; }
.import-err { margin-top: 10px; color: var(--red); font-size: 12.5px; }
.import-result { margin-top: 10px; border: 1px solid var(--line); border-radius: 9px; padding: 10px 12px; }
.err-list { margin-top: 7px; border-top: 1px dashed var(--line); padding-top: 7px; color: var(--muted); max-height: 170px; overflow-y: auto; }
.batch-list { display: flex; flex-direction: column; }
.batch-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px dashed var(--line); font-size: 12.5px; flex-wrap: wrap; }
.batch-row:last-child { border-bottom: none; }

@media (max-width: 860px) {
  .auto-grid { grid-template-columns: 1fr; }
  .truth-grid { grid-template-columns: repeat(3, 1fr); }
  .src-head { display: none; }
  .src-row {
    grid-template-columns: 1fr auto; gap: 6px 10px;
    border: 1px solid var(--line); border-radius: 9px; padding: 10px; margin-bottom: 8px;
  }
  .src-name { grid-column: 1 / -1; }
  .field-table { grid-template-columns: 1fr; }
}
</style>
