<!--
  管理台：回答三个问题
    1. 网站配置在哪儿？（.env / 管理台两处，哪些必须改文件、哪些能在线改）
    2. 多久自动更新一次？（定时增量计算 + 是否抓新数据）
    3. 我怎么管理？（口令登录、企微配置、手动重算）
  权限：本页所有写操作都需要口令；其他人打开网站只能看，改不了任何东西。
-->
<template>
  <div>
    <!-- 未登录 -->
    <div v-if="!authed" class="login-wrap">
      <div class="card login-card">
        <div class="login-icon">🔐</div>
        <div class="login-title">管理台</div>
        <div class="login-sub">
          这个网站只有持口令的人能修改数据与配置，其他人只能查看房源。
        </div>

        <el-input
          v-model="password"
          type="password"
          show-password
          placeholder="请输入管理员口令"
          size="large"
          style="margin-bottom:12px"
          @keyup.enter="doLogin"
        />
        <el-button type="primary" size="large" style="width:100%" :loading="logging" @click="doLogin">
          登录
        </el-button>

        <div v-if="!required" class="hint-box" style="margin-top:12px">
          当前未启用口令校验（ADMIN_ENABLED=0），任何人都能修改。建议在 <code>.env</code> 里设
          <code>ADMIN_ENABLED=1</code> 并重启。
        </div>

        <div class="hint-box" style="margin-top:14px">
          <div class="hb-title">口令在哪？</div>
          <div>第一次启动时后端会自动生成一个口令，写在项目 <code>data/admin_token.txt</code>，
            同时打印在启动日志里（搜「管理员口令」）。</div>
          <div style="margin-top:6px">你也可以在项目根目录 <code>.env</code> 里自己指定：
            <code>ADMIN_TOKEN=你的口令</code>，重启后生效。</div>
        </div>
      </div>
    </div>

    <!-- 已登录 -->
    <div v-else>
      <div class="admin-top card">
        <div>
          <div class="at-title">已登录 · 你拥有完整管理权限</div>
          <div class="tiny muted">其他人打开同一个链接只能查看，不能修改任何数据</div>
        </div>
        <el-button size="small" @click="logout">退出登录</el-button>
      </div>

      <!-- 运行状态 -->
      <div class="stat-grid section-gap">
        <div class="card stat-card">
          <div class="sc-val mono">{{ counts.listing }}</div>
          <div class="sc-key">在售二手</div>
        </div>
        <div class="card stat-card">
          <div class="sc-val mono">{{ counts.auction }}</div>
          <div class="sc-key">法拍在拍</div>
        </div>
        <div class="card stat-card">
          <div class="sc-val mono">{{ counts.new_home }}</div>
          <div class="sc-key">在售新盘</div>
        </div>
        <div class="card stat-card">
          <div class="sc-val mono">{{ counts.community }}</div>
          <div class="sc-key">覆盖小区</div>
        </div>
        <div class="card stat-card">
          <div class="sc-val mono">{{ counts.event }}</div>
          <div class="sc-key">异动记录</div>
        </div>
        <div class="card stat-card">
          <div class="sc-val mono">{{ counts.alert }}</div>
          <div class="sc-key">推送日志</div>
        </div>
      </div>

      <!-- 法拍成交批量回填：真实成交价锚点的人工入口 -->
      <div class="card card-pad section-gap">
        <div class="card-title">
          法拍成交批量回填
          <span class="hint">真实成交价锚点 · 人工录入，不是自动采集</span>
        </div>
        <div class="tiny muted" style="line-height:1.85;margin-bottom:8px">
          粘贴 CSV，列头固定为 <code>auction_id,deal_price,deal_date</code>（金额单位：元，日期 YYYY-MM-DD）。
          单行填错只跳过该行并给出原因，不影响其它行。回填后标的标记为已成交、从「在拍机会」移出，
          并出现在 <a style="color:var(--brand);cursor:pointer" @click="$router.push('/deals')">成交锚点页</a>。
          <strong>法拍成交价 ≠ 二手成交价</strong>，请不要拿它当全城成交均价。
        </div>
        <el-input v-model="dealCsv" type="textarea" :rows="4"
                  placeholder="auction_id,deal_price,deal_date&#10;12,720000,2026-10-01&#10;13,680000,2026-10-05" />
        <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <el-button size="small" type="primary" :loading="importing" @click="importDeals">导入</el-button>
          <el-button size="small" @click="dealCsv = ''">清空</el-button>
          <a class="tiny" style="color:var(--brand);cursor:pointer" @click="$router.push('/deals')">查看成交锚点 →</a>
        </div>
        <div v-if="dealResult" class="tiny" style="margin-top:8px;line-height:1.9">
          成功 <b>{{ dealResult.saved }}</b> 条，跳过 <b>{{ dealResult.skipped }}</b> 条。
          <div v-for="(e, i) in dealResult.errors" :key="i" style="color:#b45309">
            第 {{ e.row }} 行：{{ e.reason }}
          </div>
        </div>
      </div>

      <div class="grid-2 section-gap">
        <!-- 更新机制 -->
        <div class="card card-pad">
          <div class="card-title">数据更新机制 <span class="hint">多久跑一次</span></div>
          <div class="kv-list">
            <div class="kv">
              <span class="k">增量计算（评分 + 异动）</span>
              <b class="v">每天 {{ schedule.daily_at }}</b>
            </div>
            <div class="kv">
              <span class="k">下次执行</span>
              <b class="v">{{ nextRunText }}</b>
            </div>
            <div class="kv">
              <span class="k">上次计算完成</span>
              <b class="v">{{ fmt.date(data.last_score_at) }} {{ timeOf(data.last_score_at) }}</b>
            </div>
            <div class="kv">
              <span class="k">定时任务状态</span>
              <b class="v" :class="schedule.running ? 'ok' : 'warn'">
                {{ schedule.running ? '运行中' : (schedule.enabled ? '已开启但未运行' : '已关闭（ENABLE_SCHEDULER=0）') }}
              </b>
            </div>
            <div class="kv">
              <span class="k">观察周报</span>
              <b class="v">{{ schedule.weekly }}</b>
            </div>
          </div>

          <div class="note-box" :class="crawl.enabled ? 'note-ok' : 'note-warn'">
            <div class="nb-title">{{ crawl.enabled ? '✅ 会自动抓取新数据' : '⚠️ 当前不会抓取新数据' }}</div>
            <div class="nb-body">{{ crawl.note }}</div>
          </div>

          <el-button size="small" :loading="running" style="margin-top:10px" @click="runNow">
            立即重算一次
          </el-button>
        </div>

        <!-- 配置位置 -->
        <div class="card card-pad">
          <div class="card-title">配置在哪改 <span class="hint">两类，别搞混</span></div>

          <div class="cfg-block">
            <div class="cb-title">① 可以在这里在线改（保存即生效，不用重启）</div>
            <div class="cb-body">
              企业微信 Webhook、企微应用 Secret、高德地图 Key 等。
              存在数据库里，优先级高于 <code>.env</code>。
            </div>
          </div>

          <div class="cfg-block">
            <div class="cb-title">② 必须改文件（改完要重启服务）</div>
            <div class="cb-body">
              <code>.env</code>（项目根目录）——定时时间、评分阈值、抓取开关、监听端口等。
              首次使用可 <code>copy .env.example .env</code>。
            </div>
          </div>

          <div class="kv-list" style="margin-top:4px">
            <div class="kv"><span class="k">数据库文件</span><b class="v mono tiny">{{ data.db_path }}</b></div>
            <div class="kv"><span class="k">口令来源</span><b class="v">{{ security.token_source }}</b></div>
            <div class="kv"><span class="k">口令文件</span><b class="v mono tiny">{{ security.token_file }}</b></div>
          </div>
        </div>
      </div>

      <!-- 企业微信配置 -->
      <div class="card card-pad section-gap">
        <div class="card-title">
          企业微信推送配置
          <span class="hint">填完保存立刻生效，不用重启</span>
          <span v-if="webhookReady" class="tag info" style="margin-left:8px">已接通</span>
          <span v-else class="tag warn" style="margin-left:8px">未配置</span>
        </div>

        <div class="ch-steps">
          <div class="step-item"><span class="step-no">1</span>手机装「企业微信」，手机号注册即可（个人也能注册，企业名称随便填，不用营业执照）</div>
          <div class="step-item"><span class="step-no">2</span>建一个内部群 → 右上角「…」→「消息推送」（旧版叫「群机器人」）→ 添加</div>
          <div class="step-item"><span class="step-no">3</span>点开刚建的机器人 → 复制「Webhook 地址」，整条粘到下面输入框 → 保存</div>
          <div class="step-item"><span class="step-no">4</span>点「发送测试消息」，群里弹出卡片即接通；没收到，下面会翻译报错原因</div>
        </div>

        <el-form label-position="top" size="default" style="margin-top:14px">
          <el-form-item v-for="f in wecomFields" :key="f.key" :label="f.label">
            <el-input
              v-model="form[f.key]"
              :placeholder="f.configured ? `当前已配置：${f.display}` : f.placeholder"
              :type="f.secret ? 'password' : 'text'"
              :show-password="f.secret"
              clearable
            />
            <div class="field-help">
              {{ f.help }}
              <span v-if="f.configured" class="tag" style="margin-left:6px">来自{{ f.from }}</span>
            </div>
          </el-form-item>
        </el-form>

        <div class="form-actions">
          <el-button type="primary" :loading="saving" @click="saveIntegration">保存配置</el-button>
          <el-button :loading="testing" @click="sendTest">发送测试消息</el-button>
          <span class="tiny muted">
            测试消息会发到当前生效的通道；未配置任何通道时会落到「站内提醒」。
          </span>
        </div>

        <div v-if="testResult" class="test-result" :class="testResult.ok ? 'tr-ok' : 'tr-fail'">
          {{ testResult.ok ? '✅ 发送成功' : '❌ 发送失败' }}
          <span class="tiny">通道：{{ testResult.channel_label || testResult.channel }}</span>
          <div v-if="testResult.hint" class="tr-hint">💡 {{ testResult.hint }}</div>
          <pre v-if="testResult.response" class="tr-resp">{{ testResult.response }}</pre>
        </div>

        <div class="kv-list" style="margin-top:14px">
          <div class="kv">
            <span class="k">当前生效通道</span>
            <b class="v">{{ push.channel_label }}</b>
          </div>
          <div class="kv">
            <span class="k">各通道状态</span>
            <span class="v">
              <span v-for="c in push.channels" :key="c.key" class="tag" :class="c.active ? 'info' : ''" style="margin-right:4px">
                {{ c.label }}{{ c.available ? '' : '（未配）' }}
              </span>
            </span>
          </div>
        </div>
      </div>

      <!-- 地图底图配置（可选） -->
      <div class="card card-pad section-gap">
        <div class="card-title">
          地图底图配置（可选）
          <span class="hint">不配也能用，只是底图是示意投影</span>
          <span v-if="mapReady" class="tag info" style="margin-left:8px">已接通</span>
          <span v-else class="tag warn" style="margin-left:8px">未配置</span>
        </div>

        <div class="hint-box">
          <div class="hb-title">高德 Key 怎么申请（约 5 分钟，免费）</div>
          <div>1. 打开 <code>console.amap.com</code> → 用手机号注册并登录（个人开发者即可，不用企业资质）</div>
          <div>2. 右上角「控制台」→ 左侧「应用管理 → 我的应用」→「创建新应用」（名称随意，类型选出行/生活）</div>
          <div>3. 该应用右侧「添加 Key」→ <b>服务平台必须选「Web端(JS API)」</b> → 提交</div>
          <div>4. 把生成的 <b>Key</b> 和同页下方的 <b>安全密钥（jscode）</b> 一起粘到下面两栏，保存即可</div>
          <div style="margin-top:6px">注意：选成「Web服务」的 Key 用不了（那是给后端调接口的），必须选 JS API。</div>
        </div>

        <el-form label-position="top" size="default" style="margin-top:14px">
          <el-form-item v-for="f in mapFields" :key="f.key" :label="f.label">
            <el-input
              v-model="form[f.key]"
              :placeholder="f.configured ? `当前已配置：${f.display}` : f.placeholder"
              :type="f.secret ? 'password' : 'text'"
              :show-password="f.secret"
              clearable
            />
            <div class="field-help">
              {{ f.help }}
              <span v-if="f.configured" class="tag" style="margin-left:6px">来自{{ f.from }}</span>
            </div>
          </el-form-item>
        </el-form>

        <div class="form-actions">
          <el-button type="primary" :loading="saving" @click="saveIntegration">保存配置</el-button>
          <span class="tiny muted">保存后打开「地图」页面刷新一次即可看到真实底图。</span>
        </div>
      </div>

      <!-- 数据备份 -->
      <div class="card card-pad section-gap">
        <div class="card-title">数据备份（你自己攒的东西不能丢）</div>

        <div class="bk-split">
          <div class="bk-col bk-yes">
            <div class="bk-h">会备份</div>
            <ul>
              <li>观察清单（含跟进状态、备注、加观察时的价格）</li>
              <li>自住偏好（板块 / 面积 / 户型 / 首付比例 / 利率）</li>
              <li>推送配置（阈值、红黄开关、静默时段）</li>
              <li>在线配置里的企微 Webhook 与密钥、管理口令</li>
            </ul>
          </div>
          <div class="bk-col bk-no">
            <div class="bk-h">不备份</div>
            <ul>
              <li>小区、房源、法拍、新盘——可由样本或抓取重建</li>
              <li>评分与异动结果——每天自动重算</li>
            </ul>
            <div class="tiny muted" style="margin-top:6px">
              这样备份文件只有几十 KB，能直接发到自己微信里存着。
            </div>
          </div>
        </div>

        <div class="bk-actions">
          <el-button type="primary" :loading="bkBusy.download" @click="doDownload">
            下载备份文件
          </el-button>
          <el-button :loading="bkBusy.snapshot" @click="doSnapshot">立即快照整库</el-button>
          <el-button :loading="bkBusy.restore" @click="pickFile">从备份文件恢复</el-button>
          <input ref="fileInput" type="file" accept=".json,application/json" style="display:none" @change="onRestoreFile" />
        </div>

        <div v-if="bk" class="kv-list" style="margin-top:12px">
          <div class="kv">
            <span class="k">当前资产</span>
            <span class="v">
              观察 <b>{{ bk.asset_counts.watch_item }}</b> 条 ·
              偏好 <b>{{ bk.asset_counts.user_pref }}</b> 份 ·
              推送配置 <b>{{ bk.asset_counts.alert_config }}</b> 份
            </span>
          </div>
          <div class="kv">
            <span class="k">整库快照</span>
            <span class="v">
              <b>{{ bk.snapshot_count }}</b> 份（保留最近 {{ bk.keep }} 份，库 {{ bk.db_size_kb }} KB）
            </span>
          </div>
          <div v-if="bk.snapshots.length" class="kv">
            <span class="k">最近快照</span>
            <span class="v tiny">
              <span v-for="s in bk.snapshots.slice(0, 3)" :key="s.name" class="bk-snap">
                {{ s.at.replace('T', ' ').slice(5, 16) }} · {{ s.size_kb }}KB
              </span>
            </span>
          </div>
        </div>

        <div class="note-box note-info" style="margin-top:12px">
          <div class="nb-title">三重保险，各管一段</div>
          <div class="nb-body">
            <b>每日自动快照</b>——每天跑增量前先备份整个库，出问题能回到前一天；<br />
            <b>手动下载备份</b>——几十 KB 的 JSON，存手机/网盘，换了设备或换了服务器都能导入回来；<br />
            <b>重灌数据不伤人</b>——升级演示样本前会先把你这些数据取出来，灌完再还回去。
          </div>
        </div>
      </div>

      <!-- 安全说明 -->
      <div class="card card-pad section-gap">
        <div class="card-title">权限与安全</div>
        <div class="sec-list">
          <div class="sec-row">
            <span class="sec-badge ok">可看</span>
            <div>
              <div class="sec-name">所有只读页面</div>
              <div class="tiny muted">首页、异动、地图、小区、房源详情、新房、改善专区——拿到链接的人都能看</div>
            </div>
          </div>
          <div class="sec-row">
            <span class="sec-badge lock">需口令</span>
            <div>
              <div class="sec-name">所有修改操作</div>
              <div class="tiny muted">观察清单增删、自住偏好、推送配置、企微配置、手动重算——没有口令一律 401</div>
            </div>
          </div>
          <div class="sec-row">
            <span class="sec-badge lock">已脱敏</span>
            <div>
              <div class="sec-name">密钥回显</div>
              <div class="tiny muted">Webhook、Secret 等敏感项只显示尾号，不会把完整密钥发给浏览器</div>
            </div>
          </div>
        </div>

        <div class="note-box note-info">
          <div class="nb-title">想换口令？</div>
          <div class="nb-body">
            在项目根目录 <code>.env</code> 里加一行 <code>ADMIN_TOKEN=新口令</code>，重启服务即可；
            环境变量优先级最高，会覆盖数据库里自动生成的那个。改完记得在各设备上重新登录一次。
          </div>
        </div>
      </div>

      <div class="disclaimer section-gap">
        管理台改的配置存在数据库里；<code>.env</code> 是冷启动默认值。两者冲突时以管理台为准。
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, auth, fmt } from '../api'

