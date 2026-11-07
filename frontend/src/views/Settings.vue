<!-- 推送设置：通道优先企微 Webhook → 企微应用 → 公众号 → 兜底；未配置时降级为站内提醒 -->
<template>
  <div v-loading="loading">
    <div class="row section-gap" style="align-items:flex-start">
      <div style="flex:1 1 58%;display:flex;flex-direction:column;gap:16px">
        <!-- 通道状态 -->
        <div class="card card-pad">
          <div class="card-title">
            推送通道
            <span class="hint">按优先级自动选择，可在下方强制指定</span>
          </div>
          <div v-for="c in channels" :key="c.key" class="channel-row">
            <span class="check-dot" :class="c.available ? 'low' : 'ok'" style="margin-top:6px;background:c.available?'var(--good)':'#cbd5e1'"></span>
            <div style="flex:1">
              <div class="small strong">
                {{ c.label }}
                <span v-if="c.active" class="tag info" style="margin-left:6px">当前生效</span>
                <span v-if="!c.available" class="tag" style="margin-left:6px">未配置</span>
              </div>
              <div class="tiny muted">{{ channelHint(c.key) }}</div>
            </div>
          </div>

          <div class="divider"></div>
          <div class="webhook-cta" @click="$router.push('/admin')">
            <div>
              <div class="wc-title">不想改文件？去管理台在线填</div>
              <div class="tiny muted">企业微信 Webhook / 应用 Secret 可直接在管理台填写，保存即生效，不用重启</div>
            </div>
            <span class="wc-arrow">前往 →</span>
          </div>

          <div class="tiny muted" style="line-height:1.9;margin-top:10px">
            <div><b>或者改配置文件</b>：在项目根目录 <code>.env</code> 中填写，重启后端生效（前端不保存任何密钥）。</div>
            <div class="env-line">企业微信群机器人（推荐，最轻量）：WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx</div>
            <div class="env-line">企业微信应用：WECOM_CORP_ID / WECOM_AGENT_SECRET / WECOM_AGENT_ID / WECOM_TO_USER</div>
            <div class="env-line">公众号模板消息：MP_APPID / MP_SECRET / MP_TEMPLATE_ID / MP_OPENID / MP_TEMPLATE_KEYS</div>
            <div class="env-line">兜底：TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID，或 SMTP_HOST 等邮件参数</div>
            <div style="margin-top:6px">全部留空也不会报错：消息自动降级为「站内提醒」，在右上角铃铛查看。</div>
          </div>
        </div>

        <!-- 推送策略 -->
        <div class="card card-pad">
          <div class="card-title">推送策略</div>
          <el-form :label-width="isMobile ? 'auto' : '132px'" :label-position="isMobile ? 'top' : 'left'" size="default">
            <el-form-item label="总开关">
              <el-switch v-model="form.enabled" :active-value="1" :inactive-value="0" />
            </el-form-item>
            <el-form-item label="指定通道">
              <el-select v-model="form.channel" :style="{ width: isMobile ? '100%' : '220px' }">
                <el-option value="auto" label="自动（按优先级）" />
                <el-option v-for="c in channels" :key="c.key" :value="c.key" :label="c.label" />
              </el-select>
            </el-form-item>
            <el-form-item label="最低推送分数">
              <el-slider v-model="form.min_score" :min="40" :max="90" :step="1" show-input
                :style="{ width: isMobile ? '100%' : '420px' }" />
            </el-form-item>
            <el-form-item label="分级开关">
              <el-checkbox v-model="form.push_red" :true-value="1" :false-value="0">🔴 重点留意（实时）</el-checkbox>
              <el-checkbox v-model="form.push_yellow" :true-value="1" :false-value="0">🟡 建议观察</el-checkbox>
            </el-form-item>
            <el-form-item label="黄级推送方式">
              <el-radio-group v-model="form.yellow_daily">
                <el-radio :value="1">每日汇总（推荐，不逐条轰炸）</el-radio>
                <el-radio :value="0">实时推送</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="静默时段">
              <el-input v-model="form.quiet_hours" :style="{ width: isMobile ? '100%' : '200px' }" placeholder="23:00-07:00" />
              <span class="tiny muted" :style="{ marginLeft: isMobile ? 0 : '10px', marginTop: isMobile ? '4px' : 0, display: 'block' }">静默期内非红级消息不推，红级照推</span>
            </el-form-item>
            <el-form-item label="去重窗口">
              <el-input-number v-model="form.dedup_hours" :min="1" :max="168" />
              <span class="tiny muted" style="margin-left:10px">小时内同一房源不重复推送（价格再降 3% 会重推）</span>
            </el-form-item>
          </el-form>
          <div style="display:flex;gap:10px;margin-top:6px">
            <el-button type="primary" @click="save">保存设置</el-button>
            <el-button @click="testChannel" :loading="testing">发送测试消息</el-button>
          </div>
        </div>
      </div>

      <!-- 用户偏好 -->
      <div style="flex:1 1 38%;display:flex;flex-direction:column;gap:16px">
        <div class="card card-pad">
          <div class="card-title">自住偏好 <span class="hint">影响「自住适配」20% 权重</span></div>
          <el-form :label-width="isMobile ? 'auto' : '90px'" :label-position="isMobile ? 'top' : 'left'" size="small">
            <el-form-item label="关注板块">
              <el-select v-model="pref.district_pref" multiple collapse-tags style="width:100%">
                <el-option v-for="d in allDistricts" :key="d" :value="d" :label="d" />
              </el-select>
            </el-form-item>
            <el-form-item label="面积区间">
              <div style="display:flex;gap:8px;align-items:center">
                <el-input-number v-model="pref.area_min" :min="30" :max="300" controls-position="right" style="width:96px" />
                <span class="muted">—</span>
                <el-input-number v-model="pref.area_max" :min="30" :max="400" controls-position="right" style="width:96px" />
                <span class="tiny muted">㎡</span>
              </div>
            </el-form-item>
            <el-form-item label="优先户型">
              <el-radio-group v-model="pref.rooms_prefer">
                <el-radio-button :value="2">两室</el-radio-button>
                <el-radio-button :value="3">三室</el-radio-button>
                <el-radio-button :value="4">四室</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="硬性要求">
              <el-checkbox v-model="pref.need_elevator" :true-value="1" :false-value="0">需要电梯</el-checkbox>
              <el-checkbox v-model="pref.avoid_top_floor" :true-value="1" :false-value="0">回避顶楼</el-checkbox>
              <el-checkbox v-model="pref.care_school" :true-value="1" :false-value="0">关注学区</el-checkbox>
            </el-form-item>
            <el-form-item label="房龄上限">
              <el-input-number v-model="pref.max_age" :min="5" :max="40" controls-position="right" style="width:110px" />
              <span class="tiny muted" style="margin-left:8px">年</span>
            </el-form-item>
          </el-form>
          <el-button type="primary" size="small" @click="savePref">保存偏好</el-button>
          <div class="tiny muted" style="margin-top:8px">保存后点顶栏「重算」即可按新偏好重新评分。</div>
        </div>

        <div class="card card-pad">
          <div class="card-title">数据与合规</div>
          <div class="tiny muted" style="line-height:1.9">
            <div>· 只抓公开列表页：单站间隔 ≥3 秒、遵守 robots.txt、不登录不破解</div>
            <div>· 去重指纹 md5(source+小区+户型+面积+总价)</div>
            <div>· 单个站点失败不影响整体，抓取结果见 crawl_log</div>
            <div>· 当前抓取状态：<b>{{ health.crawl_enabled ? '已启用' : '未启用（使用演示数据）' }}</b></div>
            <div>· 每日增量计算：{{ health.scheduler ? '已启动（默认 02:30）' : '已关闭（ENABLE_SCHEDULER=0）' }}</div>
          </div>
          <div class="disclaimer" style="margin-top:10px">
            挂牌价 ≠ 成交价。捡漏分只用于缩小看房范围，不能替代实地尽调与产权核验。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { useIsMobile } from '../composables/useIsMobile'