const authed = ref(false)
const required = ref(true)
const password = ref('')
const logging = ref(false)
const running = ref(false)
const saving = ref(false)
const testing = ref(false)
const testResult = ref(null)
const dealCsv = ref('')
const importing = ref(false)
const dealResult = ref(null)

async function importDeals() {
  if (!dealCsv.value.trim()) {
    ElMessage.warning('请先粘贴 CSV 内容')
    return
  }
  importing.value = true
  dealResult.value = null
  try {
    dealResult.value = await api.importAuctionResults({ csv: dealCsv.value })
    const r = dealResult.value
    ElMessage[r.saved ? 'success' : 'warning'](
      r.saved ? `已回填 ${r.saved} 条成交价，跳过 ${r.skipped} 条` : `没有成功回填的行，跳过 ${r.skipped} 条`
    )
  } catch (e) {
    ElMessage.error('导入失败：' + e.message)
  } finally {
    importing.value = false
  }
}

const integration = ref([])
const webhookReady = ref(false)
const mapReady = ref(false)
// 表单分两块：企业微信 / 地图底图（后端给 group，前端不写死字段名）
const wecomFields = computed(() => integration.value.filter((f) => f.group !== 'map'))
const mapFields = computed(() => integration.value.filter((f) => f.group === 'map'))
const form = reactive({})

const counts = ref({})
const data = ref({})
const schedule = ref({})
const crawl = ref({})
const security = ref({})
const push = ref({ channels: [] })

const now = ref(Date.now())
let timer = null

const nextRunText = computed(() => {
  const target = schedule.value.next_daily_run
  if (!target) return '—'
  const t = new Date(target).getTime()
  if (Number.isNaN(t)) return target
  const diff = t - now.value
  if (diff <= 0) return '即将执行'
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  const s = Math.floor((diff % 60000) / 1000)
  const when = new Date(t)
  const clock = `${String(when.getHours()).padStart(2, '0')}:${String(when.getMinutes()).padStart(2, '0')}`
  return h > 0 ? `${clock}（还有 ${h} 小时 ${m} 分）` : `${clock}（还有 ${m} 分 ${s} 秒）`
})

function timeOf(iso) {
  if (!iso) return ''
  const s = String(iso)
  return s.length > 10 ? s.slice(11, 19) : ''
}

async function doLogin() {
  if (!password.value) {
    ElMessage.warning('请输入口令')
    return
  }
  logging.value = true
  try {
    const r = await api.adminLogin(password.value)
    if (r.token) auth.set(r.token)
    authed.value = true
    password.value = ''
    ElMessage.success('登录成功，本浏览器会记住口令')
    await loadAll()
  } catch (e) {
    // 401 的 detail 里带着「还可再试几次」，直接展示比笼统报错有用
    ElMessage.error(e.name === 'AuthError' ? (e.message || '口令不正确') : '登录失败：' + e.message)
  } finally {
    logging.value = false
  }
}