const { isMobile } = useIsMobile()
const loading = ref(true)
const testing = ref(false)
const channels = ref([])
const health = ref({})
const allDistricts = ['新市区', '东港区', '石臼', '老城', '山海天', '高新区', '开发区', '莒县', '五莲', '岚山']

const form = reactive({
  enabled: 1, channel: 'auto', min_score: 60, push_red: 1, push_yellow: 1,
  yellow_daily: 1, quiet_hours: '23:00-07:00', dedup_hours: 24,
})
const pref = reactive({
  district_pref: ['新市区', '东港区', '石臼'], area_min: 80, area_max: 130,
  rooms_prefer: 3, need_elevator: 1, avoid_top_floor: 1, max_age: 25, care_school: 1,
})

function channelHint(key) {
  return {
    wecom_webhook: '群机器人 Webhook：最轻量，一条 URL 即可，推荐先用它跑通',
    wecom_app: '企业微信应用消息：需 corpid / secret / agentid，可发给指定成员',
    mp_template: '公众号模板消息：需已认证服务号 + 模板 ID + 用户 openid 授权',
    telegram: 'Telegram Bot：海外兜底通道',
    email: 'SMTP 邮件：企业邮箱常见配置即可',
    log: '站内提醒：未配置任何外部通道时自动生效，写推送日志 + 前端铃铛',
  }[key] || ''
}

async function load() {
  loading.value = true
  try {
    const [cfg, p, h] = await Promise.all([api.alertConfig(), api.pref(), api.health()])
    channels.value = cfg.channels
    Object.assign(form, {
      enabled: cfg.config.enabled, channel: cfg.config.channel, min_score: cfg.config.min_score,
      push_red: cfg.config.push_red, push_yellow: cfg.config.push_yellow,
      yellow_daily: cfg.config.yellow_daily, quiet_hours: cfg.config.quiet_hours,
      dedup_hours: cfg.config.dedup_hours,
    })
    Object.assign(pref, p)
    health.value = h
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

async function save() {
  try {
    const res = await api.saveAlertConfig({ ...form })
    Object.assign(form, res.config)
    ElMessage.success('推送设置已保存')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  }
}

async function testChannel() {
  testing.value = true
  try {
    const r = await api.testAlert()
    if (r.ok) ElMessage.success(`已通过「${r.channel_label}」发送测试消息`)
    else ElMessage.warning(`通道「${r.channel_label}」返回失败：${r.response}`)
  } catch (e) {
    ElMessage.error('测试失败：' + e.message)
  } finally {
    testing.value = false
  }
}

async function savePref() {
  try {
    await api.savePref({ ...pref })
    ElMessage.success('偏好已保存，点顶栏「重算」生效')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.webhook-cta {
  display: flex; align-items: center; gap: 12px; cursor: pointer;
  background: linear-gradient(135deg, #f2fafc, #f8fbfc);
  border: 1px solid rgba(14, 116, 144, .25); border-radius: 10px;
  padding: 10px 12px; margin-bottom: 4px;
}
.webhook-cta:hover { border-color: var(--brand); }
.wc-title { font-size: 12.5px; font-weight: 650; margin-bottom: 2px; }
.wc-arrow { margin-left: auto; font-size: 12px; color: var(--brand); font-weight: 650; white-space: nowrap; }
.channel-row { display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px dashed var(--line); }
.channel-row:last-of-type { border-bottom: none; }
.env-line {
  font-family: var(--mono); font-size: 11px; background: var(--panel-soft);
  border: 1px solid var(--line); border-radius: 6px; padding: 5px 8px; margin: 4px 0;
  word-break: break-all;
}
</style>