function logout() {
  auth.clear()
  authed.value = false
  ElMessage.info('已退出，之后只能查看')
}

async function loadStatus() {
  try {
    const s = await api.adminStatus()
    counts.value = s.counts
    data.value = s.data
    schedule.value = s.schedule
    crawl.value = s.crawl
    security.value = s.security
    push.value = s.push
  } catch (e) {
    ElMessage.error('状态加载失败：' + e.message)
  }
}

async function loadIntegration() {
  try {
    const d = await api.integration()
    integration.value = d.items
    webhookReady.value = d.webhook_ready
    mapReady.value = !!d.map_ready
    // 不回填真实密钥：留空表示「保持不变」
    d.items.forEach((f) => { form[f.key] = '' })
  } catch (e) {
    ElMessage.error('集成配置加载失败：' + e.message)
  }
}

async function saveIntegration() {
  saving.value = true
  try {
    // 只提交有填写的字段：空串代表「不改」，避免误清空已配好的 Webhook
    const payload = {}
    Object.entries(form).forEach(([k, v]) => {
      if (v !== '' && v !== null && v !== undefined) payload[k] = v
    })
    if (!Object.keys(payload).length) {
      ElMessage.info('没有填写新内容，未做修改')
      return
    }
    const r = await api.saveIntegration(payload)
    webhookReady.value = r.webhook_ready
    mapReady.value = !!r.map_ready
    integration.value = r.items
    Object.keys(form).forEach((k) => { form[k] = '' })
    ElMessage.success('已保存，立即生效')
    await loadStatus()
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

async function sendTest() {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await api.testIntegration()
  } catch (e) {
    ElMessage.error('测试失败：' + e.message)
  } finally {
    testing.value = false
  }
}

async function runNow() {
  running.value = true
  try {
    const s = await api.runNow(true)
    ElMessage.success(`重算完成：${s.scored} 条评分 · ${s.events} 条异动 · 推送 ${s.pushed} 条`)
    await loadStatus()
  } catch (e) {
    ElMessage.error('重算失败：' + e.message)
  } finally {
    running.value = false
  }
}

async function loadAll() {
  await Promise.all([loadStatus(), loadIntegration(), loadBackup()])
}

// --------------------------------------------------------------------------
// 数据备份
// --------------------------------------------------------------------------
const bk = ref(null)
const fileInput = ref(null)
const bkBusy = reactive({ download: false, snapshot: false, restore: false })

async function loadBackup() {
  try {
    bk.value = await api.backup()
  } catch (e) {
    // 备份信息拿不到不影响管理台其它功能
  }
}

async function doDownload() {
  bkBusy.download = true
  try {
    await api.backupDownload()
    ElMessage.success('已下载，建议存到自己微信/网盘里')
  } catch (e) {
    ElMessage.error('导出失败：' + e.message)
  } finally {
    bkBusy.download = false
  }
}

async function doSnapshot() {
  bkBusy.snapshot = true
  try {
    const r = await api.backupSnapshot()
    ElMessage.success(`快照完成：${r.file}（${r.size_kb}KB）`)
    await loadBackup()
  } catch (e) {
    ElMessage.error('快照失败：' + e.message)
  } finally {
    bkBusy.snapshot = false
  }
}

function pickFile() {
  fileInput.value?.click()
}

async function onRestoreFile(ev) {
  const file = ev.target.files?.[0]
  ev.target.value = ''            // 允许重复选同一个文件
  if (!file) return
  bkBusy.restore = true
  try {
    const text = await file.text()
    let payload
    try {
      payload = JSON.parse(text)
    } catch (e) {
      ElMessage.error('这个文件不是合法的 JSON')
      return
    }
    const counts = payload?.counts || {}
    await ElMessageBox.confirm(
      `即将用这份备份覆盖当前的观察清单、偏好与推送配置。\n\n` +
      `备份时间：${payload?.exported_at || '未知'}\n` +
      `包含：观察 ${counts.watch_item ?? 0} 条 / 偏好 ${counts.user_pref ?? 0} 份 / 配置 ${counts.alert_config ?? 0} 份\n\n` +
      `房源、评分、异动这些数据不受影响。继续？`,
      '确认恢复',
      { confirmButtonText: '覆盖恢复', cancelButtonText: '取消', type: 'warning' },
    )
    const r = await api.backupRestore(payload, true)
    const dropped = r.dropped_dangling || 0
    ElMessage.success(
      `恢复完成：观察 ${r.restored?.watch_item ?? 0} 条` +
      (dropped ? `，另有 ${dropped} 条指向已不存在的房源已清理` : ''),
    )
    await loadAll()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('恢复失败：' + e.message)
  } finally {
    bkBusy.restore = false
  }
}

onMounted(async () => {
  timer = setInterval(() => { now.value = Date.now() }, 1000)
  try {
    const st = await api.adminCheck()
    required.value = st.required
    authed.value = st.authed
    if (st.authed) await loadAll()
  } catch (e) {
    // 后端没起来时保持登录页
  }
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.login-wrap { display: flex; justify-content: center; padding-top: 40px; }
.login-card { width: 100%; max-width: 460px; padding: 26px 26px 22px; text-align: center; }
.login-icon { font-size: 34px; }
.login-title { font-size: 20px; font-weight: 750; margin-top: 6px; }
.login-sub { font-size: 12.5px; color: var(--muted); line-height: 1.7; margin: 8px 0 20px; }
.hint-box {
  text-align: left; background: #f6f9fb; border-radius: 9px; padding: 11px 13px;
  font-size: 12px; color: #4a5768; line-height: 1.75;
}
.hb-title { font-weight: 700; margin-bottom: 5px; }
.hint-box code { background: #eaf1f5; padding: 1px 5px; border-radius: 4px; font-size: 11px; }

.admin-top {
  display: flex; align-items: center; gap: 12px; padding: 12px 16px;
  background: linear-gradient(135deg, #f2fbfd, #ffffff); border-left: 3px solid var(--brand);
}
.at-title { font-size: 14px; font-weight: 700; }

.stat-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; }
.stat-card { padding: 11px 13px; }
.sc-val { font-size: 18px; font-weight: 720; }
.sc-key { font-size: 10.5px; color: var(--muted); margin-top: 2px; }

.kv-list { display: flex; flex-direction: column; gap: 7px; }
.kv { display: flex; justify-content: space-between; gap: 12px; font-size: 12.5px; }
.kv .k { color: var(--muted); flex: 0 0 auto; }
.kv .v { text-align: right; word-break: break-all; }
.kv .v.ok { color: #0e7490; }
.kv .v.warn { color: #b45309; }

.note-box { border-radius: 9px; padding: 10px 12px; margin-top: 12px; font-size: 12px; line-height: 1.7; }
.note-warn { background: #fff8ec; border: 1px solid #f2dfc0; color: #7a5a24; }
.note-ok { background: #f0fbf7; border: 1px solid #bfe8d8; color: #14634a; }
.note-info { background: #f2f8fb; border: 1px solid #d8e7ee; color: #3c5c6b; }
.nb-title { font-weight: 700; margin-bottom: 3px; }
.note-box code { background: rgba(0, 0, 0, .05); padding: 1px 5px; border-radius: 4px; font-size: 11px; }

.cfg-block { border-left: 2px solid var(--line); padding-left: 11px; margin-bottom: 12px; }
.cb-title { font-size: 12.5px; font-weight: 650; }
.cb-body { font-size: 11.5px; color: var(--muted); line-height: 1.7; margin-top: 2px; }
.cfg-block code, .kv code { background: #eef2f6; padding: 1px 5px; border-radius: 4px; font-size: 11px; }

.ch-steps { display: flex; flex-direction: column; gap: 7px; margin-bottom: 4px; }
.step-item {
  display: flex; align-items: center; gap: 9px; font-size: 12.5px; color: #4a5768;
  background: #f7fafb; border-radius: 8px; padding: 8px 11px;
}
.step-no {
  flex: 0 0 19px; height: 19px; border-radius: 50%; background: var(--brand); color: #fff;
  display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700;
}
.field-help { font-size: 11px; color: var(--muted); line-height: 1.65; margin-top: 3px; }
.form-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

.test-result {
  margin-top: 12px; border-radius: 9px; padding: 10px 12px; font-size: 12.5px;
  display: flex; flex-direction: column; gap: 4px;
}
.tr-ok { background: #f0fbf7; border: 1px solid #bfe8d8; color: #14634a; }
.tr-fail { background: #fdf2f4; border: 1px solid #f4ccd5; color: #a41c3c; }
.tr-resp {
  margin: 4px 0 0; font-size: 10.5px; font-family: var(--mono); white-space: pre-wrap;
  max-height: 120px; overflow: auto; color: var(--muted);
}
.tr-hint {
  margin-top: 6px; padding: 6px 9px; border-radius: 6px;
  background: rgba(255, 255, 255, 0.7); border: 1px solid currentColor;
  font-size: 11.5px; line-height: 1.6;
}

.sec-list { display: flex; flex-direction: column; gap: 10px; }
.sec-row { display: flex; gap: 10px; font-size: 12.5px; }
.sec-badge {
  flex: 0 0 auto; font-size: 10.5px; font-weight: 650; padding: 2px 8px;
  border-radius: 20px; height: 19px; display: inline-flex; align-items: center;
}
.sec-badge.ok { color: #0e7490; background: rgba(14, 116, 144, .1); }
.sec-badge.lock { color: #be123c; background: rgba(225, 29, 72, .1); }
.sec-name { font-weight: 650; margin-bottom: 1px; }

/* ---- 数据备份 ---- */
.bk-split { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px; }
.bk-col { border-radius: 9px; padding: 10px 12px; font-size: 12px; line-height: 1.75; }
.bk-yes { background: rgba(16, 185, 129, .07); border: 1px solid rgba(16, 185, 129, .2); }
.bk-no { background: #f7f9fa; border: 1px solid var(--line, #e6ebef); }
.bk-h { font-weight: 700; font-size: 12px; margin-bottom: 4px; }
.bk-yes .bk-h { color: #047857; }
.bk-no .bk-h { color: var(--muted); }
.bk-col ul { margin: 0; padding-left: 16px; }
.bk-col li { margin-bottom: 1px; }
.bk-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.bk-snap { display: inline-block; margin-right: 10px; }
.bk-snap::before { content: '·'; margin-right: 4px; color: var(--muted); }
.bk-snap:first-child::before { content: ''; margin: 0; }

:deep(.el-form-item) { margin-bottom: 14px; }
:deep(.el-form-item__label) { font-size: 12.5px; padding-bottom: 3px !important; }

@media (max-width: 1080px) {
  .stat-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 860px) {
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .kv { flex-direction: column; gap: 2px; }
  .kv .v { text-align: left; }
  .login-wrap { padding-top: 14px; }
  .login-card { padding: 20px 16px; }
  .bk-split { grid-template-columns: 1fr; }
  .bk-actions :deep(.el-button) { flex: 1 1 45%; margin-left: 0 !important; }
}
</style>
